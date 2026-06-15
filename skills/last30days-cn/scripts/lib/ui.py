"""last30days-cn 终端 UI 工具。"""

import sys
import time
import threading
import random
from typing import Optional

from .render import _skill_version

# 判断是否运行在真实终端中（而非被 Claude Code 捕获）
IS_TTY = sys.stderr.isatty()

# ANSI 颜色码
class Colors:
    PURPLE = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RESET = '\033[0m'


BANNER = f"""{Colors.PURPLE}{Colors.BOLD}
  ██╗      █████╗ ███████╗████████╗██████╗  ██████╗ ██████╗  █████╗ ██╗   ██╗███████╗
  ██║     ██╔══██╗██╔════╝╚══██╔══╝╚════██╗██╔═████╗██╔══██╗██╔══██╗╚██╗ ██╔╝██╔════╝
  ██║     ███████║███████╗   ██║    █████╔╝██║██╔██║██║  ██║███████║ ╚████╔╝ ███████╗
  ██║     ██╔══██║╚════██║   ██║    ╚═══██╗████╔╝██║██║  ██║██╔══██║  ╚██╔╝  ╚════██║
  ███████╗██║  ██║███████║   ██║   ██████╔╝╚██████╔╝██████╔╝██║  ██║   ██║   ███████║
  ╚══════╝╚═╝  ╚═╝╚══════╝   ╚═╝   ╚═════╝  ╚═════╝ ╚═════╝ ╚═╝  ╚═╝   ╚═╝   ╚══════╝
{Colors.RESET}{Colors.DIM}  30 天的调研，30 秒搞定。{Colors.RESET}
"""

MINI_BANNER = f"""{Colors.PURPLE}{Colors.BOLD}/last30days-cn{Colors.RESET} {Colors.DIM}· 调研中...{Colors.RESET}"""

# 各阶段的趣味状态文案（按规范数据源命名，见 PORT_CONTRACT §1）
ZHIHU_MESSAGES = [
    "正在翻阅知乎回答...",
    "扫描知乎话题里的精华...",
    "看看知乎用户在聊什么...",
    "翻看高赞讨论...",
    "寻找有价值的回答...",
    "默默点个赞同...",
    "刷一刷评论区...",
]

WEIBO_MESSAGES = [
    "看看微博在热议什么...",
    "刷一刷时间线...",
    "捕捉热搜上的观点...",
    "扫描微博和长文...",
    "发现正在升温的话题...",
    "跟上这波讨论...",
    "在转评赞之间读弦外之音...",
]

ENRICHING_MESSAGES = [
    "挖掘更细的细节...",
    "拉取互动数据...",
    "阅读热门评论...",
    "提炼洞察...",
    "分析讨论内容...",
]

BILIBILI_MESSAGES = [
    "在 B 站搜索相关视频...",
    "寻找相关的视频内容...",
    "扫描 UP 主的投稿...",
    "发现视频里的讨论...",
    "抓取简介与字幕...",
]

DOUYIN_MESSAGES = [
    "在抖音搜索热门视频...",
    "看看抖音上什么在爆...",
    "扫描抖音里的相关内容...",
]

XIAOHONGSHU_MESSAGES = [
    "翻看小红书笔记...",
    "看看小红书在种草什么...",
    "扫描相关的小红书笔记...",
]

FORUM_MESSAGES = [
    "在技术社区里搜索...",
    "扫描 V2EX / 掘金的帖子...",
    "寻找技术讨论...",
    "发现开发者们的对话...",
]

XUEQIU_MESSAGES = [
    "看看雪球上的讨论...",
    "感受一下散户的情绪...",
    "扫描雪球的热门话题...",
    "发现资金在押注什么...",
]

PROCESSING_MESSAGES = [
    "处理数据中...",
    "打分与排序...",
    "寻找模式...",
    "去除重复项...",
    "整理调研结果...",
]

WEB_ONLY_MESSAGES = [
    "正在搜索网页...",
    "寻找博客和文档...",
    "抓取新闻站点...",
    "发现相关教程...",
]

