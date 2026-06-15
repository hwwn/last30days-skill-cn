"""v3.0.0 orchestration pipeline (China-market port)."""

from __future__ import annotations

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from shutil import which
from typing import Any

from . import (
    bilibili,
    dates,
    dedupe,
    douyin,
    env,
    github,
    grounding,
    juejin,
    normalize,
    planner,
    providers,
    query,
    relevance,
    rerank,
    schema,
    signals,
    snippet,
    v2ex,
    weibo,
    xiaohongshu,
    xueqiu,
    zhihu,
)
from .cluster import cluster_candidates
from .fusion import weighted_rrf

DEPTH_SETTINGS = {
    "quick": {"per_stream_limit": 6, "pool_limit": 15, "rerank_limit": 12},
    "default": {"per_stream_limit": 12, "pool_limit": 40, "rerank_limit": 40},
    "deep": {"per_stream_limit": 20, "pool_limit": 60, "rerank_limit": 60},
}

SEARCH_ALIAS = {
    "wb": "weibo",
    "zh": "zhihu",
    "bili": "bilibili",
    "b站": "bilibili",
    "dy": "douyin",
    "xhs": "xiaohongshu",
    "小红书": "xiaohongshu",
    "gh": "github",
    "xq": "xueqiu",
    "web": "grounding",
}

MAX_SOURCE_FETCHES: dict[str, int] = {"weibo": 2}

MOCK_AVAILABLE_SOURCES = [
    "weibo",
    "zhihu",
    "bilibili",
    "douyin",
    "xiaohongshu",
    "v2ex",
    "juejin",
    "github",
    "xueqiu",
    "grounding",
]


def normalize_requested_sources(sources: list[str] | None) -> list[str] | None:
    if not sources:
        return None
    normalized = []
    for source in sources:
        key = SEARCH_ALIAS.get(source.lower(), source.lower())
        if key not in normalized:
            normalized.append(key)
    return normalized


def available_sources(config: dict[str, Any], requested_sources: list[str] | None = None) -> list[str]:
    available: list[str] = []
    # Baseline always-available sources: each attempts a key-free public/semi-public
    # endpoint and falls back to [] when it cannot reach data (never fabricates).
    available.extend(["v2ex", "juejin", "github", "bilibili", "xueqiu"])
    # Login-gated social sources: only offered when a cookie/scraper key is present.
    if env.is_weibo_available(config):
        available.append("weibo")
    if env.is_zhihu_available(config):
        available.append("zhihu")
    if env.is_douyin_available(config):
        available.append("douyin")
    if requested_sources and "xiaohongshu" in requested_sources and env.is_xiaohongshu_available(config):
        available.append("xiaohongshu")
    if config.get("BRAVE_API_KEY") or config.get("EXA_API_KEY") or config.get("SERPER_API_KEY") or config.get("PARALLEL_API_KEY"):
        available.append("grounding")
    exclude = {s.strip().lower() for s in (config.get("EXCLUDE_SOURCES") or "").split(",") if s.strip()}
    if exclude:
        available = [s for s in available if s not in exclude]
    return available


def diagnose(config: dict[str, Any], requested_sources: list[str] | None = None) -> dict[str, Any]:
    requested_sources = normalize_requested_sources(requested_sources)
    google_key = _google_key(config)
    native_web_backend = None
    if config.get("BRAVE_API_KEY"):
        native_web_backend = "brave"
    elif config.get("EXA_API_KEY"):
        native_web_backend = "exa"
    elif config.get("SERPER_API_KEY"):
        native_web_backend = "serper"
    elif config.get("PARALLEL_API_KEY"):
        native_web_backend = "parallel"
    providers_status = {
        "google": bool(google_key),
        "openai": bool(config.get("OPENAI_API_KEY")) and config.get("OPENAI_AUTH_STATUS") == env.AUTH_STATUS_OK,
        "xai": bool(config.get("XAI_API_KEY")),
        "openrouter": bool(config.get("OPENROUTER_API_KEY")),
        "deepseek": bool(config.get("DEEPSEEK_API_KEY")),
        "dashscope": bool(config.get("DASHSCOPE_API_KEY")),
        "moonshot": bool(config.get("MOONSHOT_API_KEY")),
        "zhipu": bool(config.get("ZHIPU_API_KEY")),
    }
    return {
        "providers": providers_status,
        "local_mode": not any(providers_status.values()),
        "reasoning_provider": (config.get("LAST30DAYS_REASONING_PROVIDER") or "auto").lower(),
        "native_web_backend": native_web_backend,
        "has_scrapecreators": bool(config.get("SCRAPECREATORS_API_KEY")),
        "has_weibo_cookie": bool(config.get("WEIBO_COOKIE")),
        "has_zhihu_cookie": bool(config.get("ZHIHU_COOKIE")),
        "has_github": bool(config.get("GITHUB_TOKEN") or which("gh")),
        "available_sources": available_sources(config, requested_sources),
    }


