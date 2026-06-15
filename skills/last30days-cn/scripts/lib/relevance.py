"""Shared token-overlap relevance scoring for search result ranking.

The score is intentionally query-centric:
- exact phrase matches should score very high
- partial matches should pay a meaningful penalty
- matches on generic words alone ("odds", "review") should not pass as relevant

CJK note (port contract §6): Chinese text has no whitespace word boundaries,
so the original ``str.split()`` tokenizer would collapse an entire sentence
into a single token and make relevance scoring useless. ``tokenize`` below
handles CJK by segmenting via ``jieba`` when it is importable, and otherwise
falling back to character bigrams (e.g. "人工智能" -> {人工, 工智, 智能}). All
of the original English tokenizing/scoring logic is preserved untouched; CJK
handling is layered on top so mixed Chinese/English queries still work.
"""

import re
from typing import List, Optional, Set

# Stopwords for relevance computation (common English words that dilute token overlap)
STOPWORDS = frozenset({
    'the', 'a', 'an', 'to', 'for', 'how', 'is', 'in', 'of', 'on',
    'and', 'with', 'from', 'by', 'at', 'this', 'that', 'it', 'my',
    'your', 'i', 'me', 'we', 'you', 'what', 'are', 'do', 'can',
    'its', 'be', 'or', 'not', 'no', 'so', 'if', 'but', 'about',
    'all', 'just', 'get', 'has', 'have', 'was', 'will',
})

# Chinese stopwords for relevance computation (common particles/pronouns that
# dilute token overlap). Kept high-frequency so we don't accidentally drop
# meaningful single-char topic words.
CJK_STOPWORDS = frozenset({
    '的', '了', '和', '是', '在', '我', '你', '他', '她', '它', '们',
    '这', '那', '也', '就', '都', '与', '及', '或', '把', '被', '让',
    '为', '对', '从', '到', '向', '于', '以', '之', '其', '该', '吧',
    '吗', '呢', '啊', '哦', '嗯', '又', '再', '很', '太', '更', '最',
    '会', '能', '要', '有', '没', '不', '上', '下', '里', '中', '个',
    '呀', '着', '过', '给', '等', '哪', '怎', '什', '么',
})

# Synonym groups for relevance scoring (bidirectional expansion)
# Superset of all platform-specific synonym dicts
SYNONYMS = {
    'hip': {'rap', 'hiphop'},
    'hop': {'rap', 'hiphop'},
    'rap': {'hip', 'hop', 'hiphop'},
    'hiphop': {'rap', 'hip', 'hop'},
    'js': {'javascript'},
    'javascript': {'js'},
    'ts': {'typescript'},
    'typescript': {'ts'},
    'ai': {'artificial', 'intelligence'},
    'ml': {'machine', 'learning'},
    'react': {'reactjs'},
    'reactjs': {'react'},
    'svelte': {'sveltejs'},
    'sveltejs': {'svelte'},
    'vue': {'vuejs'},
    'vuejs': {'vue'},
}

# Generic query words that should not carry relevance on their own.
# They still help when paired with stronger entity/topic matches.
LOW_SIGNAL_QUERY_TOKENS = frozenset({
    'advice', 'animation', 'animations', 'best', 'chance', 'chances',
    'code', 'compare', 'comparison', 'differences', 'explain', 'guide',
    'guides', 'how', 'latest', 'news', 'odds', 'opinion', 'opinions',
    'prediction', 'predictions', 'probability', 'probabilities', 'prompt',
    'prompting', 'prompts', 'rate', 'review', 'reviews', 'thoughts',
    'tip', 'tips', 'tutorial', 'tutorials', 'update', 'updates', 'use',
    'using', 'versus', 'vs', 'worth',
    # Chinese generic / meta query words (parallel to the English set above):
    # they describe the *kind* of query rather than the subject, so they must
    # not carry relevance on their own.
    '最新', '最近', '推荐', '对比', '评测', '测评', '教程', '攻略',
    '排行', '排行榜', '盘点', '怎么样', '如何', '怎么', '什么',
    '哪个', '哪些', '值不值', '体验', '入门', '预测', '赔率', '资讯',
    '新闻', '更新', '指南', '建议', '看法', '观点', '使用',
})

# CJK character range (CJK Unified Ideographs + common Ext-A). Used to decide
# whether a run of characters needs bigram/jieba segmentation vs. whitespace
# splitting.
_CJK_RE = re.compile(r'[㐀-䶿一-鿿]+')


def _segment_cjk_run(run: str) -> Set[str]:
    """Segment a contiguous run of CJK characters into tokens.

    Tries ``jieba`` first (optional dependency); on ImportError or any failure
    falls back to character bigrams so segmentation degrades gracefully without
    third-party packages. Single-character runs are returned as-is so very short
    topic words are not lost.
    """
    if not run:
        return set()

    # Optional jieba path. Imported lazily so the module imports cleanly with
    # the standard library only; any failure falls through to the bigram path.
    try:
        import jieba  # type: ignore
        cut = [w.strip() for w in jieba.lcut(run) if w.strip()]
        if cut:
            return {w for w in cut if w not in CJK_STOPWORDS}
    except Exception:
        pass

    # Character-bigram fallback: "人工智能" -> {人工, 工智, 智能}.
    if len(run) == 1:
        return {run} if run not in CJK_STOPWORDS else set()
    bigrams = {
        run[i:i + 2]
        for i in range(len(run) - 1)
        if not all(c in CJK_STOPWORDS for c in run[i:i + 2])
    }
    # Also surface single non-stopword chars so a lone meaningful char (e.g. a
    # surname) is still matchable even when only bigrams are produced.
    chars = {c for c in run if c not in CJK_STOPWORDS}
    return (bigrams | chars)


