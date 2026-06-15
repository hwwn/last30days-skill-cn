"""Juejin (掘金) search client for the last30days-cn pipeline.

取数方式（务实，标准库 urllib，无第三方依赖）：
  - 掘金站内搜索接口 ``POST https://api.juejin.cn/search_api/v1/search``
    （**免 key**）。请求体形如
    ``{"key_word": <关键词>, "id_type": 2, "limit": <条数>,
       "cursor": "0", "search_type": 0}`` —— ``id_type=2`` 表示只搜「文章」，
    ``search_type=0`` 为综合搜索。响应 ``data[]`` 中每个 ``result_model``
    的 ``article_info`` 即一篇文章，``cursor``/``has_more`` 用于翻页。

降级路径：
  - 接口免 key，匿名即可调用；但仍可能遇到风控 / 网络错误 / 空响应。
  - 任何失败（HTTP 错误、JSON 解析失败、err_no!=0、空 data）一律返回 ``[]``，
    **绝不伪造数据**。由 SKILL.md 指挥宿主模型用 WebSearch（``site:juejin.cn``
    / ``掘金``）补充。

移植说明（port contract §2/§3）：这是「论坛体」provider，结构对标参考源
``hackernews.py`` —— 同样的「打接口 → parse_*_response → 逐条按 token-overlap
算 relevance」套路，导出 ``search_juejin`` + ``parse_juejin_response``。掘金的
点赞（digg）/评论数映射到 §3 论坛体的 ``engagement:{points, comments}``；
掘金无独立的「站内讨论页」概念，故 ``hn_url`` 留空（``""``）。掘金搜索接口
没有服务端日期过滤，逐条保留 ``date`` 供后续 pipeline 的时间窗过滤。
"""

import datetime
import math
from typing import Any, Dict, List, Optional

from . import http, log
from .query import extract_core_subject
from .relevance import token_overlap_relevance

# Depth -> number of juejin articles to aim for. Mirrors the contract's
# DEPTH_CONFIG = {"quick":15,"default":30,"deep":60}.
DEPTH_CONFIG = {
    "quick": 15,
    "default": 30,
    "deep": 60,
}

# Juejin站内搜索接口（免 key）。
_SEARCH_URL = "https://api.juejin.cn/search_api/v1/search"

# id_type=2 -> 文章；search_type=0 -> 综合搜索。固定值，照契约 §2。
_ID_TYPE_ARTICLE = 2
_SEARCH_TYPE_GENERAL = 0

# Hard cap on pages so a single source can't dominate latency. The endpoint
# returns ~20 results per page regardless of the requested ``limit``, and
# advances via the opaque ``cursor`` string it echoes back.
_MAX_PAGES = 6


def _log(msg: str) -> None:
    log.source_log("掘金", msg, tty_only=False)


def _headers() -> Dict[str, str]:
    """Build request headers. The endpoint is keyless but expects a normal
    browser-ish UA + JSON content type; a Referer/Origin from juejin.cn makes
    the anti-bot layer happier.
    """
    return {
        "User-Agent": http.BROWSER_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Content-Type": "application/json",
        "Origin": "https://juejin.cn",
        "Referer": "https://juejin.cn/",
    }


def search_juejin(
    query: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    **kw: Any,
) -> Dict[str, Any]:
    """Search Juejin via the public search_api endpoint.

    Args:
        query: Search topic.
        from_date: Start date (YYYY-MM-DD). The search endpoint has no
            server-side date filter, so the window is applied later in the
            pipeline; the parse stage retains the per-article date for filtering.
        to_date: End date (YYYY-MM-DD). Kept for API symmetry.
        depth: "quick" | "default" | "deep".
        **kw: Ignored extra kwargs (interface symmetry with other providers).

    Returns:
        A dict ``{"items": [<result_model>, ...], "query": <core subject>}``
        whose ``items`` are the raw juejin ``result_model`` dicts. On any
        failure returns ``{"items": [], "query": ...}`` — never raises, never
        fabricates.
    """
    target = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])

    # Use the extracted core subject for cleaner keyword matching (parallels
    # hackernews.py using extract_core_subject before hitting Algolia). Fall
    # back to the raw query if extraction emptied it out.
    core = extract_core_subject(query) or query.strip()
    if not core:
        return {"items": [], "query": core}

    _log(f"搜索 '{core}' (原始: '{query}', 目标 {target} 篇)")

    headers = _headers()
    all_models: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    cursor = "0"

    for page in range(1, _MAX_PAGES + 1):
        body = {
            "key_word": core,
            "id_type": _ID_TYPE_ARTICLE,
            "limit": target,
            "cursor": cursor,
            "search_type": _SEARCH_TYPE_GENERAL,
        }
        try:
            resp = http.post(
                _SEARCH_URL,
                json_data=body,
                headers=headers,
                timeout=20,
                retries=2,
            )
        except http.HTTPError as exc:
            _log(f"第 {page} 页请求失败: {exc}")
            break

        if not isinstance(resp, dict):
            break

        # err_no != 0 means the endpoint refused / errored.
        err_no = resp.get("err_no")
        if err_no not in (0, "0", None):
            _log(f"第 {page} 页 err_no={err_no} {resp.get('err_msg') or ''}".rstrip())
            break

        data = resp.get("data")
        if not isinstance(data, list) or not data:
            break

        new_this_page = 0
        for entry in data:
            model = _entry_model(entry)
            if model is None:
                continue
            aid = _article_id(model)
            if aid and aid in seen_ids:
                continue
            if aid:
                seen_ids.add(aid)
            all_models.append(model)
            new_this_page += 1

        if new_this_page == 0:
            break
        if len(all_models) >= target:
            break

        # Advance the cursor for the next page; bail if the endpoint says
        # there's nothing more or fails to hand back a usable cursor.
        if not resp.get("has_more"):
            break
        next_cursor = resp.get("cursor")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = str(next_cursor)

    _log(f"取得 {len(all_models)} 篇文章 (核心词: {core})")
    return {"items": all_models[:target], "query": core}


