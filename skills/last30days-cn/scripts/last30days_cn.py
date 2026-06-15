#!/usr/bin/env python3
# ruff: noqa: E402
"""last30days-cn CLI（中国市场移植版）。"""

from __future__ import annotations

import argparse
import atexit
import datetime
import json
import os
import re
import signal
import sys
import threading
from pathlib import Path

MIN_PYTHON = (3, 12)


def ensure_supported_python(version_info: tuple[int, int, int] | object | None = None) -> None:
    if version_info is None:
        version_info = sys.version_info
    major, minor, micro = tuple(version_info[:3])
    if (major, minor) >= MIN_PYTHON:
        return
    sys.stderr.write(
        "last30days-cn 需要 Python 3.12+。\n"
        f"检测到 Python {major}.{minor}.{micro}。\n"
        "请安装并使用 python3.12 或 python3.13，然后重新运行。\n"
    )
    raise SystemExit(1)


ensure_supported_python()

if os.name == "nt":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from lib import env, html_render, pipeline, render, schema, ui

_child_pids: set[int] = set()
_child_pids_lock = threading.Lock()


def register_child_pid(pid: int) -> None:
    with _child_pids_lock:
        _child_pids.add(pid)


def unregister_child_pid(pid: int) -> None:
    with _child_pids_lock:
        _child_pids.discard(pid)


def _cleanup_children() -> None:
    with _child_pids_lock:
        pids = list(_child_pids)
    for pid in pids:
        try:
            if hasattr(os, "killpg"):
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            else:
                os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            continue


atexit.register(_cleanup_children)


def parse_search_flag(raw: str) -> list[str]:
    sources = []
    for source in raw.split(","):
        source = source.strip().lower()
        if not source:
            continue
        normalized = pipeline.SEARCH_ALIAS.get(source, source)
        if normalized not in pipeline.MOCK_AVAILABLE_SOURCES:
            raise SystemExit(f"未知的检索源: {source}")
        if normalized not in sources:
            sources.append(normalized)
    if not sources:
        raise SystemExit("--search 至少需要一个源。")
    return sources


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "last30days"


