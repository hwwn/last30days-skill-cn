"""juejin.py — parse_juejin_response emits the §3 论坛体 raw item shape.

The fixture is a raw juejin search response (``data[].result_model``).
"""

from conftest import load_fixture

from lib import juejin

KEYS = {"id", "title", "text", "url", "hn_url", "author", "date",
        "engagement", "top_comments", "comment_insights",
        "relevance", "why_relevant"}


def test_parse_juejin_shape():
    items = juejin.parse_juejin_response(load_fixture("juejin.json"), query="大模型 推理")
    assert items
    for it in items:
        assert KEYS.issubset(it.keys())
        assert set(it["engagement"]) == {"points", "comments"}
        # No comment scraping for juejin; lists stay empty (never fabricated).
        assert it["top_comments"] == []
        assert it["comment_insights"] == []


def test_parse_juejin_url_author_engagement_date():
    items = juejin.parse_juejin_response(load_fixture("juejin.json"), query="大模型 推理")
    first = items[0]
    assert first["url"] == "https://juejin.cn/post/7400123456789012345"
    assert first["hn_url"] == ""  # no separate discussion page
    assert first["author"] == "推理小王子"
    assert first["engagement"]["points"] == 1280
    assert first["engagement"]["comments"] == 96
    assert first["date"] == "2026-06-08"  # ctime 1780891200 (UTC+8)


def test_parse_juejin_empty_input_returns_empty():
    assert juejin.parse_juejin_response(None) == []
    assert juejin.parse_juejin_response({"error": "bad"}) == []
    assert juejin.parse_juejin_response({"foo": "bar"}) == []
    assert juejin.parse_juejin_response("garbage") == []
