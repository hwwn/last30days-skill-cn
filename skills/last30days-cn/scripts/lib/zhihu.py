"""知乎 search_v3 provider（原型 Reddit，见 PORT_CONTRACT §1/§2/§3）。

知乎体 = reddit 体：每个 raw item 产出
``{id, title, selftext, url, subreddit(话题/专栏名), date,
   engagement:{score(赞同), num_comments}, top_comments:[{excerpt, score}],
   comment_insights:[], relevance, why_relevant}``
喂给 ``normalize._normalize_zhihu``（reddit normalizer 原型）。

取数方式与降级路径（PORT_CONTRACT §2「取数分层」）
--------------------------------------------------------------------
知乎全文搜索走 Web v4 接口：

    https://www.zhihu.com/api/v4/search_v3?t=general&q={query}

该接口是**登录墙**：服务端校验 ``z_c0``（登录态）与 ``d_c0``（设备指纹）
两个 cookie。无有效 cookie 时返回 403 / 反爬 HTML / 空 ``data``。因此：

  - 有 ``ZHIHU_COOKIE``（``env.COOKIE_DOMAINS["zhihu"]`` 映射 ``z_c0``）时，
    带 cookie + 浏览器 UA 打 search_v3，解析 ``data[].object``。
  - **拿不到 cookie（无 ZHIHU_COOKIE）直接返回 ``[]``**，绝不伪造数据；
    由 SKILL.md 指挥宿主模型用 WebSearch（``site:zhihu.com``）补充。

与参考源 ``reddit_public.py`` 一致：``search_zhihu`` 是高层入口
（depth → 条数、日期窗口过滤、去重、ID 赋值），``parse_zhihu_response``
把原始 JSON 解析成 raw item dict 列表。网络/反爬失败一律降级为 ``[]``，
绝不抛到调用方（pipeline 据此回退）。

知乎 search_v3 response 形态（实测）
--------------------------------------------------------------------
``{"data": [{"type": "search_result", "object": {...}}, ...]}``
其中 ``object.type`` ∈ {answer, article, question, ...}：

  - answer：``{type, id, question:{title, url}, content(HTML), excerpt,
              voteup_count, comment_count, created_time/updated_time,
              author:{name}}``
  - article：``{type, id, title, content/excerpt, voteup_count,
               comment_count, created/updated, column:{title}}``
  - question：``{type, id, title, detail/excerpt, answer_count,
                comment_count, created/updated_time}``

不同 type 字段名不统一，解析时逐一兜底取值（``_first``）。
"""

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import env, http, log
from .query import extract_core_subject
from .relevance import token_overlap_relevance

# Depth-aware result counts (PORT_CONTRACT §2: quick/default/deep).
DEPTH_LIMITS = {
    "quick": 15,
    "default": 30,
    "deep": 60,
}

# search_v3 returns paged results; one page is plenty for our depth ceiling.
SEARCH_URL = "https://www.zhihu.com/api/v4/search_v3"

# Browser User-Agent — search_v3 rejects the default skill UA outright.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Strip HTML tags from answer/article content (knzhi content is HTML).
_TAG_RE = re.compile(r"<[^>]+>")


def _log(msg: str) -> None:
    log.source_log("知乎", msg, tty_only=False)


def _first(d: Dict[str, Any], keys: tuple, default: Any = None) -> Any:
    """Return the first present, non-empty value among ``keys``."""
    for k in keys:
        v = d.get(k)
        if v not in (None, "", []):
            return v
    return default


def _strip_html(text: str) -> str:
    """Flatten HTML content to plain text (search_v3 answer/article bodies)."""
    if not text:
        return ""
    return " ".join(_TAG_RE.sub(" ", str(text)).split())


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _parse_date(epoch: Any) -> Optional[str]:
    """Convert a unix epoch (search_v3 ``created_time``) to ``YYYY-MM-DD``."""
    if epoch in (None, "", 0):
        return None
    try:
        dt = datetime.fromtimestamp(float(epoch), tz=timezone.utc)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError, OSError):
        return None


