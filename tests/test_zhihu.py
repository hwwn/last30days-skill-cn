"""zhihu.py — parse_zhihu_response emits the §3 知乎体 raw item shape."""

from conftest import load_fixture

from lib import zhihu

KEYS = {"id", "title", "selftext", "url", "subreddit", "date", "engagement",
        "top_comments", "comment_insights", "relevance", "why_relevant"}


def test_parse_zhihu_shape():
    items = zhihu.parse_zhihu_response(load_fixture("zhihu.json"), query="大模型 微调")
    assert items
    for it in items:
        assert KEYS.issubset(it.keys())
        eng = it["engagement"]
        assert set(eng) == {"score", "num_comments"}
        assert isinstance(it["top_comments"], list)
        assert isinstance(it["comment_insights"], list)


def test_parse_zhihu_skips_non_content_cards():
    # The fixture has a "people" card that must not become an item.
    items = zhihu.parse_zhihu_response(load_fixture("zhihu.json"), query="大模型")
    assert len(items) == 2
    assert all("people" not in (it["title"] or "") for it in items)


def test_parse_zhihu_answer_url_and_container():
    items = zhihu.parse_zhihu_response(load_fixture("zhihu.json"), query="大模型 微调")
    answer = items[0]
    # Answer borrows the parent question's title and builds a question/answer URL.
    assert answer["title"] == "如何看待国产大模型的微调实践？"
    assert "/question/601234567/answer/3399887766" in answer["url"]
    # Article column title -> subreddit slot.
    article = items[1]
    assert article["url"].startswith("https://zhuanlan.zhihu.com/p/")
    assert article["subreddit"] == "AI 工程实践"


def test_parse_zhihu_empty_input_returns_empty():
    assert zhihu.parse_zhihu_response(None) == []
    assert zhihu.parse_zhihu_response({}) == []
    assert zhihu.parse_zhihu_response("garbage") == []
