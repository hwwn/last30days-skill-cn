"""B站（哔哩哔哩）视频搜索 provider（移植契约 §2/§3，B站体 = youtube 体）。

取数方式（务实，标准库 urllib；参考 youtube_yt.py 的 parse/enrich 结构）：

降级路径（PORT_CONTRACT §2）：
  1. wbi 签名接口 ``https://api.bilibili.com/x/web-interface/wbi/search/type``
     —— 需要 wbi 签名（img_key/sub_key 取自 nav 接口）+ buvid cookie。签名
     失败或缺 cookie 时降级。
  2. 普通接口 ``https://api.bilibili.com/x/web-interface/search/type``
     —— 带浏览器 header（含 Referer / 一个临时 buvid3 cookie）尝试免签名。
  3. 仍失败 → 返回 ``[]``（绝不伪造数据），由 SKILL.md 指挥宿主模型用
     WebSearch 补充（``哔哩哔哩 <话题>`` 之类）。

解析 ``data.result`` 视频项，engagement 取 view/like/coin/danmaku/reply。
导出 ``search_bilibili`` + ``parse_bilibili_response``，喂给 normalize 的
``_normalize_bilibili``（B站体）。
"""

import hashlib
import json
import re
import time
import urllib.parse
from functools import reduce
from typing import Any, Dict, List, Optional, Set

from . import http, log
from .relevance import token_overlap_relevance as _compute_relevance

# 深度 → 搜索条数（与契约 §2 的 DEPTH_CONFIG 一致）。
DEPTH_CONFIG = {
    "quick": 15,
    "default": 30,
    "deep": 60,
}

# 简介截断长度（与 youtube_yt.py 的 description[:500] 对齐）。
_DESCRIPTION_MAX = 500

# B站 Web 搜索接口。
_WBI_SEARCH_URL = "https://api.bilibili.com/x/web-interface/wbi/search/type"
_PLAIN_SEARCH_URL = "https://api.bilibili.com/x/web-interface/search/type"
_NAV_URL = "https://api.bilibili.com/x/web-interface/nav"

# 浏览器 header：B站接口对 UA / Referer 较敏感，半公开访问需要伪装成网页端。
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# wbi 签名的 mixin key 重排表（B站固定常量，取自其前端 wbi 算法）。
_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


def _log(msg: str):
    log.source_log("B站", msg, tty_only=False)


# ---------------------------------------------------------------------------
# 查询扩展（移植 youtube_yt.expand_youtube_queries / reddit.expand_reddit_queries
# 的形态：抽核心主题 → 加清洗后的原句 → 按 depth 限量）。
# ---------------------------------------------------------------------------

def _extract_core_subject(topic: str) -> str:
    """抽取核心主题用于 B站搜索。

    NOTE: 与 youtube_yt 一样保留内容类型词（教程/评测/解读 等不在噪声集），
    因为它们能提升 B站站内搜索命中率；只剥离时间/口水 meta 词。
    """
    from .query import extract_core_subject
    # B站专用噪声集：比默认窄，保留内容类型词（教程/评测/解读 等）。
    _BILI_NOISE = frozenset({
        # 时间 / meta（planner 会生成但 B站标题里很少出现）
        '最近', '最新', '近期', '近日', '今年', '去年', '本月',
        '2024', '2025', '2026', '2027',
        # 通用口水 / 推荐 meta
        '推荐', '盘点', '排行', '排行榜', '热门', '火爆',
        # 英文对齐 youtube_yt 的部分
        'best', 'top', 'latest', 'new', 'recent',
    })
    return extract_core_subject(topic, noise=_BILI_NOISE)


def expand_bilibili_queries(topic: str, depth: str) -> List[str]:
    """从话题生成 1~2 个 B站搜索 query。

    对齐 youtube_yt.expand_youtube_queries 的骨架，但去掉 YouTube 专属的
    英文内容类型 OR 拼接（B站搜索对中文 OR 语法不友好），只保留核心主题与
    清洗后的原句。按 depth 限量。
    """
    core = _extract_core_subject(topic)
    queries = [core]

    # 与核心主题不同且不太长时，把清洗后的原句也作为一个变体。
    original_clean = topic.strip().rstrip('?!.。！？')
    if core.lower() != original_clean.lower() and len(original_clean) <= 40:
        queries.append(original_clean)

    # 按 depth 限量（quick 1，default/deep 2）。
    caps = {"quick": 1, "default": 2, "deep": 2}
    cap = caps.get(depth, 2)
    # 去重保序。
    seen: Set[str] = set()
    out: List[str] = []
    for q in queries:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out[:cap]