def save_output(
    report: schema.Report,
    emit: str,
    save_dir: str,
    suffix: str = "",
    synthesis_md: str | None = None,
    topic_override: str | None = None,
    rendered_content: str | None = None,
) -> Path:
    from datetime import datetime
    path = Path(save_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    slug = slugify(topic_override or report.topic)
    extension = "json" if emit == "json" else "html" if emit == "html" else "md"
    raw_label = "raw-html" if emit == "html" else "raw"
    suffix_part = f"-{suffix}" if suffix else ""
    out_path = path / f"{slug}-{raw_label}{suffix_part}.{extension}"
    if out_path.exists():
        out_path = path / f"{slug}-{raw_label}{suffix_part}-{datetime.now().strftime('%Y-%m-%d')}.{extension}"
    # Markdown saves keep the complete debug artifact. JSON and HTML preserve
    # their requested wire format so file extensions match their content.
    if rendered_content is not None:
        content = rendered_content
    elif emit in {"json", "html"}:
        content = emit_output(report, emit, synthesis_md=synthesis_md)
    else:
        content = render.render_full(report)
    out_path.write_text(content, encoding="utf-8")
    return out_path


def emit_output(
    report: schema.Report,
    emit: str,
    fun_level: str = "medium",
    save_path: str | None = None,
    synthesis_md: str | None = None,
) -> str:
    if emit == "json":
        return json.dumps(schema.to_dict(report), indent=2, sort_keys=True)
    if emit == "html":
        return html_render.render_html(
            report, fun_level=fun_level, save_path=save_path, synthesis_md=synthesis_md,
        )
    if emit in {"compact", "md"}:
        return render.render_compact(report, fun_level=fun_level, save_path=save_path)
    if emit == "context":
        return render.render_context(report)
    raise SystemExit(f"不支持的 emit 模式: {emit}")


def emit_comparison_output(
    entity_reports: list[tuple[str, schema.Report]],
    emit: str,
    fun_level: str = "medium",
    save_path: str | None = None,
    synthesis_md: str | None = None,
) -> str:
    if emit == "json":
        payload = {
            "comparison": True,
            "entities": [label for label, _ in entity_reports],
            "reports": [
                {"entity": label, "report": schema.to_dict(report)}
                for label, report in entity_reports
            ],
        }
        return json.dumps(payload, indent=2, sort_keys=True)
    if emit == "html":
        return html_render.render_html_comparison(
            entity_reports,
            fun_level=fun_level,
            save_path=save_path,
            synthesis_md=synthesis_md,
        )
    if emit in {"compact", "md"}:
        return render.render_comparison_multi(
            entity_reports, fun_level=fun_level, save_path=save_path,
        )
    if emit == "context":
        return render.render_comparison_multi_context(entity_reports)
    raise SystemExit(f"不支持的 emit 模式: {emit}")


def comparison_topic(entity_reports: list[tuple[str, schema.Report]]) -> str:
    return " vs ".join(label for label, _ in entity_reports)


def compute_save_path_display(save_dir: str, topic: str, suffix: str, emit: str) -> str:
    """Compute the user-friendly save path string that will be shown in the footer.

    Uses ~ when the saved file is under the user's home directory; otherwise
    returns the absolute path.
    """
    from pathlib import Path as _Path
    path = _Path(save_dir).expanduser().resolve()
    slug = slugify(topic)
    extension = "json" if emit == "json" else "html" if emit == "html" else "md"
    raw_label = "raw-html" if emit == "html" else "raw"
    suffix_part = f"-{suffix}" if suffix else ""
    raw = path / f"{slug}-{raw_label}{suffix_part}.{extension}"
    try:
        home = _Path.home().resolve()
        relative = raw.relative_to(home)
        return f"~/{relative.as_posix()}"
    except ValueError:
        return raw.as_posix()


def read_synthesis_file(path: str) -> str:
    try:
        return Path(path).expanduser().read_text(encoding="utf-8")
    except OSError as exc:
        sys.stderr.write(f"[last30days-cn] 无法读取 --synthesis-file: {exc}\n")
        raise SystemExit(2)


def persist_report(report: schema.Report) -> dict[str, int]:
    import store

    store.init_db()
    topic_row = store.add_topic(report.topic)
    topic_id = topic_row["id"]
    source_mode = ",".join(sorted(report.items_by_source)) or "v3"
    run_id = store.record_run(topic_id, source_mode=source_mode, status="running")
    try:
        findings = store.findings_from_report(report)
        counts = store.store_findings(run_id, topic_id, findings)
        store.update_run(
            run_id,
            status="completed",
            findings_new=counts["new"],
            findings_updated=counts["updated"],
        )
        return counts
    except Exception as exc:
        store.update_run(run_id, status="failed", error_message=str(exc)[:500])
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="跨微博、知乎、B站、抖音、小红书、V2EX、掘金、GitHub、雪球与联网网页等实时社交、市场与网页源调研一个话题。")
    parser.add_argument("topic", nargs="*", help="调研话题")
    parser.add_argument("--emit", default="compact", choices=["compact", "json", "context", "md", "html"])
    parser.add_argument("--search", help="逗号分隔的源列表")
    parser.add_argument("--quick", action="store_true", help="低延迟检索档位")
    parser.add_argument("--deep", action="store_true", help="高召回检索档位")
    parser.add_argument("--debug", action="store_true", help="开启 HTTP 调试日志")
    parser.add_argument("--mock", action="store_true", help="使用 mock 检索 fixtures")
    parser.add_argument("--diagnose", action="store_true", help="打印推理 provider 与源可用性")
    parser.add_argument("--save-dir", help="可选：保存渲染输出的目录")
    parser.add_argument("--synthesis-file", help="嵌入 --emit=html 输出的 Markdown 综述")
    parser.add_argument("--store", action="store_true", help="将排序后的结论持久化到 SQLite 研究库")
    parser.add_argument("--weibo-handle", help="用于定向补充检索的微博账号/handle")
    parser.add_argument("--weibo-related", help="逗号分隔的相关微博账号（以较低权重检索）")
    parser.add_argument("--web-backend", default="auto",
                        choices=["auto", "brave", "exa", "serper", "parallel", "none"],
                        help="联网搜索后端（默认 auto，依次尝试 Brave → Exa → Serper → Parallel）")
    parser.add_argument("--deep-research", action="store_true",
                        help="使用 Perplexity Deep Research（约 $0.90/次）做深度分析。需要 OPENROUTER_API_KEY。")
    parser.add_argument("--plan", help="JSON 查询计划（跳过内置 LLM planner）。可为 JSON 字符串或文件路径。")
    parser.add_argument("--save-suffix", help="保存文件名后缀（如 'qwen' → kanye-west-raw-qwen.md）")
    parser.add_argument("--zhihu-topics", dest="zhihu_topics", help="逗号分隔的知乎话题名（如 人工智能,创业）")
    parser.add_argument("--douyin-hashtags", dest="douyin_hashtags", help="逗号分隔的抖音话题标签，无需 #（如 tella,屏幕录制）")
    parser.add_argument("--douyin-creators", dest="douyin_creators", help="逗号分隔的抖音创作者 handle（如 TellaHQ,taborplace）")
    parser.add_argument("--xhs-creators", dest="xhs_creators", help="逗号分隔的小红书博主 handle（如 tella.tv,laborstories）")
    parser.add_argument(
        "--days",
        "--lookback-days",
        dest="lookback_days",
        type=int,
        default=30,
        help="调研回看天数（默认 30，watchlist 用 90）",
    )
    parser.add_argument("--auto-resolve", action="store_true",
                        help="规划前用联网搜索发现知乎话题/微博账号（适用于无 WebSearch 的宿主）")
    parser.add_argument("--github-user", help="按 person 模式检索的 GitHub 用户名（如 steipete）")
    parser.add_argument("--github-repo", help="按 project 模式检索的 owner/repo，逗号分隔（如 openclaw/openclaw,paperclipai/paperclip）")
    parser.add_argument(
        "--competitors",
        nargs="?",
        const=2,
        type=int,
        default=None,
        metavar="N",
        help="自动发现 N 个竞品实体并把 last30days 在它们之间做对比展开（默认 N=2 → 三方：原始 + 2 个同类；范围 1..6）。用 --competitors-list 覆盖发现。",
    )
    parser.add_argument(
        "--competitors-list",
        dest="competitors_list",
        help="逗号分隔的竞品实体，跳过自动发现（如 '阿里,腾讯,字节跳动'）。隐含 --competitors。",
    )
    parser.add_argument(
        "--xueqiu-keywords",
        dest="xueqiu_keywords",
        help=(
            "逗号分隔的关键词，雪球讨论/话题标题必须命中才会被纳入。"
            "用于消歧义的单 token 话题（如 '小米' 加 xiaomi,1810），过滤掉跨实体噪声。"
            "省略时雪球会返回所有匹配项，泛话题上要预期跨实体噪声。"
        ),
    )
    parser.add_argument(
        "--competitors-plan",
        dest="competitors_plan",
        help=(
            "竞品 / vs 模式子运行的逐实体 Step 0.55 定向 JSON 映射。"
            "Schema: {entity_name: {weibo_handle?, weibo_related?, zhihu_topics?, "
            "github_user?, github_repos?, context?}}。接受内联 JSON 或文件路径。"
            "隐含 --competitors。当宿主模型已解析好逐实体 handle 与话题时优先于 --competitors-list。"
        ),
    )
    return parser


