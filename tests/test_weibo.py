"""weibo.py — parse_weibo_response emits the §3 微博体 raw item shape."""

from conftest import load_fixture

from lib import weibo

KEYS = {"id", "text", "url", "author_handle", "date", "engagement",
        "relevance", "why_relevant"}


def test_parse_weibo_shape():
    items = weibo.parse_weibo_response(load_fixture("weibo.json"), query="大模型")
    assert items, "expected at least one weibo item"
    for it in items:
        assert KEYS.issubset(it.keys())
        assert it["url"].startswith("https://m.weibo.cn/")
        assert it["text"]  # body required (cards with no text are skipped)
        eng = it["engagement"]
        assert set(eng) == {"reposts", "comments", "attitudes"}
        assert 0.0 <= it["relevance"] <= 1.0


def test_parse_weibo_skips_empty_cards():
    # The fixture's third card has no text/permalink and must be dropped.
    items = weibo.parse_weibo_response(load_fixture("weibo.json"), query="大模型")
    assert all(it["text"] for it in items)
    assert len(items) == 2


def test_parse_weibo_decodes_count_and_strips_html():
    items = weibo.parse_weibo_response(load_fixture("weibo.json"), query="大模型")
    first = items[0]
    # "1.2万" -> 12000
    assert first["engagement"]["attitudes"] == 12000
    # HTML tags / @mentions flattened.
    assert "<" not in first["text"] and ">" not in first["text"]


def test_parse_weibo_empty_input_returns_empty():
    assert weibo.parse_weibo_response(None) == []
    assert weibo.parse_weibo_response({"error": "blocked"}) == []
    assert weibo.parse_weibo_response("garbage") == []