# ---------------------------------------------------------------------------
# wbi 签名（PORT_CONTRACT §2：复杂时降级）。
# ---------------------------------------------------------------------------

def _get_mixin_key(orig: str) -> str:
    """按 B站固定重排表生成 32 位 mixin key。"""
    return reduce(lambda s, i: s + orig[i], _MIXIN_KEY_ENC_TAB, "")[:32]


def _fetch_wbi_keys(headers: Dict[str, str]) -> Optional[tuple]:
    """从 nav 接口取 (img_key, sub_key)，失败返回 None。"""
    try:
        data = http.get(_NAV_URL, headers=headers, timeout=15, retries=1)
    except Exception as exc:
        _log(f"wbi nav 接口失败，降级普通接口: {exc}")
        return None

    wbi_img = (data.get("data") or {}).get("wbi_img") or {}
    img_url = str(wbi_img.get("img_url") or "")
    sub_url = str(wbi_img.get("sub_url") or "")
    if not img_url or not sub_url:
        return None

    # img_url 形如 https://i0.hdslb.com/bfs/wbi/<key>.png，取末段文件名去扩展名。
    img_key = img_url.rsplit("/", 1)[-1].split(".", 1)[0]
    sub_key = sub_url.rsplit("/", 1)[-1].split(".", 1)[0]
    if not img_key or not sub_key:
        return None
    return img_key, sub_key


def _enc_wbi(params: Dict[str, Any], img_key: str, sub_key: str) -> Dict[str, Any]:
    """对 query 参数做 wbi 签名，返回带 wts/w_rid 的新参数字典。"""
    mixin_key = _get_mixin_key(img_key + sub_key)
    signed = dict(params)
    signed["wts"] = int(time.time())
    # 按 key 排序并过滤特殊字符（B站要求剔除 !'()* ）。
    signed = {k: signed[k] for k in sorted(signed)}
    signed = {
        k: "".join(ch for ch in str(v) if ch not in "!'()*")
        for k, v in signed.items()
    }
    query = urllib.parse.urlencode(signed)
    w_rid = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
    signed["w_rid"] = w_rid
    return signed


# ---------------------------------------------------------------------------
# 取数：wbi 签名 → 普通 header → []
# ---------------------------------------------------------------------------

def _base_headers(buvid: str = "") -> Dict[str, str]:
    headers = {
        "User-Agent": _BROWSER_UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.bilibili.com/",
        "Origin": "https://www.bilibili.com",
    }
    if buvid:
        headers["Cookie"] = f"buvid3={buvid}"
    return headers


def _resolve_buvid(config: Optional[Dict[str, Any]]) -> str:
    """取一个 buvid3 cookie 值。

    config 里若有 ``BILIBILI_COOKIE``（完整 cookie 串或裸 buvid3）则优先用；
    否则造一个临时 buvid3（B站对未登录搜索接受任意 buvid3 占位）。
    """
    cookie = ""
    if config:
        cookie = str(config.get("BILIBILI_COOKIE") or "").strip()
    if cookie:
        m = re.search(r"buvid3=([^;]+)", cookie)
        if m:
            return m.group(1).strip()
        # 整串里没有 buvid3 字段，但调用方给了裸值。
        if ";" not in cookie and "=" not in cookie:
            return cookie
    # 临时占位 buvid3：32 位十六进制 + 固定后缀，B站未登录搜索可接受。
    return hashlib.md5(str(time.time()).encode("utf-8")).hexdigest().upper() + "infoc"