def _build_headers(cookie: str) -> Dict[str, str]:
    """Headers for an authenticated search_v3 call.

    The cookie carries ``z_c0`` (login) and ideally ``d_c0`` (device id); both
    are required for the endpoint to return data rather than a 403/anti-bot
    page. ``x-requested-with`` mirrors the web client.
    """
    return {
        "User-Agent": BROWSER_UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://www.zhihu.com/search?type=content",
        "x-requested-with": "fetch",
        "Cookie": cookie,
    }


def _object_url(obj: Dict[str, Any], obj_type: str, obj_id: str) -> str:
    """Best-effort canonical zhihu.com URL for a search object."""
    if obj_type == "answer":
        question = obj.get("question") or {}
        qid = str(question.get("id") or "").strip()
        if qid and obj_id:
            return f"https://www.zhihu.com/question/{qid}/answer/{obj_id}"
        url = str(question.get("url") or obj.get("url") or "").strip()
        if url:
            return url.replace("api.zhihu.com", "www.zhihu.com")
    if obj_type == "article":
        if obj_id:
            return f"https://zhuanlan.zhihu.com/p/{obj_id}"
    if obj_type == "question":
        if obj_id:
            return f"https://www.zhihu.com/question/{obj_id}"
    # Fallback: whatever url the object carried, normalized off the api host.
    url = str(obj.get("url") or "").strip()
    return url.replace("api.zhihu.com", "www.zhihu.com") if url else ""


def _container(obj: Dict[str, Any], obj_type: str) -> str:
    """Topic / column name → ``subreddit`` field (PORT_CONTRACT §3 zhihu 体)."""
    # Article columns carry an explicit column title.
    column = obj.get("column") or {}
    if isinstance(column, dict) and column.get("title"):
        return str(column["title"]).strip()
    # Answers/questions: surface the first attached topic when present.
    topics = obj.get("topics") or (obj.get("question") or {}).get("topics") or []
    if isinstance(topics, list):
        for t in topics:
            if isinstance(t, dict) and t.get("name"):
                return str(t["name"]).strip()
    return obj_type


def parse_zhihu_response(result: Any, query: str = "") -> List[Dict[str, Any]]:
    """Parse a raw search_v3 response into zhihu-body raw item dicts.

    Args:
        result: Parsed search_v3 JSON (dict with ``data``), or a list of
            already-extracted ``object`` dicts.
        query: Original search query — used for token-overlap relevance.

    Returns:
        List of raw item dicts (PORT_CONTRACT §3 zhihu 体). Empty on bad input.
    """
    if not result:
        return []

    # Accept both the full envelope ({"data": [...]}) and a bare list.
    if isinstance(result, dict):
        rows = result.get("data") or []
    elif isinstance(result, list):
        rows = result
    else:
        return []

    items: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        # search_v3 wraps the payload under ``object``; tolerate a bare object.
        obj = row.get("object") if isinstance(row.get("object"), dict) else row
        if not isinstance(obj, dict):
            continue

        obj_type = str(obj.get("type") or "").strip()
        # Skip non-content cards (users, columns-as-results, ads, etc.).
        if obj_type and obj_type not in ("answer", "article", "question", "zvideo"):
            continue

        obj_id = str(obj.get("id") or "").strip()

        # Title: questions/articles have one directly; answers borrow the
        # parent question's title.
        question = obj.get("question") or {}
        title = str(
            _first(obj, ("title",))
            or (question.get("title") if isinstance(question, dict) else "")
            or ""
        ).strip()

        # Body: answer/article HTML content, else question detail/excerpt.
        body = _strip_html(
            _first(obj, ("content", "detail", "excerpt", "description"), "")
        )

        url = _object_url(obj, obj_type, obj_id)
        if not url:
            continue

        score = _to_int(_first(obj, ("voteup_count", "vote_count", "favlists_count"), 0))
        num_comments = _to_int(_first(obj, ("comment_count", "comments_count"), 0))
        date_str = _parse_date(
            _first(obj, ("created_time", "created", "updated_time", "updated"))
        )

        # Relevance: query-centric token overlap over title + body (mirrors the
        # reddit provider's content-based relevance). Empty query → neutral.
        match_text = f"{title} {body}".strip()
        if query and match_text:
            relevance = token_overlap_relevance(query, match_text)
            why = "标题/正文与查询主题词重合"
        else:
            relevance = 0.5
            why = "知乎全文搜索结果"

        items.append({
            "id": "",  # assigned after dedup
            "title": title,
            "selftext": body[:500] if body else "",
            "url": url,
            "subreddit": _container(obj, obj_type),
            "date": date_str,
            "engagement": {
                "score": score,
                "num_comments": num_comments,
            },
            # search_v3 carries no comment bodies; enrichment is out of scope
            # (reddit's shreddit tier has no zhihu analog). Keep keys present
            # so the normalizer's metadata wiring stays uniform.
            "top_comments": [],
            "comment_insights": [],
            "relevance": relevance,
            "why_relevant": why,
        })

    return items