def run(
    *,
    topic: str,
    config: dict[str, Any],
    depth: str,
    requested_sources: list[str] | None = None,
    mock: bool = False,
    weibo_handle: str | None = None,
    weibo_related: list[str] | None = None,
    web_backend: str = "auto",
    external_plan: dict | None = None,
    zhihu_topics: list[str] | None = None,
    douyin_hashtags: list[str] | None = None,
    douyin_creators: list[str] | None = None,
    xhs_creators: list[str] | None = None,
    lookback_days: int = 30,
    github_user: str | None = None,
    github_repos: list[str] | None = None,
    internal_subrun: bool = False,
) -> schema.Report:
    settings = DEPTH_SETTINGS[depth]
    requested_sources = normalize_requested_sources(requested_sources)
    from_date, to_date = dates.get_date_range(lookback_days)

    if mock:
        runtime = providers.mock_runtime(config, depth)
        reasoning_provider = None
        available = list(requested_sources or MOCK_AVAILABLE_SOURCES)
    else:
        runtime, reasoning_provider = providers.resolve_runtime(config, depth)
        available = available_sources(config, requested_sources)
        if requested_sources:
            available = [source for source in available if source in requested_sources]
    if web_backend == "none":
        available = [s for s in available if s != "grounding"]
    elif web_backend in ("brave", "exa", "serper", "parallel") and "grounding" not in available:
        available.append("grounding")
    if not available:
        raise RuntimeError("No sources are available for this run.")

    if external_plan:
        # External plan provided (e.g., from Claude Code via --plan flag).
        # Parse it through the same sanitizer to validate structure.
        plan = planner._sanitize_plan(
            external_plan, topic, available, requested_sources, depth,
        )
        plan_source = "external"
    else:
        plan = planner.plan_query(
            topic=topic,
            available_sources=available,
            requested_sources=requested_sources,
            depth=depth,
            provider=None if mock else reasoning_provider,
            model=None if mock else runtime.planner_model,
            context=config.get("_auto_resolve_context", ""),
            internal_subrun=internal_subrun,
        )
        # Source labelling: the fallback path annotates notes with "fallback-plan"
        # or "deterministic-comparison-plan"; anything else came from the LLM.
        if any("fallback" in note or "deterministic" in note for note in (plan.notes or [])):
            plan_source = "deterministic"
        elif not mock and reasoning_provider and runtime.planner_model:
            plan_source = "llm"
        else:
            plan_source = "deterministic"

    # Safety net: ensure grounding appears in all subqueries even if the planner
    # omits it. This is redundant when the planner includes grounding via
    # SOURCE_CAPABILITIES, but kept as a fallback.
    if web_backend != "none" and "grounding" in available:
        for sq in plan.subqueries:
            if "grounding" not in sq.sources:
                sq.sources.append("grounding")

    # Always-on planner trace. Emits one summary line plus one per subquery
    # so retrieval-breadth failures like the 2026-04-19 Hermes Agent Use Cases
    # disaster are visible without --debug. Stderr only; does not leak into
    # the user-facing stdout synthesis.
    print(
        f"[Planner] Plan: intent={plan.intent}, freshness={plan.freshness_mode}, "
        f"cluster_mode={plan.cluster_mode}, subqueries={len(plan.subqueries)}, "
        f"source={plan_source}",
        file=sys.stderr,
    )
    if plan.subqueries:
        for index, sq in enumerate(plan.subqueries, start=1):
            sources_str = ",".join(sq.sources) if sq.sources else "(none)"
            print(
                f"[Planner]   sq{index} label={sq.label} "
                f'search="{sq.search_query}" sources=[{sources_str}]',
                file=sys.stderr,
            )
    else:
        print("[Planner]   (no subqueries in plan)", file=sys.stderr)

    bundle = schema.RetrievalBundle(artifacts={"grounding": []})
    # Expose plan_source to the renderer so render_compact can emit the
    # DEGRADED RUN banner when a named-entity topic was invoked bare
    # (source=deterministic AND no pre-research flags). LAW 7 backstop.
    bundle.artifacts["plan_source"] = plan_source

    # Project-mode or person-mode GitHub: run once before the main subquery loop
    _github_custom_done = False
    _github_enriched_repos: set[str] = set()

    # Project mode takes priority over person mode
    if github_repos and "github" in available:
        try:
            project_items = github.search_github_project(
                github_repos, from_date, to_date,
                depth=depth, token=config.get("GITHUB_TOKEN"),
            )
            if project_items:
                normalized = _normalize_score_dedupe(
                    "github", project_items, from_date, to_date,
                    freshness_mode=plan.freshness_mode,
                    ranking_query=f"What are {', '.join(github_repos)} doing on GitHub?",
                )
                primary_label = plan.subqueries[0].label if plan.subqueries else "primary"
                bundle.add_items(primary_label, "github", normalized)
                _github_custom_done = True
                _github_enriched_repos = {r.lower() for r in github_repos}
        except Exception as exc:
            bundle.errors_by_source["github"] = f"Project-mode failed: {exc}"

    _github_person_done = False
    if github_user and "github" in available and not _github_custom_done:
        try:
            person_items = github.search_github_person(
                github_user, from_date, to_date,
                depth=depth, token=config.get("GITHUB_TOKEN"),
            )
            if person_items:
                normalized = _normalize_score_dedupe(
                    "github", person_items, from_date, to_date,
                    freshness_mode=plan.freshness_mode,
                    ranking_query=f"What is @{github_user} doing on GitHub?",
                )
                # Use the first subquery's label so RRF can look up the weight
                primary_label = plan.subqueries[0].label if plan.subqueries else "primary"
                bundle.add_items(primary_label, "github", normalized)
                _github_person_done = True
        except Exception as exc:
            bundle.errors_by_source["github"] = f"Person-mode failed: {exc}"

    # Thread-safe set prevents redundant fetches after a source returns 429
    rate_limited_sources: set[str] = set()
    rate_limit_lock = threading.Lock()

    futures = {}
    # Per-source fetch budget prevents redundant API calls
    source_fetch_count: dict[str, int] = {}
    stream_count = sum(
        1
        for subquery in plan.subqueries
        for source in subquery.sources
        if source in available
    )
    max_workers = max(4, min(16, stream_count or 1))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for subquery in plan.subqueries:
            for source in subquery.sources:
                if source not in available:
                    continue
                # Skip GitHub keyword search if person-mode already ran
                if source == "github" and (_github_person_done or _github_custom_done):
                    continue
                # Enforce per-source fetch cap
                cap = MAX_SOURCE_FETCHES.get(source)
                if cap is not None:
                    current = source_fetch_count.get(source, 0)
                    if current >= cap:
                        continue
                    source_fetch_count[source] = current + 1
                futures[
                    executor.submit(
                        _retrieve_stream,
                        topic=topic,
                        subquery=subquery,
                        source=source,
                        config=config,
                        depth=depth,
                        date_range=(from_date, to_date),
                        runtime=runtime,
                        mock=mock,
                        rate_limited_sources=rate_limited_sources,
                        rate_limit_lock=rate_limit_lock,
                        web_backend=web_backend,
                        raw_topic=topic,
                        zhihu_topics=zhihu_topics,
                        douyin_hashtags=douyin_hashtags,
                        douyin_creators=douyin_creators,
                        xhs_creators=xhs_creators,
                    )
                ] = (subquery, source)

        for future in as_completed(futures):
            subquery, source = futures[future]
            try:
                raw_items, artifact = future.result()
            except Exception as exc:
                # Share 429 signal so pending futures skip this source
                if _is_rate_limit_error(exc):
                    with rate_limit_lock:
                        rate_limited_sources.add(source)
                    bundle.errors_by_source[source] = str(exc)
                    continue
                # Retry once for transient 5xx errors
                if _is_transient_error(exc):
                    time.sleep(3)
                    try:
                        raw_items, artifact = _retrieve_stream(
                            topic=topic, subquery=subquery, source=source,
                            config=config, depth=depth, date_range=(from_date, to_date),
                            runtime=runtime, mock=mock,
                            rate_limited_sources=rate_limited_sources,
                            rate_limit_lock=rate_limit_lock,
                            web_backend=web_backend,
                            raw_topic=topic,
                            zhihu_topics=zhihu_topics,
                            douyin_hashtags=douyin_hashtags,
                            douyin_creators=douyin_creators,
                            xhs_creators=xhs_creators,
                        )
                    except Exception as retry_exc:
                        bundle.errors_by_source[source] = f"{exc} (retried once, still failed: {retry_exc})"
                        continue
                else:
                    bundle.errors_by_source[source] = str(exc)
                    continue
            normalized = _normalize_score_dedupe(
                source, raw_items, from_date, to_date,
                freshness_mode=plan.freshness_mode,
                ranking_query=subquery.ranking_query,
            )
            normalized = normalized[: settings["per_stream_limit"]]
            bundle.add_items(subquery.label, source, normalized)
            if artifact:
                bundle.artifacts.setdefault("grounding", []).append(artifact)

    # Phase 2: supplemental entity-based searches
    _run_supplemental_searches(
        topic=topic,
        bundle=bundle,
        plan=plan,
        config=config,
        depth=depth,
        date_range=(from_date, to_date),
        runtime=runtime,
        mock=mock,
        rate_limited_sources=rate_limited_sources,
        rate_limit_lock=rate_limit_lock,
        weibo_handle=weibo_handle,
        weibo_related=weibo_related,
    )

    # Phase 2b: retry thin sources with simplified query
    # Note: _github_skip_sources tells the retry to not re-run GitHub keyword search
    # when project-mode or person-mode already provided authoritative data.
    _github_skip_retry = {"github"} if (_github_person_done or _github_custom_done) else set()
    _retry_thin_sources(
        topic=topic,
        bundle=bundle,
        plan=plan,
        config=config,
        depth=depth,
        date_range=(from_date, to_date),
        runtime=runtime,
        mock=mock,
        rate_limited_sources=rate_limited_sources,
        rate_limit_lock=rate_limit_lock,
        settings=settings,
        web_backend=web_backend,
        skip_sources=_github_skip_retry,
    )

    # Clear errors for sources that returned items despite partial failures.
    # A source that 429'd on one subquery but succeeded on another is not "errored".
    for source in list(bundle.errors_by_source):
        if bundle.items_by_source.get(source):
            del bundle.errors_by_source[source]

    items_by_source = _finalize_items_by_source(bundle.items_by_source, topic=topic, config=config)
    candidates = weighted_rrf(bundle.items_by_source_and_query, plan, pool_limit=settings["pool_limit"])
    ranked_candidates = rerank.rerank_candidates(
        topic=topic,
        plan=plan,
        candidates=candidates,
        provider=None if mock else reasoning_provider,
        model=None if mock else runtime.rerank_model,
        shortlist_size=settings["rerank_limit"],
    )
    rerank.score_fun(
        topic=topic,
        candidates=ranked_candidates,
        provider=None if mock else reasoning_provider,
        model=None if mock else runtime.rerank_model,
    )

    # Phase 3: post-rerank GitHub star enrichment
    if "github" in available and not mock:
        github.enrich_candidates_with_stars(
            ranked_candidates,
            token=config.get("GITHUB_TOKEN"),
            already_enriched=_github_enriched_repos,
        )

    clusters = cluster_candidates(ranked_candidates, plan)
    warnings = _warnings(items_by_source, ranked_candidates, bundle.errors_by_source)

    return schema.Report(
        topic=topic,
        range_from=from_date,
        range_to=to_date,
        generated_at=datetime.now(timezone.utc).isoformat(),
        provider_runtime=runtime,
        query_plan=plan,
        clusters=clusters,
        ranked_candidates=ranked_candidates,
        items_by_source=items_by_source,
        errors_by_source=bundle.errors_by_source,
        warnings=warnings,
        artifacts=bundle.artifacts,
    )