def _do_search(url: str, params: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
    """打一次搜索接口，返回解析后的 JSON dict（异常向上抛）。"""
    return http.get(url, params=params, headers=headers, timeout=20, retries=1)


def search_bilibili(
    query: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    config: Optional[Dict[str, Any]] = None,
    **kw,
) -> Dict[str, Any]:
    """搜索 B站视频。

    降级路径（PORT_CONTRACT §2）：wbi 签名接口 → 普通 header 接口 → []。

    Args:
        query: 搜索话题
        from_date: 起始日期 (YYYY-MM-DD)
        to_date: 结束日期 (YYYY-MM-DD)
        depth: 'quick' / 'default' / 'deep'
        config: 运行配置（可含 BILIBILI_COOKIE）

    Returns:
        ``{"items": [...]}``；失败返回 ``{"items": [], "error": ...}``。
    """
    count = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    queries = expand_bilibili_queries(query, depth)
    if not queries:
        return {"items": []}

    buvid = _resolve_buvid(config)
    headers = _base_headers(buvid)

    # 每条 query 想取的页大小：B站 search/type 单页最多 ~50；用 count 截断。
    page_size = min(count, 50)

    seen_ids: Set[str] = set()
    items: List[Dict[str, Any]] = []
    last_error: Optional[str] = None

    # 尝试取 wbi key（取一次，多 query 复用）。
    wbi_keys = _fetch_wbi_keys(headers)

    for q in queries:
        _log(f"搜索 B站 '{q}' (depth={depth}, page_size={page_size})")
        base_params: Dict[str, Any] = {
            "search_type": "video",
            "keyword": q,
            "page": 1,
            "page_size": page_size,
            "order": "totalrank",  # 综合排序（命中率优先，日期软过滤在 parse 阶段）
        }

        raw: Dict[str, Any] = {}
        # --- 路径 1：wbi 签名接口 ---
        if wbi_keys is not None:
            try:
                signed = _enc_wbi(base_params, wbi_keys[0], wbi_keys[1])
                raw = _do_search(_WBI_SEARCH_URL, signed, headers)
                if raw.get("code") not in (0, None):
                    _log(f"wbi 接口返回 code={raw.get('code')}，降级普通接口")
                    raw = {}
            except Exception as exc:
                last_error = str(exc)
                _log(f"wbi 接口异常，降级普通接口: {exc}")
                raw = {}

        # --- 路径 2：普通 header 接口（免签名）---
        if not raw or raw.get("code") not in (0, None):
            try:
                raw = _do_search(_PLAIN_SEARCH_URL, base_params, headers)
            except http.HTTPError as exc:
                last_error = str(exc)
                _log(f"普通接口失败: {exc}")
                raw = {}
            except Exception as exc:  # 网络/解析异常一律记下并继续下一 query
                last_error = str(exc)
                _log(f"普通接口异常: {exc}")
                raw = {}

        # --- 路径 3：仍失败 → 跳过本 query（最终可能返回 []）---
        if not raw or raw.get("code") not in (0, None):
            continue

        results = ((raw.get("data") or {}).get("result")) or []
        for v in results:
            # search/type 的 result 项 type 字段为 'video' 时才是视频。
            if isinstance(v, dict) and v.get("type") not in (None, "video"):
                continue
            vid = str(v.get("bvid") or v.get("aid") or "")
            if not vid or vid in seen_ids:
                continue
            seen_ids.add(vid)
            items.append(v)

    if not items:
        out: Dict[str, Any] = {"items": []}
        if last_error:
            out["error"] = last_error
        return out

    return {"items": items, "_core_query": queries[0], "_from_date": from_date}


# ---------------------------------------------------------------------------
# 字段映射 helpers
# ---------------------------------------------------------------------------

def _strip_html(text: str) -> str:
    """B站搜索标题里关键词带 <em class="keyword">...</em> 高亮标签，去掉。"""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    # 反转义常见 HTML 实体。
    text = (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    return text.strip()


def _to_int(value: Any) -> int:
    """B站某些计数可能是 '--' 或字符串，安全转 int，失败给 0。"""
    if value is None:
        return 0
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    if not s or not s.lstrip("-").isdigit():
        return 0
    try:
        return int(s)
    except ValueError:
        return 0


def _pubdate_to_date(value: Any) -> Optional[str]:
    """B站 pubdate 为 unix 秒；转 YYYY-MM-DD。无则 None。"""
    ts = _to_int(value)
    if ts <= 0:
        return None
    try:
        return time.strftime("%Y-%m-%d", time.localtime(ts))
    except (ValueError, OSError):
        return None


def _video_url(item: Dict[str, Any]) -> str:
    """构造视频 URL。result 项可能带 arcurl，否则用 bvid 拼。"""
    arcurl = str(item.get("arcurl") or "").strip()
    if arcurl:
        # arcurl 可能是 //www.bilibili.com/... 协议相对形式。
        if arcurl.startswith("//"):
            return "https:" + arcurl
        return arcurl
    bvid = str(item.get("bvid") or "").strip()
    if bvid:
        return f"https://www.bilibili.com/video/{bvid}"
    aid = _to_int(item.get("aid"))
    if aid:
        return f"https://www.bilibili.com/video/av{aid}"
    return ""


# ---------------------------------------------------------------------------
# 解析（移植 youtube_yt.search_youtube 内联解析 + parse_youtube_response 的形态）。
# ---------------------------------------------------------------------------

def parse_bilibili_response(result, query: str = "") -> List[Dict[str, Any]]:
    """把 ``search_bilibili`` 的原始响应解析为 B站体 raw item dict 列表。

    产出形状（PORT_CONTRACT §3 B站体）::

        {id|video_id, title, description, transcript_snippet, url,
         channel_name, date, engagement:{view,like,coin,danmaku,reply},
         top_comments:[{text,likes}], relevance, why_relevant}

    与 youtube_yt 一致：软日期过滤（足够近期则只留近期，否则全留），
    按播放量降序排。空响应返回 ``[]``（绝不伪造）。
    """
    if isinstance(result, list):
        # 已经是 item list（容错：万一上游直接传列表）。
        raw_items = result
        from_date = ""
        core_query = query
    elif isinstance(result, dict):
        raw_items = result.get("items") or []
        from_date = str(result.get("_from_date") or "")
        core_query = str(result.get("_core_query") or query)
    else:
        return []

    if not raw_items:
        return []

    # 打分用核心主题（context 优先用 search_bilibili 记录的核心 query）。
    ranking_subject = core_query or _extract_core_subject(query) if query else core_query

    items: List[Dict[str, Any]] = []
    for v in raw_items:
        if not isinstance(v, dict):
            continue
        title = _strip_html(str(v.get("title") or ""))
        description = _strip_html(str(v.get("description") or v.get("desc") or ""))[:_DESCRIPTION_MAX]
        bvid = str(v.get("bvid") or "")
        aid = _to_int(v.get("aid"))
        video_id = bvid or (f"av{aid}" if aid else "")
        channel = str(v.get("author") or v.get("uploader") or "").strip()
        date_str = _pubdate_to_date(v.get("pubdate") or v.get("senddate"))

        # engagement：view/like/coin/danmaku/reply。
        # search/type 字段名：play(播放)、like(点赞)、video_review(弹幕)、
        # review(评论/回复)；coin 搜索接口通常不返回，缺失给 0。
        engagement = {
            "view": _to_int(v.get("play") or v.get("view")),
            "like": _to_int(v.get("like")),
            "coin": _to_int(v.get("coin")),
            "danmaku": _to_int(v.get("video_review") or v.get("danmaku")),
            "reply": _to_int(v.get("review") or v.get("reply") or v.get("comment")),
        }

        text_for_score = f"{title} {description}".strip()
        relevance = (
            _compute_relevance(ranking_subject, text_for_score)
            if ranking_subject else 0.5
        )

        why = f"B站: {(title or ranking_subject or '')[:60]}"

        items.append({
            "id": video_id,
            "video_id": video_id,
            "title": title,
            "description": description,
            # transcript_snippet 承载简介/字幕；搜索接口拿不到字幕，
            # 用简介兜底（normalize 会把它当 snippet 与正文）。
            "transcript_snippet": description,
            "url": _video_url(v),
            "channel_name": channel,
            "date": date_str,
            "engagement": engagement,
            "top_comments": [],
            "relevance": relevance,
            "why_relevant": why,
        })

    if not items:
        return []

    # 软日期过滤：近期项 >= 3 条则只留近期，否则全留（移植 youtube_yt 逻辑）。
    if from_date:
        recent = [i for i in items if i["date"] and i["date"] >= from_date]
        if len(recent) >= 3:
            items = recent
            _log(f"日期窗口内 {len(items)} 条")
        else:
            _log(f"共 {len(items)} 条（窗口内 {len(recent)} 条，全部保留）")

    # 按播放量降序排（移植 youtube_yt 的 views 降序）。
    items.sort(key=lambda x: x["engagement"]["view"], reverse=True)
    return items
