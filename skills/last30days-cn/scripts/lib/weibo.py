"""Weibo (微博) search client for the last30days-cn pipeline.

取数方式（务实，标准库 urllib，无第三方依赖）：
  - 移动端容器搜索接口 ``https://m.weibo.cn/api/container/getIndex``
    （半公开）。containerid 形如 ``100103type=1&q=<关键词>``，对应微博站内
    的「综合」搜索结果页。响应里 ``data.cards[]`` 中带 ``mblog`` 的卡片即一条
    微博。
  - 建议带 ``WEIBO_COOKIE``（浏览器里 ``.weibo.cn`` 域名下的 SUB cookie，
    env.py 的 COOKIE_DOMAINS 会自动从本地浏览器提取并映射到 WEIBO_COOKIE）。
    带 cookie 时可拿到更完整、更稳定的结果。

降级路径：
  - 无 cookie 时**尽力尝试**（移动端接口对匿名请求经常仍可返回部分结果，但也
    可能被风控拦截返回空 cards 或 HTTP 4xx）。
  - 任何失败（HTTP 错误、JSON 解析失败、风控空响应）一律返回 ``[]``，**绝不
    伪造数据**。由 SKILL.md 指挥宿主模型用 WebSearch（``site:weibo.com`` /
    ``微博``）补充。

移植说明（port contract §2/§3）：这是「微博体」provider，结构对标参考源
``bird_x.py`` 的 X provider —— 同样的「打接口 → parse_*_response → 逐条算
relevance」套路，导出 ``search_weibo`` + ``parse_weibo_response``。微博的
转发/评论/点赞映射到 §3 微博体的 ``engagement:{reposts,comments,attitudes}``。
"""

import html
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from . import http, log
from .query import extract_core_subject
from .relevance import token_overlap_relevance as _compute_relevance

# Depth -> number of weibo posts to aim for. Mirrors the contract's
# DEPTH_CONFIG = {"quick":15,"default":30,"deep":60}. The mobile container
# endpoint pages roughly ~10 mblog cards per page, so we translate the target
# count into a page budget below.
DEPTH_CONFIG = {
    "quick": 15,
    "default": 30,
    "deep": 60,
}

# Mobile container search base. containerid encodes the "综合" (general) search
# tab: ``100103type=1&q=<query>``. ``page_type=searchall`` keeps us on the
# aggregated results list.
_GETINDEX_URL = "https://m.weibo.cn/api/container/getIndex"

# Roughly how many mblog cards a single getIndex page yields. Used to derive a
# page budget from the depth target.
_CARDS_PER_PAGE = 10
# Hard cap on pages so a single source can't dominate latency.
_MAX_PAGES = 8

# Module-level cookie injected from .env config (parallels bird_x.set_credentials).
_cookie: Optional[str] = None


def set_credentials(cookie: Optional[str]) -> None:
    """Inject WEIBO_COOKIE from .env config so requests can carry a session.

    Optional — ``search_weibo`` also accepts a ``cookie=`` kwarg and falls back
    to ``os.environ['WEIBO_COOKIE']``. Without any cookie we still attempt the
    request (best-effort) and return ``[]`` on failure.
    """
    global _cookie
    if cookie:
        _cookie = cookie


def _resolve_cookie(explicit: Optional[str]) -> Optional[str]:
    """Resolve the cookie to use: explicit kwarg > injected > process env."""
    return explicit or _cookie or os.environ.get("WEIBO_COOKIE") or None


def _log(msg: str) -> None:
    log.source_log("微博", msg, tty_only=False)


def _extract_core_subject(topic: str) -> str:
    """Extract the core subject for weibo search.

    Weibo's search box behaves like a literal keyword match, so we strip
    question/meta words down to the core subject (same intent as bird_x).
    ``max_words`` counts CJK segmentation units for Chinese queries (see
    query.extract_core_subject / port contract §6).
    """
    return extract_core_subject(topic, max_words=6)


def _headers(cookie: Optional[str]) -> Dict[str, str]:
    """Build mobile-web headers. The mobile API is picky about Referer/UA and
    the ``MWeibo-Pwa`` / ``X-Requested-With`` hints that real m.weibo.cn sends.
    """
    headers = {
        "User-Agent": http.BROWSER_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://m.weibo.cn/",
        "X-Requested-With": "XMLHttpRequest",
        "MWeibo-Pwa": "1",
    }
    if cookie:
        headers["Cookie"] = cookie
    return headers


