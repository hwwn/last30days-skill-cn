"""planner.py — Chinese intent inference (PORT_CONTRACT §9).

``_infer_intent`` gained Chinese regex branches parallel to the English ones.
These tests pin each Chinese branch and confirm English classification is
preserved, plus the freshness/source-weight knobs that read off the intent.
"""

import pytest

from lib import planner


@pytest.mark.parametrize("topic, expected", [
    # comparison 对比
    ("DeepSeek 和通义千问对比", "comparison"),
    ("小米SU7和特斯拉哪个好", "comparison"),
    ("国产大模型对决", "comparison"),
    # prediction 预测
    ("英伟达股价会不会继续涨", "prediction"),
    ("今年AI泡沫会破吗的预测", "prediction"),
    ("比特币赔率", "prediction"),
    # how_to 教程
    ("怎么本地部署 DeepSeek", "how_to"),
    ("Kubernetes 入门教程", "how_to"),
    ("如何搭建个人博客", "how_to"),
    # factual 事实
    ("RAG 是什么", "factual"),
    ("什么是 MCP 协议", "factual"),
    ("DeepSeek V3 参数量多少", "factual"),
    # opinion 观点
    # NOTE: opinion examples avoid 怎么/如何 substrings — the how_to branch runs
    # first, so "怎么样" would classify as how_to. This ordering is intentional
    # in the lib; opinion is matched via 值不值/体验/香不香/评价/靠不靠谱/值得吗.
    ("Vision Pro 值不值得买", "opinion"),
    ("理想L9 真实体验", "opinion"),
    ("这门课靠不靠谱", "opinion"),
    # breaking_news 突发
    ("OpenAI 最新发布", "breaking_news"),
    ("某新品今日开售", "breaking_news"),
    ("苹果发布会", "breaking_news"),
])
def test_chinese_intent_branches(topic, expected):
    assert planner._infer_intent(topic) == expected


@pytest.mark.parametrize("topic, expected", [
    ("Claude vs GPT", "comparison"),
    ("how to deploy nextjs", "how_to"),
    ("what is rag", "factual"),
    ("latest openai news", "breaking_news"),
])
def test_english_intent_preserved(topic, expected):
    assert planner._infer_intent(topic) == expected


def test_unclassified_chinese_defaults_to_concept():
    # Bare entity with no meta signal -> concept (evergreen-safe default).
    assert planner._infer_intent("向量数据库") == "concept"


def test_freshness_follows_intent():
    assert planner._default_freshness("breaking_news") == "strict_recent"
    assert planner._default_freshness("prediction") == "strict_recent"
    assert planner._default_freshness("concept") == "evergreen_ok"
    assert planner._default_freshness("how_to") == "evergreen_ok"
    assert planner._default_freshness("comparison") == "balanced_recent"


def test_source_weights_use_cn_sources():
    # prediction boosts xueqiu; how_to boosts bilibili — CN source names only.
    sources = ["weibo", "zhihu", "bilibili", "v2ex", "xueqiu"]
    pred = planner._default_source_weights("prediction", sources)
    assert pred["xueqiu"] > pred["zhihu"]
    howto = planner._default_source_weights("how_to", sources)
    assert howto["bilibili"] > howto["weibo"]
    news = planner._default_source_weights("breaking_news", sources)
    assert news["weibo"] > news["v2ex"]
