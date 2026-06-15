"""雪球 (Xueqiu) discussion/sentiment search for the last30days-cn pipeline.

移植说明 (PORT_CONTRACT §1/§2/§3)：这是「情绪体」provider，原型为参考源
``polymarket.py`` 的预测市场 provider —— 同一套「打接口 → parse_*_response →
逐条算 relevance + 卡相关性阈值」的流水线骨架被忠实保留，只是数据源从
Polymarket Gamma API 换成了雪球的讨论/股票搜索接口。雪球没有「赔率/概率」语义，
所以把 **讨论量映射到 volume、关注度/流动性映射到 liquidity、涨跌幅或情绪描述
放进 price_movement**，喂给 ``normalize._normalize_xueqiu``（polymarket 归一化原型）。

导出（与 polymarket.py 对齐）：
  - ``search_xueqiu``            高层入口（depth → 条数、cookie 引导、查询展开）
  - ``parse_xueqiu_response``    把原始响应解析成 §3「情绪体」raw item dict 列表
  - ``filter_items_against_topic``     post-merge 按原始 topic 卡相关性（任一信息词命中即留）
  - ``filter_items_against_keywords``  按 --xueqiu-keywords 关键词消歧

取数方式（务实，标准库 urllib，无第三方依赖）
--------------------------------------------------------------------
雪球的搜索接口（``query/v1/symbol/search/status.json`` 等）需要一个匿名会话
cookie ``xq_a_token``，否则返回 ``400 / {"error_code": "400016"}``（「未登录」）。
该 cookie 可通过一次匿名 ``GET https://xueqiu.com/`` 拿到（站点会在响应的
``Set-Cookie`` 里下发 ``xq_a_token`` / ``xqat`` 等），无需真实登录账号。因此：

  1. 先 ``GET https://xueqiu.com/`` 引导 cookie（解析 Set-Cookie 取 xq_a_token）。
  2. 带上引导到的 cookie 打股票讨论/话题搜索接口。
  3. **拿不到 xq_a_token cookie 直接返回 ``[]``**，绝不伪造数据；由 SKILL.md
     指挥宿主模型用 WebSearch（``site:xueqiu.com`` / ``雪球``）补充。

降级路径
--------------------------------------------------------------------
任何失败（cookie 引导失败、HTTP 错误、JSON 解析失败、风控空响应）一律降级为
``[]`` / ``{"items": []}``，绝不抛到调用方，也绝不伪造数据。
"""

import html
import math
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from . import http, log
from .query import extract_core_subject
from .relevance import LOW_SIGNAL_QUERY_TOKENS, token_overlap_relevance

# Homepage GET used to bootstrap the anonymous ``xq_a_token`` session cookie.
XUEQIU_HOME = "https://xueqiu.com/"

# Stock/symbol status (discussion) search. Returns recent posts/discussions that
# mention the query. ``count`` caps results, ``q`` is the keyword.
STATUS_SEARCH_URL = "https://xueqiu.com/query/v1/symbol/search/status.json"

# Fallback: keyword search across symbols + posts. Used when the status endpoint
# yields nothing (e.g. for a non-ticker topic). Returns matching symbols whose
# follower/discussion counts we map into the sentiment-body shape.
SYMBOL_SEARCH_URL = "https://xueqiu.com/query/v1/suggest_stock.json"

# Depth -> number of discussions to aim for. Mirrors the contract's
# DEPTH_CONFIG = {"quick":15,"default":30,"deep":60}.
DEPTH_CONFIG = {
    "quick": 15,
    "default": 30,
    "deep": 60,
}

# Max items to return after parse + relevance filtering, parallels polymarket's
# RESULT_CAP so a single source can't dominate the footer.
RESULT_CAP = {
    "quick": 5,
    "default": 15,
    "deep": 25,
}

_CST = timezone(timedelta(hours=8))


def _log(msg: str) -> None:
    log.source_log("雪球", msg, tty_only=False)


