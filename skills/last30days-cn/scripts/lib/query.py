"""Shared query preprocessing utilities: noise-word stripping, core subject
extraction, and compound term detection. Used by all search modules.

CJK note (port contract §6): Chinese queries have no whitespace, so the
English ``str.split()`` path would treat an entire sentence as a single
"word" and never strip noise. ``extract_core_subject`` below detects CJK runs
and segments them (jieba when importable, else character bigrams), strips
Chinese noise/meta words, then re-joins the surviving segments with spaces so
the result is still a usable search query and downstream ``.split()`` callers
keep working. ``max_words`` counts segmentation units for CJK. All English
prefix/suffix/noise-word logic is preserved unchanged."""

import re
from typing import FrozenSet, List, Optional, Set

# Common multi-word prefixes stripped from all queries (identical across modules)
PREFIXES = [
    'what are the best', 'what is the best', 'what are the latest',
    'what are people saying about', 'what do people think about',
    'how do i use', 'how to use', 'how to',
    'what are', 'what is', 'tips for', 'best practices for',
]

# Multi-word suffixes (used by bird_x)
SUFFIXES = [
    'best practices', 'use cases', 'prompt techniques',
    'prompting techniques', 'prompting tips',
]

# Base noise words shared across most modules
NOISE_WORDS = frozenset({
    # Articles/prepositions/conjunctions
    'a', 'an', 'the', 'is', 'are', 'was', 'were', 'and', 'or',
    'of', 'in', 'on', 'for', 'with', 'about', 'to',
    # Question words
    'how', 'what', 'which', 'who', 'why', 'when', 'where',
    'does', 'should', 'could', 'would',
    # Research/meta descriptors
    'best', 'top', 'good', 'great', 'awesome', 'killer',
    'latest', 'new', 'news', 'update', 'updates',
    'trendiest', 'trending', 'hottest', 'hot', 'popular', 'viral',
    'practices', 'features', 'guide', 'tutorial',
    'recommendations', 'advice', 'review', 'reviews',
    'usecases', 'examples', 'comparison', 'versus', 'vs',
    'plugin', 'plugins', 'skill', 'skills', 'tool', 'tools',
    # Prompting meta words
    'prompt', 'prompts', 'prompting', 'techniques', 'tips',
    'tricks', 'methods', 'strategies', 'approaches',
    # Action words
    'using', 'uses', 'use',
    # Misc filler
    'people', 'saying', 'think', 'said', 'lately',
})

# Chinese noise / meta words. Parallel to NOISE_WORDS: these describe the kind
# of query (recency, how-to, ranking, comparison, recommendation) rather than
# the subject, and are stripped from CJK queries before re-joining.
CJK_NOISE_WORDS = frozenset({
    # Recency / freshness
    '最近', '最新', '近期', '近日', '如今', '现在', '目前', '当前',
    # Question / how-to
    '怎么', '怎样', '如何', '咋', '什么', '是什么', '什么是',
    '有哪些', '哪些', '哪个', '哪家', '为什么', '多少',
    # Recommendation / evaluation
    '推荐', '介绍', '对比', '比较', '评测', '测评', '点评', '盘点',
    '排行', '排行榜', '排名', '榜单', '教程', '攻略', '指南',
    '入门', '建议', '心得', '体验', '看法', '观点', '感受',
    # Quality descriptors
    '最好', '最佳', '最强', '最火', '最热', '热门', '火爆', '爆款',
    '好用', '值不值', '值得', '怎么样', '咋样',
    # Generic content nouns
    '资讯', '新闻', '消息', '动态', '更新', '版本',
    # Prediction / market meta
    '预测', '赔率', '会不会', '能不能',
    # Action / filler
    '使用', '用法', '方法', '技巧', '大家', '网友', '说说', '聊聊',
})

# CJK character range (matches relevance.py). Used to detect runs of CJK text
# that need segmentation instead of whitespace splitting.
_CJK_RE = re.compile(r'[㐀-䶿一-鿿]+')


def _has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def _segment_cjk_run_ordered(run: str) -> List[str]:
    """Segment a contiguous CJK run into an *ordered* list of tokens.

    Mirrors relevance._segment_cjk_run but preserves order (so we can re-join
    into a search query). Tries jieba first; falls back to character bigrams.
    """
    if not run:
        return []
    try:
        import jieba  # type: ignore
        cut = [w.strip() for w in jieba.lcut(run) if w.strip()]
        if cut:
            return cut
    except Exception:
        pass
    if len(run) == 1:
        return [run]
    return [run[i:i + 2] for i in range(len(run) - 1)]