def search_zhihu(
    query: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    **kw: Any,
) -> List[Dict[str, Any]]:
    """Search Zhihu via the web search_v3 endpoint (login-walled).

    Requires ``ZHIHU_COOKIE`` (z_c0 / d_c0). When no cookie is available the
    function returns ``[]`` immediately — it never fabricates data — so the
    pipeline can fall through to host-model WebSearch (PORT_CONTRACT §2).

    Args:
        query: Search query string.
        from_date: Window start (YYYY-MM-DD), inclusive.
        to_date: Window end (YYYY-MM-DD), inclusive.
        depth: 'quick' | 'default' | 'deep' — controls result count.
        **kw: Forward-compat (ignored); ``config`` may be passed to reuse a
            preloaded credentials dict.

    Returns:
        List of zhihu-body raw item dicts (PORT_CONTRACT §3). Empty on any
        failure or when unauthenticated.
    """
    config = kw.get("config") or env.get_config()
    cookie = (config.get("ZHIHU_COOKIE") or "").strip()
    if not cookie:
        # Login wall: no z_c0 → search_v3 returns 403/anti-bot. Return [] so the
        # host model supplements via WebSearch instead of surfacing nothing fake.
        _log("无 ZHIHU_COOKIE，跳过 search_v3，返回 []")
        return []

    # Ensure the cookie string at least carries z_c0 (ZHIHU_COOKIE maps z_c0).
    cookie_str = cookie if "z_c0=" in cookie else f"z_c0={cookie}"

    limit = DEPTH_LIMITS.get(depth, DEPTH_LIMITS["default"])
    # Strip Chinese noise/meta words so the search term is the core subject.
    core = extract_core_subject(query) or query

    params = {
        "t": "general",
        "q": core,
        "correction": 1,
        "offset": 0,
        "limit": limit,
        "lc_idx": 0,
        "show_all_topics": 0,
    }

    try:
        result = http.get(
            SEARCH_URL,
            headers=_build_headers(cookie_str),
            params=params,
            timeout=15,
            retries=2,
        )
    except http.HTTPError as e:
        # 403/401 → cookie expired or device id (d_c0) missing; any HTTP error
        # degrades to [] (never raises) so the pipeline can supplement.
        _log(f"search_v3 失败（{e}），返回 []")
        return []
    except Exception as e:  # defensive: never let zhihu sink the run
        _log(f"search_v3 异常（{e}），返回 []")
        return []

    items = parse_zhihu_response(result, query=query)

    # Date-window filter: keep items in range or with unknown dates (mirrors
    # reddit_keyless — search_v3 has no reliable per-result recency filter).
    items = [
        it for it in items
        if it.get("date") is None or (from_date <= it["date"] <= to_date)
    ]

    # Dedupe by URL, preserving search rank order.
    seen: set = set()
    unique: List[Dict[str, Any]] = []
    for it in items:
        if it["url"] not in seen:
            seen.add(it["url"])
            unique.append(it)

    # Assign stable IDs (ZH1, ZH2, ...) after dedup.
    for i, it in enumerate(unique):
        it["id"] = f"ZH{i + 1}"

    _log(f"search_v3 返回 {len(unique)} 条（depth={depth}, limit={limit}）")
    return unique[:limit]