def _entry_model(entry: Any) -> Optional[Dict[str, Any]]:
    """Pull the ``result_model`` out of a search data entry.

    Each ``data[]`` element wraps the actual item under ``result_model``;
    ``id_type=2`` should restrict results to articles, but we still guard
    against non-article shapes (no ``article_info``). Some callers may pass a
    bare ``result_model`` already, so accept that too.
    """
    if not isinstance(entry, dict):
        return None
    model = entry.get("result_model")
    if isinstance(model, dict):
        return model if isinstance(model.get("article_info"), dict) else None
    # Already a bare result_model.
    if isinstance(entry.get("article_info"), dict):
        return entry
    return None


def _article_id(model: Dict[str, Any]) -> str:
    """Return the article id for dedupe / item id, preferring article_info."""
    info = model.get("article_info")
    if isinstance(info, dict):
        aid = info.get("article_id")
        if aid:
            return str(aid)
    aid = model.get("article_id")
    return str(aid) if aid else ""


def _to_date(ctime: Any) -> Optional[str]:
    """Convert a juejin ``ctime`` (Unix seconds, usually a string) to
    ``YYYY-MM-DD`` (UTC+8 / Asia-Shanghai, which is what juejin timestamps
    represent). Returns None on any unusable value.
    """
    if ctime is None:
        return None
    try:
        ts = int(str(ctime).strip())
    except (ValueError, TypeError):
        return None
    if ts <= 0:
        return None
    try:
        dt = datetime.datetime.fromtimestamp(
            ts, tz=datetime.timezone(datetime.timedelta(hours=8))
        )
    except (OverflowError, OSError, ValueError):
        return None
    return dt.strftime("%Y-%m-%d")


def _as_int(value: Any) -> int:
    """Coerce a juejin count field to a non-negative int (0 on failure)."""
    if value is None:
        return 0
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return 0


def parse_juejin_response(result: Any, query: str = "") -> List[Dict[str, Any]]:
    """Parse a search_juejin result into raw item dicts (port contract §3 论坛体).

    Output shape per item::

        {id, title, text, url, hn_url, author, date,
         engagement: {points, comments},
         top_comments: [], comment_insights: [],
         relevance, why_relevant}

    Args:
        result: The dict returned by ``search_juejin`` (``{"items": [...]}``),
            or a bare list of juejin entries / result_models, or a raw search
            response dict (``{"data": [...]}``).
        query: Original search query, used for token-overlap relevance scoring.
            Falls back to the core subject stashed in ``result["query"]`` when
            omitted.

    Returns:
        List of raw item dicts. Empty list on any unusable input — never raises.
    """
    items: List[Dict[str, Any]] = []
    if not result:
        return items

    # Normalize the many input shapes into a flat list of result_model dicts.
    ranking_query = query
    if isinstance(result, dict):
        if result.get("error"):
            _log(f"解析时遇到错误响应: {result.get('error')}")
            return items
        if not ranking_query:
            ranking_query = result.get("query", "") or ""
        if isinstance(result.get("items"), list):
            entries = result["items"]
        elif isinstance(result.get("data"), list):
            entries = result["data"]
        else:
            return items
    elif isinstance(result, list):
        entries = result
    else:
        return items

    if not ranking_query:
        ranking_query = query or ""

    for i, entry in enumerate(entries):
        model = _entry_model(entry)
        if model is None:
            continue
        info = model.get("article_info")
        if not isinstance(info, dict):
            continue

        title = str(info.get("title", "") or "").strip()
        text = str(info.get("brief_content", "") or "").strip()
        if not title and not text:
            continue

        aid = _article_id(model)
        if not aid:
            continue
        url = f"https://juejin.cn/post/{aid}"

        author = ""
        author_info = model.get("author_user_info")
        if isinstance(author_info, dict):
            author = str(author_info.get("user_name") or "").strip()

        date = _to_date(info.get("ctime"))

        points = _as_int(info.get("digg_count"))
        comments = _as_int(info.get("comment_count"))

        # Relevance: blend search rank with token-overlap content matching,
        # plus a small engagement boost (mirrors hackernews.py's scoring).
        rank_score = max(0.3, 1.0 - (i * 0.02))
        engagement_boost = min(0.2, math.log1p(points) / 40)
        match_text = f"{title} {text}".strip()
        if ranking_query:
            content_score = token_overlap_relevance(ranking_query, match_text)
            relevance = min(1.0, 0.6 * rank_score + 0.4 * content_score + engagement_boost)
        else:
            relevance = min(1.0, rank_score * 0.7 + engagement_boost + 0.1)

        items.append({
            "id": aid or f"JJ{i + 1}",
            "title": title,
            "text": text[:500],
            "url": url,
            # 掘金 has no separate in-site discussion page distinct from the
            # article URL, so hn_url is left empty per §3 (hn_url 可空).
            "hn_url": "",
            "author": author,
            "date": date,
            "engagement": {
                "points": points,
                "comments": comments,
            },
            # Comment enrichment is not wired for juejin (the search endpoint
            # returns no comments); keep the keys present and empty per §3.
            "top_comments": [],
            "comment_insights": [],
            "relevance": round(relevance, 2),
            "why_relevant": f"掘金文章: {(title or text)[:60]}",
        })

    return items
