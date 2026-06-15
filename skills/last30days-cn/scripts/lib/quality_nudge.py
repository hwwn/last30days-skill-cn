"""研究结束后的质量评分与升级提示（中国版）。

按 5 个核心源计算质量分，并生成一段中文提示，告诉用户漏了什么、怎么免费补上。

核心源（中国版）：知乎、微博、B站、V2EX、雪球。
- B站 / V2EX / 雪球：免 key，开箱即用（除非本次报错）。
- 知乎 / 微博：需登录态 cookie（ZHIHU_COOKIE / WEIBO_COOKIE）或付费爬虫 key 才算激活。
- 抖音 / 小红书：附加源，配 SCRAPECREATORS_API_KEY / XIAOHONGSHU_API_BASE 解锁。
"""

from typing import List

# 5 个核心源
CORE_SOURCES = ["zhihu", "weibo", "bilibili", "v2ex", "xueqiu"]

# 展示用中文标签
SOURCE_LABELS = {
    "zhihu": "知乎",
    "weibo": "微博",
    "bilibili": "B站",
    "v2ex": "V2EX",
    "xueqiu": "雪球",
    "juejin": "掘金",
    "github": "GitHub",
    "douyin": "抖音",
    "xiaohongshu": "小红书",
}

# 字幕/简介抓取比例低于此值时，B站视为「退化」而非「激活」。
DEFAULT_DEGRADED_TRANSCRIPT_THRESHOLD = 0.5


def _is_zhihu_active(config: dict, errors: dict) -> bool:
    has_creds = bool(config.get("ZHIHU_COOKIE") or config.get("SCRAPECREATORS_API_KEY"))
    return has_creds and not errors.get("zhihu")


def _is_weibo_active(config: dict, errors: dict) -> bool:
    has_creds = bool(config.get("WEIBO_COOKIE") or config.get("SCRAPECREATORS_API_KEY"))
    return has_creds and not errors.get("weibo")


def _is_bilibili_degraded(research_results: dict, threshold: float) -> bool:
    """B站返回了视频但简介/字幕抓取比例过低时视为退化（通常是接口签名或风控所致）。"""
    videos = int(research_results.get("bilibili_videos") or 0)
    transcripts = int(research_results.get("bilibili_transcripts") or 0)
    if videos <= 0:
        return False
    return (transcripts / videos) < threshold


def compute_quality_score(config: dict, research_results: dict) -> dict:
    """按 5 个核心源计算研究质量分。

    Args:
        config: env.get_config() 返回的配置
        research_results: 含 errors_by_source(dict)、active_sources(list)、
            bilibili_videos、bilibili_transcripts、xiaohongshu_count 等键。

    Returns:
        {score_pct, core_active, core_missing, core_errored, core_degraded,
         bonus_errored, nudge_text}
    """
    errors = research_results.get("errors_by_source") or {}
    core_active: List[str] = []
    core_missing: List[str] = []
    core_errored: List[str] = []
    core_degraded: List[str] = []
    bonus_errored: List[str] = []

    # B站 / V2EX / 雪球：免 key，默认激活，除非本次报错
    for src in ("bilibili", "v2ex", "xueqiu"):
        if errors.get(src):
            core_missing.append(src)
            core_errored.append(src)
        else:
            core_active.append(src)

    # 知乎
    if _is_zhihu_active(config, errors):
        core_active.append("zhihu")
    else:
        core_missing.append("zhihu")
        if (config.get("ZHIHU_COOKIE") or config.get("SCRAPECREATORS_API_KEY")) and errors.get("zhihu"):
            core_errored.append("zhihu")

    # 微博
    if _is_weibo_active(config, errors):
        core_active.append("weibo")
    else:
        core_missing.append("weibo")
        if (config.get("WEIBO_COOKIE") or config.get("SCRAPECREATORS_API_KEY")) and errors.get("weibo"):
            core_errored.append("weibo")

    # B站退化检测（已激活时）
    if "bilibili" in core_active:
        threshold = float(
            config.get("DEGRADED_TRANSCRIPT_THRESHOLD") or DEFAULT_DEGRADED_TRANSCRIPT_THRESHOLD
        )
        if _is_bilibili_degraded(research_results, threshold):
            core_degraded.append("bilibili")

    # 附加源静默失败：配了 SCRAPECREATORS 但小红书 0 条
    if config.get("SCRAPECREATORS_API_KEY") or config.get("XIAOHONGSHU_API_BASE"):
        count = research_results.get("xiaohongshu_count")
        if count is not None and int(count) == 0 and not errors.get("xiaohongshu"):
            bonus_errored.append("xiaohongshu")

    score_pct = int(len(core_active) / len(CORE_SOURCES) * 100)

    nudge_text = (
        _build_nudge_text(config, core_missing, core_errored, core_degraded, bonus_errored)
        if (core_missing or core_degraded or bonus_errored)
        else None
    )

    return {
        "score_pct": score_pct,
        "core_active": core_active,
        "core_missing": core_missing,
        "core_errored": core_errored,
        "core_degraded": core_degraded,
        "bonus_errored": bonus_errored,
        "nudge_text": nudge_text,
    }