def _normalize_score_dedupe(
    source: str,
    raw_items: list[dict],
    from_date: str,
    to_date: str,
    freshness_mode: str,
    ranking_query: str,
) -> list[schema.SourceItem]:
    """Normalize, annotate, prune, dedupe, and extract snippets for a batch of raw items."""
    normalized = normalize.normalize_source_items(
        source, raw_items, from_date, to_date,
        freshness_mode=freshness_mode,
    )
    prepared_query = relevance.PreparedQuery(ranking_query)
    normalized = signals.annotate_stream(normalized, prepared_query, freshness_mode)
    normalized = signals.prune_low_relevance(normalized)
    normalized = dedupe.dedupe_items(normalized)
    for item in normalized:
        item.snippet = snippet.extract_best_snippet(item, prepared_query)
    return normalized


def _finalize_items_by_source(
    items_by_source_raw: dict[str, list[schema.SourceItem]],
    topic: str = "",
    config: dict | None = None,
) -> dict[str, list[schema.SourceItem]]:
    finalized = {}
    for source, items in items_by_source_raw.items():
        items = sorted(items, key=lambda item: item.local_rank_score or 0.0, reverse=True)
        items = dedupe.dedupe_items(items)
        # Post-merge topic-relevance filter for Xueqiu: comparison queries fan
        # out into per-entity subqueries whose topic is too narrow for the
        # discussion/symbol API to filter meaningfully. Re-validating the merged
        # list against the full original topic via token overlap drops off-topic
        # discussions (e.g., unrelated tickers/sectors) before footer emission.
        if source == "xueqiu" and topic and items:
            prepared = relevance.PreparedQuery(topic)
            kept: list[schema.SourceItem] = []
            for item in items:
                text = " ".join(
                    part
                    for part in (item.title or "", item.body or "", item.snippet or "")
                    if part
                )
                if relevance.token_overlap_relevance(prepared, text) > 0.0:
                    kept.append(item)
            # Only apply the filter when it leaves something behind; otherwise
            # keep the original ranked list rather than emptying a thin source.
            if kept:
                items = kept
        finalized[source] = items
    return finalized


