"""为一个话题自动解析知乎话题、微博账号、B站 UP主与时事背景。

通过 web 搜索（Brave/Exa/Serper）在 planner 运行之前发现相关社区与背景。
这是 SKILL.md 步骤 0.55/0.75（在那两步里用的是 Claude Code 的 WebSearch 工具）
的引擎侧等价实现。
"""

from __future__ import annotations

import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

from . import categories, dates, grounding

MAX_TOPICS = 10


def _log(msg: str) -> None:
    print(f"[Resolve] {msg}", file=sys.stderr)


def _merge_category_peers(topic: str, zhihu_topics: list[str]) -> tuple[list[str], Optional[str]]:
    """用品类同类话题扩展 WebSearch 抽取出的知乎话题列表。

    对话题做分类，取该品类的同类话题，与现有列表做大小写不敏感去重，
    再按优先级追加缺失的同类话题。最终列表上限 MAX_TOPICS，保留每一个
    WebSearch 返回的话题（它们是最新鲜的信号），从同类话题追加端裁剪。

    返回 (merged_topics, matched_category_id_or_None)。仅当确实追加了
    同类话题时（而非每个同类话题都已在 WebSearch 集合里）才打印一行
    [Resolve] Matched category 日志。

    分类失败降级为“无匹配”——返回未扩展的列表并打印一条告警。
    """
    try:
        category = categories.detect_category(topic)
    except Exception as exc:
        _log(f"Category classification failed: {exc}")
        return list(zhihu_topics)[:MAX_TOPICS], None

    if category is None:
        return list(zhihu_topics)[:MAX_TOPICS], None

    peers = categories.peer_subs_for(category)
    if not peers:
        return list(zhihu_topics)[:MAX_TOPICS], category

    existing_lower = {s.lower() for s in zhihu_topics}
    merged = list(zhihu_topics)
    added: list[str] = []
    for peer in peers:
        if len(merged) >= MAX_TOPICS:
            break
        if peer.lower() in existing_lower:
            continue
        merged.append(peer)
        existing_lower.add(peer.lower())
        added.append(peer)

    if added:
        _log(f"Matched category={category}, adding peers: {', '.join(added)}")

    return merged, category


def _has_backend(config: dict) -> bool:
    """检查是否有任一 web 搜索后端可用。"""
    return bool(
        config.get("BRAVE_API_KEY")
        or config.get("EXA_API_KEY")
        or config.get("SERPER_API_KEY")
        or config.get("PARALLEL_API_KEY")
        or config.get("OPENROUTER_API_KEY")
    )


def _extract_zhihu_topics(items: list[dict]) -> list[str]:
    """从搜索结果的标题、摘要与 URL 中解析知乎话题名。

    知乎话题以 `zhihu.com/topic/{id}` 形式出现，话题中文名出现在标题里
    （常形如「话题名 - 知乎」）。优先从 zhihu.com 结果的标题里抽取中文
    话题名，去重后返回。
    """
    seen: set[str] = set()
    results: list[str] = []
    title_pattern = re.compile(r"^(.{2,30}?)\s*[-—|]\s*知乎")
    for item in items:
        url = item.get("url", "")
        title = item.get("title", "")
        if "zhihu.com" not in url:
            continue
        match = title_pattern.search(title)
        if not match:
            continue
        name = match.group(1).strip()
        lower = name.lower()
        if name and lower not in seen:
            seen.add(lower)
            results.append(name)
    return results


def _extract_weibo_handle(items: list[dict]) -> str:
    """从搜索结果中抽取最可能的微博账号（昵称/handle）。

    微博个人页/话题以 `weibo.com/u/{uid}`、`weibo.com/n/{name}` 或
    `m.weibo.cn/...` 出现；昵称带 `@` 前缀。统计候选出现频次，URL 命中
    是更强的信号，最后返回得分最高者。
    """
    pattern = re.compile(r"@([一-龥A-Za-z0-9_\-]{1,20})")
    url_pattern = re.compile(r"(?:weibo\.com|m\.weibo\.cn)/n/([一-龥A-Za-z0-9_\-]{1,20})(?:/|$|\?)")
    counts: dict[str, int] = {}
    for item in items:
        text = f"{item.get('title', '')} {item.get('snippet', '')}"
        url = item.get("url", "")
        for match in pattern.findall(text):
            lower = match.lower()
            counts[lower] = counts.get(lower, 0) + 1
        for match in url_pattern.findall(url):
            lower = match.lower()
            # URL 命中是更强的信号
            counts[lower] = counts.get(lower, 0) + 3
    # 过滤掉通用词
    skip = {"weibo", "微博", "search", "搜索", "话题", "home", "u", "n"}
    counts = {k: v for k, v in counts.items() if k not in skip}
    if not counts:
        return ""
    return max(counts, key=counts.get)