def parse_competitors_plan(raw: str | None) -> dict[str, dict]:
    """Parse a --competitors-plan argument into a {entity_name_lower: plan_entry} dict.

    Accepts inline JSON or a file path (matches --plan). Returns {} on None/empty.
    Validation: top-level must be a dict; each value must be a dict. Unknown fields
    in entry values log a warning but do not abort. Invalid JSON or non-dict shape
    raises SystemExit(2) with a clear stderr message.
    """
    if not raw:
        return {}
    plan_str = raw
    if os.path.isfile(plan_str):
        try:
            plan_str = open(plan_str).read()
        except OSError as exc:
            sys.stderr.write(f"[CompetitorsPlan] 无法读取 plan 文件: {exc}\n")
            raise SystemExit(2)
    try:
        parsed = json.loads(plan_str)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"[CompetitorsPlan] 无效 JSON: {exc}\n")
        raise SystemExit(2)
    if not isinstance(parsed, dict):
        sys.stderr.write(
            f"[CompetitorsPlan] 顶层必须是 "
            f"{{entity: {{targeting}}}} 形式的 dict，得到 {type(parsed).__name__}\n"
        )
        raise SystemExit(2)
    known_fields = {
        "weibo_handle", "weibo_related", "zhihu_topics",
        "github_user", "github_repos", "context",
    }
    normalized: dict[str, dict] = {}
    for entity, entry in parsed.items():
        if not isinstance(entry, dict):
            sys.stderr.write(
                f"[CompetitorsPlan] {entity!r} 的条目必须是 dict，"
                f"得到 {type(entry).__name__}；跳过。\n"
            )
            continue
        unknown = set(entry.keys()) - known_fields
        if unknown:
            sys.stderr.write(
                f"[CompetitorsPlan] {entity!r} 中存在未知字段: "
                f"{sorted(unknown)}；忽略。\n"
            )
        normalized[entity.strip().lower()] = {
            k: v for k, v in entry.items() if k in known_fields
        }
    return normalized


def subrun_kwargs_for(
    entity: str,
    plan_entry: dict,
    *,
    resolved: dict,
) -> dict:
    """Build an explicit per-entity kwargs dict for pipeline.run().

    Plan values win over auto_resolve values. Returns keys for all per-entity
    targeting flags so callers never fall through to closure defaults.

    This helper is the single source of truth for sub-run kwargs — main-topic
    flags can only leak if a caller bypasses it.
    """
    def _choose(plan_key: str, resolved_key: str | None = None):
        if plan_key in plan_entry and plan_entry[plan_key]:
            return plan_entry[plan_key]
        if resolved_key is not None and resolved.get(resolved_key):
            return resolved[resolved_key]
        return None

    weibo_handle = _choose("weibo_handle", "weibo_handle")
    if isinstance(weibo_handle, str):
        weibo_handle = weibo_handle.lstrip("@") or None

    zhihu_topics = _choose("zhihu_topics", "zhihu_topics")
    if isinstance(zhihu_topics, list):
        zhihu_topics = [s.strip() for s in zhihu_topics if s.strip()] or None

    weibo_related = plan_entry.get("weibo_related")
    if isinstance(weibo_related, list):
        weibo_related = [h.strip().lstrip("@") for h in weibo_related if h.strip()] or None
    else:
        weibo_related = None

    github_user = _choose("github_user", "github_user")
    if isinstance(github_user, str):
        github_user = github_user.lstrip("@").lower() or None

    github_repos = _choose("github_repos", "github_repos")
    if isinstance(github_repos, list):
        github_repos = [r.strip() for r in github_repos if r.strip() and "/" in r.strip()] or None

    context = plan_entry.get("context") or resolved.get("context") or ""

    return {
        "weibo_handle": weibo_handle,
        "weibo_related": weibo_related,
        "zhihu_topics": zhihu_topics,
        "github_user": github_user,
        "github_repos": github_repos,
        "_context": context,
    }


