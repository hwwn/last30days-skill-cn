"""douyin.py — parse path emits the §3 短视频体 raw item shape.

The raw ScrapeCreators response nests items under ``search_item_list[].aweme_info``;
search_douyin extracts those, runs ``_parse_items`` to produce the §3 shape, then
wraps them as ``{"items": [...]}``. parse_douyin_response validates/backfills that
shape. The test reproduces the extraction and exercises both functions.
"""

from conftest import load_fixture

from lib import douyin

KEYS = {"id", "text", "caption_snippet", "url", "author_name", "date",
        "engagement", "hashtags", "top_comments", "relevance", "why_relevant"}


def _raw_aweme_infos():
    raw = load_fixture("douyin.json")
    return [entry["aweme_info"] for entry in raw["search_item_list"]]


def test_parse_items_produces_shortform_shape():
    parsed = douyin._parse_items(_raw_aweme_infos(), core_topic="大模型")
    assert parsed
    for it in parsed:
        assert KEYS.issubset(it.keys())
        eng = it["engagement"]
        assert {"views", "digg_count", "comment", "share"}.issubset(eng)
        assert isinstance(it["hashtags"], list)


def test_parse_items_maps_author_and_url_and_hashtags():
    parsed = douyin._parse_items(_raw_aweme_infos(), core_topic="大模型")
    first = parsed[0]
    assert first["author_name"] == "数码老王"  # nickname preferred over unique_id
    assert first["url"].startswith("https://www.douyin.com/video/")
    assert "?" not in first["url"]  # query string stripped from share_url
    assert first["hashtags"] == ["人工智能", "科普"]
    assert first["engagement"]["digg_count"] == 185000


def test_parse_douyin_response_passthrough_of_search_output():
    parsed = douyin._parse_items(_raw_aweme_infos(), core_topic="大模型")
    out = douyin.parse_douyin_response({"items": parsed}, query="大模型")
    assert len(out) == len(parsed)
    for it in out:
        assert KEYS.issubset(it.keys())


def test_parse_douyin_response_backfills_relevance_for_bare_list():
    bare = [{"id": "x", "text": "国产大模型实测", "url": "https://www.douyin.com/video/x",
             "author_name": "a", "date": "2026-06-01", "engagement": {},
             "hashtags": ["大模型"], "top_comments": [], "relevance": None,
             "caption_snippet": "", "why_relevant": ""}]
    out = douyin.parse_douyin_response(bare, query="大模型")
    assert out[0]["relevance"] is not None
    assert 0.0 <= out[0]["relevance"] <= 1.0


def test_parse_douyin_empty_input_returns_empty():
    assert douyin.parse_douyin_response(None) == []
    assert douyin.parse_douyin_response({"items": []}) == []
    assert douyin.parse_douyin_response("garbage") == []
