"""github.py — parse_github_response emits the retained §1 github item shape.

github.py is ported unchanged (PORT_CONTRACT §1/§12); parse is a pure function
over a ``{"items": [...], "context": {...}}`` envelope.
"""

from conftest import load_fixture

from lib import github

KEYS = {"id", "title", "url", "date", "author", "source", "score",
        "container", "snippet", "relevance", "why_relevant",
        "engagement", "metadata"}


def test_parse_github_shape():
    items = github.parse_github_response(load_fixture("github.json"))
    assert items
    for it in items:
        assert KEYS.issubset(it.keys())
        assert it["source"] == "github"
        assert set(it["engagement"]) >= {"reactions", "comments"}
        assert {"labels", "state", "is_pr"}.issubset(it["metadata"])


def test_parse_github_pr_vs_issue_flag():
    items = github.parse_github_response(load_fixture("github.json"))
    by_url = {it["url"]: it for it in items}
    issue = by_url["https://github.com/deepseek-ai/inference/issues/128"]
    pr = by_url["https://github.com/owner/repo/pull/77"]
    assert issue["metadata"]["is_pr"] is False
    assert pr["metadata"]["is_pr"] is True
    assert issue["engagement"]["reactions"] == 137
    assert issue["author"] == "alice-dev"


def test_parse_github_sorted_by_relevance_desc():
    items = github.parse_github_response(load_fixture("github.json"))
    rels = [it["relevance"] for it in items]
    assert rels == sorted(rels, reverse=True)


def test_parse_github_empty_input_returns_empty():
    assert github.parse_github_response({}) == []
    assert github.parse_github_response({"items": "not-a-list"}) == []
    assert github.parse_github_response("garbage") == []
