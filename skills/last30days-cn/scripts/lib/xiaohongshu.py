"""Xiaohongshu (小红书) search client for the last30days-cn pipeline.

取数方式（务实，标准库 urllib，无第三方依赖），分层降级：
  1. **本地服务（首选）**：``XIAOHONGSHU_API_BASE`` 指向的
     ``xpzouying/xiaohongshu-mcp`` REST 服务。它持有真实登录态的浏览器会话，
     提供 ``GET /api/v1/login/status`` 与 ``POST /api/v1/feeds/search``，能返回
     站内「综合」搜索的笔记卡片（``data.feeds[].noteCard``）。这是唯一稳定、
     不需要在本进程里破解小红书风控/签名（xsec_token / x-s 等）的路径。默认
     base 见 env.get_xiaohongshu_api_base（``http://host.docker.internal:18060``）。
  2. **ScrapeCreators（次选）**：若没有可用的本地服务但配置了
     ``SCRAPECREATORS_API_KEY``，尽力走 ScrapeCreators 的小红书搜索端点（与
     参考源 instagram.py / tiktok.py 共用 ``http.scrapecreators_headers``）。
     ScrapeCreators 对小红书的覆盖不如 IG/TikTok 稳定，因此这条路是 best-effort：
     端点不存在 / 报错 / 返回空一律继续降级。
  3. **都拿不到 → 返回 []**：绝不伪造数据，由 SKILL.md 指挥宿主模型用
     WebSearch（``小红书`` / ``site:xiaohongshu.com``）补充。

移植说明（port contract §2/§3）：这是「短视频体」provider，结构对标参考源
``instagram.py`` —— 同样的「打接口 → parse_*_response → 逐条算 relevance →
硬日期过滤」套路，导出 ``search_xiaohongshu`` + ``parse_xiaohongshu_response``。
小红书的点赞/评论/收藏映射到 §3 短视频体的
``engagement:{likes, comment, collected}``，与抖音共用
normalize._normalize_shortform_video（id_prefix=XHS）。

注意：参考仓库里的 ``xiaohongshu_api.py`` 把结果归一成 *web-item* 形态
（title/snippet/source_domain），那是给旧的 grounding/web 管线用的。本移植按
契约 §1 把小红书归到「短视频体」，所以这里产出的是短视频体 raw dict，字段名
（text/caption_snippet/author_name/hashtags/...）与 normalizer 对齐。
"""

import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from . import http, log
from .query import extract_core_subject
from .relevance import token_overlap_relevance as _compute_relevance

# Depth -> number of notes to keep. Mirrors the contract's DEPTH_CONFIG target
# counts, but capped a bit lower per-source so a single feed source doesn't
# dominate volume (parallels xiaohongshu_api.search_feeds' per-depth limit).
DEPTH_CONFIG = {
    "quick": 8,
    "default": 15,
    "deep": 25,
}

# Map depth -> 小红书搜索的「发布时间」过滤档位 (本地 mcp 服务的 filters 取值).
_PUBLISH_TIME = {
    "quick": "一天内",
    "default": "一周内",
    "deep": "半年内",
}

# Truncate caption / desc snippets to a sane length before handing to the
# normalizer (mirrors instagram.py's CAPTION_MAX_WORDS intent; CJK has no word
# spaces so we cap on characters instead).
SNIPPET_MAX_CHARS = 500

# ScrapeCreators base + best-effort Xiaohongshu search endpoint. ScrapeCreators
# does not document a first-class 小红书 keyword search the way it does for
# IG/TikTok, so this is attempted defensively and any failure falls through to
# an empty result (never fabricated).
_SC_BASE = "https://api.scrapecreators.com"
_SC_SEARCH_PATH = "/v1/xiaohongshu/search"


def _log(msg: str) -> None:
    log.source_log("小红书", msg, tty_only=False)


def _extract_core_subject(topic: str) -> str:
    """Extract the core subject for a Xiaohongshu keyword search.

    Xiaohongshu's search box behaves like a literal keyword match, so we strip
    question/meta words down to the core subject. ``max_words`` counts CJK
    segmentation units for Chinese queries (see query.extract_core_subject /
    port contract §6).
    """
    return extract_core_subject(topic, max_words=6)


# ---------------------------------------------------------------------------
# Field coercion helpers (shared by both backends).
# ---------------------------------------------------------------------------


