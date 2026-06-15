"""signals.py — CN-source scoring + anti-manipulation layer.

Covers:
- CN source_quality / engagement routing (zhihu, bilibili, weighted, generic)
- manipulation_signals: promo spam, keyword stuffing, hashtag/link stuffing,
  astroturf-shaped engagement (水军), and that clean items are untouched
- annotate_stream applies the penalty so a manipulated item ranks below an
  otherwise-identical clean item (anti-SEO/GEO/水军 behavior).
"""

from lib import signals, schema
from lib.relevance import PreparedQuery


def _item(source, *, title="", body="", snippet="", engagement=None, metadata=None, url="https://x/1", item_id="i1"):
    return schema.SourceItem(
        item_id=item_id,
        source=source,
        title=title,
        body=body,
        url=url,
        snippet=snippet,
        engagement=engagement or {},
        metadata=metadata or {},
    )


# --------------------------- source quality / routing --------------------- #

def test_source_quality_cn_values():
    assert signals.source_quality("github") == 0.85
    assert signals.source_quality("weibo") == 0.62
    # unknown source falls back to default
    assert signals.source_quality("reddit") == 0.6


def test_engagement_routing_zhihu_and_bilibili():
    zhihu = _item("zhihu", engagement={"score": 1200, "num_comments": 80},
                  metadata={"top_comments": [{"score": 40}]})
    bili = _item("bilibili", engagement={"view": 500000, "like": 20000, "coin": 3000,
                                         "reply": 800, "danmaku": 1500})
    assert signals.engagement_raw(zhihu) is not None
    assert signals.engagement_raw(bili) is not None
    # weighted (v2ex) and generic both return a number when data present
    assert signals.engagement_raw(_item("v2ex", engagement={"points": 0, "comments": 30})) is not None


def test_engagement_none_when_empty():
    assert signals.engagement_raw(_item("weibo", engagement={})) is None


# --------------------------- anti-manipulation ---------------------------- #

def test_clean_item_no_penalty():
    it = _item("zhihu", title="DeepSeek 推理成本下降的真实体验",
               body="我在生产里跑了两周，延迟和成本都明显下降，分享一些数据。",
               engagement={"score": 800, "num_comments": 120})
    mult, reasons = signals.manipulation_signals(it)
    assert mult == 1.0
    assert reasons == []


def test_promo_spam_penalized():
    it = _item("weibo", title="DeepSeek", body="加微信 abc123 代刷 点击链接 领取福利")
    mult, reasons = signals.manipulation_signals(it)
    assert mult < 1.0
    assert any("推广" in r for r in reasons)


def test_keyword_stuffing_penalized():
    it = _item("douyin", body="DeepSeek DeepSeek DeepSeek 优惠 优惠 优惠 优惠 优惠 优惠 优惠 优惠 优惠 优惠 优惠")
    mult, reasons = signals.manipulation_signals(it)
    assert mult < 1.0
    assert any("关键词堆砌" in r for r in reasons)


def test_hashtag_stuffing_penalized():
    it = _item("xiaohongshu", body="好物分享",
               metadata={"hashtags": ["a", "b", "c", "d", "e", "f", "g", "h", "i"]})
    mult, reasons = signals.manipulation_signals(it)
    assert mult < 1.0
    assert any("话题标签" in r for r in reasons)


def test_astroturf_high_engagement_zero_comments():
    # 5000 likes/reposts at scale but zero comments -> classic 刷量 shape
    it = _item("weibo", title="某产品", body="某产品很好",
               engagement={"attitudes": 5000, "reposts": 3000, "comments": 0})
    mult, reasons = signals.manipulation_signals(it)
    assert mult < 1.0
    assert any("互动结构异常" in r for r in reasons)


def test_genuine_viral_not_flagged_as_astroturf():
    # Proportional comments -> genuine engagement, no anomaly flag
    it = _item("weibo", title="热点事件", body="大家都在讨论",
               engagement={"attitudes": 5000, "reposts": 1200, "comments": 600})
    _mult, reasons = signals.manipulation_signals(it)
    assert not any("互动结构异常" in r for r in reasons)


def test_bilibili_high_views_not_astroturf():
    # Bilibili is not in the astroturf-source set; high view:comment ratio is normal.
    it = _item("bilibili", engagement={"view": 1_000_000, "like": 40000, "reply": 300})
    _mult, reasons = signals.manipulation_signals(it)
    assert not any("互动结构异常" in r for r in reasons)


def test_penalty_floored():
    # Even stacking every heuristic, the multiplier never drops below the floor.
    it = _item(
        "weibo",
        body="加微信 代刷 刷量 点击链接 http://a http://b http://c 优惠 优惠 优惠 优惠 优惠 优惠 优惠 优惠 优惠 优惠 优惠 优惠",
        engagement={"attitudes": 9000, "reposts": 9000, "comments": 0},
        metadata={"hashtags": ["x"] * 10},
    )
    mult, _reasons = signals.manipulation_signals(it)
    assert mult >= signals._MANIPULATION_FLOOR
    assert mult < 1.0


def test_annotate_stream_demotes_manipulated_item():
    q = PreparedQuery("DeepSeek 体验")
    clean = _item("weibo", item_id="clean", title="DeepSeek 体验", body="真实使用体验分享",
                  engagement={"attitudes": 2000, "reposts": 500, "comments": 300})
    farmed = _item("weibo", item_id="farmed", title="DeepSeek 体验", body="DeepSeek 体验 加微信 代刷 领取福利",
                   engagement={"attitudes": 9000, "reposts": 9000, "comments": 0})
    ranked = signals.annotate_stream([farmed, clean], q, "balanced_recent")
    # Despite far higher raw engagement, the farmed item is demoted below clean.
    assert ranked[0].item_id == "clean"
    farmed_after = next(i for i in ranked if i.item_id == "farmed")
    assert farmed_after.metadata.get("manipulation_flags")
