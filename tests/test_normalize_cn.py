"""normalize.py — each CN source's normalizer emits the §3 SourceItem shape.

Feeds hand-built §3 raw item dicts (the shape each provider's parse_* emits)
through ``normalize_source_items`` and asserts the resulting ``schema.SourceItem``
fields and source-specific metadata wiring. Dates are chosen inside the window
so the date filter keeps every item.
"""

import pytest

from lib import normalize, schema

FROM_DATE = "2026-05-15"
TO_DATE = "2026-06-14"
IN_WINDOW = "2026-06-01"


def _norm(source, raw_items):
    return normalize.normalize_source_items(source, raw_items, FROM_DATE, TO_DATE)


def test_unknown_source_raises():
    with pytest.raises(ValueError):
        _norm("twitter", [{"text": "x"}])


# --------------------------------------------------------------------------- #
# 微博体
# --------------------------------------------------------------------------- #

def test_normalize_weibo():
    raw = [{
        "id": "WB1",
        "text": "国产大模型又出新版本了",
        "url": "https://m.weibo.cn/status/Pabc123",
        "author_handle": "@某博主",
        "date": IN_WINDOW,
        "engagement": {"reposts": 100, "comments": 30, "attitudes": 500},
        "relevance": 0.8,
        "why_relevant": "命中主题",
    }]
    items = _norm("weibo", raw)
    assert len(items) == 1
    it = items[0]
    assert isinstance(it, schema.SourceItem)
    assert it.source == "weibo"
    assert it.body == "国产大模型又出新版本了"
    assert it.author == "某博主"  # leading @ stripped
    assert it.url.startswith("https://m.weibo.cn/")
    assert it.engagement == {"reposts": 100, "comments": 30, "attitudes": 500}
    assert it.relevance_hint == pytest.approx(0.8)


# --------------------------------------------------------------------------- #
# 知乎体 (reddit prototype): subreddit -> container, comment metadata
# --------------------------------------------------------------------------- #

def test_normalize_zhihu():
    raw = [{
        "id": "ZH1",
        "title": "如何看待国产大模型？",
        "selftext": "正文内容，讲了不少干货。",
        "url": "https://www.zhihu.com/question/1/answer/2",
        "subreddit": "AI 工程实践",
        "date": IN_WINDOW,
        "engagement": {"score": 5000, "num_comments": 200},
        "top_comments": [{"excerpt": "评论一", "score": 99}],
        "comment_insights": ["洞见A"],
        "relevance": 0.9,
        "why_relevant": "高赞回答",
    }]
    items = _norm("zhihu", raw)
    it = items[0]
    assert it.source == "zhihu"
    assert it.title == "如何看待国产大模型？"
    assert it.container == "AI 工程实践"
    assert it.engagement["score"] == 5000
    assert it.metadata["top_comments"][0]["excerpt"] == "评论一"
    assert it.metadata["comment_insights"] == ["洞见A"]


# --------------------------------------------------------------------------- #
# B站体 (youtube prototype): transcript_snippet, channel, remapped comments
# --------------------------------------------------------------------------- #

def test_normalize_bilibili():
    raw = [{
        "video_id": "BV1abc",
        "title": "大模型推理加速",
        "description": "视频简介",
        "transcript_snippet": "字幕片段",
        "url": "https://www.bilibili.com/video/BV1abc",
        "channel_name": "技术阿宅",
        "date": IN_WINDOW,
        "engagement": {"view": 50000, "like": 2000, "coin": 0, "danmaku": 100, "reply": 80},
        "top_comments": [{"text": "讲得好", "likes": 42}],
        "relevance": 0.7,
        "why_relevant": "B站讲解",
    }]
    items = _norm("bilibili", raw)
    it = items[0]
    assert it.source == "bilibili"
    assert it.item_id == "BV1abc"
    assert it.author == "技术阿宅"
    assert it.snippet == "字幕片段"
    assert it.engagement["view"] == 50000
    # B站 comments get remapped to score/excerpt shape (likes->score, text->excerpt).
    remapped = it.metadata["top_comments"][0]
    assert remapped["score"] == 42
    assert remapped["excerpt"] == "讲得好"


# --------------------------------------------------------------------------- #
# 短视频体 (douyin & xiaohongshu share _normalize_shortform_video)
# --------------------------------------------------------------------------- #

