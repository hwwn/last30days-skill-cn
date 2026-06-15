"""bilibili.py — parse_bilibili_response emits the §3 B站体 raw item shape.

The fixture is a raw search/type API response (``data.result``). search_bilibili
copies those result items verbatim into ``{"items": [...]}`` before parse sees
them, so the test reproduces that hand-off.
"""

from conftest import load_fixture

from lib import bilibili

KEYS = {"id", "video_id", "title", "description", "transcript_snippet", "url",
        "channel_name", "date", "engagement", "top_comments",
        "relevance", "why_relevant"}
ENG_KEYS = {"view", "like", "coin", "danmaku", "reply"}


def _search_envelope(query="大模型"):
    raw = load_fixture("bilibili.json")
    results = raw["data"]["result"]
    return {"items": results, "_core_query": query, "_from_date": "2026-05-15"}


def test_parse_bilibili_shape():
    items = bilibili.parse_bilibili_response(_search_envelope(), query="大模型")
    assert items
    for it in items:
        assert KEYS.issubset(it.keys())
        assert set(it["engagement"]) == ENG_KEYS
        assert isinstance(it["top_comments"], list)
        assert 0.0 <= it["relevance"] <= 1.0


def test_parse_bilibili_strips_keyword_highlight_and_builds_url():
    items = bilibili.parse_bilibili_response(_search_envelope(), query="大模型")
    top = items[0]  # sorted by view desc
    assert "<em" not in top["title"]
    assert top["url"].startswith("https://www.bilibili.com/video/") or top["url"].startswith("https:")
    assert top["video_id"].startswith("BV")


def test_parse_bilibili_handles_dirty_counts():
    # The 2nd fixture video has play as a string and review as "--".
    items = bilibili.parse_bilibili_response(_search_envelope(), query="大模型")
    by_id = {it["video_id"]: it for it in items}
    dirty = by_id["BV1XyZab9cDe"]
    assert dirty["engagement"]["view"] == 1200000  # "1200000" -> int
    assert dirty["engagement"]["reply"] == 0  # "--" -> 0


def test_parse_bilibili_sorted_by_views_desc():
    items = bilibili.parse_bilibili_response(_search_envelope(), query="大模型")
    views = [it["engagement"]["view"] for it in items]
    assert views == sorted(views, reverse=True)


def test_parse_bilibili_empty_input_returns_empty():
    assert bilibili.parse_bilibili_response({"items": []}) == []
    assert bilibili.parse_bilibili_response(None) == []
    assert bilibili.parse_bilibili_response("garbage") == []