def _to_int(value: Any) -> int:
    """Convert a Xiaohongshu count to int.

    Supports plain ints/floats and Chinese magnitude suffixes like ``1.2万`` /
    ``3亿`` that the note interactInfo renders for large counts. Returns 0 on
    anything unparseable.
    """
    if value is None:
        return 0
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)

    text = str(value).strip().replace(",", "")
    if not text:
        return 0
    mult = 1
    if text.endswith("万"):
        mult, text = 10_000, text[:-1]
    elif text.endswith("亿"):
        mult, text = 100_000_000, text[:-1]
    try:
        return int(float(text) * mult)
    except (TypeError, ValueError):
        return 0


def _parse_date(value: Any) -> Optional[str]:
    """Parse a Xiaohongshu publish time into ``YYYY-MM-DD`` (or None).

    Handles, in order:
      - millisecond unix timestamp (int or numeric string) — the local mcp
        service's ``noteCard.time`` shape;
      - second-precision unix timestamp;
      - ISO-ish strings (``2026-02-26T16:00:00.000Z`` / ``2026-02-26 16:00``).
    """
    if value is None:
        return None

    # Numeric timestamp path (ms first, then seconds).
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        try:
            iv = int(value)
        except (TypeError, ValueError):
            iv = 0
        if iv <= 0:
            return None
        # Heuristic: >= 1e12 looks like milliseconds.
        seconds = iv / 1000.0 if iv >= 1_000_000_000_000 else float(iv)
        try:
            dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d")
        except (OSError, OverflowError, ValueError):
            return None

    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # Full ISO string.
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            pass
        # Leading YYYY-MM-DD.
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    return None


def _extract_hashtags(text: str) -> List[str]:
    """Pull #话题# / #tag style hashtags out of a note's text.

    Xiaohongshu renders topics as ``#话题[话题]#`` or plain ``#tag`` — capture
    the inner label in both shapes, stripping a trailing ``[话题]`` marker.
    """
    if not text:
        return []
    tags: List[str] = []
    for raw in re.findall(r"#([^#\[\]\s][^#\[\]]*)", text):
        tag = raw.strip()
        # Drop a trailing "[话题]" / "[地点]" annotation if it leaked in.
        tag = re.sub(r"\[[^\]]*\]$", "", tag).strip()
        if tag:
            tags.append(tag)
    return tags


def _build_note_url(feed_id: str, xsec_token: str = "") -> str:
    """Build a stable Xiaohongshu note permalink.

    The explore URL needs an ``xsec_token`` to open without a session; include
    it when the backend provides one, otherwise fall back to the bare id.
    """
    if not feed_id:
        return ""
    if xsec_token:
        return f"https://www.xiaohongshu.com/explore/{feed_id}?xsec_token={xsec_token}"
    return f"https://www.xiaohongshu.com/explore/{feed_id}"


# ---------------------------------------------------------------------------
# Backend 1: local xiaohongshu-mcp HTTP service (preferred).
# ---------------------------------------------------------------------------


def _resolve_base_url(kw: Dict[str, Any]) -> Optional[str]:
    """Resolve the local mcp base URL: explicit kwarg > config > env default.

    Returns None only when there's truly nothing to try (no kwarg, no config,
    no env). The env default (host.docker.internal:18060) means we usually have
    *something* to attempt; reachability is decided by the request itself.
    """
    explicit = kw.get("base_url")
    if explicit:
        return str(explicit).rstrip("/")

    config = kw.get("config")
    if isinstance(config, dict):
        # Reuse env's resolver so the default base stays in one place.
        try:
            from . import env

            base = env.get_xiaohongshu_api_base(config)
            if base:
                return base.rstrip("/")
        except Exception:  # pragma: no cover - defensive
            pass
        raw = config.get("XIAOHONGSHU_API_BASE")
        if raw:
            return str(raw).rstrip("/")

    raw = os.environ.get("XIAOHONGSHU_API_BASE")
    return raw.rstrip("/") if raw else None


