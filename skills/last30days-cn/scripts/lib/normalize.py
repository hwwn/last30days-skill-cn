"""把各源专属载荷归一化为 v3 通用 item 模型（中国市场移植版）。

源名映射（西方原型 → CN 源）见 PORT_CONTRACT.md §1：
    x          → weibo        (微博体)
    reddit     → zhihu        (知乎)
    youtube    → bilibili     (B站)
    tiktok/ig  → douyin/xiaohongshu (短视频体, 共用 _normalize_shortform_video)
    hackernews → v2ex/juejin  (论坛体, 共用 _normalize_forum)
    polymarket → xueqiu       (雪球, 情绪体)
    github     → github       (保留原样)
    grounding  → grounding    (网页, 保留原样)
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from . import dates, schema


def filter_by_date_range(
    items: list[schema.SourceItem],
    from_date: str,
    to_date: str,
    require_date: bool = False,
) -> list[schema.SourceItem]:
    """只保留落在请求时间窗口内的 item。"""
    filtered: list[schema.SourceItem] = []
    for item in items:
        if not item.published_at:
            if not require_date:
                filtered.append(item)
            continue
        if item.published_at < from_date or item.published_at > to_date:
            continue
        filtered.append(item)
    return filtered


def normalize_source_items(
    source: str,
    items: list[dict[str, Any]],
    from_date: str,
    to_date: str,
    freshness_mode: str = "balanced_recent",
) -> list[schema.SourceItem]:
    """归一化各源 raw item，按时间窗口过滤；how_to 类查询走常青回退。"""
    source = source.lower()
    normalizers = {
        "weibo": _normalize_weibo,
        "zhihu": _normalize_zhihu,
        "bilibili": _normalize_bilibili,
        "douyin": lambda s, i, idx, fd, td: _normalize_shortform_video(s, i, idx, fd, td, "DY", "抖音视频"),
        "xiaohongshu": lambda s, i, idx, fd, td: _normalize_shortform_video(s, i, idx, fd, td, "XHS", "小红书笔记"),
        "v2ex": _normalize_forum,
        "juejin": _normalize_forum,
        "xueqiu": _normalize_xueqiu,
        "github": _normalize_github,
        "grounding": _normalize_grounding,
    }
    normalizer = normalizers.get(source)
    if normalizer is None:
        raise ValueError(f"不支持的数据源: {source}")
    normalized = [normalizer(source, item, index, from_date, to_date) for index, item in enumerate(items)]
    require_date = source == "grounding"
    filtered = filter_by_date_range(normalized, from_date, to_date, require_date=require_date)
    if filtered:
        return filtered
    if freshness_mode == "evergreen_ok" and source == "bilibili":
        if require_date:
            return [item for item in normalized if item.published_at]
        return normalized
    return filtered


def _remap_comments(
    raw: list[Any],
    score_keys: tuple[str, ...],
    excerpt_keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    """把任意源的评论归一为共享的 Reddit/知乎兼容形态。

    下游代码（signals._top_comment_score、render._top_comments_list、
    entity_extract、rerank）都期望 `score` 与 `excerpt`。本 helper 把
    各源字段名（B站: likes/text，抖音: digg_count/text）映射到该形态，
    同时透传 author/date/url。
    """
    out: list[dict[str, Any]] = []
    for raw_c in raw:
        if not isinstance(raw_c, dict):
            continue
        score = _first_present(raw_c, score_keys, default=0)
        excerpt = _first_present(raw_c, excerpt_keys, default="")
        try:
            score_int = int(score or 0)
        except (TypeError, ValueError):
            score_int = 0
        entry: dict[str, Any] = {
            "score": score_int,
            "excerpt": str(excerpt or "")[:400],
            "author": str(raw_c.get("author") or ""),
            "date": str(raw_c.get("date") or ""),
        }
        if raw_c.get("url"):
            entry["url"] = str(raw_c["url"])
        out.append(entry)
    return out


def _first_present(d: dict[str, Any], keys: tuple[str, ...], default: Any) -> Any:
    for key in keys:
        if key in d and d[key] not in (None, ""):
            return d[key]
    return default


def _join_comment_excerpts(
    top_comments: list[Any],
    key: str,
    limit: int = 3,
) -> str:
    """把前 `limit` 条 dict 形态评论的 `key` 字段用空格拼接。"""
    return " ".join(
        str(comment.get(key) or "").strip()
        for comment in top_comments[:limit]
        if isinstance(comment, dict)
    )


def _domain_from_url(url: str) -> str | None:
    if not url:
        return None
    domain = urlparse(url).netloc.strip().lower()
    return domain or None


def _date_confidence(item: dict[str, Any], from_date: str, to_date: str, default: str = "low") -> str:
    if item.get("date_confidence"):
        return str(item["date_confidence"])
    date_value = item.get("date")
    if not date_value:
        return default
    return dates.get_date_confidence(str(date_value), from_date, to_date)


def _source_item(
    *,
    item_id: str,
    source: str,
    title: str,
    body: str,
    url: str,
    published_at: str | None,
    date_confidence: str,
    relevance_hint: float,
    why_relevant: str,
    author: str | None = None,
    container: str | None = None,
    engagement: dict[str, float | int] | None = None,
    snippet: str = "",
    metadata: dict[str, Any] | None = None,
) -> schema.SourceItem:
    return schema.SourceItem(
        item_id=item_id,
        source=source,
        title=title.strip() or body.strip()[:160] or item_id,
        body=body.strip(),
        url=url.strip(),
        author=(author or "").strip() or None,
        container=(container or "").strip() or None,
        published_at=published_at,
        date_confidence=date_confidence,
        engagement=engagement or {},
        relevance_hint=max(0.0, min(1.0, float(relevance_hint or 0.0))),
        why_relevant=why_relevant.strip(),
        snippet=snippet.strip(),
        metadata=metadata or {},
    )


def _normalize_zhihu(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
) -> schema.SourceItem:
    """知乎归一化（原型 reddit）。subreddit 字段承载话题/专栏名。"""
    top_comments = item.get("top_comments") or []
    comment_text = _join_comment_excerpts(top_comments, "excerpt")
    body = "\n".join(
        part
        for part in [
            str(item.get("title") or "").strip(),
            str(item.get("selftext") or "").strip(),
            comment_text,
        ]
        if part
    )
    return _source_item(
        item_id=str(item.get("id") or f"ZH{index + 1}"),
        source=source,
        title=str(item.get("title") or ""),
        body=body,
        url=str(item.get("url") or ""),
        author=None,
        container=str(item.get("subreddit") or ""),
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date),
        engagement=item.get("engagement") or {},
        relevance_hint=item.get("relevance", 0.5),
        why_relevant=str(item.get("why_relevant") or ""),
        snippet=comment_text or str(item.get("selftext") or "")[:400],
        metadata={
            "top_comments": top_comments,
            "comment_insights": item.get("comment_insights") or [],
        },
    )


def _normalize_weibo(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
) -> schema.SourceItem:
    """微博归一化（原型 x）。微博短文本正文 + 转评赞互动。"""
    text = str(item.get("text") or "").strip()
    return _source_item(
        item_id=str(item.get("id") or f"WB{index + 1}"),
        source=source,
        title=text[:140] or f"微博 {index + 1}",
        body=text,
        url=str(item.get("url") or ""),
        author=str(item.get("author_handle") or "").lstrip("@"),
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date),
        engagement=item.get("engagement") or {},
        relevance_hint=item.get("relevance", 0.5),
        why_relevant=str(item.get("why_relevant") or ""),
    )


def _normalize_bilibili(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
) -> schema.SourceItem:
    """B站归一化（原型 youtube）。transcript_snippet 承载简介/字幕。"""
    transcript = str(item.get("transcript_snippet") or "").strip()
    description = str(item.get("description") or "").strip()
    title = str(item.get("title") or "").strip()
    highlights = item.get("transcript_highlights") or []
    metadata: dict[str, Any] = {}
    if highlights:
        metadata["transcript_highlights"] = highlights
    if item.get("captions_disabled"):
        # 透传给 quality_nudge：UP主关闭了字幕，因此该视频应从
        # degraded-transcript-ratio 的分母中扣除（它本就不会产出字幕）。
        metadata["captions_disabled"] = True
    metadata["top_comments"] = _remap_comments(
        item.get("top_comments") or [],
        score_keys=("score", "likes"),
        excerpt_keys=("excerpt", "text"),
    )
    return _source_item(
        item_id=str(item.get("video_id") or item.get("id") or f"BV{index + 1}"),
        source=source,
        title=title,
        body="\n".join(part for part in [title, description, transcript] if part),
        url=str(item.get("url") or ""),
        author=str(item.get("channel_name") or ""),
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date, default="high"),
        engagement=item.get("engagement") or {},
        relevance_hint=item.get("relevance", 0.5),
        why_relevant=str(item.get("why_relevant") or ""),
        snippet=transcript,
        metadata=metadata,
    )


def _normalize_shortform_video(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
    id_prefix: str,
    default_title: str,
) -> schema.SourceItem:
    """抖音与小红书共用归一化（结构相同，原型 tiktok/instagram）。"""
    caption = str(item.get("caption_snippet") or "").strip()
    text = str(item.get("text") or "").strip()
    return _source_item(
        item_id=str(item.get("id") or f"{id_prefix}{index + 1}"),
        source=source,
        title=text[:140] or caption[:140] or f"{default_title} {index + 1}",
        body="\n".join(part for part in [text, caption] if part),
        url=str(item.get("url") or ""),
        author=str(item.get("author_name") or ""),
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date, default="high"),
        engagement=item.get("engagement") or {},
        relevance_hint=item.get("relevance", 0.5),
        why_relevant=str(item.get("why_relevant") or ""),
        snippet=caption,
        metadata={
            "hashtags": item.get("hashtags") or [],
            "top_comments": _remap_comments(
                item.get("top_comments") or [],
                # 抖音用 digg_count 作为点赞字段；小红书目前没有评论抓取，
                # 因此 digg_count key 不存在也无害。
                score_keys=("score", "digg_count", "likes"),
                excerpt_keys=("excerpt", "text"),
            ),
        },
    )


def _normalize_forum(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
) -> schema.SourceItem:
    """V2EX 与掘金共用归一化（论坛体，原型 hackernews）。"""
    top_comments = item.get("top_comments") or []
    comment_text = _join_comment_excerpts(top_comments, "text")
    title = str(item.get("title") or "").strip()
    body = "\n".join(part for part in [title, str(item.get("text") or "").strip(), comment_text] if part)
    container = "V2EX" if source == "v2ex" else "掘金"
    return _source_item(
        item_id=str(item.get("id") or f"FM{index + 1}"),
        source=source,
        title=title or f"{container}帖 {index + 1}",
        body=body,
        url=str(item.get("url") or item.get("hn_url") or ""),
        author=str(item.get("author") or ""),
        container=container,
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date, default="high"),
        engagement=item.get("engagement") or {},
        relevance_hint=item.get("relevance", 0.5),
        why_relevant=str(item.get("why_relevant") or ""),
        snippet=comment_text,
        metadata={
            "hn_url": item.get("hn_url"),
            "top_comments": top_comments,
            "comment_insights": item.get("comment_insights") or [],
        },
    )


def _normalize_xueqiu(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
) -> schema.SourceItem:
    """雪球归一化（情绪体，原型 polymarket）。讨论量/情绪映射到 volume/liquidity。"""
    title = str(item.get("title") or "").strip()
    question = str(item.get("question") or "").strip()
    engagement = {
        "volume": item.get("volume1mo") or item.get("volume24hr") or 0,
        "liquidity": item.get("liquidity") or 0,
    }
    return _source_item(
        item_id=str(item.get("id") or f"XQ{index + 1}"),
        source=source,
        title=title or question or f"雪球讨论 {index + 1}",
        body="\n".join(part for part in [title, question, str(item.get("price_movement") or "")] if part),
        url=str(item.get("url") or ""),
        author=None,
        container="雪球",
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date, default="high"),
        engagement=engagement,
        relevance_hint=item.get("relevance", 0.5),
        why_relevant=str(item.get("why_relevant") or ""),
        snippet=str(item.get("price_movement") or ""),
        metadata={
            "question": question,
            "end_date": item.get("end_date"),
            "outcome_prices": item.get("outcome_prices") or [],
            "outcomes_remaining": item.get("outcomes_remaining"),
        },
    )


def _normalize_github(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
) -> schema.SourceItem:
    title = str(item.get("title") or "").strip()
    snippet_text = str(item.get("snippet") or "").strip()
    top_comments = item.get("metadata", {}).get("top_comments") or []
    comment_text = _join_comment_excerpts(top_comments, "excerpt")
    body = "\n".join(part for part in [title, snippet_text, comment_text] if part)
    metadata = item.get("metadata") or {}
    return _source_item(
        item_id=str(item.get("id") or f"GH{index + 1}"),
        source=source,
        title=title or f"GitHub 条目 {index + 1}",
        body=body,
        url=str(item.get("url") or ""),
        author=str(item.get("author") or ""),
        container=str(item.get("container") or ""),
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date, default="high"),
        engagement=item.get("engagement") or {},
        relevance_hint=item.get("relevance", 0.5),
        why_relevant=str(item.get("why_relevant") or ""),
        snippet=comment_text or snippet_text[:400],
        metadata={
            "top_comments": top_comments,
            "labels": metadata.get("labels") or [],
            "state": metadata.get("state", ""),
            "is_pr": metadata.get("is_pr", False),
        },
    )


def _normalize_grounding(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
) -> schema.SourceItem:
    title = str(item.get("title") or "").strip()
    snippet = str(item.get("snippet") or "").strip()
    url = str(item.get("url") or "").strip()
    return _source_item(
        item_id=str(item.get("id") or f"W{index + 1}"),
        source=source,
        title=title or _domain_from_url(url) or f"网页结果 {index + 1}",
        body="\n".join(part for part in [title, snippet] if part),
        url=url,
        author=None,
        container=str(item.get("source_domain") or _domain_from_url(url) or ""),
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date),
        engagement=item.get("engagement") or {},
        relevance_hint=item.get("relevance", 0.5),
        why_relevant=str(item.get("why_relevant") or ""),
        snippet=snippet,
        metadata=item.get("metadata") or {},
    )
