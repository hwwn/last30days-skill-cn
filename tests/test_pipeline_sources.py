"""pipeline.py — available_sources baseline & alias map (PORT_CONTRACT §8).

Baseline always-available: v2ex, juejin, github, bilibili, xueqiu.
weibo/zhihu gate on cookie/scraper, douyin on scraper, xiaohongshu on request +
health probe, grounding on a web-search key. EXCLUDE_SOURCES filtering retained.
"""

from lib import pipeline

BASELINE = {"v2ex", "juejin", "github", "bilibili", "xueqiu"}


def test_baseline_sources_always_available():
    available = pipeline.available_sources({})
    assert BASELINE.issubset(set(available))


def test_clean_config_yields_only_baseline():
    available = set(pipeline.available_sources({}))
    # No creds -> no login-gated, scraper, or grounding sources.
    assert available == BASELINE


def test_weibo_added_with_cookie():
    available = pipeline.available_sources({"WEIBO_COOKIE": "SUB=x"})
    assert "weibo" in available


def test_zhihu_added_with_cookie():
    available = pipeline.available_sources({"ZHIHU_COOKIE": "z_c0=x"})
    assert "zhihu" in available


def test_douyin_added_with_scrapecreators():
    available = pipeline.available_sources({"SCRAPECREATORS_API_KEY": "sc"})
    # Scraper key unlocks douyin AND (per §4) weibo/zhihu availability.
    assert "douyin" in available


def test_xiaohongshu_only_when_requested(monkeypatch):
    # Probe is network-bound; force it true so the gating logic is what's tested.
    monkeypatch.setattr(pipeline.env, "is_xiaohongshu_available", lambda config: True)
    # Not requested -> not added even though "available".
    assert "xiaohongshu" not in pipeline.available_sources({}, requested_sources=None)
    assert "xiaohongshu" not in pipeline.available_sources({}, requested_sources=["weibo"])
    # Requested -> added.
    assert "xiaohongshu" in pipeline.available_sources({}, requested_sources=["xiaohongshu"])


def test_grounding_added_with_any_web_key():
    for key in ("BRAVE_API_KEY", "EXA_API_KEY", "SERPER_API_KEY", "PARALLEL_API_KEY"):
        assert "grounding" in pipeline.available_sources({key: "k"})


def test_exclude_sources_filtering():
    available = pipeline.available_sources({"EXCLUDE_SOURCES": "github, xueqiu"})
    assert "github" not in available
    assert "xueqiu" not in available
    assert "v2ex" in available


# --------------------------------------------------------------------------- #
# Alias map & canonical source set (PORT_CONTRACT §1)
# --------------------------------------------------------------------------- #

def test_search_alias_resolves_cn_shortcuts():
    assert pipeline.SEARCH_ALIAS["wb"] == "weibo"
    assert pipeline.SEARCH_ALIAS["zh"] == "zhihu"
    assert pipeline.SEARCH_ALIAS["bili"] == "bilibili"
    assert pipeline.SEARCH_ALIAS["b站"] == "bilibili"
    assert pipeline.SEARCH_ALIAS["dy"] == "douyin"
    assert pipeline.SEARCH_ALIAS["xhs"] == "xiaohongshu"
    assert pipeline.SEARCH_ALIAS["小红书"] == "xiaohongshu"
    assert pipeline.SEARCH_ALIAS["gh"] == "github"
    assert pipeline.SEARCH_ALIAS["xq"] == "xueqiu"
    assert pipeline.SEARCH_ALIAS["web"] == "grounding"


def test_normalize_requested_sources_applies_aliases_and_dedupes():
    out = pipeline.normalize_requested_sources(["wb", "weibo", "b站", "XQ"])
    assert out == ["weibo", "bilibili", "xueqiu"]
    assert pipeline.normalize_requested_sources(None) is None
    assert pipeline.normalize_requested_sources([]) is None


def test_mock_available_sources_are_the_ten_canonical_names():
    assert set(pipeline.MOCK_AVAILABLE_SOURCES) == {
        "weibo", "zhihu", "bilibili", "douyin", "xiaohongshu",
        "v2ex", "juejin", "github", "xueqiu", "grounding",
    }