# ---------------------------------------------------------------------------
# Topic / keyword filtering helpers (parallel to polymarket.py)
# ---------------------------------------------------------------------------

# Words too generic to serve as the sole topic-match signal. Parallel to
# polymarket._NOISE_WORDS but tuned for Chinese market discussions: drops
# finance/market filler that would let an off-topic ticker survive a loose
# single-entity subquery match (e.g. "白酒板块行情" surviving a "茅台" subquery).
_NOISE_WORDS = frozenset({
    # English articles / prepositions / conjunctions (mixed CN/EN topics)
    "the", "a", "an", "in", "on", "at", "of", "for", "and", "or", "to", "is",
    "are", "was", "were", "will", "be", "by", "with", "from", "as", "it", "vs",
    "versus",
    # Generic finance / market filler (Chinese)
    "股票", "股价", "行情", "板块", "市场", "大盘", "指数", "基金", "投资",
    "理财", "财经", "证券", "交易", "持仓", "讨论", "观点", "分析", "公司",
    "企业", "概念", "题材", "走势", "趋势", "涨跌", "涨停", "跌停",
    # Generic prediction / sentiment meta words
    "预测", "赔率", "概率", "看多", "看空", "多空", "情绪",
})


def _informative_words(topic: str) -> List[str]:
    """Tokenize a topic's core subject into informative words.

    Uses ``extract_core_subject`` (CJK-aware: jieba/bigram segmentation per
    PORT_CONTRACT §6) then drops generic noise words. Mirrors the intent of
    polymarket._passes_topic_filter's informative-word split.
    """
    core = extract_core_subject(topic)
    # extract_core_subject re-joins CJK segments with spaces, so a plain split
    # recovers the segmentation units. Keep tokens length>1 (CJK bigrams are 2).
    words = [w for w in re.sub(r"[^\w\s]", " ", core.lower()).split() if len(w) > 1]
    informative = [w for w in words if w not in _NOISE_WORDS]
    return informative


def _passes_any_informative_word(topic: str, text: str) -> bool:
    """Keep an item if ANY informative word from the topic appears in its text.

    Looser variant (parallel to polymarket._passes_any_informative_word): for a
    comparison topic ("茅台 vs 五粮液 vs 泸州老窖") a discussion mentioning just one
    entity is still on-topic, so a single informative-word hit suffices.
    """
    informative = _informative_words(topic)
    if not informative:
        # All words generic -> can't meaningfully filter, keep everything.
        return True
    text_lower = (text or "").lower()
    for word in informative:
        if word in text_lower:
            return True
    return False


def filter_items_against_topic(topic: str, items: List[Any]) -> List[Any]:
    """Drop items whose title/text shares no informative word with the topic.

    Mirrors polymarket.filter_items_against_topic. Called post-merge so per-entity
    subquery results for comparison topics get re-validated against the ORIGINAL
    full topic before landing in the footer — prevents an unrelated ticker that
    survived a loose single-entity subquery from polluting the output.

    Accepts a list of either raw dicts (with 'title'/'question') or SourceItem-like
    objects (with .title attribute). Returns the filtered list in the same order.
    """
    if not topic:
        return items

    filtered = []
    for item in items:
        title = getattr(item, "title", None)
        if title is None and isinstance(item, dict):
            title = item.get("title") or item.get("question") or ""
        title = title or ""

        if _passes_any_informative_word(topic, title):
            filtered.append(item)

    dropped = len(items) - len(filtered)
    if dropped:
        _log(f"Post-merge 话题过滤丢弃 {dropped} 条雪球项 (完整话题 '{topic}')")

    return filtered