def tokenize(text: str) -> Set[str]:
    """Lowercase, strip punctuation, remove stopwords, drop single-char tokens.

    Expands tokens with synonyms for better cross-domain matching.

    CJK handling (port contract §6): contiguous runs of CJK characters are
    segmented via jieba (if importable) or character bigrams, with Chinese
    stopwords removed. Non-CJK text keeps the original whitespace tokenizer and
    English-only rules (drop stopwords, drop single-char tokens, synonym
    expansion) exactly as before.
    """
    lowered = text.lower()

    # Pull out CJK runs first and segment them separately; replace each run with
    # whitespace so the remaining English/numeric text tokenizes as it always
    # did. This keeps the English logic path byte-for-byte equivalent for
    # all-ASCII input.
    cjk_tokens: Set[str] = set()
    for match in _CJK_RE.finditer(lowered):
        cjk_tokens |= _segment_cjk_run(match.group())
    non_cjk = _CJK_RE.sub(' ', lowered)

    words = re.sub(r'[^\w\s]', ' ', non_cjk).split()
    tokens = {w for w in words if w not in STOPWORDS and len(w) > 1}

    # Merge CJK tokens. CJK tokens are kept even at length 1 (single
    # ideographs can be meaningful); stopwords were already filtered in
    # _segment_cjk_run.
    tokens |= cjk_tokens

    expanded = set(tokens)
    for t in tokens:
        if t in SYNONYMS:
            expanded.update(SYNONYMS[t])
    return expanded


def _normalize_phrase(text: str) -> str:
    """Normalize text for phrase containment checks."""
    return ' '.join(re.sub(r'[^\w\s]', ' ', text.lower()).split())


class PreparedQuery:
    """Precomputed query shape reused across items in a stream.

    Built once per ranking_query; reused by token_overlap_relevance so the
    per-item normalize/score loops don't re-tokenize the same query N times.
    """

    __slots__ = ("raw", "q_tokens", "informative_q_tokens", "normalized_phrase")

    def __init__(self, query: str) -> None:
        self.raw = query
        self.q_tokens = tokenize(query)
        informative = {t for t in self.q_tokens if t not in LOW_SIGNAL_QUERY_TOKENS}
        self.informative_q_tokens = informative or self.q_tokens
        self.normalized_phrase = _normalize_phrase(query)


def _as_prepared(query: "str | PreparedQuery") -> PreparedQuery:
    return query if isinstance(query, PreparedQuery) else PreparedQuery(query)


def token_overlap_relevance(
    query: "str | PreparedQuery",
    text: str,
    hashtags: Optional[List[str]] = None,
) -> float:
    """Compute a query-centric relevance score between 0.0 and 1.0.

    The score combines:
    - query coverage
    - informative-token coverage
    - a small precision term to penalize extra noise
    - an exact phrase bonus

    Generic tokens alone are capped below typical relevance filter thresholds.

    Args:
        query: Search query
        text: Content text to match against
        hashtags: Optional list of hashtags (TikTok/Instagram). Concatenated
            hashtags are split to match query tokens (e.g. "claudecode" matches "claude").

    Returns:
        Float between 0.0 and 1.0 (0.5 for empty queries)
    """
    prepared = _as_prepared(query)
    q_tokens = prepared.q_tokens

    # Combine text and hashtags for matching
    combined = text
    if hashtags:
        combined = f"{text} {' '.join(hashtags)}"
    t_tokens = tokenize(combined)

    # Split concatenated hashtags (e.g., "claudecode" -> matches "claude", "code")
    if hashtags:
        for tag in hashtags:
            tag_lower = tag.lower()
            for qt in q_tokens:
                if qt in tag_lower and qt != tag_lower:
                    t_tokens.add(qt)

    if not q_tokens:
        return 0.5  # Neutral fallback for empty/stopword-only queries

    overlap_tokens = q_tokens & t_tokens
    overlap = len(overlap_tokens)
    if overlap == 0:
        return 0.0

    informative_q_tokens = prepared.informative_q_tokens

    coverage = overlap / len(q_tokens)
    informative_overlap = len(informative_q_tokens & t_tokens) / len(informative_q_tokens)
    precision_denominator = min(len(t_tokens), len(q_tokens) + 4) or 1
    precision = overlap / precision_denominator

    phrase_bonus = 0.0
    normalized_query = prepared.normalized_phrase
    normalized_text = _normalize_phrase(combined)
    if normalized_query and normalized_query in normalized_text:
        phrase_bonus = 0.12 if len(normalized_query.split()) > 1 else 0.16

    base = (
        0.55 * (coverage ** 1.35) +
        0.25 * informative_overlap +
        0.20 * precision
    )

    # If we only matched generic query words, keep the score below the
    # normal relevance filter threshold so these do not survive by default.
    if informative_q_tokens and not (informative_q_tokens & t_tokens):
        return round(min(0.24, base), 2)

    return round(min(1.0, base + phrase_bonus), 2)