def _search_via_local(
    core: str,
    depth: str,
    base: str,
) -> List[Dict[str, Any]]:
    """Search the local xiaohongshu-mcp service and return raw note dicts.

    Raises http.HTTPError when the service is reachable but unusable (not
    logged in). Other failures bubble up as http.HTTPError from http.get/post.
    On a successful-but-empty search returns [].
    """
    # Quick login sanity check — the service is reachable but useless without a
    # logged-in browser session.
    login = http.get(f"{base}/api/v1/login/status", timeout=8, retries=1)
    is_logged_in = (
        login.get("data", {}).get("is_logged_in")
        if isinstance(login, dict) else False
    )
    if not is_logged_in:
        raise http.HTTPError("小红书本地服务可达但未登录")

    payload = {
        "keyword": core,
        "filters": {
            "sort_by": "综合",
            "note_type": "不限",
            "publish_time": _PUBLISH_TIME.get(depth, _PUBLISH_TIME["default"]),
            "search_scope": "不限",
            "location": "不限",
        },
    }
    resp = http.post(f"{base}/api/v1/feeds/search", payload, timeout=20, retries=1)
    feeds = resp.get("data", {}).get("feeds", []) if isinstance(resp, dict) else []
    if not isinstance(feeds, list):
        return []
    return [f for f in feeds if isinstance(f, dict)]


def _normalize_local_feed(feed: Dict[str, Any], index: int, core: str) -> Optional[Dict[str, Any]]:
    """Map one local-service feed card to a §3 短视频体 raw item dict."""
    note = feed.get("noteCard") if isinstance(feed.get("noteCard"), dict) else {}
    interact = note.get("interactInfo") if isinstance(note.get("interactInfo"), dict) else {}
    user = note.get("user") if isinstance(note.get("user"), dict) else {}

    feed_id = str(feed.get("id") or note.get("noteId") or "").strip()
    if not feed_id:
        return None
    xsec_token = str(feed.get("xsecToken") or note.get("xsecToken") or "").strip()

    title = str(note.get("displayTitle") or note.get("title") or "").strip()
    desc = str(note.get("desc") or note.get("displayDesc") or "").strip()
    # The note "body" we carry as text is the title; the longer desc becomes the
    # caption_snippet (parallels instagram.py: short text + richer caption).
    text = title or desc
    caption = desc if desc and desc != title else ""

    author_name = str(user.get("nickname") or user.get("nickName") or user.get("name") or "").strip()

    likes = _to_int(interact.get("likedCount"))
    comments = _to_int(interact.get("commentCount"))
    collected = _to_int(interact.get("collectedCount"))
    shares = _to_int(interact.get("shareCount"))

    date = _parse_date(note.get("time") or note.get("lastUpdateTime"))
    hashtags = _extract_hashtags(" ".join(p for p in (title, desc) if p))

    relevance = _compute_relevance(core, f"{text} {caption}".strip(), hashtags)

    return {
        "id": f"XHS{index + 1}",
        "text": text[:SNIPPET_MAX_CHARS],
        "caption_snippet": caption[:SNIPPET_MAX_CHARS],
        "url": _build_note_url(feed_id, xsec_token),
        "author_name": author_name,
        "date": date,
        "engagement": {
            "likes": likes,
            "comment": comments,
            "collected": collected,
            "share": shares,
        },
        "hashtags": hashtags,
        "top_comments": [],  # local service search does not return comments
        "relevance": relevance,
        "why_relevant": "",  # 小红书 provides no relevance rationale
    }


# ---------------------------------------------------------------------------
# Backend 2: ScrapeCreators (best-effort fallback).
# ---------------------------------------------------------------------------