def filter_items_against_keywords(items: List[Any], keywords: List[str]) -> List[Any]:
    """Keep only items whose title contains at least one keyword (大小写不敏感).

    Mirrors polymarket.filter_items_against_keywords. Intended for disambiguating
    an ambiguous single-token topic via ``--xueqiu-keywords`` (e.g. '苹果,AAPL,iphone'
    to separate Apple Inc. discussions from 苹果期货/水果板块 noise).
    """
    if not keywords:
        return items
    normalized_keywords = [kw.strip().lower() for kw in keywords if kw and kw.strip()]
    if not normalized_keywords:
        return items

    filtered = []
    for item in items:
        title = getattr(item, "title", None)
        if title is None and isinstance(item, dict):
            title = item.get("title") or item.get("question") or ""
        title = (title or "").lower()
        if any(kw in title for kw in normalized_keywords):
            filtered.append(item)

    dropped = len(items) - len(filtered)
    if dropped:
        _log(
            f"关键词过滤丢弃 {dropped} 条雪球项; "
            f"保留 {len(filtered)} 条匹配 {normalized_keywords}"
        )

    return filtered


# ---------------------------------------------------------------------------
# Cookie bootstrap
# ---------------------------------------------------------------------------

def _browser_headers(cookie: Optional[str] = None) -> Dict[str, str]:
    """Build browser-like headers; xueqiu rejects the default skill UA."""
    headers = {
        "User-Agent": http.BROWSER_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://xueqiu.com/",
    }
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _bootstrap_cookie(timeout: int = 15) -> Optional[str]:
    """GET https://xueqiu.com/ once to obtain the anonymous ``xq_a_token`` cookie.

    Parses the response ``Set-Cookie`` headers and returns a ``Cookie:`` header
    value string (e.g. ``"xq_a_token=...; xqat=..."``) if ``xq_a_token`` was
    issued, else None. Never raises — any failure returns None so the caller
    degrades to ``[]``.
    """
    req = urllib.request.Request(
        XUEQIU_HOME,
        headers={
            "User-Agent": http.BROWSER_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # Set-Cookie headers (possibly multiple). get_all preserves them all.
            set_cookies = resp.headers.get_all("Set-Cookie") or []
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as exc:
        _log(f"cookie 引导失败 (GET xueqiu.com): {exc}")
        return None
    except Exception as exc:  # defensive: never let bootstrap raise
        _log(f"cookie 引导异常: {exc}")
        return None

    # Collect the cookie name=value pairs we care about for the session.
    jar: Dict[str, str] = {}
    for raw in set_cookies:
        # Each Set-Cookie looks like "name=value; Path=/; Domain=.xueqiu.com; ..."
        first = raw.split(";", 1)[0].strip()
        if "=" not in first:
            continue
        name, _, value = first.partition("=")
        name = name.strip()
        value = value.strip()
        if name in ("xq_a_token", "xqat", "u", "device_id", "s", "bid"):
            jar[name] = value

    if "xq_a_token" not in jar:
        _log("未取得 xq_a_token cookie，返回空")
        return None

    cookie = "; ".join(f"{k}={v}" for k, v in jar.items())
    _log("已引导匿名 xq_a_token cookie")
    return cookie


def _resolve_cookie(explicit: Optional[str], config: Optional[dict]) -> Optional[str]:
    """Resolve a starting cookie before bootstrapping.

    Honors an explicit ``cookie=`` kwarg, then ``XUEQIU_COOKIE`` from config/env;
    these are optional — if absent we bootstrap an anonymous one. Returns None to
    signal "no preconfigured cookie, must bootstrap".
    """
    if explicit:
        return explicit
    if config and config.get("XUEQIU_COOKIE"):
        return config["XUEQIU_COOKIE"]
    env_cookie = os.environ.get("XUEQIU_COOKIE")
    if env_cookie:
        return env_cookie
    return None


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def _expand_queries(topic: str) -> List[str]:
    """Generate search queries to cast a wider net.

    Mirrors polymarket._expand_queries: always include the core subject, then add
    individual informative segmentation units as standalone searches (helps the
    keyword-based status endpoint surface more discussions), capped + deduped.
    """
    core = extract_core_subject(topic) or topic.strip()
    queries = [core]

    words = [w for w in core.split() if len(w) > 1]
    if len(words) >= 2:
        for word in words:
            wl = word.lower()
            if wl not in LOW_SIGNAL_QUERY_TOKENS and wl not in _NOISE_WORDS:
                queries.append(word)

    if topic.strip().lower() != core.lower():
        queries.append(topic.strip())

    seen = set()
    unique = []
    for q in queries:
        ql = q.lower().strip()
        if ql and ql not in seen:
            seen.add(ql)
            unique.append(q.strip())
    return unique[:5]


def _fetch_status(query: str, cookie: str, count: int, timeout: int = 15) -> List[Dict[str, Any]]:
    """Hit the symbol status (discussion) search endpoint for one query."""
    params = {
        "q": query,
        "count": str(count),
        "page": "1",
        "sort": "time",
        "source": "all",
    }
    try:
        resp = http.get(
            STATUS_SEARCH_URL,
            headers=_browser_headers(cookie),
            params=params,
            timeout=timeout,
            retries=2,
        )
    except http.HTTPError as exc:
        _log(f"讨论搜索失败 '{query}': {exc}")
        return []
    if not isinstance(resp, dict):
        return []
    # status endpoint returns {"list": [...]} or {"count": n, "list": [...]}.
    statuses = resp.get("list")
    if isinstance(statuses, list):
        return [s for s in statuses if isinstance(s, dict)]
    return []


def _fetch_symbols(query: str, cookie: str, count: int, timeout: int = 15) -> List[Dict[str, Any]]:
    """Fallback: suggest_stock keyword search (symbols w/ follower counts)."""
    params = {"q": query, "count": str(count)}
    try:
        resp = http.get(
            SYMBOL_SEARCH_URL,
            headers=_browser_headers(cookie),
            params=params,
            timeout=timeout,
            retries=2,
        )
    except http.HTTPError as exc:
        _log(f"标的搜索失败 '{query}': {exc}")
        return []
    if not isinstance(resp, dict):
        return []
    data = resp.get("data")
    if isinstance(data, list):
        return [s for s in data if isinstance(s, dict)]
    if isinstance(data, dict) and isinstance(data.get("stocks"), list):
        return [s for s in data["stocks"] if isinstance(s, dict)]
    return []


def search_xueqiu(
    query: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    **kw: Any,
) -> Dict[str, Any]:
    """Search Xueqiu discussions/symbols for a topic (情绪体, polymarket 原型).

    Bootstraps an anonymous ``xq_a_token`` cookie, then runs expanded queries
    against the status (discussion) search endpoint, merging + deduping by post
    id. Falls back to the symbol search endpoint when no discussions surface.

    Args:
        query: Search topic.
        from_date: Start date (YYYY-MM-DD). The search endpoint has no server-side
            date filter; the per-item date is retained for downstream windowing.
        to_date: End date (YYYY-MM-DD). Kept for API symmetry.
        depth: "quick" | "default" | "deep".
        **kw: Optional ``config=`` (for XUEQIU_COOKIE) and ``cookie=`` override.

    Returns:
        A dict ``{"statuses": [...], "symbols": [...], "query": <core>, "_cap": n}``.
        On any failure — including no bootstrapped cookie — returns
        ``{"statuses": [], "symbols": [], "query": <core>, "_cap": n}``. Never
        raises, never fabricates.
    """
    config = kw.get("config")
    target = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    cap = RESULT_CAP.get(depth, RESULT_CAP["default"])
    core = extract_core_subject(query) or query.strip()

    empty = {"statuses": [], "symbols": [], "query": core, "_cap": cap}
    if not core:
        return empty

    # Resolve / bootstrap the session cookie. A preconfigured cookie skips the
    # homepage GET; otherwise we bootstrap an anonymous one. No cookie -> [].
    cookie = _resolve_cookie(kw.get("cookie"), config)
    if not cookie or "xq_a_token" not in cookie:
        cookie = _bootstrap_cookie()
    if not cookie:
        # Per PORT_CONTRACT §2: no xq_a_token cookie -> return empty (no fabrication).
        return empty

    queries = _expand_queries(query)
    _log(f"搜索 '{query}'，查询: {queries} (target={target})")

    all_statuses: Dict[str, Dict[str, Any]] = {}
    for q in queries:
        for status in _fetch_status(q, cookie, target):
            sid = str(status.get("id") or status.get("retweet_id") or "")
            if not sid:
                # No id -> fall back to text hash to dedupe identical posts.
                sid = str(hash(status.get("text") or status.get("description") or ""))
            if sid not in all_statuses:
                all_statuses[sid] = status
        if len(all_statuses) >= target:
            break

    statuses = list(all_statuses.values())

    # Fallback to symbol search only when discussions came back empty — keeps the
    # source from going silent for non-ticker topics that still match a symbol.
    symbols: List[Dict[str, Any]] = []
    if not statuses:
        seen_codes: set = set()
        for q in queries:
            for sym in _fetch_symbols(q, cookie, target):
                code = str(sym.get("code") or sym.get("symbol") or "")
                if code and code in seen_codes:
                    continue
                if code:
                    seen_codes.add(code)
                symbols.append(sym)
            if len(symbols) >= target:
                break

    _log(f"取得 {len(statuses)} 条讨论 / {len(symbols)} 个标的 (核心词: {core})")
    return {
        "statuses": statuses[:target],
        "symbols": symbols[:target],
        "query": core,
        "_cap": cap,
    }


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")


def _clean_text(raw: Any) -> str:
    """Flatten xueqiu's HTML post body into plain text."""
    if not raw:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", str(raw), flags=re.IGNORECASE)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    return " ".join(text.split()).strip()


def _as_int(value: Any) -> int:
    """Coerce a count field to int, tolerating '1.2万'/'3.4亿' and None."""
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    if not s:
        return 0
    mult = 1
    if s.endswith("万"):
        mult, s = 10_000, s[:-1]
    elif s.endswith("亿"):
        mult, s = 100_000_000, s[:-1]
    try:
        return int(float(s) * mult)
    except (ValueError, TypeError):
        return 0


def _parse_created_at(value: Any) -> Optional[str]:
    """Parse xueqiu timestamps into YYYY-MM-DD.

    Xueqiu posts carry ``created_at`` as epoch milliseconds; symbols may carry an
    ISO-ish string. Returns None when unparseable.
    """
    if value is None:
        return None
    # Epoch millis / seconds.
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        try:
            ts = float(value)
        except (ValueError, TypeError):
            return None
        # Heuristic: >1e12 means milliseconds.
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, _CST).strftime("%Y-%m-%d")
        except (ValueError, OSError, OverflowError):
            return None
    if isinstance(value, str):
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", value.strip())
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def _post_url(status: Dict[str, Any]) -> str:
    """Build the canonical xueqiu permalink for a discussion/status post."""
    target = status.get("target")
    if isinstance(target, str) and target:
        # ``target`` is often a relative path like "/1234/567890".
        if target.startswith("http"):
            return target
        return f"https://xueqiu.com{target}"
    user = status.get("user") or {}
    uid = user.get("id") if isinstance(user, dict) else status.get("user_id")
    pid = status.get("id")
    if uid and pid:
        return f"https://xueqiu.com/{uid}/{pid}"
    if pid:
        return f"https://xueqiu.com/statuses/show/{pid}"
    return ""


# Sentiment lexicon: map a discussion's framing to a 看多/看空 (bullish/bearish)
# description for price_movement. Parallel to polymarket._format_price_movement,
# but driven by text sentiment + engagement rather than market price deltas
# (Xueqiu exposes no probability/odds).
_BULLISH = ("看多", "看涨", "利好", "买入", "加仓", "牛", "涨", "新高", "突破")
_BEARISH = ("看空", "看跌", "利空", "卖出", "减仓", "清仓", "熊", "跌", "新低", "破位", "亏")


def _sentiment_movement(text: str) -> Optional[str]:
    """Derive a 看多/看空 sentiment label from a discussion's text.

    Returns a short Chinese description (e.g. "讨论偏多") or None when neutral /
    no signal — mirroring polymarket._format_price_movement returning None on noise.
    """
    if not text:
        return None
    bull = sum(text.count(w) for w in _BULLISH)
    bear = sum(text.count(w) for w in _BEARISH)
    if bull == 0 and bear == 0:
        return None
    if bull > bear:
        return "讨论偏多"
    if bear > bull:
        return "讨论偏空"
    return "多空分歧"


def _parse_statuses(
    statuses: List[Dict[str, Any]],
    ranking_query: str,
    start_index: int = 0,
) -> List[Dict[str, Any]]:
    """Parse discussion posts into §3「情绪体」item dicts.

    讨论量(评论+转发+点赞) -> volume24hr；点赞数(粉丝/认同近似流动性) -> liquidity；
    多空情绪 -> price_movement。
    """
    items: List[Dict[str, Any]] = []
    for i, status in enumerate(statuses):
        if not isinstance(status, dict):
            continue
        text = _clean_text(status.get("text") or status.get("description") or "")
        title = _clean_text(status.get("title") or "")
        url = _post_url(status)
        if not url or not (text or title):
            continue

        reply = _as_int(status.get("reply_count"))
        retweet = _as_int(status.get("retweet_count"))
        fav = _as_int(status.get("fav_count") or status.get("like_count"))
        # 讨论量 = 互动总量（评论 + 转发 + 点赞）。映射到 volume(讨论量)。
        volume = reply + retweet + fav
        # 关注度 / 流动性近似：点赞 + 转发（更接近「被认同/扩散」程度）。
        liquidity = fav + retweet

        question = title or text[:80]
        date = _parse_created_at(status.get("created_at") or status.get("timeBefore"))
        movement = _sentiment_movement(f"{title} {text}")

        score_text = f"{title} {text}".strip()
        relevance = (
            token_overlap_relevance(ranking_query, score_text) if ranking_query else 0.5
        )

        items.append({
            "id": f"XQ{start_index + i + 1}",
            "title": (title or text[:60]) or f"雪球讨论 {start_index + i + 1}",
            "question": question,
            "url": url,
            "date": date,
            "volume24hr": volume,
            "volume1mo": volume,
            "liquidity": liquidity,
            "price_movement": movement,
            "end_date": None,
            "outcome_prices": [],
            "outcomes_remaining": 0,
            "relevance": round(relevance, 2),
            "why_relevant": f"雪球讨论: {(title or text)[:50]}",
        })
    return items


def _parse_symbols(
    symbols: List[Dict[str, Any]],
    ranking_query: str,
    start_index: int = 0,
) -> List[Dict[str, Any]]:
    """Parse symbol-search hits into §3「情绪体」item dicts (fallback shape).

    关注度(关注人数/讨论数) -> volume；成交/流动性字段 -> liquidity；当日涨跌幅 ->
    price_movement (涨跌描述)。
    """
    items: List[Dict[str, Any]] = []
    for i, sym in enumerate(symbols):
        if not isinstance(sym, dict):
            continue
        name = _clean_text(sym.get("name") or sym.get("stock_name") or "")
        code = str(sym.get("code") or sym.get("symbol") or "").strip()
        if not (name or code):
            continue

        title = f"{name} {code}".strip() if (name and code) else (name or code)
        # 关注度 / 讨论度近似 volume。
        volume = _as_int(sym.get("follower_count") or sym.get("status_count") or sym.get("hot"))
        # 成交额 / 成交量近似流动性。
        liquidity = _as_int(sym.get("volume") or sym.get("amount") or sym.get("turnover"))

        # 当日涨跌幅 -> price_movement（涨跌描述），无则按情绪兜底。
        movement = None
        pct = sym.get("percent") or sym.get("change_percent") or sym.get("chg")
        if pct is not None:
            try:
                pctf = float(str(pct).rstrip("%"))
                if abs(pctf) >= 0.01:
                    direction = "涨" if pctf > 0 else "跌"
                    movement = f"今日{direction} {abs(pctf):.2f}%"
            except (ValueError, TypeError):
                movement = None

        url = f"https://xueqiu.com/S/{code}" if code else "https://xueqiu.com/"
        relevance = token_overlap_relevance(ranking_query, title) if ranking_query else 0.5

        items.append({
            "id": f"XQS{start_index + i + 1}",
            "title": title or f"雪球标的 {start_index + i + 1}",
            "question": f"雪球上对 {title} 的多空讨论。",
            "url": url,
            "date": None,
            "volume24hr": volume,
            "volume1mo": volume,
            "liquidity": liquidity,
            "price_movement": movement,
            "end_date": None,
            "outcome_prices": [],
            "outcomes_remaining": 0,
            "relevance": round(relevance, 2),
            "why_relevant": f"雪球标的: {title[:50]}",
        })
    return items


# Relevance floors (parallel to polymarket's _MIN_RELEVANCE / _ITEM_MIN_RELEVANCE):
# drop everything if nothing is genuinely on-topic, then drop per-item noise.
_MIN_RELEVANCE = 0.15
_ITEM_MIN_RELEVANCE = 0.10


def parse_xueqiu_response(result: Any, query: str = "") -> List[Dict[str, Any]]:
    """Parse a search_xueqiu result into §3「情绪体」raw item dicts.

    Output shape per item (PORT_CONTRACT §3 情绪体)::

        {id, title, question, url, date, volume1mo|volume24hr, liquidity,
         price_movement, end_date, outcome_prices: [], relevance, why_relevant}

    Args:
        result: The dict returned by ``search_xueqiu`` (``{"statuses": [...],
            "symbols": [...]}``), or a bare list of status dicts, or a raw
            status-search response (``{"list": [...]}``).
        query: Original search query for relevance scoring. Falls back to the
            core subject stashed in ``result["query"]`` when omitted.

    Returns:
        List of raw item dicts, relevance-sorted and capped. Empty list on any
        unusable input — never raises.
    """
    if not result:
        return []

    ranking_query = query
    statuses: List[Dict[str, Any]] = []
    symbols: List[Dict[str, Any]] = []
    cap: Optional[int] = None

    if isinstance(result, dict):
        if result.get("error"):
            _log(f"解析时遇到错误响应: {result.get('error')}")
            return []
        if not ranking_query:
            ranking_query = result.get("query", "") or ""
        cap = result.get("_cap")
        if isinstance(result.get("statuses"), list):
            statuses = [s for s in result["statuses"] if isinstance(s, dict)]
            symbols = [s for s in (result.get("symbols") or []) if isinstance(s, dict)]
        elif isinstance(result.get("list"), list):
            # Raw status-search response.
            statuses = [s for s in result["list"] if isinstance(s, dict)]
        else:
            return []
    elif isinstance(result, list):
        statuses = [s for s in result if isinstance(s, dict)]
    else:
        return []

    if not ranking_query:
        ranking_query = query or ""

    items = _parse_statuses(statuses, ranking_query)
    if symbols:
        items.extend(_parse_symbols(symbols, ranking_query, start_index=len(items)))

    if not items:
        return []

    # Sort by relevance, then apply the same two-stage relevance floor as
    # polymarket so only genuinely on-topic discussions reach the footer.
    items.sort(key=lambda x: x["relevance"], reverse=True)

    if items and items[0]["relevance"] < _MIN_RELEVANCE:
        _log(
            f"全部 {len(items)} 条雪球结果低于相关性阈值 "
            f"({items[0]['relevance']:.2f} < {_MIN_RELEVANCE})，丢弃全部"
        )
        return []

    before = len(items)
    items = [i for i in items if i["relevance"] >= _ITEM_MIN_RELEVANCE]
    dropped = before - len(items)
    if dropped:
        _log(f"丢弃 {dropped} 条低于单项相关性下限 ({_ITEM_MIN_RELEVANCE}) 的雪球项")

    if cap is None:
        cap = len(items)
    return items[:cap]
