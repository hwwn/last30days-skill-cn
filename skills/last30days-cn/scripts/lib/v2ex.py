"""V2EX search client for the last30days-cn pipeline.

取数方式（务实，标准库 urllib，无第三方依赖，免 key）：
  - V2EX 官方 API 没有站内全文搜索能力，因此使用第三方全文搜索服务
    `sov2ex <https://www.sov2ex.com>`_ 的开放接口：
    ``https://www.sov2ex.com/api/search?q=<关键词>&sort=created&size=<N>``
    （免登录、免 key）。``sort=created`` 让结果按发帖时间倒序，便于落进
    「最近 30 天」窗口。响应是一个 ElasticSearch 风格的 JSON，命中条目在
    ``hits[]`` 里，每条的实际帖子字段在 ``hits[]._source``：
    ``id`` / ``title`` / ``content`` / ``replies`` / ``node`` / ``created`` /
    ``member``。``hits[].highlight.content`` 给出带 ``<em>`` 高亮的摘要片段。

降级路径：
  - sov2ex 是第三方服务，可能限流 / 偶发 5xx / DNS 失败。任何失败（HTTP 错误、
    JSON 解析失败、空 ``hits``）一律返回空（``{"hits": []}``），**绝不伪造
    数据**。由 SKILL.md 指挥宿主模型用 WebSearch（``site:v2ex.com`` / ``V2EX``）
    补充。

移植说明（port contract §2/§3）：这是「论坛体」provider，结构对标参考源
``hackernews.py``（Algolia 全文搜索免 key）—— 同样的「打第三方搜索接口 →
parse_*_response → 逐条 blend 排名/内容 token-overlap 算 relevance」套路，
导出 ``search_v2ex`` + ``parse_v2ex_response``。

与 HN 的差异需要诚实处理：
  - V2EX **没有「赞 / points」机制**，帖子只有回复数（``replies``）。§3 论坛体的
    ``engagement`` 形状是 ``{points, comments}``：这里 ``comments`` 取 ``replies``，
    ``points`` 恒为 0（V2EX 无投票系统，不编造分数）。
  - sov2ex 不返回逐条评论，因此 ``top_comments`` / ``comment_insights`` 始终为
    空列表（HN 走 Algolia items 端点二次 enrich，V2EX 无对应接口，留空而非伪造）。
  - ``hn_url`` 字段（§3 论坛体允许为空）这里复用为 V2EX 帖子的讨论页链接
    ``https://www.v2ex.com/t/<id>``，与正文 ``url`` 同值（V2EX 帖子本身即讨论页）。
"""

import html
import math
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from . import http, log
from .query import extract_core_subject
from .relevance import token_overlap_relevance

# Third-party full-text search endpoint for V2EX (free, no key required).
# sort=created -> newest first, which keeps the recent-30-days window dense.
SOV2EX_SEARCH_URL = "https://www.sov2ex.com/api/search"

# Canonical V2EX topic permalink template. The numeric ``id`` from _source is
# the topic id, e.g. https://www.v2ex.com/t/1220207
_V2EX_TOPIC_URL = "https://www.v2ex.com/t/{id}"

# Depth -> number of results to request. Mirrors the contract's
# DEPTH_CONFIG = {"quick":15,"default":30,"deep":60}.
DEPTH_CONFIG = {
    "quick": 15,
    "default": 30,
    "deep": 60,
}

# sov2ex caps the page size; keep requests within a sane bound.
_MAX_SIZE = 50

# V2EX timestamps (created) are naive ISO strings in Asia/Shanghai (UTC+8).
_CST = timezone(timedelta(hours=8))


def _log(msg: str) -> None:
    log.source_log("V2EX", msg, tty_only=False)


def _extract_core_subject(topic: str) -> str:
    """Extract the core subject for the sov2ex full-text query.

    sov2ex behaves like a keyword search, so we strip question/meta words down
    to the core subject. ``max_words`` counts CJK segmentation units for Chinese
    queries (see query.extract_core_subject / port contract §6).
    """
    return extract_core_subject(topic, max_words=6)


