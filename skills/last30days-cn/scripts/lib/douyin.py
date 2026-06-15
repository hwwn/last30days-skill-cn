"""Douyin (抖音) search client for the last30days-cn pipeline.

取数方式（务实，标准库 urllib，无第三方依赖）：
  - 抖音的搜索/评论/字幕没有稳定的免登录公开接口，本 provider 走第三方爬虫
    服务 **ScrapeCreators** 的抖音端点（`https://api.scrapecreators.com/v1/douyin`），
    用 `http.scrapecreators_headers(token)`（``x-api-key`` 头）鉴权。token 来自
    config 的 ``SCRAPECREATORS_API_KEY``（或显式 ``token=`` kwarg）。
  - ScrapeCreators 抖音端点返回的视频项形状与其 TikTok 端点高度一致
    （``aweme_id`` / ``desc`` / ``statistics`` / ``author`` / ``share_url`` /
    ``text_extra`` / ``create_time``），因此本文件忠实移植参考源 ``tiktok.py``
    的「多查询扩展 → 打接口 → parse → 逐条算 relevance → 字幕/评论补全」流水线，
    只把端点路径、平台名与 URL 模板换成抖音。

降级路径：
  - 无 ``SCRAPECREATORS_API_KEY`` 时**直接返回空**（``search_douyin`` 返回
    ``{"items": [], "error": ...}``，``parse_douyin_response`` 返回 ``[]``）。
  - 任何 HTTP/解析错误都被吞掉并记日志，对应源返回空，**绝不伪造数据**。
    由 SKILL.md 指挥宿主模型用 WebSearch（``抖音`` / ``site:douyin.com``）补充。

移植说明（port contract §2/§3）：这是「短视频体」provider，与小红书共用
``normalize._normalize_shortform_video``。导出 ``search_douyin`` +
``parse_douyin_response``。engagement 按 §3 映射到
``{digg_count(点赞), comment(评论), share(分享)}``（保留播放量 ``views`` 供排序）。
"""

import re
from typing import Any, Dict, List, Optional, Set

from . import dates, http, log
from .relevance import token_overlap_relevance as _compute_relevance

# ScrapeCreators 抖音端点根。与参考源 tiktok.py 的 ``/v1/tiktok`` 同构，
# 只是平台路径改为 ``/v1/douyin``。
SCRAPECREATORS_BASE = "https://api.scrapecreators.com/v1/douyin"
# Profile videos 在 ScrapeCreators 上走 v3（与 tiktok.py 一致的版本约定）。
SCRAPECREATORS_PROFILE_URL = "https://api.scrapecreators.com/v3/douyin/profile/videos"

# Depth configurations: how many results to fetch / captions to extract.
# 对齐契约 DEPTH_CONFIG = {"quick":15,"default":30,"deep":60} 的意图，但抖音
# 单次关键词请求页量更小，这里沿用参考源 tiktok.py 的「条数 + 字幕数」结构。
DEPTH_CONFIG = {
    "quick":   {"results_per_page": 10, "max_captions": 3},
    "default": {"results_per_page": 20, "max_captions": 5},
    "deep":    {"results_per_page": 40, "max_captions": 8},
}

# Max words to keep from each caption
CAPTION_MAX_WORDS = 500


def _resolve_token(token: Optional[str], config: Optional[Dict[str, Any]]) -> Optional[str]:
    """Resolve the ScrapeCreators token: explicit kwarg > config key.

    pipeline 调用时只传 ``config=``；CLI/测试也可直接传 ``token=``。无 token
    时返回 None，调用方据此走降级返回空。
    """
    if token:
        return token
    if config:
        return config.get("SCRAPECREATORS_API_KEY") or None
    return None