def _extract_bilibili_uploaders(items: list[dict]) -> list[str]:
    """从搜索结果中抽取 B站 UP主名。

    B站个人空间以 `space.bilibili.com/{mid}` 出现，UP主名出现在标题里
    （常形如「UP主名的个人空间-哔哩哔哩」或「UP主名 - 哔哩哔哩」）。
    去重后返回最多 5 个。
    """
    seen: set[str] = set()
    results: list[str] = []
    title_pattern = re.compile(r"^(.{2,30}?)\s*(?:的个人空间|[-—|]\s*(?:哔哩哔哩|bilibili))")
    for item in items:
        url = item.get("url", "")
        title = item.get("title", "")
        if "bilibili.com" not in url and "b23.tv" not in url:
            continue
        match = title_pattern.search(title)
        if not match:
            continue
        name = match.group(1).strip()
        lower = name.lower()
        if name and lower not in seen:
            seen.add(lower)
            results.append(name)
    return results[:5]  # 上限 5 个 UP主


def _extract_github_user(items: list[dict]) -> str:
    """从搜索结果中抽取 GitHub 用户名。"""
    url_pattern = re.compile(r"github\.com/([A-Za-z0-9_-]{1,39})(?:/|$|\?)")
    counts: dict[str, int] = {}
    for item in items:
        url = item.get("url", "")
        text = f"{item.get('title', '')} {item.get('snippet', '')}"
        for match in url_pattern.findall(url):
            lower = match.lower()
            counts[lower] = counts.get(lower, 0) + 3
        for match in url_pattern.findall(text):
            lower = match.lower()
            counts[lower] = counts.get(lower, 0) + 1
    # 过滤掉 org/repo 形态的名字与通用页面
    skip = {"topics", "explore", "settings", "orgs", "search", "features", "about", "pricing", "enterprise"}
    counts = {k: v for k, v in counts.items() if k not in skip}
    if not counts:
        return ""
    return max(counts, key=counts.get)


def _extract_github_repos(items: list[dict]) -> list[str]:
    """从搜索结果中抽取 owner/repo 字符串。"""
    repo_pattern = re.compile(r"github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)")
    skip_owners = {"topics", "explore", "settings", "orgs", "search", "features", "about", "pricing", "enterprise"}
    seen: set[str] = set()
    repos: list[str] = []
    for item in items:
        url = item.get("url", "")
        text = f"{item.get('title', '')} {item.get('snippet', '')}"
        for source in [url, text]:
            for match in repo_pattern.findall(source):
                owner = match.split("/")[0].lower()
                if owner in skip_owners:
                    continue
                lower = match.lower()
                if lower not in seen:
                    seen.add(lower)
                    repos.append(match)
    return repos[:5]  # 上限 5 个 repo


_INTEGRATION_SUFFIX_KEYWORDS: dict[str, set[str]] = {
    "-action": {"action", "actions", "workflow", "workflows"},
    "-sdk": {"sdk", "client", "library"},
    "-plugin": {"plugin", "plugins", "extension", "extensions"},
    "-plugins": {"plugin", "plugins", "extension", "extensions"},
    "-docs": {"docs", "documentation"},
    "-examples": {"example", "examples", "sample", "samples"},
    "-template": {"template", "templates", "starter", "boilerplate"},
}


def _topic_tokens(topic: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (topic or "").lower()))


def _topic_entity_slugs(topic: str) -> list[str]:
    entities = re.split(r"\b(?:vs|versus)\b", (topic or "").lower())
    slugs: list[str] = []
    for entity in entities:
        tokens = re.findall(r"[a-z0-9]+", entity)
        if tokens:
            slugs.append("-".join(tokens))
    return slugs


def _repo_slug(repo: str) -> str:
    parts = repo.split("/", 1)
    if len(parts) != 2:
        return ""
    return parts[1].lower()


def _canonicalize_integration_repo(topic: str, repo: str) -> str:
    """在话题允许时，把集成类 repo 映射回规范的产品 repo。

    例如：
      anthropics/claude-code-action -> anthropics/claude-code
    除非话题明确要 "action"/"workflow"。
    """
    parts = repo.split("/", 1)
    if len(parts) != 2:
        return repo
    owner, name = parts[0], parts[1]
    lower_name = name.lower()
    topic_words = _topic_tokens(topic)
    for suffix, intent_words in _INTEGRATION_SUFFIX_KEYWORDS.items():
        if not lower_name.endswith(suffix):
            continue
        if topic_words.intersection(intent_words):
            return repo
        base = name[: -len(suffix)]
        if base:
            return f"{owner}/{base}"
    return repo


