"""xueqiu.py — parse_xueqiu_response emits the §3 情绪体 raw item shape.

The fixture is a raw status-search response (``statuses[]`` + ``symbols[]``).
"""

from conftest import load_fixture

from lib import xueqiu

KEYS = {"id", "title", "question", "url", "date",
        "volume1mo", "volume24hr", "liquidity", "price_movement",
        "end_date", "outcome_prices", "relevance", "why_relevant"}


def test_parse_xueqiu_shape():
    items = xueqiu.parse_xueqiu_response(load_fixture("xueqiu.json"), query="AI 算力")
    assert items
    for it in items:
        assert KEYS.issubset(it.keys())
        assert isinstance(it["outcome_prices"], list)
        assert 0.0 <= it["relevance"] <= 1.0


def test_parse_xueqiu_volume_liquidity_and_url():
    items = xueqiu.parse_xueqiu_response(load_fixture("xueqiu.json"), query="AI 算力")
    # The high-engagement, on-topic status should be present.
    by_url = {it["url"]: it for it in items}
    target = by_url["https://xueqiu.com/1234567/298765432"]
    # volume = reply + retweet + fav = 320 + 88 + 540 = 948
    assert target["volume24hr"] == 948
    assert target["volume1mo"] == 948
    # liquidity = fav + retweet = 540 + 88 = 628
    assert target["liquidity"] == 628
    assert target["price_movement"]  # sentiment description present


def test_parse_xueqiu_filters_off_topic_below_floor():
    # An entirely unrelated query should fall below the relevance floor -> [].
    items = xueqiu.parse_xueqiu_response(load_fixture("xueqiu.json"),
                                         query="扫地机器人推荐")
    assert items == []


def test_parse_xueqiu_empty_input_returns_empty():
    assert xueqiu.parse_xueqiu_response(None) == []
    assert xueqiu.parse_xueqiu_response({"error": "no token"}) == []
    assert xueqiu.parse_xueqiu_response({"foo": "bar"}) == []
    assert xueqiu.parse_xueqiu_response("garbage") == []