def search_v2ex(
    query: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    **kw: Any,
) -> Dict[str, Any]:
    """Search V2EX via the sov2ex third-party full-text API.

    Args:
        query: Search topic.
        from_date: Start date (YYYY-MM-DD). sov2ex has no server-side date
            filter beyond ``sort=created``; the recent-window cut is applied
            later in the pipeline. ``parse_v2ex_response`` retains each topic's
            ``date`` for that filtering.
        to_date: End date (YYYY-MM-DD). Kept for API symmetry.
        depth: "quick" | "default" | "deep".
        **kw: Accepted and ignored (API symmetry with other providers).

    Returns:
        The raw sov2ex JSON response dict (contains a ``hits`` list). On any
        failure returns ``{"hits": [], "error": <str>}`` — never raises, never
        fabricates. The resolved core subject is stashed under ``query`` so
        ``parse_v2ex_response`` can fall back to it for relevance scoring.
    """
    size = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    size = max(1, min(_MAX_SIZE, size))

    core = _extract_core_subject(query) or query.strip()
    if not core:
        return {"hits": [], "query": core}

    _log(f"搜索 '{core}' (原始: '{query}', size={size}, since {from_date})")

    params = {
        "q": core,
        "sort": "created",
        "size": size,
    }

    try:
        response = http.get(SOV2EX_SEARCH_URL, params=params, timeout=30, retries=2)
    except http.HTTPError as exc:
        _log(f"搜索失败: {exc}")
        return {"hits": [], "error": str(exc), "query": core}
    except Exception as exc:  # pragma: no cover - defensive, never fabricate
        _log(f"搜索失败: {exc}")
        return {"hits": [], "error": str(exc), "query": core}

    if not isinstance(response, dict):
        _log("响应格式异常（非 JSON 对象），返回空")
        return {"hits": [], "query": core}

    hits = response.get("hits")
    if not isinstance(hits, list):
        hits = []
    _log(f"取得 {len(hits)} 条 V2EX 帖子 (核心词: {core})")

    # Stash the core subject so the parser can recover it when query="".
    response["query"] = core
    return response


# Strip the <em>...</em> highlight tags sov2ex wraps around matched terms, plus
# any other stray HTML, and decode entities.
_TAG_RE = re.compile(r"<[^>]+>")


def _clean_text(raw: str) -> str:
    """Flatten sov2ex highlight/content HTML into plain text."""
    if not raw:
        return ""
    text = html.unescape(str(raw))
    text = _TAG_RE.sub("", text)
    # Collapse V2EX's \r\n soup into single newlines, then trim.
    text = re.sub(r"\r\n?", "\n", text)
    return text.strip()


def _parse_created(created: Any) -> Optional[str]:
    """Parse a sov2ex ``created`` value into ``YYYY-MM-DD`` (or None).

    sov2ex returns either an ISO-ish string ("2026-06-13T13:10:08", naive,
    Asia/Shanghai) or — depending on index version — a Unix epoch (seconds or
    milliseconds). Handle all shapes; never guess when unparseable.
    """
    if created is None:
        return None

    # Numeric epoch (seconds or milliseconds).
    if isinstance(created, (int, float)) and not isinstance(created, bool):
        ts = float(created)
        if ts > 1e12:  # milliseconds
            ts /= 1000.0
        try:
            dt = datetime.fromtimestamp(ts, tz=_CST)
            return dt.strftime("%Y-%m-%d")
        except (ValueError, OSError, OverflowError):
            return None

    s = str(created).strip()
    if not s:
        return None

    # Pure digit string -> epoch.
    if s.isdigit():
        return _parse_created(int(s))

    # ISO datetime / date: "2026-06-13T13:10:08" or "2026-06-13 13:10:08".
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    return None


def _as_int(value: Any) -> int:
    """Coerce a sov2ex count field to a non-negative int (0 on failure)."""
    if value is None or isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (ValueError, TypeError):
        return 0


