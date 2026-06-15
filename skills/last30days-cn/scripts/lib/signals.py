"""Reusable local scoring signals for the v3 pipeline (China-market sources).

Includes an anti-manipulation layer (see ``manipulation_signals``) that defends
the engagement-ranked pool against three adversaries:

- **SEO** — irrelevant here by construction: the engine never reads a search
  engine's ranked results as a primary signal. It pulls platform APIs and ranks
  by real human engagement, so backlink/keyword tactics never enter the pool.
- **GEO** (Generative Engine Optimization) — content crafted to be cited by
  AI/LLM engines. Partly defeated by the engagement gate (no real engagement →
  not in the pool) and cross-source corroboration; the residual surface is
  persuasive/templated text aimed at the LLM reranker, which we down-weight via
  text-shape heuristics (keyword stuffing, promo markers, link/hashtag spam).
- **水军 / astroturfing** — bought likes/reposts/views, vote brigading. We
  down-weight implausible engagement shapes (high volume at scale with ~zero
  genuine discussion) on the platforms where paid inflation is common.

All penalties are conservative and explainable: a multiplier floored at 0.3 is
applied to ``local_rank_score`` and the reasons are recorded in
``item.metadata['manipulation_flags']`` so synthesis can see why an item ranked
lower. Heuristics never fabricate or hard-delete; they only de-prioritize.
"""

from __future__ import annotations

import math
import re

from . import dates, relevance, schema

# Editorial signal-to-noise scores per China-market source. Default is 0.6.
# Long-form / developer / code sources score higher; short-form & microblog
# (the noisiest, most-farmed) score lower.
SOURCE_QUALITY = {
    "github": 0.85,
    "bilibili": 0.85,
    "v2ex": 0.80,
    "juejin": 0.75,
    "zhihu": 0.70,
    "weibo": 0.62,
    "xiaohongshu": 0.60,
    "douyin": 0.58,
    "xueqiu": 0.55,
}


def source_quality(source: str) -> float:
    return SOURCE_QUALITY.get(source, 0.6)


def local_relevance(
    item: schema.SourceItem,
    ranking_query: "str | relevance.PreparedQuery",
) -> float:
    text = "\n".join(
        part
        for part in [item.title, item.body, item.snippet]
        if part
    )
    hashtags = item.metadata.get("hashtags") if isinstance(item.metadata, dict) else None
    score = relevance.token_overlap_relevance(ranking_query, text, hashtags=hashtags)

    # High-engagement Bilibili floor: popular videos often have titles that don't
    # keyword-match the query, but the view count says "this is important."
    if item.source == "bilibili" and item.engagement.get("view", 0) > 100_000:
        score = max(score, 0.3)

    # Project-mode GitHub floor: items fetched via --github-repo are explicitly
    # requested and relevant by construction.
    labels = item.metadata.get("labels", []) if isinstance(item.metadata, dict) else []
    if "project-mode" in labels:
        score = max(score, 0.8)

    return score


def freshness(item: schema.SourceItem, freshness_mode: str = "balanced_recent") -> int:
    score = dates.recency_score(item.published_at)
    if freshness_mode == "strict_recent":
        return int(score)
    if freshness_mode == "evergreen_ok":
        return int((score * 0.6) + 40)
    return int((score * 0.8) + 10)


def log1p_safe(value: float | int | None) -> float:
    if value is None:
        return 0.0
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    if numeric <= 0:
        return 0.0
    return math.log1p(numeric)


def _top_comment_score(item: schema.SourceItem) -> float:
    comments = item.metadata.get("top_comments") or []
    if not comments or not isinstance(comments[0], dict):
        return 0.0
    return log1p_safe(comments[0].get("score"))


# Per-source engagement weights: list of (field_name, weight) tuples.
# Zhihu and Bilibili use custom functions because they carry a top-comment slot.
ENGAGEMENT_WEIGHTS: dict[str, list[tuple[str, float]]] = {
    "weibo":       [("attitudes", 0.50), ("reposts", 0.30), ("comments", 0.20)],
    "douyin":      [("views", 0.40), ("digg_count", 0.35), ("comment", 0.15), ("share", 0.10)],
    "xiaohongshu": [("likes", 0.45), ("collected", 0.30), ("comment", 0.15), ("share", 0.10)],
    "v2ex":        [("comments", 1.0)],   # V2EX has no upvotes; replies carry all signal
    "juejin":      [("points", 0.50), ("comments", 0.50)],
    "xueqiu":      [("volume", 0.60), ("liquidity", 0.40)],
}


def _weighted_engagement(item: schema.SourceItem, weights: list[tuple[str, float]]) -> float | None:
    values = [(log1p_safe(item.engagement.get(field)), weight) for field, weight in weights]
    if not any(v for v, _ in values):
        return None
    return sum(v * w for v, w in values)