def canonicalize_github_repos(topic: str, repos: list[str], *, cap: int | None = 5) -> list[str]:
    """为当前话题归一化并按优先级排序 GitHub repo。

    - 当话题意图未提及这些集成时，把常见的集成后缀重写回规范产品 repo。
    - 把与话题 slug 精确匹配的 repo（如 `claude-code`）排到部分匹配前面。
    """
    canonicalized: list[str] = []
    seen: set[str] = set()
    for repo in repos:
        candidate = _canonicalize_integration_repo(topic, repo.strip())
        if "/" not in candidate:
            continue
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        canonicalized.append(candidate)

    topic_slugs = set(_topic_entity_slugs(topic))
    if topic_slugs:
        exact = [r for r in canonicalized if _repo_slug(r) in topic_slugs]
        prefixed = [r for r in canonicalized if any(_repo_slug(r).startswith(f"{slug}-") for slug in topic_slugs) and r not in exact]
        rest = [r for r in canonicalized if r not in exact and r not in prefixed]
        canonicalized = exact + prefixed + rest

    if cap is not None:
        return canonicalized[:cap]
    return canonicalized


def _build_context_summary(items: list[dict]) -> str:
    """从新闻搜索结果构建 1-2 句时事背景摘要。"""
    snippets: list[str] = []
    for item in items[:3]:
        snippet = item.get("snippet", "").strip()
        if snippet:
            snippets.append(snippet)
    if not snippets:
        return ""
    # 取前两条有意义的摘要并截断以保持简洁
    combined = " ".join(snippets[:2])
    if len(combined) > 300:
        combined = combined[:297] + "..."
    return combined


def auto_resolve(topic: str, config: dict) -> dict:
    """为一个话题发现知乎话题、微博账号、B站 UP主、GitHub 与时事背景。

    Args:
        topic: 研究话题。
        config: 含 API key 的字典（BRAVE_API_KEY、EXA_API_KEY、SERPER_API_KEY）。

    Returns:
        含以下 key 的字典：zhihu_topics、weibo_handle、bilibili_uploaders、
        github_user、github_repos、context、category、searches_run。无 web
        搜索后端时返回空结果。
    """
    empty = {
        "zhihu_topics": [],
        "weibo_handle": "",
        "bilibili_uploaders": [],
        "github_user": "",
        "github_repos": [],
        "context": "",
        "category": None,
        "searches_run": 0,
    }

    if not _has_backend(config):
        _log("No web search backend available, skipping resolve")
        return empty

    from_date, to_date = dates.get_date_range(30)
    date_range = (from_date, to_date)
    now = datetime.now(timezone.utc)
    current_month = now.strftime("%m")
    current_year = now.strftime("%Y")

    queries = {
        "zhihu": f"{topic} 知乎 话题",
        "news": f"{topic} 新闻 {current_year}年{current_month}月",
        "weibo": f"{topic} 微博 话题",
        "bilibili": f"{topic} 哔哩哔哩 UP主 site:bilibili.com",
        "github": f"{topic} github profile site:github.com",
    }

    results: dict[str, list[dict]] = {}
    searches_run = 0

    def _search(label: str, query: str) -> tuple[str, list[dict]]:
        items, _artifact = grounding.web_search(query, date_range, config)
        return label, items

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(_search, label, q): label
            for label, q in queries.items()
        }
        for future in as_completed(futures):
            label = futures[future]
            try:
                _label, items = future.result()
                results[label] = items
                searches_run += 1
            except Exception as exc:
                _log(f"Search failed for {label}: {exc}")
                results[label] = []

    zhihu_topics = _extract_zhihu_topics(results.get("zhihu", []))
    weibo_handle = _extract_weibo_handle(results.get("weibo", []))
    bilibili_uploaders = _extract_bilibili_uploaders(results.get("bilibili", []))
    github_user = _extract_github_user(results.get("github", []))
    github_repos = canonicalize_github_repos(topic, _extract_github_repos(results.get("github", [])))
    context = _build_context_summary(results.get("news", []))

    zhihu_topics, category = _merge_category_peers(topic, zhihu_topics)

    _log(
        f"Resolved {len(zhihu_topics)} zhihu_topics, weibo_handle={weibo_handle!r}, "
        f"bilibili_uploaders={bilibili_uploaders!r}, github_user={github_user!r}, "
        f"github_repos={github_repos!r}, context_len={len(context)}, category={category!r}"
    )

    return {
        "zhihu_topics": zhihu_topics,
        "weibo_handle": weibo_handle,
        "bilibili_uploaders": bilibili_uploaders,
        "github_user": github_user,
        "github_repos": github_repos,
        "context": context,
        "category": category,
        "searches_run": searches_run,
    }