COMPETITORS_MIN = 1
COMPETITORS_MAX = 6
COMPETITORS_DEFAULT = 2


def resolve_competitors_args(args: argparse.Namespace) -> tuple[bool, int, list[str]]:
    """Normalize --competitors / --competitors-list into (enabled, count, explicit_list).

    - (False, 0, []) when neither flag is set.
    - An explicit list always wins; count is derived from list length.
    - A numeric count outside [1, 6] is clamped with a stderr warning.
    - count <= 0 (explicit) raises SystemExit(2).
    """
    explicit_list: list[str] = []
    list_flag_provided = args.competitors_list is not None
    if list_flag_provided:
        explicit_list = [
            entity.strip()
            for entity in args.competitors_list.split(",")
            if entity.strip()
        ]
        if not explicit_list:
            sys.stderr.write("[Competitors] --competitors-list 为空。\n")
            raise SystemExit(2)

    competitors_flag = args.competitors
    list_present = bool(explicit_list)
    flag_present = competitors_flag is not None

    if not list_present and not flag_present:
        return False, 0, []

    if list_present:
        count = len(explicit_list)
        if flag_present and competitors_flag != count:
            sys.stderr.write(
                f"[Competitors] --competitors={competitors_flag} 被忽略；改用 "
                f"--competitors-list 中的 {count} 个条目。\n"
            )
        if count > COMPETITORS_MAX:
            sys.stderr.write(
                f"[Competitors] --competitors-list 有 {count} 个条目，截断到 {COMPETITORS_MAX}。\n"
            )
            explicit_list = explicit_list[:COMPETITORS_MAX]
            count = COMPETITORS_MAX
        return True, count, explicit_list

    # flag_present, no explicit list
    count = competitors_flag
    if count < COMPETITORS_MIN:
        sys.stderr.write(
            f"[Competitors] --competitors 必须 >= {COMPETITORS_MIN}（得到 {count}）。\n"
        )
        raise SystemExit(2)
    if count > COMPETITORS_MAX:
        sys.stderr.write(
            f"[Competitors] --competitors={count} 超过上限 {COMPETITORS_MAX}；截断。\n"
        )
        count = COMPETITORS_MAX
    return True, count, []


def _missing_sources_for_promo(diag: dict[str, object]) -> str | None:
    available = set(diag.get("available_sources") or [])
    missing = []
    if "zhihu" not in available:
        missing.append("zhihu")
    if "weibo" not in available:
        missing.append("weibo")
    if "grounding" not in available:
        missing.append("web")
    if not missing:
        return None
    if "zhihu" in missing and "weibo" in missing:
        return "both"
    return missing[0]


def _show_runtime_ui(
    report: schema.Report,
    progress: ui.ProgressDisplay,
    diag: dict[str, object],
    suppress_web_promo: bool = False,
) -> None:
    counts = {source: len(items) for source, items in report.items_by_source.items()}
    display_sources = list(
        dict.fromkeys(
            [
                *report.query_plan.source_weights.keys(),
                *report.items_by_source.keys(),
                *report.errors_by_source.keys(),
            ]
        )
    )
    progress.end_processing()
    progress.show_complete(
        source_counts=counts,
        display_sources=display_sources,
    )
    promo = _missing_sources_for_promo(diag)
    # The `web` promo nudges users to set BRAVE_API_KEY / SERPER_API_KEY, which
    # is wrong advice when a hosting reasoning model (Claude Code, Codex,
    # Hermes, Gemini) is driving — those already have WebSearch and can
    # pre-resolve Step 0.55 themselves. Suppress the web promo when a hosting
    # model signal is present (--plan or --competitors-plan was passed).
    if promo:
        if suppress_web_promo and promo == "web":
            return
        if suppress_web_promo and promo == "both":
            # "both" means zhihu + web both missing; still nudge zhihu but
            # skip the web line. show_promo has a per-source variant.
            progress.show_promo("zhihu", diag=diag)
            return
        progress.show_promo(promo, diag=diag)


def _write_last_run(topic: str, report: "schema.Report") -> None:
    try:
        if env.CONFIG_DIR is None:
            return
        target = env.CONFIG_DIR
        target.mkdir(parents=True, exist_ok=True)
        counts = {source: len(items) for source, items in report.items_by_source.items()}
        payload = {
            "topic": topic,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "sources": counts,
            "total": sum(counts.values()),
        }
        (target / "last-run.json").write_text(json.dumps(payload, indent=2))
    except Exception:
        pass