def parse_v2ex_response(result: Any, query: str = "") -> List[Dict[str, Any]]:
    """Parse a search_v2ex result into raw item dicts (port contract §3 论坛体).

    Output shape per item::

        {id, title, text, url, hn_url, author, date,
         engagement: {points, comments},
         top_comments: [], comment_insights: [],
         relevance, why_relevant}

    Notes (honest mapping, see module docstring):
        - ``engagement.points`` is always 0 (V2EX has no upvote system).
        - ``engagement.comments`` is the topic's ``replies`` count.
        - ``top_comments`` / ``comment_insights`` are always empty (sov2ex does
          not return per-reply text; we do not fabricate comments).

    Args:
        result: The dict returned by ``search_v2ex`` (sov2ex response with a
            ``hits`` list), or a bare list of hit dicts.
        query: Original search query, used for token-overlap relevance scoring.
            Falls back to the core subject stashed in ``result["query"]`` when
            omitted.

    Returns:
        List of raw item dicts. Empty list on any unusable input — never raises.
    """
    items: List[Dict[str, Any]] = []
    if not result:
        return items

    ranking_query = query
    if isinstance(result, dict):
        if result.get("error"):
            _log(f"解析时遇到错误响应: {result.get('error')}")
            return items
        if not ranking_query:
            ranking_query = result.get("query", "") or ""
        hits = result.get("hits")
        if not isinstance(hits, list):
            return items
    elif isinstance(result, list):
        hits = result
    else:
        return items

    if not ranking_query:
        ranking_query = query or ""

    for i, hit in enumerate(hits):
        if not isinstance(hit, dict):
            continue
        source = hit.get("_source")
        if not isinstance(source, dict):
            continue

        topic_id = source.get("id")
        if topic_id is None:
            topic_id = hit.get("_id")
        if topic_id is None:
            continue
        topic_id_str = str(topic_id)

        title = _clean_text(str(source.get("title", "")))
        # Body: prefer the full _source.content; fall back to the highlighted
        # snippet (with <em> tags stripped) when content is missing.
        body = _clean_text(str(source.get("content", "")))
        if not body:
            highlight = hit.get("highlight")
            if isinstance(highlight, dict):
                hl_content = highlight.get("content")
                if isinstance(hl_content, list):
                    body = _clean_text(" ".join(str(x) for x in hl_content))
                elif hl_content:
                    body = _clean_text(str(hl_content))

        if not title and not body:
            continue

        url = _V2EX_TOPIC_URL.format(id=topic_id_str)
        author = str(source.get("member", "") or "").strip()
        date = _parse_created(source.get("created"))
        replies = _as_int(source.get("replies"))

        # Relevance: blend sov2ex rank (results already sorted by created, so the
        # rank decay is gentle) with token-overlap content matching, plus a small
        # discussion-volume boost from reply count. Mirrors hackernews.py.
        rank_score = max(0.3, 1.0 - (i * 0.02))
        engagement_boost = min(0.2, math.log1p(replies) / 40)
        match_text = f"{title} {body}".strip()
        if ranking_query:
            content_score = token_overlap_relevance(ranking_query, match_text)
            relevance = min(1.0, 0.6 * rank_score + 0.4 * content_score + engagement_boost)
        else:
            relevance = min(1.0, rank_score * 0.7 + engagement_boost + 0.1)

        items.append({
            "id": topic_id_str,
            "title": title,
            "text": body[:500],
            "url": url,
            # V2EX topics are themselves the discussion page; reuse the same URL
            # for the §3 论坛体 hn_url slot (which is allowed to be empty).
            "hn_url": url,
            "author": author,
            "date": date,
            "engagement": {
                # V2EX has no upvote system; never invent a score.
                "points": 0,
                "comments": replies,
            },
            "top_comments": [],
            "comment_insights": [],
            "relevance": round(relevance, 2),
            "why_relevant": f"V2EX 帖子: {title[:60]}" if title else "V2EX 帖子",
        })

    return items
