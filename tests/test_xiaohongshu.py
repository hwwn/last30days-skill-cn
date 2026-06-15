"""xiaohongshu.py — parse_xiaohongshu_response emits the §3 短视频体 shape.

The fixture is a raw local-service response (``data.feeds[]``); parse handles
that shape via ``_normalize_local_feed``.
"""

from conftest import load_fixture

from lib import xiaohongshu

KEYS = {"id", "text", "caption_snippet", "url", "author_name", "date",
        "engagement", "hashtags", "top_comments", "relevance", "why_relevant"}


def test_parse_xhs_shape_from_local_feeds():
    items = xiaohongshu.parse_xiaohongshu_response(
        load_fixture("xiaohongshu.json"), query="大模型 笔记"
    )
    assert items
    for it in items:
        assert KEYS.issubset(it.keys())
        eng = it["engagement"]
        assert {"likes", "comment", "collected", "share"}.issubset(eng)
        assert isinstance(it["relevance"], float)
        assert 0.0 <= it["relevance"] <= 1.0


def test_parse_xhs_maps_author_engagement_and_url():
    items = xiaohongshu.parse_xiaohongshu_response(
        load_fixture("xiaohongshu.json"), query="大模型 笔记"
    )
    first = items[0]
    assert first["author_name"] == "效率喵"
    assert first["url"].startswith("https://www.xiaohongshu.com/explore/")
    # "23400" string -> 23400 int.
    assert first["engagement"]["likes"] == 23400
    assert first["engagement"]["collected"] == 9100


def test_parse_xhs_accepts_prebuilt_items_list():
    bare = [{
        "id": "XHS9", "text": "标题", "caption_snippet": "描述",
        "url": "https://www.xiaohongshu.com/explore/abc",
        "author_name": "作者", "date": "2026-06-01",
        "engagement": {"likes": 10, "comment": 1, "collected": 2, "share": 0},
        "hashtags": [], "top_comments": [], "relevance": 0.6, "why_relevant": "",
    }]
    out = xiaohongshu.parse_xiaohongshu_response(bare, query="标题")
    assert len(out) == 1
    assert out[0]["id"] == "XHS9"


def test_parse_xhs_drops_items_without_url_or_body():
    bare = [{"id": "x", "text": "", "caption_snippet": "", "url": ""}]
    assert xiaohongshu.parse_xiaohongshu_response(bare, query="x") == []


def test_parse_xhs_empty_input_returns_empty():
    assert xiaohongshu.parse_xiaohongshu_response(None) == []
    assert xiaohongshu.parse_xiaohongshu_response({"error": "down"}) == []
    assert xiaohongshu.parse_xiaohongshu_response("garbage") == []