def search_weibo(
    query: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    **kw: Any,
) -> Dict[str, Any]:
    """Search Weibo via the m.weibo.cn container search endpoint.

    Args:
        query: Search topic.
        from_date: Start date (YYYY-MM-DD). The mobile search endpoint has no
            server-side date filter, so the window is applied later in the
            pipeline / parse stage retains the per-post date for filtering.
        to_date: End date (YYYY-MM-DD). Kept for API symmetry.
        depth: "quick" | "default" | "deep".
        **kw: Optional ``cookie=`` override.

    Returns:
        A dict ``{"items": [<mblog card>, ...], "query": <core subject>}`` whose
        ``items`` are the raw weibo card dicts (each containing an ``mblog``).
        On any failure returns ``{"items": [], "query": ...}`` — never raises,
        never fabricates.
    """
    cookie = _resolve_cookie(kw.get("cookie"))
    target = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    max_pages = max(1, min(_MAX_PAGES, -(-target // _CARDS_PER_PAGE)))  # ceil div

    core = _extract_core_subject(query) or query.strip()
    if not core:
        return {"items": [], "query": core}

    if not cookie:
        _log("未提供 WEIBO_COOKIE，匿名尽力尝试（可能被风控拦截）")

    # containerid for the aggregated ("综合") search tab. The query must be
    # percent-encoded *inside* the containerid value, then the whole containerid
    # is sent as a normal query param (http.request will encode it again, which
    # the endpoint tolerates because it decodes once on its side via the
    # already-encoded q value). To keep it deterministic we build the
    # containerid string ourselves and pass it through params.
    containerid = f"100103type=1&q={core}"

    headers = _headers(cookie)
    all_cards: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()

    for page in range(1, max_pages + 1):
        params = {
            "containerid": containerid,
            "page_type": "searchall",
            "page": page,
        }
        try:
            resp = http.get(
                _GETINDEX_URL,
                headers=headers,
                params=params,
                timeout=20,
                retries=2,
            )
        except http.HTTPError as exc:
            # 4xx (often anti-bot / login wall) and exhausted retries land here.
            _log(f"第 {page} 页请求失败: {exc}")
            break

        if not isinstance(resp, dict):
            break

        # ok==0 typically means the endpoint refused (风控 / 需登录).
        if resp.get("ok") not in (1, "1", None):
            msg = resp.get("msg") or resp.get("data", {}).get("msg") if isinstance(resp.get("data"), dict) else None
            _log(f"第 {page} 页 ok={resp.get('ok')} {msg or ''}".rstrip())
            break

        cards = _iter_cards(resp)
        if not cards:
            break

        new_this_page = 0
        for card in cards:
            mblog = _card_mblog(card)
            if not mblog:
                continue
            mid = str(mblog.get("id") or mblog.get("mid") or mblog.get("bid") or "")
            if mid and mid in seen_ids:
                continue
            if mid:
                seen_ids.add(mid)
            all_cards.append(card)
            new_this_page += 1

        if new_this_page == 0:
            break
        if len(all_cards) >= target:
            break

    _log(f"取得 {len(all_cards)} 条微博 (核心词: {core})")
    return {"items": all_cards[:target], "query": core}


def _iter_cards(resp: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Pull the flat list of result cards out of a getIndex response.

    The aggregated search response nests cards under ``data.cards``; some cards
    are group containers (``card_type == 11``) whose actual posts live under
    ``card_group``. Flatten both shapes into a single card list.
    """
    data = resp.get("data")
    if not isinstance(data, dict):
        return []
    raw_cards = data.get("cards")
    if not isinstance(raw_cards, list):
        return []

    flat: List[Dict[str, Any]] = []
    for card in raw_cards:
        if not isinstance(card, dict):
            continue
        flat.append(card)
        group = card.get("card_group")
        if isinstance(group, list):
            for sub in group:
                if isinstance(sub, dict):
                    flat.append(sub)
    return flat


def _card_mblog(card: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the ``mblog`` payload of a result card, if it is a post card."""
    mblog = card.get("mblog")
    return mblog if isinstance(mblog, dict) else None


# Weibo's created_at comes in two shapes:
#   - absolute: "Sat Jan 18 14:30:00 +0800 2026" (older / web format)
#   - relative: "刚刚", "3分钟前", "2小时前", "昨天 14:30", "01-18", "2025-12-30"
# The mobile API mostly returns relative strings, so we resolve those against
# "now" (Asia/Shanghai, UTC+8) to recover a YYYY-MM-DD date.
_CST = timezone(timedelta(hours=8))


def _parse_created_at(created_at: str, now: Optional[datetime] = None) -> Optional[str]:
    """Parse a weibo ``created_at`` string into ``YYYY-MM-DD`` (or None)."""
    if not created_at or not isinstance(created_at, str):
        return None
    s = created_at.strip()
    now = now or datetime.now(_CST)

    # Absolute Twitter-style format: "Sat Jan 18 14:30:00 +0800 2026".
    if re.match(r"^[A-Z][a-z]{2}\s+[A-Z][a-z]{2}\s+\d", s):
        try:
            dt = datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
            return dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            pass

    # ISO-ish: "2025-12-30" / "2025-12-30 14:30".
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # "MM-DD" (current year on weibo's mobile UI).
    m = re.match(r"^(\d{1,2})-(\d{1,2})$", s)
    if m:
        return f"{now.year:04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"

    # Relative strings.
    if "刚刚" in s:
        return now.strftime("%Y-%m-%d")
    m = re.match(r"^(\d+)\s*分钟前", s)
    if m:
        return (now - timedelta(minutes=int(m.group(1)))).strftime("%Y-%m-%d")
    m = re.match(r"^(\d+)\s*小时前", s)
    if m:
        return (now - timedelta(hours=int(m.group(1)))).strftime("%Y-%m-%d")
    if s.startswith("今天"):
        return now.strftime("%Y-%m-%d")
    if s.startswith("昨天"):
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    m = re.match(r"^(\d+)\s*天前", s)
    if m:
        return (now - timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d")

    return None


# Strip HTML tags weibo wraps around text (links, @mentions, emoji <img>).
_TAG_RE = re.compile(r"<[^>]+>")


def _clean_text(raw: str) -> str:
    """Flatten weibo's HTML post text into plain text."""
    if not raw:
        return ""
    # Replace <br> with newlines first so paragraph breaks survive tag stripping.
    text = re.sub(r"<br\s*/?>", "\n", raw, flags=re.IGNORECASE)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    return text.strip()


def _as_int(value: Any) -> Optional[int]:
    """Coerce a weibo count field to int, tolerating strings like '1.2万'."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    s = str(value).strip()
    if not s:
        return None
    # Weibo sometimes renders large counts as "1.2万" / "3.4亿".
    mult = 1
    if s.endswith("万"):
        mult, s = 10_000, s[:-1]
    elif s.endswith("亿"):
        mult, s = 100_000_000, s[:-1]
    try:
        return int(float(s) * mult)
    except (ValueError, TypeError):
        return None


def _post_url(mblog: Dict[str, Any]) -> str:
    """Build the canonical weibo permalink for a post.

    Prefers the ``bid`` (base-62 short id) under ``https://m.weibo.cn/status/<bid>``
    since that resolves without auth. Falls back to the numeric id + user id.
    """
    bid = mblog.get("bid")
    if bid:
        return f"https://m.weibo.cn/status/{bid}"
    mid = mblog.get("id") or mblog.get("mid")
    user = mblog.get("user") or {}
    uid = user.get("id") if isinstance(user, dict) else None
    if mid and uid:
        return f"https://m.weibo.cn/detail/{mid}"
    if mid:
        return f"https://m.weibo.cn/status/{mid}"
    return ""


def parse_weibo_response(result: Any, query: str = "") -> List[Dict[str, Any]]:
    """Parse a search_weibo result into raw item dicts (port contract §3 微博体).

    Output shape per item::

        {id, text, url, author_handle, date,
         engagement: {reposts, comments, attitudes},
         relevance, why_relevant}

    Args:
        result: The dict returned by ``search_weibo`` (``{"items": [...]}``),
            or a bare list of weibo cards, or a raw getIndex response dict.
        query: Original search query, used for relevance scoring. Falls back to
            the core subject stashed in ``result["query"]`` when omitted.

    Returns:
        List of raw item dicts. Empty list on any unusable input — never raises.
    """
    items: List[Dict[str, Any]] = []
    if not result:
        return items

    # Normalize the many input shapes into a flat list of result cards.
    ranking_query = query
    if isinstance(result, dict):
        if "error" in result and result.get("error"):
            _log(f"解析时遇到错误响应: {result.get('error')}")
            return items
        if not ranking_query:
            ranking_query = result.get("query", "") or ""
        if isinstance(result.get("items"), list):
            cards = result["items"]
        else:
            # Treat as a raw getIndex response.
            cards = _iter_cards(result)
    elif isinstance(result, list):
        cards = result
    else:
        return items

    if not ranking_query:
        ranking_query = query or ""

    for i, card in enumerate(cards):
        if not isinstance(card, dict):
            continue
        # A card may be the raw mblog already, or wrap it.
        mblog = card.get("mblog") if isinstance(card.get("mblog"), dict) else card
        if not isinstance(mblog, dict):
            continue

        text = _clean_text(str(mblog.get("text", "") or mblog.get("raw_text", "")))
        # Prefer the long text if weibo truncated the short one.
        long_text = mblog.get("longText") or mblog.get("longTextContent")
        if isinstance(long_text, dict):
            long_text = long_text.get("longTextContent") or long_text.get("content")
        if long_text:
            cleaned_long = _clean_text(str(long_text))
            if len(cleaned_long) > len(text):
                text = cleaned_long

        url = _post_url(mblog)
        if not url or not text:
            # No permalink or no body -> not a usable evidence item.
            continue

        user = mblog.get("user") or {}
        author_handle = ""
        if isinstance(user, dict):
            author_handle = str(user.get("screen_name") or user.get("name") or "").strip()

        date = _parse_created_at(str(mblog.get("created_at", "")))

        engagement = {
            "reposts": _as_int(mblog.get("reposts_count")),
            "comments": _as_int(mblog.get("comments_count")),
            "attitudes": _as_int(mblog.get("attitudes_count")),
        }
        if all(v is None for v in engagement.values()):
            engagement = None

        item = {
            "id": f"WB{i + 1}",
            "text": text[:500],
            "url": url,
            "author_handle": author_handle,
            "date": date,
            "engagement": engagement,
            "relevance": _compute_relevance(ranking_query, text) if ranking_query else 0.7,
            "why_relevant": "",  # weibo provides no relevance rationale
        }
        items.append(item)

    return items