def _extract_core_subject(topic: str) -> str:
    """Extract core subject from a verbose query for Douyin search.

    抖音搜索框是字面关键词匹配，与 TikTok 同。这里复用 query.extract_core_subject
    并补一组短视频领域的中文/英文噪声词（最近/最新/推荐/排行/盘点…），CJK 串
    会按分词单元去噪重组（见 port contract §6）。
    """
    from .query import extract_core_subject
    _DOUYIN_NOISE = frozenset({
        # 英文（保留参考源 tiktok.py 的词，便于中英混合 query）
        'best', 'top', 'good', 'great', 'awesome', 'killer',
        'latest', 'new', 'news', 'update', 'updates',
        'trending', 'hottest', 'popular', 'viral',
        'practices', 'features',
        'recommendations', 'advice',
        'prompt', 'prompts', 'prompting',
        'methods', 'strategies', 'approaches',
        # 中文短视频领域噪声/夸饰词
        '最新', '最热', '最火', '爆款', '热门', '推荐', '盘点', '排行',
        '排行榜', '合集', '教程', '攻略', '技巧', '干货', '安利', '种草',
        '测评', '评测', '对比', '神器',
    })
    return extract_core_subject(topic, noise=_DOUYIN_NOISE)


def _infer_query_intent(topic: str) -> str:
    """Tiny local intent classifier for Douyin query expansion.

    保留参考源 tiktok.py 的英文正则分支，并补中文正则（与 port contract §9 的
    planner 中文 intent 口径一致）。
    """
    text = topic.lower().strip()
    if re.search(r"\b(vs|versus|compare|difference between)\b", text) or re.search(r"对比|对决|哪个好|vs", topic):
        return "comparison"
    if re.search(r"\b(how to|tutorial|guide|setup|step by step|deploy|install)\b", text) or re.search(r"怎么|如何|教程|入门|攻略", topic):
        return "how_to"
    if re.search(r"\b(thoughts on|worth it|should i|opinion|review)\b", text) or re.search(r"值不值|怎么样|体验|测评|评测", topic):
        return "opinion"
    if re.search(r"\b(pricing|feature|features|best .* for)\b", text) or re.search(r"价格|多少钱|功能|参数", topic):
        return "product"
    return "breaking_news"


def expand_douyin_queries(topic: str, depth: str) -> List[str]:
    """Generate multiple Douyin search queries from a topic.

    Mirrors tiktok.expand_tiktok_queries():
    1. Extract core subject (strip noise words)
    2. Include original topic if different from core
    3. Add intent-specific content-type variants
    4. Cap by depth: 1 for quick, 2 for default, 3 for deep

    抖音搜索框对长 OR 串支持差，这里把内容形态变体换成抖音常见的中文措辞
    （reaction/edit → 解读/合集；review/haul → 测评/开箱）。

    Returns 1-3 query strings depending on depth.
    """
    core = _extract_core_subject(topic)
    queries = [core]

    # Include cleaned original topic as variant if different from core.
    original_clean = topic.strip().rstrip('?！？。!.')
    if core.lower() != original_clean.lower() and len(original_clean) <= 30:
        queries.append(original_clean)

    qtype = _infer_query_intent(topic)

    # Intent-specific Douyin content-type variants（抖音化措辞）.
    if qtype in ("breaking_news", "opinion"):
        queries.append(f"{core} 解读")
    elif qtype == "product":
        queries.append(f"{core} 测评")
    elif qtype == "comparison":
        queries.append(f"{core} 对比")
    elif qtype == "how_to":
        queries.append(f"{core} 教程")
    else:
        queries.append(f"{core} 解读")

    # Deep depth: add a viral/hot variant.
    if depth == "deep":
        queries.append(f"{core} 热门")

    # Cap by depth budget.
    caps = {"quick": 1, "default": 2, "deep": 3}
    cap = caps.get(depth, 2)

    # De-dupe while preserving order (core may equal a variant after cleaning).
    seen: Set[str] = set()
    out: List[str] = []
    for q in queries:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out[:cap]


def _log(msg: str) -> None:
    log.source_log("抖音", msg)


def _parse_date(item: Dict[str, Any]) -> Optional[str]:
    """Parse date from a ScrapeCreators Douyin item to YYYY-MM-DD."""
    ts = item.get("create_time")
    if ts:
        try:
            return dates.timestamp_to_date(int(ts))
        except (ValueError, TypeError):
            pass
    return None


def _clean_webvtt(text: str) -> str:
    """Strip WebVTT timestamps and headers from transcript text."""
    if not text:
        return ""
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith('WEBVTT'):
            continue
        if re.match(r'^\d{2}:\d{2}', line):
            continue
        if '-->' in line:
            continue
        cleaned.append(line)
    return ' '.join(cleaned)