def _search_via_scrapecreators(core: str, token: str, limit: int) -> List[Dict[str, Any]]:
    """Best-effort Xiaohongshu keyword search via ScrapeCreators.

    Returns raw §3 短视频体 item dicts, or [] on any failure (endpoint missing,
    HTTP error, unexpected shape). Never raises — this is the degraded path.
    """
    try:
        data = http.get(
            f"{_SC_BASE}{_SC_SEARCH_PATH}",
            params={"query": core, "keyword": core},
            headers=http.scrapecreators_headers(token),
            timeout=30,
            retries=1,
        )
    except http.HTTPError as exc:
        _log(f"ScrapeCreators 小红书搜索失败: {exc}")
        return []
    except Exception as exc:  # pragma: no cover - defensive
        _log(f"ScrapeCreators 小红书搜索异常: {type(exc).__name__}: {exc}")
        return []

    if not isinstance(data, dict):
        return []
    raw_items = (
        data.get("notes")
        or data.get("items")
        or data.get("data")
        or []
    )
    if isinstance(raw_items, dict):
        raw_items = raw_items.get("items") or raw_items.get("notes") or []
    if not isinstance(raw_items, list):
        return []

    items: List[Dict[str, Any]] = []
    for i, raw in enumerate(raw_items[:limit]):
        if not isinstance(raw, dict):
            continue
        note_id = str(raw.get("id") or raw.get("note_id") or raw.get("noteId") or "").strip()
        if not note_id:
            continue

        title = str(raw.get("title") or raw.get("display_title") or "").strip()
        desc = str(raw.get("desc") or raw.get("description") or raw.get("text") or "").strip()
        text = title or desc
        caption = desc if desc and desc != title else ""

        owner = raw.get("user") or raw.get("author") or raw.get("owner")
        if isinstance(owner, dict):
            author_name = str(owner.get("nickname") or owner.get("name") or owner.get("username") or "").strip()
        elif isinstance(owner, str):
            author_name = owner
        else:
            author_name = ""

        likes = _to_int(raw.get("liked_count") or raw.get("likes") or raw.get("like_count"))
        comments = _to_int(raw.get("comment_count") or raw.get("comments"))
        collected = _to_int(raw.get("collected_count") or raw.get("collected") or raw.get("favorites"))
        shares = _to_int(raw.get("share_count") or raw.get("shares"))

        date = _parse_date(raw.get("time") or raw.get("taken_at") or raw.get("created_at"))
        hashtags = _extract_hashtags(" ".join(p for p in (title, desc) if p))
        url = str(raw.get("url") or "").strip() or _build_note_url(note_id, str(raw.get("xsec_token") or ""))

        relevance = _compute_relevance(core, f"{text} {caption}".strip(), hashtags)
        items.append({
            "id": f"XHS{i + 1}",
            "text": text[:SNIPPET_MAX_CHARS],
            "caption_snippet": caption[:SNIPPET_MAX_CHARS],
            "url": url,
            "author_name": author_name,
            "date": date,
            "engagement": {
                "likes": likes,
                "comment": comments,
                "collected": collected,
                "share": shares,
            },
            "hashtags": hashtags,
            "top_comments": [],
            "relevance": relevance,
            "why_relevant": "",
        })
    return items


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------


def search_xiaohongshu(
    query: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    **kw: Any,
) -> Dict[str, Any]:
    """Search Xiaohongshu notes, preferring the local mcp service.

    Resolution order (port contract §2):
      1. ``XIAOHONGSHU_API_BASE`` local service (``/api/v1/feeds/search``).
      2. ``SCRAPECREATORS_API_KEY`` ScrapeCreators search (best-effort).
      3. Neither usable → ``{"items": []}`` (never fabricated).

    Args:
        query: Search topic (raw user topic; the pipeline passes raw_topic so
            core-subject extraction works from the original wording).
        from_date: Start date (YYYY-MM-DD). The note window is enforced here as
            a hard filter once per-note dates are parsed.
        to_date: End date (YYYY-MM-DD).
        depth: "quick" | "default" | "deep" — controls note count.
        **kw: Optional ``config=`` dict (from env.get_config()), ``base_url=``
            override, ``token=`` ScrapeCreators key override, and ``creators=``
            (accepted for interface symmetry with douyin; the local search
            service has no per-creator feed endpoint, so it is currently unused
            and kept as a no-op rather than fabricating creator results).

    Returns:
        ``{"items": [<short-video raw dict>, ...], "query": <core subject>}``.
        On any failure / no backend returns ``{"items": [], "query": ...}`` —
        never raises, never fabricates.
    """
    core = _extract_core_subject(query) or (query or "").strip()
    limit = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    if not core:
        return {"items": [], "query": core}

    config = kw.get("config") if isinstance(kw.get("config"), dict) else None

    items: List[Dict[str, Any]] = []

    # --- Backend 1: local xiaohongshu-mcp service -------------------------
    base = _resolve_base_url(kw)
    if base:
        try:
            feeds = _search_via_local(core, depth, base)
            for i, feed in enumerate(feeds[:limit]):
                normalized = _normalize_local_feed(feed, i, core)
                if normalized:
                    items.append(normalized)
            if items:
                _log(f"本地服务取得 {len(items)} 条小红书笔记 (核心词: {core})")
        except http.HTTPError as exc:
            _log(f"本地服务不可用: {exc}")
        except Exception as exc:  # pragma: no cover - defensive
            _log(f"本地服务异常: {type(exc).__name__}: {exc}")

    # --- Backend 2: ScrapeCreators fallback -------------------------------
    if not items:
        token = kw.get("token") or (config.get("SCRAPECREATORS_API_KEY") if config else None) \
            or os.environ.get("SCRAPECREATORS_API_KEY")
        if token:
            _log("本地服务无结果，尝试 ScrapeCreators 降级")
            items = _search_via_scrapecreators(core, token, limit)
            if items:
                _log(f"ScrapeCreators 取得 {len(items)} 条小红书笔记 (核心词: {core})")

    if not items:
        _log("无可用取数后端（本地服务/ScrapeCreators 均不可用），返回空")
        return {"items": [], "query": core}

    # --- Hard date filter (mirrors instagram.py) --------------------------
    in_range = [i for i in items if i.get("date") and from_date <= i["date"] <= to_date]
    out_of_range = len(items) - len(in_range)
    if in_range:
        items = in_range
        if out_of_range:
            _log(f"过滤掉 {out_of_range} 条窗口外的笔记")
    else:
        _log(f"窗口内无笔记，保留全部 {len(items)} 条交由后续阶段判定")

    # Sort by likes descending (the most reliable cross-backend signal).
    items.sort(key=lambda x: x.get("engagement", {}).get("likes", 0), reverse=True)

    return {"items": items[:limit], "query": core}