def _zhihu_engagement(item: schema.SourceItem) -> float | None:
    """Zhihu = Reddit analog: upvotes (score) + comments + top-comment slot."""
    score = log1p_safe(item.engagement.get("score"))
    comments = log1p_safe(item.engagement.get("num_comments"))
    top_comment = _top_comment_score(item)
    if not any([score, comments, top_comment]):
        return None
    return (0.55 * score) + (0.35 * comments) + (0.10 * top_comment)


def _bilibili_engagement(item: schema.SourceItem) -> float | None:
    """Bilibili = YouTube analog: views dominate, plus like/coin/reply/danmaku."""
    views = log1p_safe(item.engagement.get("view"))
    likes = log1p_safe(item.engagement.get("like"))
    coin = log1p_safe(item.engagement.get("coin"))
    reply = log1p_safe(item.engagement.get("reply"))
    danmaku = log1p_safe(item.engagement.get("danmaku"))
    top_comment = _top_comment_score(item)
    if not any([views, likes, coin, reply, danmaku, top_comment]):
        return None
    return (
        0.42 * views
        + 0.25 * likes
        + 0.10 * coin
        + 0.08 * reply
        + 0.05 * danmaku
        + 0.10 * top_comment
    )


def _generic_engagement(item: schema.SourceItem) -> float | None:
    if not item.engagement:
        return None
    values = [logged for v in item.engagement.values() if (logged := log1p_safe(v)) > 0]
    if not values:
        return None
    return sum(values) / len(values)


def engagement_raw(item: schema.SourceItem) -> float | None:
    if item.source == "zhihu":
        return _zhihu_engagement(item)
    if item.source == "bilibili":
        return _bilibili_engagement(item)
    weights = ENGAGEMENT_WEIGHTS.get(item.source)
    if weights:
        return _weighted_engagement(item, weights)
    return _generic_engagement(item)


# --------------------------------------------------------------------------- #
# Anti-manipulation layer (anti-GEO / anti-astroturf). Conservative + explainable.
# --------------------------------------------------------------------------- #

# Lowest multiplier any heuristic stack can reduce an item to. Heuristics only
# de-prioritize; they never zero out or delete.
_MANIPULATION_FLOOR = 0.3

# Promotional / ad / "刷量" solicitation markers (zh + en). Their presence in
# short organic-looking posts is a strong spam/GEO tell.
_PROMO_MARKERS = (
    "加微信", "加vx", "加v信", "+v", "薇信", "扫码", "二维码", "私信我", "私我",
    "代刷", "刷量", "刷赞", "刷粉", "互赞", "互粉", "求关注", "求点赞",
    "推广", "广告合作", "商务合作", "带货", "优惠券", "领取", "福利",
    "点击链接", "点我", "戳链接", "限时", "免费领", "加群", "进群",
    "buy now", "click here", "dm me", "promo code", "discount code",
    "limited time", "link in bio", "follow back", "free trial",
)
_PROMO_RE = re.compile("|".join(re.escape(m) for m in _PROMO_MARKERS), re.IGNORECASE)
_URL_RE = re.compile(r"https?://", re.IGNORECASE)
# CJK char runs OR ascii words, for a cheap unique-token-ratio measure.
_TOKEN_RE = re.compile(r"[一-鿿]|[a-zA-Z0-9]+")

# Platforms where paid engagement inflation (水军 / 刷量) is common and where
# genuine engagement is expected to spread across likes + reposts + comments.
_ASTROTURF_SOURCES = {"weibo", "zhihu", "douyin", "xiaohongshu"}
_COMMENT_FIELDS = ("comments", "num_comments", "comment", "reply")


def _text_of(item: schema.SourceItem) -> str:
    return " ".join(part for part in (item.title, item.body, item.snippet) if part)


def _engagement_anomaly(item: schema.SourceItem) -> str | None:
    """Detect astroturf-shaped engagement: volume at scale with ~no discussion.

    Only applied to platforms where paid inflation is common. Conservative
    thresholds keep genuine viral content (which always draws proportional
    comments) safe.
    """
    if item.source not in _ASTROTURF_SOURCES:
        return None
    eng = item.engagement or {}
    nums = {k: float(v) for k, v in eng.items() if isinstance(v, (int, float))}
    if not nums:
        return None
    primary = max(nums.values(), default=0.0)
    if primary < 1000:  # only suspicious at scale; small counts carry no signal
        return None
    comments = max((nums.get(f, 0.0) for f in _COMMENT_FIELDS), default=0.0)
    if comments == 0:
        return "互动结构异常(高互动零评论)"
    if primary / max(comments, 1.0) > 800:
        return "互动结构异常(评论占比极低)"
    return None