def test_normalize_douyin_shortform():
    raw = [{
        "id": "DY1",
        "text": "三分钟看懂大模型",
        "caption_snippet": "完整字幕",
        "url": "https://www.douyin.com/video/7412",
        "author_name": "数码老王",
        "date": IN_WINDOW,
        "engagement": {"views": 200000, "digg_count": 18000, "comment": 900, "share": 1400},
        "hashtags": ["人工智能", "科普"],
        "top_comments": [{"text": "学到了", "digg_count": 88}],
        "relevance": 0.75,
        "why_relevant": "抖音科普",
    }]
    items = _norm("douyin", raw)
    it = items[0]
    assert it.source == "douyin"
    assert it.author == "数码老王"
    assert it.snippet == "完整字幕"
    assert it.metadata["hashtags"] == ["人工智能", "科普"]
    # digg_count maps to score in the remapped comment.
    assert it.metadata["top_comments"][0]["score"] == 88
    assert it.metadata["top_comments"][0]["excerpt"] == "学到了"


def test_normalize_xiaohongshu_shortform():
    raw = [{
        "id": "XHS1",
        "text": "读书笔记技巧",
        "caption_snippet": "正文描述",
        "url": "https://www.xiaohongshu.com/explore/abc",
        "author_name": "效率喵",
        "date": IN_WINDOW,
        "engagement": {"likes": 23400, "comment": 1820, "collected": 9100, "share": 560},
        "hashtags": ["AI工具"],
        "relevance": 0.6,
        "why_relevant": "",
    }]
    items = _norm("xiaohongshu", raw)
    it = items[0]
    assert it.source == "xiaohongshu"
    assert it.engagement["collected"] == 9100
    assert it.metadata["hashtags"] == ["AI工具"]


# --------------------------------------------------------------------------- #
# 论坛体 (v2ex & juejin share _normalize_forum)
# --------------------------------------------------------------------------- #

def test_normalize_v2ex_forum():
    raw = [{
        "id": "1012345",
        "title": "本地跑大模型用什么显卡",
        "text": "求推荐配置。",
        "url": "https://www.v2ex.com/t/1012345",
        "hn_url": "https://www.v2ex.com/t/1012345",
        "author": "techgeek",
        "date": IN_WINDOW,
        "engagement": {"points": 0, "comments": 87},
        "top_comments": [],
        "comment_insights": [],
        "relevance": 0.8,
        "why_relevant": "讨论帖",
    }]
    items = _norm("v2ex", raw)
    it = items[0]
    assert it.source == "v2ex"
    assert it.container == "V2EX"
    assert it.engagement["comments"] == 87
    assert it.metadata["hn_url"] == "https://www.v2ex.com/t/1012345"


def test_normalize_juejin_forum_container():
    raw = [{
        "id": "7400",
        "title": "迷你推理框架",
        "text": "300 行实现。",
        "url": "https://juejin.cn/post/7400",
        "hn_url": "",
        "author": "推理小王子",
        "date": IN_WINDOW,
        "engagement": {"points": 1280, "comments": 96},
        "top_comments": [],
        "comment_insights": [],
        "relevance": 0.85,
        "why_relevant": "掘金文章",
    }]
    items = _norm("juejin", raw)
    it = items[0]
    assert it.source == "juejin"
    assert it.container == "掘金"
    assert it.engagement["points"] == 1280


# --------------------------------------------------------------------------- #
# 情绪体 (xueqiu, polymarket prototype): volume/liquidity in engagement
# --------------------------------------------------------------------------- #

def test_normalize_xueqiu_sentiment():
    raw = [{
        "id": "XQ1",
        "title": "AI 算力赛道",
        "question": "AI 算力还能涨吗",
        "url": "https://xueqiu.com/1/2",
        "date": IN_WINDOW,
        "volume1mo": 948,
        "liquidity": 628,
        "price_movement": "情绪偏多",
        "end_date": None,
        "outcome_prices": [],
        "relevance": 0.7,
        "why_relevant": "雪球讨论",
    }]
    items = _norm("xueqiu", raw)
    it = items[0]
    assert it.source == "xueqiu"
    assert it.container == "雪球"
    assert it.engagement["volume"] == 948
    assert it.engagement["liquidity"] == 628
    assert it.metadata["question"] == "AI 算力还能涨吗"
    assert it.snippet == "情绪偏多"


# --------------------------------------------------------------------------- #
# Date filtering
# --------------------------------------------------------------------------- #

def test_out_of_window_items_dropped():
    raw = [{
        "id": "WB1", "text": "太旧了", "url": "https://m.weibo.cn/status/x",
        "author_handle": "a", "date": "2020-01-01",
        "engagement": {}, "relevance": 0.9, "why_relevant": "",
    }]
    assert _norm("weibo", raw) == []


def test_undated_item_kept_for_non_grounding():
    raw = [{
        "id": "WB1", "text": "无日期", "url": "https://m.weibo.cn/status/x",
        "author_handle": "a", "date": None,
        "engagement": {}, "relevance": 0.9, "why_relevant": "",
    }]
    items = _norm("weibo", raw)
    assert len(items) == 1
    assert items[0].published_at is None