def parse_xiaohongshu_response(result: Any, query: str = "") -> List[Dict[str, Any]]:
    """Parse a search_xiaohongshu result into §3 短视频体 raw item dicts.

    Output shape per item (consumed by normalize._normalize_shortform_video)::

        {id, text, caption_snippet, url, author_name, date,
         engagement: {likes, comment, collected, share},
         hashtags: [...], top_comments: [...],
         relevance, why_relevant}

    ``search_xiaohongshu`` already produces this shape, so parse mostly
    validates / re-ranks. It also tolerates being handed a bare list of items
    or a raw local-service ``feeds`` response, so the function is robust to
    being called on either stage's output.

    Args:
        result: The dict from ``search_xiaohongshu`` (``{"items": [...]}``), a
            bare list of items, or a raw local-service response dict.
        query: Original search query for relevance re-scoring. Falls back to the
            core subject stashed in ``result["query"]`` when omitted.

    Returns:
        List of raw item dicts. Empty list on any unusable input — never raises.
    """
    if not result:
        return []

    ranking_query = query
    raw_items: List[Dict[str, Any]] = []

    if isinstance(result, dict):
        if result.get("error"):
            _log(f"解析时遇到错误响应: {result.get('error')}")
            return []
        if not ranking_query:
            ranking_query = result.get("query", "") or ""
        if isinstance(result.get("items"), list):
            raw_items = [i for i in result["items"] if isinstance(i, dict)]
        else:
            # Treat as a raw local-service response: data.feeds[].
            data = result.get("data") if isinstance(result.get("data"), dict) else {}
            feeds = data.get("feeds") if isinstance(data.get("feeds"), list) else []
            core = ranking_query or query or ""
            parsed: List[Dict[str, Any]] = []
            for i, feed in enumerate(feeds):
                if not isinstance(feed, dict):
                    continue
                normalized = _normalize_local_feed(feed, i, core)
                if normalized:
                    parsed.append(normalized)
            raw_items = parsed
    elif isinstance(result, list):
        raw_items = [i for i in result if isinstance(i, dict)]
    else:
        return []

    items: List[Dict[str, Any]] = []
    for raw in raw_items:
        text = str(raw.get("text") or "").strip()
        caption = str(raw.get("caption_snippet") or "").strip()
        url = str(raw.get("url") or "").strip()
        if not url or not (text or caption):
            # No permalink or no body -> not a usable evidence item.
            continue

        engagement = raw.get("engagement")
        if not isinstance(engagement, dict):
            engagement = {}
        hashtags = raw.get("hashtags") if isinstance(raw.get("hashtags"), list) else []

        relevance = raw.get("relevance")
        if not isinstance(relevance, (int, float)):
            relevance = _compute_relevance(ranking_query, f"{text} {caption}".strip(), hashtags) \
                if ranking_query else 0.5

        items.append({
            "id": str(raw.get("id") or f"XHS{len(items) + 1}"),
            "text": text[:SNIPPET_MAX_CHARS],
            "caption_snippet": caption[:SNIPPET_MAX_CHARS],
            "url": url,
            "author_name": str(raw.get("author_name") or "").strip(),
            "date": raw.get("date"),
            "engagement": engagement,
            "hashtags": hashtags,
            "top_comments": raw.get("top_comments") if isinstance(raw.get("top_comments"), list) else [],
            "relevance": float(relevance),
            "why_relevant": str(raw.get("why_relevant") or ""),
        })

    return items