def _segment_mixed_ordered(text: str) -> List[str]:
    """Tokenize mixed CJK/Latin text into an ordered list of units.

    CJK runs are segmented (jieba/bigram); non-CJK spans are whitespace-split
    after punctuation is flattened to spaces. Order across the original string
    is preserved so the result can be re-joined into a query.
    """
    units: List[str] = []
    pos = 0
    for match in _CJK_RE.finditer(text):
        # Non-CJK span before this CJK run.
        pre = text[pos:match.start()]
        if pre:
            units.extend(re.sub(r'[^\w\s]', ' ', pre).split())
        units.extend(_segment_cjk_run_ordered(match.group()))
        pos = match.end()
    # Trailing non-CJK span.
    tail = text[pos:]
    if tail:
        units.extend(re.sub(r'[^\w\s]', ' ', tail).split())
    return units


def extract_core_subject(
    topic: str,
    *,
    noise: Optional[FrozenSet[str]] = None,
    max_words: Optional[int] = None,
    strip_suffixes: bool = False,
) -> str:
    """Extract core subject from a verbose search query.

    Strips common question/meta prefixes and noise words to produce a
    compact search-friendly query. Platforms customize via parameters.

    For CJK queries, prefixes/suffixes (which are English multi-word phrases)
    do not apply; instead the text is segmented, Chinese noise/meta words are
    stripped, and the surviving segments are re-joined with spaces. ``max_words``
    then caps the number of segmentation units.

    Args:
        topic: Raw user query
        noise: Override noise word set (default: NOISE_WORDS). For CJK input the
            override (if given) is unioned with CJK_NOISE_WORDS.
        max_words: Cap result to N words (default: no cap). For CJK input, N
            counts segmentation units.
        strip_suffixes: Also strip trailing multi-word suffixes (bird_x uses this)

    Returns:
        Cleaned query string
    """
    text = topic.lower().strip()
    if not text:
        return text

    # --- CJK branch: segment, drop noise, re-join. ---------------------------
    # Detected up front because the English prefix/suffix/word-split logic below
    # assumes whitespace word boundaries that Chinese text does not have.
    if _has_cjk(text):
        # Compose the CJK noise set. If a caller passed a platform-specific
        # noise override, honor it *in addition to* the base CJK noise words so
        # platform tuning still applies to Chinese queries. We also fold in the
        # relevance CJK stopwords (particles/pronouns like 的/了/在) so lone
        # function words left by the segmenter don't pollute the search query.
        cjk_noise: Set[str] = set(CJK_NOISE_WORDS)
        try:
            from .relevance import CJK_STOPWORDS as _CJK_STOP
            cjk_noise |= set(_CJK_STOP)
        except Exception:
            pass
        if noise is not None:
            cjk_noise |= set(noise)

        units = _segment_mixed_ordered(text)
        # Drop noise: CJK noise set for CJK units, English NOISE_WORDS (or the
        # override) for Latin units. Single Latin chars are dropped to match the
        # English tokenizer's behavior; CJK units are kept regardless of length.
        eng_noise = noise if noise is not None else NOISE_WORDS
        filtered: List[str] = []
        for u in units:
            if _CJK_RE.fullmatch(u):
                if u not in cjk_noise:
                    filtered.append(u)
            else:
                if u not in eng_noise and len(u) > 1:
                    filtered.append(u)

        if max_words is not None and filtered:
            filtered = filtered[:max_words]

        result = ' '.join(filtered) if filtered else text
        result = result.rstrip('?!.') if not max_words else result
        # Mirror the English branch's non-empty guarantee under a word cap.
        if max_words and not result:
            return topic.lower().strip()
        return result

    # --- English branch (preserved verbatim from the source). ----------------
    # Phase 1: Strip multi-word prefixes (longest first, stop after first match)
    for p in PREFIXES:
        if text.startswith(p + ' '):
            text = text[len(p):].strip()
            break

    # Phase 2: Strip multi-word suffixes (opt-in)
    if strip_suffixes:
        for s in SUFFIXES:
            if text.endswith(' ' + s):
                text = text[:-len(s)].strip()
                break

    # Phase 3: Filter individual noise words
    noise_set = noise if noise is not None else NOISE_WORDS
    words = text.split()
    filtered = [w for w in words if w not in noise_set]

    # Apply word cap if requested
    if max_words is not None and filtered:
        filtered = filtered[:max_words]

    result = ' '.join(filtered) if filtered else text
    return result.rstrip('?!.') if not max_words else (result or topic.lower().strip())


def extract_compound_terms(topic: str) -> List[str]:
    """Detect multi-word terms that should be quoted in search queries.

    Identifies:
    - Hyphenated terms: "multi-agent", "vc-backed"
    - Title-cased multi-word names: "Claude Code", "React Native"

    Returns list of terms suitable for quoting (e.g., '"multi-agent"').
    """
    terms: List[str] = []

    # Hyphenated terms
    for match in re.finditer(r'\b\w+-\w+(?:-\w+)*\b', topic):
        terms.append(match.group())

    # Title-cased sequences (2+ capitalized words in a row)
    for match in re.finditer(r'(?:[A-Z][a-z]+\s+){1,}[A-Z][a-z]+', topic):
        terms.append(match.group())

    return terms