def main() -> int:
    parser = build_parser()
    # Use parse_known_args so setup sub-flags (--device-auth, --github,
    # --openclaw) and the --agent hosting-model signal pass through without
    # argparse hard-exiting.
    args, extra_argv = parser.parse_known_args()
    if args.debug:
        os.environ["LAST30DAYS_DEBUG"] = "1"

    config = env.get_config()

    # Handle setup subcommand
    topic = " ".join(args.topic).strip()
    if topic.lower() == "setup":
        from lib import setup_wizard
        if "--openclaw" in extra_argv:
            results = setup_wizard.run_openclaw_setup(config)
            print(json.dumps(results))
            return 0
        if "--github" in extra_argv:
            results = setup_wizard.run_github_auth()
            print(json.dumps(results))
            return 0
        if "--device-auth" in extra_argv:
            results = setup_wizard.run_full_device_auth()
            print(json.dumps(results))
            return 0
        sys.stderr.write("正在运行自动配置...\n")
        results = setup_wizard.run_auto_setup(config)
        from_browser = "auto"
        if results.get("cookies_found"):
            first_browser = next(iter(results["cookies_found"].values()))
            from_browser = first_browser
        setup_wizard.write_setup_config(env.CONFIG_FILE, from_browser=from_browser)
        results["env_written"] = True
        sys.stderr.write(setup_wizard.get_setup_status_text(results) + "\n")
        return 0

    requested_sources = parse_search_flag(args.search) if args.search else None
    diag = pipeline.diagnose(config, requested_sources)

    if args.diagnose:
        print(json.dumps(diag, indent=2, sort_keys=True))
        return 0

    if not topic:
        parser.print_usage(sys.stderr)
        return 2

    synthesis_md = None
    if args.synthesis_file:
        if args.emit == "html":
            synthesis_md = read_synthesis_file(args.synthesis_file)
        else:
            sys.stderr.write("[last30days-cn] 警告：--synthesis-file 仅在 --emit=html 时使用；已忽略。\n")

    if not os.environ.get("LAST30DAYS_SKIP_PREFLIGHT"):
        from lib import preflight
        refuse_msg = preflight.check_class_1_trap(topic)
        if refuse_msg:
            sys.stderr.write(refuse_msg)
            return 2

    progress = ui.ProgressDisplay(topic, show_banner=True)
    progress.start_processing()

    depth = "deep" if args.deep else "quick" if args.quick else "default"
    try:
        weibo_related = [h.strip() for h in args.weibo_related.split(",") if h.strip()] if args.weibo_related else None
        zhihu_topics = [s.strip() for s in args.zhihu_topics.split(",") if s.strip()] if args.zhihu_topics else None
        douyin_hashtags = [h.strip().lstrip("#") for h in args.douyin_hashtags.split(",") if h.strip()] if args.douyin_hashtags else None
        douyin_creators = [c.strip().lstrip("@") for c in args.douyin_creators.split(",") if c.strip()] if args.douyin_creators else None
        xhs_creators = [c.strip().lstrip("@") for c in args.xhs_creators.split(",") if c.strip()] if args.xhs_creators else None
        # Parse external plan if provided via --plan flag
        external_plan = None
        if args.plan:
            import json as _json
            plan_str = args.plan
            if os.path.isfile(plan_str):
                plan_str = open(plan_str).read()
            try:
                external_plan = _json.loads(plan_str)
            except _json.JSONDecodeError as exc:
                sys.stderr.write(f"[Planner] 无效的 --plan JSON: {exc}\n")

        # Auto-resolve: use web search to discover zhihu topics/weibo handle before
        # planning. This is the engine-side equivalent of SKILL.md Steps 0.55/0.75
        # for platforms without WebSearch (OpenClaw, Codex, raw CLI).
        repos_from_auto_resolve = False
        if args.auto_resolve and not external_plan:
            from lib import resolve
            resolution = resolve.auto_resolve(topic, config)
            if resolution.get("zhihu_topics") and not zhihu_topics:
                zhihu_topics = resolution["zhihu_topics"]
                sys.stderr.write(f"[AutoResolve] 知乎话题: {', '.join(zhihu_topics)}\n")
            if resolution.get("weibo_handle") and not args.weibo_handle:
                args.weibo_handle = resolution["weibo_handle"]
                sys.stderr.write(f"[AutoResolve] 微博账号: @{args.weibo_handle}\n")
            if resolution.get("github_user") and not args.github_user:
                args.github_user = resolution["github_user"]
                sys.stderr.write(f"[AutoResolve] GitHub 用户: @{args.github_user}\n")
            if resolution.get("github_repos") and not args.github_repo:
                args.github_repo = ",".join(resolution["github_repos"])
                # auto_resolve already canonicalized via canonicalize_github_repos(cap=5);
                # mark so we don't re-canonicalize below and clobber its relevance order.
                repos_from_auto_resolve = True
                sys.stderr.write(f"[AutoResolve] GitHub 仓库: {args.github_repo}\n")
            if resolution.get("context"):
                # Inject context into external_plan metadata for the planner to use
                if not external_plan:
                    external_plan = None  # planner will use its own, but with context
                # Store context for the planner prompt injection
                config["_auto_resolve_context"] = resolution["context"]
                sys.stderr.write(f"[AutoResolve] 背景: {resolution['context'][:80]}...\n")

        github_user = args.github_user.lstrip("@").lower() if args.github_user else None
        github_repos = [r.strip() for r in args.github_repo.split(",") if r.strip() and "/" in r.strip()] if args.github_repo else None

        # Only canonicalize when repos came from a user-supplied --github-repo flag.
        # When repos_from_auto_resolve is True, auto_resolve already ran
        # canonicalize_github_repos(cap=5) and ranked by relevance; re-running here
        # with cap=None can re-sort by topic-slug match and lose that ordering.
        if github_repos and not repos_from_auto_resolve:
            from lib import resolve as resolve_lib
            original_github_repos = github_repos[:]
            github_repos = resolve_lib.canonicalize_github_repos(topic, github_repos, cap=None)
            if github_repos != original_github_repos:
                sys.stderr.write(
                    "[GitHub] 已规范化仓库: "
                    f"{','.join(original_github_repos)} -> {','.join(github_repos)}\n"
                )

        # --deep-research: auto-enable perplexity source and set deep flag
        if args.deep_research:
            if not config.get("OPENROUTER_API_KEY"):
                print("错误：--deep-research 需要 OPENROUTER_API_KEY", file=sys.stderr)
                sys.exit(1)
            config["_deep_research"] = True
            # Auto-enable perplexity in INCLUDE_SOURCES
            include = config.get("INCLUDE_SOURCES") or ""
            if "perplexity" not in include.lower():
                config["INCLUDE_SOURCES"] = f"{include},perplexity" if include else "perplexity"

        comp_enabled, comp_count, comp_explicit = resolve_competitors_args(args)
        comp_plan = parse_competitors_plan(args.competitors_plan)

        # Xueqiu disambiguation: if user passed --xueqiu-keywords, store on
        # config so the xueqiu adapter can filter matches.
        if args.xueqiu_keywords:
            keywords = [
                k.strip().lower()
                for k in args.xueqiu_keywords.split(",")
                if k.strip()
            ]
            if keywords:
                config["_xueqiu_keywords"] = keywords

        # vs-mode: if the topic string contains " vs " / " versus " and the
        # planner can split it into >=2 entities, route through the same
        # N-pass fanout path as --competitors. The first entity becomes the
        # main topic; remaining entities become the competitor list. User's
        # outer --weibo-handle / --zhihu-topics apply to the first entity unless
        # --competitors-plan covers it.
        from lib import planner as _planner
        vs_entities = _planner._comparison_entities(topic)
        if len(vs_entities) >= 2 and not comp_enabled:
            topic = vs_entities[0]
            comp_enabled = True
            comp_count = len(vs_entities) - 1
            comp_explicit = vs_entities[1:]
            sys.stderr.write(
                f"[Competitors] vs 模式：路由到 N 趟 fanout: "
                f"{' vs '.join(vs_entities)}\n"
            )

        def _main_runner() -> schema.Report:
            r = pipeline.run(
                topic=topic,
                config=config,
                depth=depth,
                requested_sources=requested_sources,
                mock=args.mock,
                weibo_handle=args.weibo_handle,
                weibo_related=weibo_related,
                web_backend=args.web_backend,
                external_plan=external_plan,
                zhihu_topics=zhihu_topics,
                douyin_hashtags=douyin_hashtags,
                douyin_creators=douyin_creators,
                xhs_creators=xhs_creators,
                lookback_days=args.lookback_days,
                github_user=github_user,
                github_repos=github_repos,
            )
            r.artifacts["resolved"] = {
                "entity": topic,
                "weibo_handle": (args.weibo_handle or "").lstrip("@"),
                "zhihu_topics": list(zhihu_topics or []),
                "github_user": (github_user or ""),
                "github_repos": list(github_repos or []),
                "context": config.get("_auto_resolve_context", "") or "",
            }
            return r

        if comp_enabled:
            from lib import competitors as competitors_mod
            from lib import fanout, resolve as resolve_mod

            if comp_explicit:
                discovered = comp_explicit
            else:
                if not resolve_mod._has_backend(config) and not args.mock:
                    sys.stderr.write(
                        "[Competitors] 无外部辅助时无法自动发现同类实体。\n"
                        "\n"
                        "推荐路径（宿主推理模型 —— Claude Code、Codex、Hermes、Gemini "
                        "等任何带 WebSearch 工具的 agent）：你有 WebSearch。用它对每个实体跑完整 "
                        "Step 0.55，然后用 vs 话题加 --competitors-plan 调用引擎：\n"
                        "  1. WebSearch 搜 '{topic} 竞品' 或 '{topic} 替代品'。\n"
                        "  2. 对每个同类，WebSearch 搜 handle/话题/github（Step 0.55）。\n"
                        "  3. 重新调用：/last30days-cn '{topic} vs 同类1 vs 同类2' "
                        "--competitors-plan '{\"同类1\":{\"weibo_handle\":\"h1\",\"zhihu_topics\":"
                        "[\"s1\"],...},\"同类2\":{...}}'。\n"
                        "完整流程见 SKILL.md 'Competitor mode'。\n"
                        "\n"
                        "无人值守 / CRON 路径（无宿主模型）：设置 "
                        "BRAVE_API_KEY / EXA_API_KEY / SERPER_API_KEY / PARALLEL_API_KEY / "
                        "OPENROUTER_API_KEY 后重新运行。\n"
                        "\n"
                        "最低逃生口：传 --competitors-list 'A,B,C' 跳过发现。"
                        "不带 --competitors-plan 时，同类子运行会回退到 planner 默认值，"
                        "产出的数据明显比主话题更稀疏。\n"
                    )
                    return 2
                discovered = competitors_mod.discover_competitors(
                    topic, comp_count, config, lookback_days=args.lookback_days,
                )
                if not discovered:
                    sys.stderr.write(
                        f"[Competitors] 未为 {topic!r} 发现同类实体；中止对比运行。"
                        "可传 --competitors-list 覆盖。\n"
                    )
                    return 2

            sys.stderr.write(
                f"[Competitors] 对比: {topic} vs " + " vs ".join(discovered) + "\n"
            )

            def _competitor_runner(entity: str) -> schema.Report:
                # Deep-copy config so per-entity auto_resolve context does not
                # leak across sub-runs. Each sub-run writes its own
                # `_auto_resolve_context` into its local config copy.
                entity_config = dict(config)
                plan_entry = comp_plan.get(entity.strip().lower(), {})
                resolved = {
                    "entity": entity,
                    "weibo_handle": "",
                    "zhihu_topics": [],
                    "github_user": "",
                    "github_repos": [],
                    "context": "",
                }
                # Skip engine-internal auto_resolve when the hosting model
                # pre-resolved via --competitors-plan (saves a redundant
                # round-trip and makes per-entity Step 0.55 purely
                # hosting-model-driven).
                plan_covers_fully = bool(plan_entry.get("weibo_handle")) and bool(
                    plan_entry.get("zhihu_topics")
                )
                if (
                    not args.mock
                    and not plan_covers_fully
                    and resolve_mod._has_backend(entity_config)
                ):
                    try:
                        r = resolve_mod.auto_resolve(entity, entity_config)
                    except Exception as exc:
                        sys.stderr.write(
                            f"[Competitors] {entity!r} 的 auto_resolve 失败: "
                            f"{type(exc).__name__}: {exc}\n"
                        )
                        r = {}
                    resolved["weibo_handle"] = r.get("weibo_handle", "") or ""
                    resolved["zhihu_topics"] = list(r.get("zhihu_topics") or [])
                    resolved["github_user"] = r.get("github_user", "") or ""
                    resolved["github_repos"] = list(r.get("github_repos") or [])
                    resolved["context"] = r.get("context", "") or ""
                kwargs = subrun_kwargs_for(entity, plan_entry, resolved=resolved)
                # Record effective per-entity targeting for the Resolved block.
                resolved_effective = {
                    "entity": entity,
                    "weibo_handle": kwargs["weibo_handle"] or "",
                    "zhihu_topics": kwargs["zhihu_topics"] or [],
                    "github_user": kwargs["github_user"] or "",
                    "github_repos": kwargs["github_repos"] or [],
                    "context": kwargs["_context"],
                }
                if kwargs["_context"]:
                    entity_config["_auto_resolve_context"] = kwargs["_context"]
                sys.stderr.write(
                    f"[Competitors] {entity}: "
                    f"wb=@{resolved_effective['weibo_handle'] or '-'} "
                    f"zh={len(resolved_effective['zhihu_topics'])} "
                    f"gh={resolved_effective['github_user'] or '-'} "
                    f"({'plan' if plan_entry else 'auto'})\n"
                )
                report = pipeline.run(
                    topic=entity,
                    config=entity_config,
                    depth=depth,
                    requested_sources=requested_sources,
                    mock=args.mock,
                    weibo_handle=kwargs["weibo_handle"],
                    weibo_related=kwargs["weibo_related"],
                    zhihu_topics=kwargs["zhihu_topics"],
                    github_user=kwargs["github_user"],
                    github_repos=kwargs["github_repos"],
                    web_backend=args.web_backend,
                    lookback_days=args.lookback_days,
                    internal_subrun=True,
                )
                report.artifacts["resolved"] = resolved_effective
                return report

            entity_reports = fanout.run_competitor_fanout(
                main_topic=topic,
                main_runner=_main_runner,
                competitors=discovered,
                competitor_runner=_competitor_runner,
            )
            if len(entity_reports) < 2:
                progress.end_processing()
                sys.stderr.write(
                    f"[Competitors] 存活的子运行少于 2 个（{len(entity_reports)}）；"
                    "无法渲染对比。请去掉 --competitors 重新运行，或检查上面的警告。\n"
                )
                return 1
            report = entity_reports[0][1]
        else:
            entity_reports = None
            report = _main_runner()
    except Exception as exc:
        progress.end_processing()
        progress.show_error(str(exc))
        raise
    _show_runtime_ui(
        report, progress, diag,
        suppress_web_promo=bool(external_plan or comp_plan),
    )
    _write_last_run(topic, report)
    # LAST30DAYS_STORE env var = persistence default-on. Read both os.environ
    # (for shell-exported users) and config (for users who set it in
    # ~/.config/last30days/.env, which env.py loads but does not propagate
    # to os.environ). Mirrors the LAST30DAYS_DEBUG / LAST30DAYS_SKIP_PREFLIGHT
    # convention; env-var or config wins, with `--store` flag still working.
    _store_env = (
        os.environ.get("LAST30DAYS_STORE")
        or config.get("LAST30DAYS_STORE")
        or ""
    ).lower()
    if args.store or _store_env in ("1", "true", "yes"):
        counts = persist_report(report)
        sys.stderr.write(
            f"[last30days-cn] 已存储 {counts['new']} 条新结论，{counts['updated']} 条更新\n"
        )
        sys.stderr.flush()

    # 研究质量提示（中国版核心源：知乎/微博/B站/V2EX/雪球）。
    try:
        from lib import quality_nudge
        bilibili_items = report.items_by_source.get("bilibili") or []
        xiaohongshu_items = report.items_by_source.get("xiaohongshu") or []
        research_results = {
            "errors_by_source": report.errors_by_source,
            "active_sources": list(report.items_by_source.keys()),
            "bilibili_videos": len(bilibili_items),
            "bilibili_transcripts": sum(
                1 for it in bilibili_items
                if (it.metadata.get("transcript_highlights") or it.metadata.get("transcript_snippet"))
            ),
            "xiaohongshu_count": len(xiaohongshu_items),
        }
        quality = quality_nudge.compute_quality_score(config, research_results)
        if quality.get("nudge_text"):
            sys.stderr.write(f"\n{quality['nudge_text']}\n")
            sys.stderr.flush()
    except Exception:
        pass

    fun_level = config.get("FUN_LEVEL", "medium").lower()
    # Comparison HTML is the one case where the saved file's title and content
    # have to be overridden away from the leading entity's report. Compute the
    # gate once so the footer-display and save-output paths can't disagree.
    is_comparison_html = bool(entity_reports) and args.emit == "html"
    footer_save_path = None
    if args.save_dir:
        save_topic_for_display = comparison_topic(entity_reports) if is_comparison_html else report.topic
        footer_save_path = compute_save_path_display(
            args.save_dir, save_topic_for_display, args.save_suffix or "", args.emit
        )

    # Signal to render_compact whether pre-research flags were supplied.
    # Used to emit a Pre-Research Status warning when the model skipped
    # Step 0.5 / 0.55 and invoked the engine bare on an eligible topic.
    pre_research_flags_present = bool(
        args.weibo_handle
        or args.github_user
        or args.zhihu_topics
        or args.plan
        or args.auto_resolve
        or args.douyin_creators
        or args.xhs_creators
    )
    report.artifacts["pre_research_flags_present"] = pre_research_flags_present

    if entity_reports:
        rendered = emit_comparison_output(
            entity_reports,
            args.emit,
            fun_level=fun_level,
            save_path=footer_save_path,
            synthesis_md=synthesis_md,
        )
    else:
        rendered = emit_output(
            report,
            args.emit,
            fun_level=fun_level,
            save_path=footer_save_path,
            synthesis_md=synthesis_md,
        )
    if args.save_dir:
        # Save the main topic's raw file (single-entity or comparison main).
        save_path = save_output(
            report,
            args.emit,
            args.save_dir,
            suffix=args.save_suffix or "",
            synthesis_md=synthesis_md,
            topic_override=comparison_topic(entity_reports) if is_comparison_html else None,
            rendered_content=rendered if is_comparison_html else None,
        )
        sys.stderr.write(f"[last30days-cn] 已保存输出到 {save_path}\n")
        # Competitor / vs-mode: also save a per-entity raw file for each peer.
        # Matches historical vs-mode behavior (N passes → N save files).
        if entity_reports and len(entity_reports) > 1:
            for label, entity_report in entity_reports[1:]:
                peer_path = save_output(
                    entity_report, args.emit, args.save_dir,
                    suffix=args.save_suffix or "",
                    synthesis_md=synthesis_md,
                )
                sys.stderr.write(f"[last30days-cn] 已保存输出到 {peer_path}\n")
        sys.stderr.flush()
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
