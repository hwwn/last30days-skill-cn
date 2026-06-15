"""v2ex.py — parse_v2ex_response emits the §3 论坛体 raw item shape.

The fixture is a raw sov2ex response (``hits[]._source``).
"""

from conftest import load_fixture

from lib import v2ex

KEYS = {"id", "title", "text", "url", "hn_url", "author", "date",
        "engagement", "top_comments", "comment_insights",
        "relevance", "why_relevant"}


def test_parse_v2ex_shape():
    items = v2ex.parse_v2ex_response(load_fixture("v2ex.json"), query="大模型 显卡")
    assert items
    for it in items:
        assert KEYS.issubset(it.keys())
        assert set(it["engagement"]) == {"points", "comments"}
        # sov2ex has no upvotes; points must always be 0 (never fabricated).
        assert it["engagement"]["points"] == 0
        # No per-reply text is available; comment lists stay empty.
        assert it["top_comments"] == []
        assert it["comment_insights"] == []


def test_parse_v2ex_url_replies_and_date():
    items = v2ex.parse_v2ex_response(load_fixture("v2ex.json"), query="大模型")
    first = items[0]
    assert "/t/1012345" in first["url"]
    assert first["hn_url"] == first["url"]
    assert first["engagement"]["comments"] == 87
    assert first["date"] == "2026-06-08"  # 1780891200 (CST) -> date


def test_parse_v2ex_empty_input_returns_empty():
    assert v2ex.parse_v2ex_response(None) == []
    assert v2ex.parse_v2ex_response({"error": "rate limited"}) == []
    assert v2ex.parse_v2ex_response({"hits": "not-a-list"}) == []
    assert v2ex.parse_v2ex_response("garbage") == []