def _warnings(
    items_by_source: dict[str, list[schema.SourceItem]],
    candidates: list[schema.Candidate],
    errors_by_source: dict[str, str],
) -> list[str]:
    warnings: list[str] = []
    if not candidates:
        warnings.append("No candidates survived retrieval and ranking.")
    if len(candidates) < 5:
        warnings.append("Evidence is thin for this topic.")
    top_sources = {
        source
        for candidate in candidates[:5]
        for source in schema.candidate_sources(candidate)
    }
    if len(top_sources) <= 1 and len(candidates) >= 3:
        warnings.append("Top evidence is highly concentrated in one source.")
    if errors_by_source:
        warnings.append(f"Some sources failed: {', '.join(sorted(errors_by_source))}")
    if not items_by_source:
        warnings.append("No source returned usable items.")
    return warnings


def _is_rate_limit_error(exc: Exception) -> bool:
    """Detect 429 rate-limit errors by status code or message text."""
    if hasattr(exc, "status_code") and getattr(exc, "status_code", None) == 429:
        return True
    return "429" in str(exc)


def _is_transient_error(exc: Exception) -> bool:
    """Detect 5xx server errors that are worth retrying."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and 500 <= status < 600:
        return True
    msg = str(exc)
    return any(code in msg for code in ("500", "502", "503", "504"))


def _run_supplemental_searches(
    *,
    topic: str,
    bundle: schema.RetrievalBundle,
    plan: schema.QueryPlan,
    config: dict[str, Any],
    depth: str,
    date_range: tuple[str, str],
    runtime: schema.ProviderRuntime,
    mock: bool,
    rate_limited_sources: set[str],
    rate_limit_lock: threading.Lock,
    weibo_handle: str | None = None,
    weibo_related: list[str] | None = None,
) -> None:
    """Phase 2: targeted supplemental Weibo searches driven by --weibo-handle.

    The western original fanned out into Bird-CLI X-handle searches driven by
    entity extraction. The CN port simplifies this to an explicit --weibo-handle
    (plus optional --weibo-related) supplemental Weibo search. The skeleton is
    preserved (primary/related handle collection, dedup against Phase 1 URLs,
    lower-weight related-handle subquery registered in the plan) but only the
    weibo source is driven.
    """
    if depth == "quick" or mock:
        return

    # Weibo handle search only runs when weibo is not rate-limited and an
    # explicit handle was supplied; without a handle there is nothing to do.
    if "weibo" in rate_limited_sources:
        return
    if not weibo_handle and not weibo_related:
        return

    from_date, to_date = date_range

    # Collect explicit primary handle(s).
    handles: list[str] = []
    if weibo_handle:
        handle_clean = weibo_handle.lstrip("@").strip()
        if handle_clean:
            handles.append(handle_clean)

    # Collect related handles (searched separately with lower weight).
    related_handles: list[str] = []
    if weibo_related:
        primary_lower = weibo_handle.lstrip("@").lower().strip() if weibo_handle else ""
        for rh in weibo_related:
            rh_clean = rh.lstrip("@").strip()
            if (
                rh_clean
                and rh_clean.lower() != primary_lower
                and rh_clean.lower() not in [h.lower() for h in handles]
            ):
                related_handles.append(rh_clean)

    if not handles and not related_handles:
        return

    # Collect existing URLs for deduplication
    existing_urls = {
        item.url
        for items in bundle.items_by_source.values()
        for item in items
        if item.url
    }

    ranking_query = plan.subqueries[0].ranking_query if plan.subqueries else topic
    primary_label = plan.subqueries[0].label if plan.subqueries else "primary"

    def _search_handles(handle_list: list[str]) -> list[dict]:
        """Run a Weibo search per handle, scoping the query to that author."""
        collected: list[dict] = []
        for handle in handle_list:
            scoped_query = f"{handle} {topic}".strip()
            try:
                result = weibo.search_weibo(
                    scoped_query, from_date, to_date, depth=depth, config=config,
                )
                collected.extend(weibo.parse_weibo_response(result, query=scoped_query))
            except Exception as exc:
                print(
                    f"[Pipeline] Phase 2 weibo handle search failed for {handle}: "
                    f"{type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
        return collected

    # Search primary handles (full weight)
    if handles:
        raw_items = _search_handles(handles)
        if not raw_items and not bundle.items_by_source.get("weibo"):
            bundle.errors_by_source["weibo"] = "Phase 2 handle search returned no items"
        if raw_items:
            normalized = _normalize_score_dedupe(
                "weibo", raw_items, from_date, to_date,
                freshness_mode=plan.freshness_mode,
                ranking_query=ranking_query,
            )
            # Deduplicate against Phase 1 URLs
            normalized = [item for item in normalized if item.url not in existing_urls]
            if normalized:
                bundle.add_items(primary_label, "weibo", normalized)
                # Update existing URLs for related-handle dedup
                for item in normalized:
                    if item.url:
                        existing_urls.add(item.url)

    # Search related handles with lower weight (0.3)
    if related_handles:
        raw_items = _search_handles(related_handles)
        if raw_items:
            normalized = _normalize_score_dedupe(
                "weibo", raw_items, from_date, to_date,
                freshness_mode=plan.freshness_mode,
                ranking_query=ranking_query,
            )
            # Deduplicate against all existing URLs (Phase 1 + primary handles)
            normalized = [item for item in normalized if item.url not in existing_urls]
            if normalized:
                # Use a separate subquery label with lower weight so RRF
                # scores related-handle results below primary results.
                bundle.add_items("supplemental-related", "weibo", normalized)
                # Register the supplemental-related label in the plan for fusion
                if not any(sq.label == "supplemental-related" for sq in plan.subqueries):
                    plan.subqueries.append(
                        schema.SubQuery(
                            label="supplemental-related",
                            search_query=", ".join(related_handles),
                            ranking_query=ranking_query,
                            sources=["weibo"],
                            weight=0.3,
                        )
                    )


def _retry_thin_sources(
    *,
    topic: str,
    bundle: schema.RetrievalBundle,
    plan: schema.QueryPlan,
    config: dict[str, Any],
    depth: str,
    date_range: tuple[str, str],
    runtime: schema.ProviderRuntime,
    mock: bool,
    rate_limited_sources: set[str],
    rate_limit_lock: threading.Lock,
    settings: dict[str, Any],
    web_backend: str = "auto",
    skip_sources: set[str] | None = None,
) -> None:
    """Retry sources with thin results using simplified core subject query."""
    if depth == "quick":
        return

    planned_sources: list[str] = []
    for subquery in plan.subqueries:
        for source in subquery.sources:
            if source not in planned_sources:
                planned_sources.append(source)
    _skip = skip_sources or set()
    thin_sources = [
        source
        for source in planned_sources
        if len(bundle.items_by_source.get(source, [])) < 3
        and source not in bundle.errors_by_source
        and source not in _skip
    ]

    if not thin_sources:
        return

    core = query.extract_core_subject(topic, max_words=3)
    if not core:
        return
    # Note: we intentionally do NOT skip when core == topic. For short topics
    # the 3-unit core IS the topic — but the planner may have sent a different
    # (worse) query to the source. Retrying with the raw core subject is still
    # valuable.

    from_date, to_date = date_range

    # Create a retry subquery with the simplified core subject
    retry_subquery = schema.SubQuery(
        label="retry",
        search_query=core,
        ranking_query=f"What recent evidence from the last 30 days matters for {core}?",
        sources=thin_sources,
        weight=0.3,
    )

    def _retry_one_source(source: str) -> tuple[str, list[schema.SourceItem]]:
        raw_items, _artifact = _retrieve_stream(
            topic=topic,
            subquery=retry_subquery,
            source=source,
            config=config,
            depth=depth,
            date_range=date_range,
            runtime=runtime,
            mock=mock,
            rate_limited_sources=rate_limited_sources,
            rate_limit_lock=rate_limit_lock,
            web_backend=web_backend,
            raw_topic=topic,
        )
        normalized = _normalize_score_dedupe(
            source,
            raw_items,
            from_date,
            to_date,
            freshness_mode=plan.freshness_mode,
            ranking_query=retry_subquery.ranking_query,
        )
        return source, normalized[:settings["per_stream_limit"]]

    retryable = [s for s in thin_sources if s not in rate_limited_sources]

    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=min(4, len(retryable) or 1)) as executor:
        futures = {executor.submit(_retry_one_source, s): s for s in retryable}
        for future in as_completed(futures):
            source = futures[future]
            try:
                source, normalized = future.result()
                existing_urls = {item.url for item in bundle.items_by_source.get(source, []) if item.url}
                new_items = [item for item in normalized if item.url not in existing_urls]

                if new_items:
                    bundle.items_by_source.setdefault(source, []).extend(new_items)
                    primary_label = plan.subqueries[0].label if plan.subqueries else "primary"
                    bundle.items_by_source_and_query.setdefault((primary_label, source), []).extend(new_items)
            except Exception as exc:
                print(f"[Pipeline] Retry failed for {source}: {type(exc).__name__}: {exc}", file=sys.stderr)


def _retrieve_stream(
    *,
    topic: str,
    subquery: schema.SubQuery,
    source: str,
    config: dict[str, Any],
    depth: str,
    date_range: tuple[str, str],
    runtime: schema.ProviderRuntime,
    mock: bool,
    rate_limited_sources: set[str] | None = None,
    rate_limit_lock: threading.Lock | None = None,
    web_backend: str = "auto",
    raw_topic: str = "",
    zhihu_topics: list[str] | None = None,
    douyin_hashtags: list[str] | None = None,
    douyin_creators: list[str] | None = None,
    xhs_creators: list[str] | None = None,
) -> tuple[list[dict], dict]:
    # Early exit if source was rate-limited by a sibling future
    if rate_limited_sources is not None and source in rate_limited_sources:
        return [], {}
    from_date, to_date = date_range
    if mock:
        return _mock_stream_results(source, subquery)
    if source == "grounding":
        return grounding.web_search(
            subquery.search_query, date_range, config, backend=web_backend)
    if source == "weibo":
        result = weibo.search_weibo(subquery.search_query, from_date, to_date, depth=depth, config=config)
        return weibo.parse_weibo_response(result, query=subquery.search_query), {}
    if source == "zhihu":
        result = zhihu.search_zhihu(subquery.search_query, from_date, to_date, depth=depth, config=config)
        return zhihu.parse_zhihu_response(result, query=subquery.search_query), {}
    if source == "bilibili":
        result = bilibili.search_bilibili(subquery.search_query, from_date, to_date, depth=depth, config=config)
        return bilibili.parse_bilibili_response(result, query=subquery.search_query), {}
    if source == "douyin":
        # Use raw_topic so query expansion works from the original user topic,
        # not the planner's narrowed search_query.
        douyin_query = raw_topic or subquery.search_query
        result = douyin.search_douyin(
            douyin_query, from_date, to_date,
            depth=depth, config=config,
            hashtags=douyin_hashtags, creators=douyin_creators,
        )
        return douyin.parse_douyin_response(result, query=douyin_query), {}
    if source == "xiaohongshu":
        # Use raw_topic so query expansion works from the original user topic,
        # not the planner's narrowed search_query.
        xhs_query = raw_topic or subquery.search_query
        result = xiaohongshu.search_xiaohongshu(
            xhs_query, from_date, to_date,
            depth=depth, config=config, creators=xhs_creators,
        )
        return xiaohongshu.parse_xiaohongshu_response(result, query=xhs_query), {}
    if source == "v2ex":
        result = v2ex.search_v2ex(subquery.search_query, from_date, to_date, depth=depth)
        return v2ex.parse_v2ex_response(result, query=subquery.search_query), {}
    if source == "juejin":
        result = juejin.search_juejin(subquery.search_query, from_date, to_date, depth=depth)
        return juejin.parse_juejin_response(result, query=subquery.search_query), {}
    if source == "github":
        # Resolve once at the pipeline boundary so search and enrich
        # share the result; otherwise each call would re-run the env
        # lookup and gh-CLI subprocess fallback (up to 5s timeout each).
        token = github.resolve_token(config.get("GITHUB_TOKEN"))
        response = github.search_github(subquery.search_query, from_date, to_date, depth=depth, token=token)
        items = github.parse_github_response(response)
        items = github.enrich_with_comments(items, depth=depth, token=token)
        return items, {}
    if source == "xueqiu":
        result = xueqiu.search_xueqiu(subquery.search_query, from_date, to_date, depth=depth, config=config)
        return xueqiu.parse_xueqiu_response(result, query=subquery.search_query), {}
    raise RuntimeError(f"Unsupported source: {source}")


def _google_key(config: dict[str, Any]) -> str | None:
    return config.get("GOOGLE_API_KEY") or config.get("GEMINI_API_KEY") or config.get("GOOGLE_GENAI_API_KEY")




def _mock_stream_results(source: str, subquery: schema.SubQuery) -> tuple[list[dict], dict]:
    payloads = {
        "v2ex": [
            {
                "id": "V2EX1",
                "title": f"{subquery.search_query} 讨论帖",
                "text": f"V2EX 社区关于 {subquery.search_query} 的讨论。",
                "url": "https://www.v2ex.com/t/1",
                "hn_url": None,
                "author": "exampleuser",
                "date": dates.get_date_range(5)[0],
                "engagement": {"points": 88, "comments": 36},
                "top_comments": [{"text": "来自用户的真实一手反馈。", "points": 12}],
                "comment_insights": [],
                "relevance": 0.82,
                "why_relevant": "Mock V2EX 结果",
            }
        ],
        "bilibili": [
            {
                "id": "BV1mock",
                "video_id": "BV1mock",
                "title": f"{subquery.search_query} 视频解读",
                "description": f"关于 {subquery.search_query} 的最新视频内容简介。",
                "transcript_snippet": f"视频里详细讲解了 {subquery.search_query}。",
                "url": "https://www.bilibili.com/video/BV1mock",
                "channel_name": "示例UP主",
                "date": dates.get_date_range(6)[0],
                "engagement": {"view": 120000, "like": 8800, "coin": 1200, "danmaku": 540, "reply": 320},
                "top_comments": [{"text": "讲得很清楚，学到了。", "likes": 210}],
                "relevance": 0.85,
                "why_relevant": "Mock B站 结果",
            }
        ],
        "weibo": [
            {
                "id": "WB1",
                "text": f"微博上大家都在讨论 {subquery.search_query}。",
                "url": "https://weibo.com/example/1",
                "author_handle": "example",
                "date": dates.get_date_range(2)[0],
                "engagement": {"reposts": 35, "comments": 18, "attitudes": 200},
                "relevance": 0.79,
                "why_relevant": "Mock 微博 结果",
            }
        ],
        "zhihu": [
            {
                "id": "ZH1",
                "title": f"如何看待 {subquery.search_query}？",
                "selftext": f"知乎上关于 {subquery.search_query} 的高赞回答与讨论。",
                "url": "https://www.zhihu.com/question/1",
                "subreddit": "示例话题",
                "date": dates.get_date_range(5)[0],
                "engagement": {"score": 1200, "num_comments": 48},
                "top_comments": [{"excerpt": "高赞回答给出的关键论据。", "score": 320}],
                "comment_insights": [],
                "relevance": 0.82,
                "why_relevant": "Mock 知乎 结果",
            }
        ],
        "xueqiu": [
            {
                "id": "XQ1",
                "title": f"{subquery.search_query} 讨论热度",
                "question": f"雪球上对 {subquery.search_query} 的多空讨论摘要。",
                "url": "https://xueqiu.com/1/1",
                "date": dates.get_date_range(4)[0],
                "volume1mo": 5200,
                "volume24hr": 380,
                "liquidity": 0.0,
                "price_movement": "情绪偏多，讨论量上升",
                "end_date": None,
                "outcome_prices": [],
                "relevance": 0.8,
                "why_relevant": "Mock 雪球 结果",
            }
        ],
        "grounding": [
            {
                "id": "WB1",
                "title": f"{subquery.search_query} 报道",
                "url": "https://example.com/article",
                "source_domain": "example.com",
                "snippet": f"关于 {subquery.search_query} 的最新网页报道。",
                "date": dates.get_date_range(7)[0],
                "relevance": 0.88,
                "why_relevant": "网页搜索",
            }
        ],
    }
    if source == "grounding":
        return payloads.get(source, []), {
            "label": subquery.label,
            "mock": True,
            "webSearchQueries": [subquery.search_query],
            "resultCount": 1,
        }
    return payloads.get(source, []), {}
