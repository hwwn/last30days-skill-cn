"""relevance.py — CJK bigram tokenization & token-overlap scoring (PORT_CONTRACT §6).

The fallback path (no jieba) must segment contiguous CJK runs into character
bigrams so Chinese relevance scoring works, while leaving the English logic
untouched. jieba is an optional dependency; these tests assert behavior that
holds for the bigram fallback and are still valid (looser) under jieba.
"""

import pytest

from lib import relevance
from lib.relevance import PreparedQuery, token_overlap_relevance, tokenize


def _has_jieba():
    try:
        import jieba  # noqa: F401
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# CJK segmentation
# --------------------------------------------------------------------------- #

def test_bigram_fallback_segments_cjk_run(monkeypatch):
    # Force the bigram path even if jieba happens to be installed, so the
    # contract's documented fallback ("人工智能" -> {人工, 工智, 智能}) is tested.
    import builtins
    real_import = builtins.__import__

    def _no_jieba(name, *args, **kwargs):
        if name == "jieba":
            raise ImportError("forced off for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_jieba)

    toks = relevance._segment_cjk_run("人工智能")
    assert {"人工", "工智", "智能"}.issubset(toks)


def test_tokenize_splits_chinese_phrase_into_multiple_tokens():
    # A multi-concept phrase must yield >= 2 tokens under BOTH segmenters:
    # jieba cuts "大模型 推理 优化" into words; the bigram fallback yields bigrams.
    # (A single dictionary word like "人工智能" can legitimately stay one jieba
    # token, so this case uses a phrase that splits either way.)
    toks = tokenize("大模型推理优化")
    assert len(toks) >= 2


def test_tokenize_drops_chinese_stopwords():
    toks = tokenize("我的人工智能")
    # 我 / 的 are CJK stopwords and must not survive as standalone tokens.
    assert "我" not in toks
    assert "的" not in toks


def test_single_char_cjk_run_kept_unless_stopword():
    assert tokenize("猫") == {"猫"}
    # A lone stopword char produces nothing.
    assert tokenize("的") == set()


# --------------------------------------------------------------------------- #
# English path is preserved byte-for-byte
# --------------------------------------------------------------------------- #

def test_english_tokenize_unchanged():
    toks = tokenize("the best React tutorial")
    assert "react" in toks
    assert "tutorial" in toks
    # Stopwords and single-char tokens dropped.
    assert "the" not in toks
    # Synonym expansion still works.
    assert tokenize("react") >= {"react", "reactjs"}


def test_mixed_cjk_and_latin():
    toks = tokenize("DeepSeek 大模型推理")
    # Latin token survives the original whitespace/English path.
    assert "deepseek" in toks
    # The CJK run also contributes tokens (jieba words or bigrams) — at least one
    # token must contain a Chinese character from the run.
    assert any(any("一" <= ch <= "鿿" for ch in t) for t in toks)


# --------------------------------------------------------------------------- #
# Scoring behavior on Chinese queries
# --------------------------------------------------------------------------- #

def test_chinese_exact_overlap_scores_high():
    score = token_overlap_relevance("大模型推理优化", "聊聊大模型推理优化的几种方法")
    assert score >= 0.5


def test_chinese_unrelated_scores_low():
    score = token_overlap_relevance("大模型推理", "今天天气真好适合出去散步")
    assert score < 0.3


def test_chinese_relevance_orders_related_above_unrelated():
    q = "国产大模型对比"
    related = token_overlap_relevance(q, "国产大模型横向对比：DeepSeek vs 通义千问")
    unrelated = token_overlap_relevance(q, "如何挑选一台扫地机器人")
    assert related > unrelated


def test_empty_query_returns_neutral():
    assert token_overlap_relevance("", "任意内容") == 0.5


def test_prepared_query_reuse_matches_raw():
    q = "大模型推理优化"
    text = "大模型推理优化实战"
    prepared = PreparedQuery(q)
    assert token_overlap_relevance(prepared, text) == token_overlap_relevance(q, text)


def test_generic_chinese_query_words_alone_capped_when_subject_present():
    # PORT_CONTRACT §6: low-signal meta words ("推荐"/"评测") must not carry
    # relevance ON THEIR OWN once the query also has an informative subject token.
    # Here the text matches only the meta word "推荐", not the subject "扫地机器人",
    # so the score is capped below the relevance filter threshold.
    score = token_overlap_relevance("扫地机器人 推荐", "国产大模型最新发布会推荐看点")
    assert score <= 0.24


def test_subject_match_scores_high_despite_meta_words():
    # When the informative subject token DOES overlap, the score is strong.
    score = token_overlap_relevance("扫地机器人 推荐", "2026 扫地机器人选购推荐榜单")
    assert score >= 0.5