# 完成度展示的源排序与元信息。源标签 + emoji 见 PORT_CONTRACT §7。
SOURCE_COMPLETION_ORDER = [
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

# (展示标签, 单位单数, 单位复数, 颜色)。中文量词不分单复数，单复保持一致。
SOURCE_COMPLETION_META = {
    "weibo": ("微博 🔴", "条", "条", Colors.RED),
    "zhihu": ("知乎 🔵", "条", "条", Colors.BLUE),
    "bilibili": ("B站 📺", "个", "个", Colors.CYAN),
    "douyin": ("抖音 🎵", "个", "个", Colors.PURPLE),
    "xiaohongshu": ("小红书 📕", "篇", "篇", Colors.RED),
    "v2ex": ("V2EX 💻", "帖", "帖", Colors.YELLOW),
    "juejin": ("掘金 ⛏️", "篇", "篇", Colors.YELLOW),
    "github": ("GitHub 🐙", "项", "项", Colors.GREEN),
    "xueqiu": ("雪球 📈", "条", "条", Colors.GREEN),
    "grounding": ("网页 🌐", "条", "条", Colors.GREEN),
}


def _completion_sources(source_counts: dict[str, int], display_sources: list[str] | None) -> list[str]:
    requested = list(dict.fromkeys(display_sources or []))
    if not requested:
        requested = [source for source, count in source_counts.items() if count]
    if not requested and source_counts:
        requested = list(source_counts)

    candidate_set = set(requested) | set(source_counts)
    ordered = [source for source in SOURCE_COMPLETION_ORDER if source in candidate_set]
    for source in requested + list(source_counts):
        if source in candidate_set and source not in ordered:
            ordered.append(source)
    return ordered


def _format_completion_part(source: str, count: int, tty: bool) -> str:
    label, singular, plural, color = SOURCE_COMPLETION_META.get(
        source,
        (source.replace("_", " ").title(), "条", "条", Colors.RESET),
    )
    unit = singular if count == 1 else plural
    if tty:
        return f"{color}{label}:{Colors.RESET} {count} {unit}"
    return f"{label}: {count} {unit}"

def _build_nux_message(diag: dict = None) -> str:
    """构造对话式的首次体验（NUX）文案，动态展示各源状态。"""
    available = set((diag or {}).get("available_sources", []))
    if diag:
        zhihu = "✓" if "zhihu" in available else "✗"
        weibo = "✓" if "weibo" in available else "✗"
        bilibili = "✓" if "bilibili" in available else "✗"
        web = "✓" if "grounding" in available else "✗"
        status_line = f"知乎 {zhihu}，微博 {weibo}，B站 {bilibili}，网页 {web}"
    else:
        status_line = "B站 ✓，掘金 ✓，V2EX ✓，知乎 ✗，微博 ✗"

    return f"""
我刚帮你调研了一下，这是目前能用到的源：

{status_line}

源越多调研越扎实，不过现在这样也够用。你可以免费解锁更多：在浏览器里登录知乎/微博后，把 ZHIHU_COOKIE / WEIBO_COOKIE 填进配置即可。免 key 的 B站、掘金、V2EX、雪球、GitHub 开箱即用。

可以这样玩：
- "last30 大家在聊飞书的什么"
- "last30 每周帮我盯一下最大的竞品"
- "last30 每月看看 AI 视频工具的动向"
- "last30 你都查到了哪些关于 AI 视频的内容？"

直接用 "last30" 开头，像平时聊天一样跟我说就行。
"""

# 针对单个缺失 key 的精简提示
PROMO_SINGLE_KEY = {
    "zhihu": "\n💡 登录知乎后把 ZHIHU_COOKIE（或 d_c0）填进配置即可解锁知乎回答与评论。\n",
    "weibo": "\n💡 登录微博后把 WEIBO_COOKIE 填进配置即可解锁更完整的微博内容。\n",
    "douyin": "\n💡 配置 SCRAPECREATORS_API_KEY 可解锁抖音视频检索 - scrapecreators.com 有免费额度，无需信用卡。\n",
    "web": "\n💡 配置 BRAVE_API_KEY 或 SERPER_API_KEY 可解锁原生联网搜索。\n",
}

# 抓取登录态失败时的帮助文案（针对需要 cookie 的源：微博/知乎）
COOKIE_AUTH_HELP = f"""
{Colors.YELLOW}未能读取到登录态 cookie。{Colors.RESET}

修复方式：
1. 在浏览器里登录微博 / 知乎，然后重新运行（会自动尝试从浏览器抓取）
2. 或手动把 WEIBO_COOKIE / ZHIHU_COOKIE 填进 ~/.config/last30days/.env 或 .claude/last30days.env
"""

COOKIE_AUTH_HELP_PLAIN = """
未能读取到登录态 cookie。

修复方式：
1. 在浏览器里登录微博 / 知乎，然后重新运行（会自动尝试从浏览器抓取）
2. 或手动把 WEIBO_COOKIE / ZHIHU_COOKIE 填进 ~/.config/last30days/.env 或 .claude/last30days.env
"""

# 旋转动画帧
SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
DOTS_FRAMES = ['   ', '.  ', '.. ', '...']


class Spinner:
    """长耗时操作的动画旋转指示器。"""

    def __init__(self, message: str = "处理中", color: str = Colors.CYAN, quiet: bool = False):
        self.message = message
        self.color = color
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.frame_idx = 0
        self.shown_static = False
        self.quiet = quiet  # 非 TTY 下抑制开始提示（仍会显示 ✓ 完成提示）

    def _spin(self):
        while self.running:
            frame = SPINNER_FRAMES[self.frame_idx % len(SPINNER_FRAMES)]
            sys.stderr.write(f"\r{self.color}{frame}{Colors.RESET} {self.message}  ")
            sys.stderr.flush()
            self.frame_idx += 1
            time.sleep(0.08)

    def start(self):
        self.running = True
        if IS_TTY:
            # 真实终端 - 播放动画
            self.thread = threading.Thread(target=self._spin, daemon=True)
            self.thread.start()
        else:
            # 非 TTY（Claude Code）- 只打印一次
            if not self.shown_static and not self.quiet:
                sys.stderr.write(f"⏳ {self.message}\n")
                sys.stderr.flush()
                self.shown_static = True

    def update(self, message: str):
        self.message = message
        if not IS_TTY and not self.shown_static:
            # 非 TTY 模式下打印更新
            sys.stderr.write(f"⏳ {message}\n")
            sys.stderr.flush()

    def stop(self, final_message: str = ""):
        self.running = False
        if self.thread:
            self.thread.join(timeout=0.2)
        if IS_TTY:
            # 真实终端中清空当前行
            sys.stderr.write("\r" + " " * 80 + "\r")
        if final_message:
            sys.stderr.write(f"✓ {final_message}\n")
        sys.stderr.flush()


class ProgressDisplay:
    """调研各阶段的进度展示。"""

    def __init__(self, topic: str, show_banner: bool = True):
        self.topic = topic
        self.spinner: Optional[Spinner] = None
        self.start_time = time.time()

        if show_banner:
            self._show_banner()

    def _show_banner(self):
        if IS_TTY:
            sys.stderr.write(MINI_BANNER + "\n")
            sys.stderr.write(f"{Colors.DIM}话题：{Colors.RESET}{Colors.BOLD}{self.topic}{Colors.RESET}\n\n")
        else:
            # 非 TTY 下的简洁文本
            sys.stderr.write(f"/last30days-cn · 调研中：{self.topic}\n")
        sys.stderr.flush()

    def start_zhihu(self):
        msg = random.choice(ZHIHU_MESSAGES)
        self.spinner = Spinner(f"{Colors.BLUE}知乎{Colors.RESET} {msg}", Colors.BLUE)
        self.spinner.start()

    def end_zhihu(self, count: int):
        if self.spinner:
            self.spinner.stop(f"{Colors.BLUE}知乎{Colors.RESET} 找到 {count} 条回答")

    def start_zhihu_enrich(self, current: int, total: int):
        if self.spinner:
            self.spinner.stop()
        msg = random.choice(ENRICHING_MESSAGES)
        self.spinner = Spinner(f"{Colors.BLUE}知乎{Colors.RESET} [{current}/{total}] {msg}", Colors.BLUE)
        self.spinner.start()

    def update_zhihu_enrich(self, current: int, total: int):
        if self.spinner:
            msg = random.choice(ENRICHING_MESSAGES)
            self.spinner.update(f"{Colors.BLUE}知乎{Colors.RESET} [{current}/{total}] {msg}")

    def end_zhihu_enrich(self):
        if self.spinner:
            self.spinner.stop(f"{Colors.BLUE}知乎{Colors.RESET} 已补充互动数据")

    def start_weibo(self):
        msg = random.choice(WEIBO_MESSAGES)
        self.spinner = Spinner(f"{Colors.RED}微博{Colors.RESET} {msg}", Colors.RED)
        self.spinner.start()

    def end_weibo(self, count: int):
        if self.spinner:
            self.spinner.stop(f"{Colors.RED}微博{Colors.RESET} 找到 {count} 条")

    def start_bilibili(self):
        msg = random.choice(BILIBILI_MESSAGES)
        self.spinner = Spinner(f"{Colors.CYAN}B站{Colors.RESET} {msg}", Colors.CYAN)
        self.spinner.start()

    def end_bilibili(self, count: int):
        if self.spinner:
            self.spinner.stop(f"{Colors.CYAN}B站{Colors.RESET} 找到 {count} 个视频")

    def start_douyin(self):
        msg = random.choice(DOUYIN_MESSAGES)
        self.spinner = Spinner(f"{Colors.PURPLE}抖音{Colors.RESET} {msg}", Colors.PURPLE)
        self.spinner.start()

    def end_douyin(self, count: int):
        if self.spinner:
            self.spinner.stop(f"{Colors.PURPLE}抖音{Colors.RESET} 找到 {count} 个视频")

    def start_xiaohongshu(self):
        msg = random.choice(XIAOHONGSHU_MESSAGES)
        self.spinner = Spinner(f"{Colors.RED}小红书{Colors.RESET} {msg}", Colors.RED)
        self.spinner.start()

    def end_xiaohongshu(self, count: int):
        if self.spinner:
            self.spinner.stop(f"{Colors.RED}小红书{Colors.RESET} 找到 {count} 篇笔记")

    def start_v2ex(self):
        msg = random.choice(FORUM_MESSAGES)
        self.spinner = Spinner(f"{Colors.YELLOW}V2EX{Colors.RESET} {msg}", Colors.YELLOW, quiet=True)
        self.spinner.start()

    def end_v2ex(self, count: int):
        if self.spinner:
            self.spinner.stop(f"{Colors.YELLOW}V2EX{Colors.RESET} 找到 {count} 帖")

    def start_juejin(self):
        msg = random.choice(FORUM_MESSAGES)
        self.spinner = Spinner(f"{Colors.YELLOW}掘金{Colors.RESET} {msg}", Colors.YELLOW, quiet=True)
        self.spinner.start()

    def end_juejin(self, count: int):
        if self.spinner:
            self.spinner.stop(f"{Colors.YELLOW}掘金{Colors.RESET} 找到 {count} 篇")

    def start_xueqiu(self):
        msg = random.choice(XUEQIU_MESSAGES)
        self.spinner = Spinner(f"{Colors.GREEN}雪球{Colors.RESET} {msg}", Colors.GREEN, quiet=True)
        self.spinner.start()

    def end_xueqiu(self, count: int):
        if self.spinner:
            self.spinner.stop(f"{Colors.GREEN}雪球{Colors.RESET} 找到 {count} 条讨论")

    def start_processing(self):
        msg = random.choice(PROCESSING_MESSAGES)
        self.spinner = Spinner(f"{Colors.PURPLE}处理{Colors.RESET} {msg}", Colors.PURPLE)
        self.spinner.start()

    def end_processing(self):
        if self.spinner:
            self.spinner.stop()

    # ---------------------------------------------------------------------
    # 向后兼容别名：保留参考项目（西方源）的方法名，转调到对应的中国源，
    # 这样无论 pipeline.py 是否已按 PORT_CONTRACT §8 改名都不会报错。
    # ---------------------------------------------------------------------
    def start_reddit(self):
        self.start_zhihu()

    def end_reddit(self, count: int):
        self.end_zhihu(count)

    def start_reddit_enrich(self, current: int, total: int):
        self.start_zhihu_enrich(current, total)

    def update_reddit_enrich(self, current: int, total: int):
        self.update_zhihu_enrich(current, total)

    def end_reddit_enrich(self):
        self.end_zhihu_enrich()

    def start_x(self):
        self.start_weibo()

    def end_x(self, count: int):
        self.end_weibo(count)

    def start_youtube(self):
        self.start_bilibili()

    def end_youtube(self, count: int):
        self.end_bilibili(count)

    def start_tiktok(self):
        self.start_douyin()

    def end_tiktok(self, count: int):
        self.end_douyin(count)

    def start_instagram(self):
        self.start_xiaohongshu()

    def end_instagram(self, count: int):
        self.end_xiaohongshu(count)

    def start_hackernews(self):
        self.start_v2ex()

    def end_hackernews(self, count: int):
        self.end_v2ex(count)

    def start_polymarket(self):
        self.start_xueqiu()

    def end_polymarket(self, count: int):
        self.end_xueqiu(count)

    def show_complete(
        self,
        weibo_count: int = 0,
        zhihu_count: int = 0,
        bilibili_count: int = 0,
        v2ex_count: int = 0,
        xueqiu_count: int = 0,
        douyin_count: int = 0,
        xiaohongshu_count: int = 0,
        *,
        source_counts: dict[str, int] | None = None,
        display_sources: list[str] | None = None,
    ):
        elapsed = time.time() - self.start_time
        if source_counts is None:
            source_counts = {
                "weibo": weibo_count,
                "zhihu": zhihu_count,
                "bilibili": bilibili_count,
                "douyin": douyin_count,
                "xiaohongshu": xiaohongshu_count,
                "v2ex": v2ex_count,
                "xueqiu": xueqiu_count,
            }
            if display_sources is None:
                display_sources = [source for source, count in source_counts.items() if count]
                if not display_sources:
                    display_sources = ["zhihu", "weibo"]

        ordered_sources = _completion_sources(source_counts, display_sources)
        parts = [
            _format_completion_part(source, source_counts.get(source, 0), tty=IS_TTY)
            for source in ordered_sources
        ]
        if IS_TTY:
            sys.stderr.write(f"\n{Colors.GREEN}{Colors.BOLD}✓ 调研完成{Colors.RESET} ")
            sys.stderr.write(f"{Colors.DIM}({elapsed:.1f}s){Colors.RESET}\n")
            sys.stderr.write("  " + "  ".join(parts))
            sys.stderr.write("\n\n")
        else:
            sys.stderr.write(f"✓ 调研完成 ({elapsed:.1f}s) - {', '.join(parts)}\n")
        sys.stderr.flush()

    def show_cached(self, age_hours: float = None):
        if age_hours is not None:
            age_str = f"（{age_hours:.1f} 小时前）"
        else:
            age_str = ""
        sys.stderr.write(f"{Colors.GREEN}⚡{Colors.RESET} {Colors.DIM}使用缓存结果{age_str} - 加 --refresh 可拉取最新数据{Colors.RESET}\n\n")
        sys.stderr.flush()

    def show_error(self, message: str):
        sys.stderr.write(f"{Colors.RED}✗ 错误：{Colors.RESET} {message}\n")
        sys.stderr.flush()

    def start_web_only(self):
        """展示仅联网模式指示。"""
        msg = random.choice(WEB_ONLY_MESSAGES)
        self.spinner = Spinner(f"{Colors.GREEN}网页{Colors.RESET} {msg}", Colors.GREEN)
        self.spinner.start()

    def end_web_only(self):
        """结束仅联网模式的旋转动画。"""
        if self.spinner:
            self.spinner.stop(f"{Colors.GREEN}网页{Colors.RESET} 助手将进行联网搜索")

    def show_web_only_complete(self):
        """展示仅联网模式的完成提示。"""
        elapsed = time.time() - self.start_time
        if IS_TTY:
            sys.stderr.write(f"\n{Colors.GREEN}{Colors.BOLD}✓ 已就绪，可联网搜索{Colors.RESET} ")
            sys.stderr.write(f"{Colors.DIM}({elapsed:.1f}s){Colors.RESET}\n")
            sys.stderr.write(f"  {Colors.GREEN}网页：{Colors.RESET}助手将搜索博客、文档与新闻\n\n")
        else:
            sys.stderr.write(f"✓ 已就绪，可联网搜索 ({elapsed:.1f}s)\n")
        sys.stderr.flush()

    def show_promo(self, missing: str = "both", diag: dict = None):
        """展示缺失 API key 时的 NUX / 推广文案。

        Args:
            missing: 'both' / 'all' 展示完整 NUX；或单个源 key（'zhihu'/'weibo'/'douyin'/'web'）
            diag: 可选的诊断 dict，用于动态展示各源状态
        """
        if missing in ("both", "all"):
            sys.stderr.write(_build_nux_message(diag))
        elif missing in PROMO_SINGLE_KEY:
            sys.stderr.write(PROMO_SINGLE_KEY[missing])
        sys.stderr.flush()

    def show_cookie_auth_help(self):
        """展示登录态 cookie 抓取失败时的帮助。"""
        if IS_TTY:
            sys.stderr.write(COOKIE_AUTH_HELP)
        else:
            sys.stderr.write(COOKIE_AUTH_HELP_PLAIN)
        sys.stderr.flush()

    # 向后兼容别名：保留旧的 Bird 帮助方法名，转调到 cookie 帮助。
    def show_bird_auth_help(self):
        self.show_cookie_auth_help()


def show_diagnostic_banner(diag: dict):
    """当部分源不可用时，展示预检的源状态横幅。

    Args:
        diag: 来自 pipeline.diagnose() 的 dict，含 available_sources、
            has_scrapecreators、native_web_backend 等字段。
    """
    available_sources = set(diag.get("available_sources") or [])
    has_zhihu = "zhihu" in available_sources
    has_weibo = "weibo" in available_sources
    has_bilibili = "bilibili" in available_sources
    has_scrapecreators = diag.get("has_scrapecreators", False)
    has_douyin = "douyin" in available_sources
    has_xiaohongshu = "xiaohongshu" in available_sources
    has_web = "grounding" in available_sources
    native_web_backend = diag.get("native_web_backend")

    # 若核心源都已就绪，无需横幅
    if has_zhihu and has_weibo and has_bilibili and has_web:
        return

    lines = []

    if IS_TTY:
        lines.append(f"{Colors.DIM}┌─────────────────────────────────────────────────────┐{Colors.RESET}")
        _header = f"/last30days-cn v{_skill_version()} - 源状态"
        lines.append(f"{Colors.DIM}│{Colors.RESET} {Colors.BOLD}{_header}{Colors.RESET}{' ' * max(0, 52 - len(_header))}{Colors.DIM}│{Colors.RESET}")
        lines.append(f"{Colors.DIM}│{Colors.RESET}                                                     {Colors.DIM}│{Colors.RESET}")

        # 知乎
        if has_zhihu:
            lines.append(f"{Colors.DIM}│{Colors.RESET}  {Colors.GREEN}✅ 知乎{Colors.RESET}    - 回答与评论（已配置 cookie）        {Colors.DIM}│{Colors.RESET}")
        else:
            lines.append(f"{Colors.DIM}│{Colors.RESET}  {Colors.YELLOW}⚡ 知乎{Colors.RESET}    - 配置 ZHIHU_COOKIE 后可用          {Colors.DIM}│{Colors.RESET}")

        # 微博
        if has_weibo:
            lines.append(f"{Colors.DIM}│{Colors.RESET}  {Colors.GREEN}✅ 微博{Colors.RESET}    - 可用                              {Colors.DIM}│{Colors.RESET}")
        else:
            lines.append(f"{Colors.DIM}│{Colors.RESET}  {Colors.YELLOW}⚡ 微博{Colors.RESET}    - 配置 WEIBO_COOKIE 后更完整        {Colors.DIM}│{Colors.RESET}")

        # B站（免 key，默认可用）
        if has_bilibili:
            lines.append(f"{Colors.DIM}│{Colors.RESET}  {Colors.GREEN}✅ B站{Colors.RESET}     - 免 key 公开接口                   {Colors.DIM}│{Colors.RESET}")
        else:
            lines.append(f"{Colors.DIM}│{Colors.RESET}  {Colors.RED}❌ B站{Colors.RESET}     - 暂不可用                          {Colors.DIM}│{Colors.RESET}")

        # 抖音（需 SCRAPECREATORS）
        if has_douyin:
            lines.append(f"{Colors.DIM}│{Colors.RESET}  {Colors.GREEN}✅ 抖音{Colors.RESET}    - ScrapeCreators 已配置             {Colors.DIM}│{Colors.RESET}")
        elif not has_scrapecreators:
            lines.append(f"{Colors.DIM}│{Colors.RESET}  {Colors.YELLOW}⚡ 抖音{Colors.RESET}    - 配置 SCRAPECREATORS_API_KEY 后可用 {Colors.DIM}│{Colors.RESET}")

        # 小红书（仅在已配置时展示）
        if has_xiaohongshu:
            lines.append(f"{Colors.DIM}│{Colors.RESET}  {Colors.GREEN}✅ 小红书{Colors.RESET}  - API 已连接且已登录                {Colors.DIM}│{Colors.RESET}")

        # 网页
        if has_web:
            backend = native_web_backend or "native"
            lines.append(f"{Colors.DIM}│{Colors.RESET}  {Colors.GREEN}✅ 网页{Colors.RESET}    - {backend} API                       {Colors.DIM}│{Colors.RESET}")
        else:
            lines.append(f"{Colors.DIM}│{Colors.RESET}  {Colors.YELLOW}⚡ 网页{Colors.RESET}    - 配置 BRAVE_API_KEY 或 SERPER_API_KEY {Colors.DIM}│{Colors.RESET}")

        lines.append(f"{Colors.DIM}│{Colors.RESET}                                                     {Colors.DIM}│{Colors.RESET}")
        lines.append(f"{Colors.DIM}│{Colors.RESET}  配置文件：{Colors.BOLD}~/.config/last30days/.env{Colors.RESET}             {Colors.DIM}│{Colors.RESET}")
        lines.append(f"{Colors.DIM}└─────────────────────────────────────────────────────┘{Colors.RESET}")
    else:
        # 非 TTY（Claude Code / Codex）的纯文本
        lines.append("┌─────────────────────────────────────────────────────┐")
        _header_plain = f"/last30days-cn v{_skill_version()} - 源状态"
        lines.append(f"│ {_header_plain}{' ' * max(0, 52 - len(_header_plain))}│")
        lines.append("│                                                     │")

        if has_zhihu:
            lines.append("│  ✅ 知乎    - 回答与评论（已配置 cookie）           │")
        else:
            lines.append("│  ⚡ 知乎    - 配置 ZHIHU_COOKIE 后可用              │")

        if has_weibo:
            lines.append("│  ✅ 微博    - 可用                                   │")
        else:
            lines.append("│  ⚡ 微博    - 配置 WEIBO_COOKIE 后更完整            │")

        if has_bilibili:
            lines.append("│  ✅ B站     - 免 key 公开接口                       │")
        else:
            lines.append("│  ❌ B站     - 暂不可用                              │")

        if has_douyin:
            lines.append("│  ✅ 抖音    - ScrapeCreators 已配置                 │")
        elif not has_scrapecreators:
            lines.append("│  ⚡ 抖音    - 配置 SCRAPECREATORS_API_KEY 后可用    │")

        if has_xiaohongshu:
            lines.append("│  ✅ 小红书  - API 已连接且已登录                    │")

        if has_web:
            backend = native_web_backend or "native"
            lines.append(f"│  ✅ 网页    - {backend} API 可用{' ' * max(0, 13 - len(backend))}│")
        else:
            lines.append("│  ⚡ 网页    - 配置 BRAVE_API_KEY 或 SERPER_API_KEY  │")

        lines.append("│                                                     │")
        lines.append("│  配置文件：~/.config/last30days/.env                │")
        lines.append("└─────────────────────────────────────────────────────┘")

    sys.stderr.write("\n".join(lines) + "\n\n")
    sys.stderr.flush()


def print_phase(phase: str, message: str):
    """打印一条阶段消息。"""
    colors = {
        "zhihu": Colors.BLUE,
        "weibo": Colors.RED,
        "process": Colors.PURPLE,
        "done": Colors.GREEN,
        "error": Colors.RED,
        # 向后兼容旧的阶段键
        "reddit": Colors.BLUE,
        "x": Colors.RED,
    }
    color = colors.get(phase, Colors.RESET)
    sys.stderr.write(f"{color}▸{Colors.RESET} {message}\n")
    sys.stderr.flush()