def _build_nudge_text(
    config: dict,
    core_missing: List[str],
    core_errored: List[str],
    core_degraded: List[str],
    bonus_errored: List[str],
) -> str:
    lines: List[str] = []

    active_count = len(CORE_SOURCES) - len(core_missing)
    lines.append(f"研究质量：{active_count}/{len(CORE_SOURCES)} 个核心源。")

    missed_parts: List[str] = []
    for src in core_missing:
        label = SOURCE_LABELS.get(src, src)
        missed_parts.append(f"{label}（本次报错）" if src in core_errored else label)
    if missed_parts:
        lines.append("缺少：" + "、".join(missed_parts) + "。")
    if core_degraded:
        lines.append("数据偏薄：" + "、".join(SOURCE_LABELS.get(s, s) for s in core_degraded) + "。")
    if bonus_errored:
        lines.append("附加源静默无结果：" + "、".join(SOURCE_LABELS.get(s, s) for s in bonus_errored) + "。")
    lines.append("")

    fixes: List[str] = []
    if "zhihu" in core_missing:
        fixes.append(
            "知乎：高赞回答与评论是中文深度讨论的主场。浏览器登录知乎后，"
            "把 ZHIHU_COOKIE（或 d_c0）填进 .env 即可解锁。"
        )
    if "weibo" in core_missing:
        fixes.append(
            "微博：实时帖子与转评赞，热点话题最快的信号源。浏览器登录微博后，"
            "把 WEIBO_COOKIE 填进 .env 即可解锁。"
        )
    for src in ("bilibili", "v2ex", "xueqiu"):
        if src in core_errored:
            fixes.append(f"{SOURCE_LABELS[src]} 本次报错，多为接口限流，稍后重试通常即可恢复。")
    if "bilibili" in core_degraded:
        fixes.append(
            "B站返回了视频但简介/字幕抓取偏少，通常是搜索接口 wbi 签名或风控所致；"
            "配置 B站 登录 cookie 可提升抓取成功率。"
        )
    if "xiaohongshu" in bonus_errored:
        fixes.append(
            "小红书配了爬虫 key 却 0 条：换更短、更具体的关键词（如最有辨识度的名词）再试。"
        )

    if fixes:
        lines.append("免费/低成本补强：")
        for f in fixes:
            lines.append(f"  - {f}")
        lines.append("")

    if not (config.get("SCRAPECREATORS_API_KEY") or config.get("XIAOHONGSHU_API_BASE")):
        lines.append(
            "附加：抖音、小红书可通过 SCRAPECREATORS_API_KEY 或本地 XIAOHONGSHU_API_BASE 服务解锁。"
        )
    lines.append("（本工具与任何 API 提供方无利益关系。）")

    return "\n".join(lines)