def manipulation_signals(item: schema.SourceItem) -> tuple[float, list[str]]:
    """Return (score_multiplier, reasons) for an item.

    Multiplier is in [_MANIPULATION_FLOOR, 1.0]. Empty reasons => clean (1.0).
    Reasons are short Chinese tags recorded for explainability.
    """
    mult = 1.0
    reasons: list[str] = []
    text = _text_of(item)
    low = text.lower()

    # 1) Promo / ad / 刷量-solicitation spam (GEO seeding + spam).
    promo_hits = len(_PROMO_RE.findall(text))
    if promo_hits >= 2 or (promo_hits >= 1 and len(text) < 60):
        mult *= 0.5
        reasons.append("推广/广告话术")

    # 2) Link stuffing.
    if len(_URL_RE.findall(low)) >= 3:
        mult *= 0.7
        reasons.append("链接堆砌")

    # 3) Hashtag stuffing (short-video GEO tactic).
    hashtags = item.metadata.get("hashtags") if isinstance(item.metadata, dict) else None
    if isinstance(hashtags, list) and len(hashtags) >= 8:
        mult *= 0.8
        reasons.append("话题标签堆砌")

    # 4) Keyword stuffing / templated text (low unique-token ratio at length).
    tokens = _TOKEN_RE.findall(text)
    if len(tokens) >= 12:
        unique_ratio = len(set(tokens)) / len(tokens)
        if unique_ratio < 0.45:
            mult *= 0.7
            reasons.append("关键词堆砌")

    # 5) Astroturf-shaped engagement (水军 / 刷量).
    anomaly = _engagement_anomaly(item)
    if anomaly:
        mult *= 0.6
        reasons.append(anomaly)

    return max(mult, _MANIPULATION_FLOOR), reasons


def normalize(values: list[float | None]) -> list[int | None]:
    valid = [value for value in values if value is not None]
    if not valid:
        return [None for _ in values]
    low = min(valid)
    high = max(valid)
    if math.isclose(low, high):
        return [50 if value is not None else None for value in values]
    return [
        None
        if value is None
        else int(((value - low) / (high - low)) * 100)
        for value in values
    ]


def annotate_stream(
    items: list[schema.SourceItem],
    ranking_query: "str | relevance.PreparedQuery",
    freshness_mode: str,
) -> list[schema.SourceItem]:
    """Attach local scoring metadata and return items sorted by local_rank_score."""
    prepared_query = ranking_query if isinstance(ranking_query, relevance.PreparedQuery) else relevance.PreparedQuery(ranking_query)
    engagement_scores = normalize([engagement_raw(item) for item in items])
    for item, eng_score in zip(items, engagement_scores, strict=True):
        item.local_relevance = local_relevance(item, prepared_query)
        item.freshness = freshness(item, freshness_mode)
        item.engagement_score = eng_score
        item.source_quality = source_quality(item.source)
        base = (
            0.65 * item.local_relevance
            + 0.25 * (item.freshness / 100.0)
            + 0.10 * ((eng_score or 0) / 100.0)
        )
        # Anti-manipulation: down-weight astroturf/GEO/spam-shaped items.
        mult, flags = manipulation_signals(item)
        item.local_rank_score = base * mult
        if flags and isinstance(item.metadata, dict):
            item.metadata["manipulation_penalty"] = round(mult, 2)
            item.metadata["manipulation_flags"] = flags
    return sorted(items, key=lambda item: item.local_rank_score or 0, reverse=True)


# Microblog / short-form / Q&A sources where zero engagement is a strong noise
# signal (used by prune_low_relevance to apply a stricter relevance threshold).
_SOCIAL_SOURCES = {"weibo", "zhihu", "douyin", "xiaohongshu"}

# Minimum view count for short-video platforms. Items below this floor are
# typically spam reposts or low-effort clips that add no unique signal.
_VIDEO_ENGAGEMENT_FLOOR_SOURCES = {"douyin"}
_VIDEO_ENGAGEMENT_FLOOR_VIEWS = 1000


def _passes_engagement_floor(item: schema.SourceItem, sole_source: bool) -> bool:
    """Check whether a short-video item meets the minimum view floor.

    Items from sources not in _VIDEO_ENGAGEMENT_FLOOR_SOURCES always pass.
    If the item's source is the *only* source in the batch (sole_source=True),
    all items pass so we never return an empty result for a whole source.
    """
    if item.source not in _VIDEO_ENGAGEMENT_FLOOR_SOURCES:
        return True
    if sole_source:
        return True
    views = item.engagement.get("views", 0) if item.engagement else 0
    return views >= _VIDEO_ENGAGEMENT_FLOOR_VIEWS


def prune_low_relevance(
    items: list[schema.SourceItem],
    minimum: float = 0.15,
) -> list[schema.SourceItem]:
    """Drop weak lexical matches when stronger evidence exists.

    Social-source items with zero engagement get a stricter threshold because
    zero engagement on a microblog/short-form platform is a strong noise signal.
    Douyin items with fewer than 1000 views are pruned (unless they are the only
    source represented in the batch).
    """
    sources_present = {item.source for item in items}

    def passes(item: schema.SourceItem) -> bool:
        rel = item.local_relevance if item.local_relevance is not None else 0.0
        if rel < minimum:
            return False
        if item.source in _SOCIAL_SOURCES and (item.engagement_score is None or item.engagement_score == 0):
            if rel < minimum * 1.5:
                return False
        sole_source = sources_present == {item.source}
        if not _passes_engagement_floor(item, sole_source):
            return False
        return True

    filtered = [item for item in items if passes(item)]
    return filtered or items