def _parse_items(raw_items: List[Dict[str, Any]], core_topic: str) -> List[Dict[str, Any]]:
    """Parse raw Douyin items into normalized short-form-video dicts.

    输出形状对齐 port contract §3 短视频体 + normalize._normalize_shortform_video
    实际读取的 key（id/text/caption_snippet/url/author_name/date/engagement/
    hashtags/relevance/why_relevant）。engagement 用 §3 的
    ``digg_count/comment/share``，另留 ``views`` 供排序。
    """
    items: List[Dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        video_id = str(raw.get("aweme_id", "") or raw.get("id", ""))
        text = raw.get("desc", "") or ""

        stats = raw.get("statistics") if isinstance(raw.get("statistics"), dict) else {}
        play_count = stats.get("play_count") if stats.get("play_count") is not None else 0
        digg_count = stats.get("digg_count") if stats.get("digg_count") is not None else 0
        comment_count = stats.get("comment_count") if stats.get("comment_count") is not None else 0
        share_count = stats.get("share_count") if stats.get("share_count") is not None else 0

        author_raw = raw.get("author")
        if isinstance(author_raw, dict):
            # 抖音昵称比英文 unique_id 更适合中文渲染；缺失时退回 unique_id/sec_uid。
            author_name = (
                author_raw.get("nickname")
                or author_raw.get("unique_id")
                or author_raw.get("sec_uid")
                or ""
            )
        elif isinstance(author_raw, str):
            author_name = author_raw
        else:
            author_name = ""

        share_url = raw.get("share_url", "") or ""
        text_extra = raw.get("text_extra") or []
        hashtag_names = [t.get("hashtag_name", "") for t in text_extra
                         if isinstance(t, dict) and t.get("hashtag_name")]

        video_raw = raw.get("video")
        duration = video_raw.get("duration") if isinstance(video_raw, dict) else None

        date_str = _parse_date(raw)

        # Compute relevance with hashtag boost.
        relevance = _compute_relevance(core_topic, text, hashtag_names)

        # Build URL: prefer share_url, fallback to constructed douyin URL.
        url = share_url.split("?")[0] if share_url else ""
        if not url and video_id:
            url = f"https://www.douyin.com/video/{video_id}"

        items.append({
            "id": video_id,
            "video_id": video_id,
            "text": text,
            "url": url,
            "author_name": author_name,
            "date": date_str,
            "engagement": {
                "views": play_count,
                "digg_count": digg_count,
                "comment": comment_count,
                "share": share_count,
            },
            "hashtags": hashtag_names,
            "duration": duration,
            "top_comments": [],
            "relevance": relevance,
            "why_relevant": f"抖音: {text[:60]}" if text else f"抖音: {core_topic}",
            "caption_snippet": "",  # populated by fetch_captions
        })
    return items


def _hashtag_search(hashtag: str, token: str) -> List[Dict[str, Any]]:
    """Search Douyin by hashtag via ScrapeCreators.

    Args:
        hashtag: Hashtag name (without #)
        token: ScrapeCreators API key

    Returns:
        List of raw Douyin item dicts (aweme_info format). Empty on any error.
    """
    _log(f"话题搜索: #{hashtag}")
    try:
        data = http.get(
            f"{SCRAPECREATORS_BASE}/search/hashtag",
            params={"hashtag": hashtag},
            headers=http.scrapecreators_headers(token),
            timeout=30,
            retries=2,
        )
    except Exception as e:
        _log(f"话题 #{hashtag} 搜索失败: {e}")
        return []

    raw_items = data.get("aweme_list") or data.get("data") or []
    _log(f"  -> #{hashtag} 命中 {len(raw_items)} 条")
    return raw_items


def _profile_videos(handle: str, token: str, count: int = 10) -> List[Dict[str, Any]]:
    """Fetch a Douyin creator's recent videos via ScrapeCreators.

    Args:
        handle: Douyin creator handle / id
        token: ScrapeCreators API key
        count: Max videos to return

    Returns:
        List of raw Douyin item dicts (aweme_info format). Empty on any error.
    """
    _log(f"创作者视频: @{handle}")
    try:
        data = http.get(
            SCRAPECREATORS_PROFILE_URL,
            params={"handle": handle, "sort_by": "latest"},
            headers=http.scrapecreators_headers(token),
            timeout=30,
            retries=2,
        )
    except Exception as e:
        _log(f"创作者 @{handle} 视频拉取失败: {e}")
        return []

    raw_items = data.get("aweme_list") or data.get("data") or []
    _log(f"  -> @{handle} 命中 {len(raw_items)} 条")
    return raw_items[:count]


def search_douyin(
    query: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    token: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    hashtags: Optional[List[str]] = None,
    creators: Optional[List[str]] = None,
    **kw: Any,
) -> Dict[str, Any]:
    """Search Douyin via the ScrapeCreators API.

    Full flow (mirrors tiktok.search_and_enrich): optional hashtag / creator
    seeds → multi-query keyword search → merge/dedupe by video id → date filter
    → fetch captions for top results.

    Args:
        query: Search topic (raw topic; query expansion runs from it).
        from_date: Start date (YYYY-MM-DD).
        to_date: End date (YYYY-MM-DD).
        depth: "quick" | "default" | "deep".
        token: ScrapeCreators API key (explicit override).
        config: Pipeline config dict (token resolved from SCRAPECREATORS_API_KEY).
        hashtags: Optional Douyin hashtags to seed (without #).
        creators: Optional Douyin creator handles to seed.
        **kw: Ignored extras (API symmetry with other providers).

    Returns:
        Dict ``{"items": [<short-form-video item>, ...], "error": <str|None>}``.
        Without a token returns ``{"items": [], "error": ...}`` — never raises,
        never fabricates.
    """
    resolved_token = _resolve_token(token, config)
    if not resolved_token:
        return {"items": [], "error": "未配置 SCRAPECREATORS_API_KEY，跳过抖音"}

    core_topic = _extract_core_subject(query) or query.strip()
    seen_ids: Set[str] = set()
    items: List[Dict[str, Any]] = []
    last_error: Optional[str] = None

    def _absorb(parsed: List[Dict[str, Any]]) -> None:
        for item in parsed:
            vid = item.get("video_id", "") or item.get("id", "")
            if vid and vid not in seen_ids:
                seen_ids.add(vid)
                items.append(item)

    # Step 0a: Hashtag seeds (high-signal, runs first).
    if hashtags:
        for hashtag in hashtags:
            _absorb(_parse_items(_hashtag_search(hashtag, resolved_token), core_topic))

    # Step 0b: Creator profile videos (high-signal).
    if creators:
        for creator in creators:
            _absorb(_parse_items(_profile_videos(creator, resolved_token), core_topic))

    # Step 1: Multi-query keyword search — run ScrapeCreators for each variant.
    config_depth = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    queries = expand_douyin_queries(query, depth)
    _log(f"搜索抖音 (depth={depth}, 变体={queries})")
    for q in queries:
        try:
            data = http.get(
                f"{SCRAPECREATORS_BASE}/search/keyword",
                params={"query": q, "sort_by": "relevance"},
                headers=http.scrapecreators_headers(resolved_token),
                timeout=30,
                retries=2,
            )
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            _log(f"ScrapeCreators 请求失败 ('{q}'): {e}")
            continue

        # Items are nested under aweme_info (same shape as the TikTok endpoint).
        raw_entries = data.get("search_item_list") or data.get("aweme_list") or data.get("data") or []
        raw_items: List[Dict[str, Any]] = []
        for entry in raw_entries:
            if isinstance(entry, dict):
                info = entry.get("aweme_info", entry)
                raw_items.append(info)
        raw_items = raw_items[:config_depth["results_per_page"]]
        _absorb(_parse_items(raw_items, core_topic))

    if not items:
        return {"items": [], "error": last_error}

    # Hard date filter: keep in-range; if none in range, keep all (best-effort).
    in_range = [i for i in items if i["date"] and from_date <= i["date"] <= to_date]
    out_of_range = len(items) - len(in_range)
    if in_range:
        items = in_range
        if out_of_range:
            _log(f"过滤掉 {out_of_range} 条超出日期窗口的视频")
    else:
        _log(f"窗口内无视频，保留全部 {len(items)} 条")

    # Sort merged results by views descending.
    items.sort(key=lambda x: x.get("engagement", {}).get("views", 0) or 0, reverse=True)

    # Step 2: Fetch captions for top N and attach.
    captions = _fetch_captions(items, resolved_token, depth)
    for item in items:
        caption = captions.get(item.get("video_id", ""))
        if caption:
            item["caption_snippet"] = caption

    _log(f"共取得 {len(items)} 条抖音视频")
    return {"items": items, "error": last_error}


def _fetch_captions(
    video_items: List[Dict[str, Any]],
    token: str,
    depth: str = "default",
) -> Dict[str, str]:
    """Fetch transcripts/captions for the top N Douyin videos.

    Strategy (mirrors tiktok.fetch_captions):
    1. Use the 'text' field (video description) as baseline caption — always
       available, no extra credits.
    2. For the top N, call /video/transcript for spoken-word captions.

    Returns a dict mapping video_id -> caption text (truncated to CAPTION_MAX_WORDS).
    Caption failures never crash the pipeline.
    """
    config = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    max_captions = config["max_captions"]

    if not video_items or not token:
        return {}

    top_items = video_items[:max_captions]
    _log(f"为 {len(top_items)} 条视频补全字幕")

    captions: Dict[str, str] = {}

    # First pass: use the description text as caption (free, always present).
    for item in top_items:
        vid = item.get("video_id", "")
        text = item.get("text", "")
        if vid and text:
            captions[vid] = _truncate_words(text)

    # Second pass: try spoken-word transcripts (1 credit each).
    for item in top_items:
        vid = item.get("video_id", "")
        url = item.get("url", "")
        if not vid or not url:
            continue
        try:
            data = http.get(
                f"{SCRAPECREATORS_BASE}/video/transcript",
                params={"url": url},
                headers=http.scrapecreators_headers(token),
                timeout=15,
                retries=1,
            )
        except Exception as e:
            _log(f"字幕拉取失败 {vid}: {e}")
            continue
        transcript = data.get("transcript")
        if transcript:
            if isinstance(transcript, list):
                transcript = " ".join(str(s) for s in transcript)
            transcript = _clean_webvtt(transcript)
            if transcript:
                captions[vid] = _truncate_words(transcript)

    got = sum(1 for v in captions.values() if v)
    _log(f"取得字幕 {got}/{len(top_items)} 条")
    return captions


def _truncate_words(text: str) -> str:
    """Truncate caption text to CAPTION_MAX_WORDS whitespace-delimited tokens.

    中文无空格时 ``split()`` 整体作为一个 token，这里再按字符长度兜底截断，
    避免超长字幕拖累下游 token 预算。
    """
    words = text.split()
    if len(words) > CAPTION_MAX_WORDS:
        return ' '.join(words[:CAPTION_MAX_WORDS]) + '...'
    # CJK fallback: cap by characters so a space-less transcript can't blow up.
    if len(text) > CAPTION_MAX_WORDS * 4:
        return text[: CAPTION_MAX_WORDS * 4] + '...'
    return text


def parse_douyin_response(result: Any, query: str = "") -> List[Dict[str, Any]]:
    """Parse a search_douyin result into raw item dicts (port contract §3 短视频体).

    Output shape per item (consumed by normalize._normalize_shortform_video)::

        {id, text, caption_snippet, url, author_name, date,
         engagement: {digg_count, comment, share, views},
         hashtags: [...], top_comments: [...], relevance, why_relevant}

    Args:
        result: The dict returned by ``search_douyin`` (``{"items": [...]}``),
            or a bare list of already-parsed items.
        query: Original search query. Used to recompute relevance when items
            carry none; otherwise the per-item relevance from ``_parse_items``
            is kept.

    Returns:
        List of raw item dicts. Empty list on any unusable input — never raises,
        never fabricates.
    """
    if not result:
        return []

    if isinstance(result, dict):
        if result.get("error") and not result.get("items"):
            _log(f"解析时遇到错误响应: {result.get('error')}")
        items = result.get("items") or []
    elif isinstance(result, list):
        items = result
    else:
        return []

    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        # Backfill relevance for bare-list inputs that skipped _parse_items.
        if query and item.get("relevance") is None:
            item = {
                **item,
                "relevance": _compute_relevance(
                    query, item.get("text", "") or "", item.get("hashtags") or []
                ),
            }
        out.append(item)
    return out
