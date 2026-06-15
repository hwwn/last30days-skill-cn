"""last30days-cn 首次运行向导。

检测首次运行、执行自动配置（从浏览器抓取登录态 cookie + 检查 yt-dlp），
并写入配置。真正的向导交互界面由 SKILL.md 驱动（由大模型呈现），
本模块只提供检测与配置动作。
"""

import json
import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


def is_first_run(config: Dict[str, Any]) -> bool:
    """若尚未完成配置向导则返回 True。

    检查配置 dict 中的 SETUP_COMPLETE。若未设置（None 或空字符串），
    说明用户还没走过首次配置流程。
    """
    return not config.get("SETUP_COMPLETE")


def run_auto_setup(config: Dict[str, Any]) -> Dict[str, Any]:
    """执行自动配置动作。

    - 对所有已登记的域名以 auto 模式运行 cookie 抓取（微博 / 知乎登录态）
    - 检查是否已安装 yt-dlp（用于 B站等视频字幕）

    Returns:
        Dict，包含以下键：
          cookies_found: {源名: 浏览器名}，记录在哪个浏览器抓到了该源的 cookie
          ytdlp_installed: bool
          env_written: bool（这里恒为 False —— 由调用方单独写配置）
    """
    from . import cookie_extract
    from .env import COOKIE_DOMAINS

    cookies_found: Dict[str, str] = {}

    for source_name, spec in COOKIE_DOMAINS.items():
        domain = spec["domain"]
        cookie_names = spec["cookies"]

        try:
            result = cookie_extract.extract_cookies_with_source("auto", domain, cookie_names)
        except Exception as exc:
            logger.debug("Cookie extraction failed for %s: %s", source_name, exc)
            continue

        if result is not None:
            _cookies, browser_name = result
            cookies_found[source_name] = browser_name

    # 检查 yt-dlp 是否可用，缺失则尝试用 Homebrew 安装
    ytdlp_action: str
    if shutil.which("yt-dlp") is not None:
        ytdlp_installed = True
        ytdlp_action = "already_installed"
    elif shutil.which("brew") is not None:
        brew_stderr = ""
        try:
            proc = subprocess.run(
                ["brew", "install", "yt-dlp"],
                capture_output=True, text=True, timeout=120,
            )
            if proc.returncode == 0:
                ytdlp_installed = True
                ytdlp_action = "installed"
            else:
                ytdlp_installed = False
                ytdlp_action = "install_failed"
                brew_stderr = proc.stderr
                logger.warning("brew install yt-dlp failed: %s", proc.stderr)
        except Exception as exc:
            ytdlp_installed = False
            ytdlp_action = "install_failed"
            brew_stderr = str(exc)
            logger.warning("brew install yt-dlp exception: %s", exc)
    else:
        ytdlp_installed = False
        ytdlp_action = "no_homebrew"

    results: Dict[str, Any] = {
        "cookies_found": cookies_found,
        "ytdlp_installed": ytdlp_installed,
        "ytdlp_action": ytdlp_action,
        "env_written": False,
    }
    if ytdlp_action == "install_failed":
        results["ytdlp_stderr"] = brew_stderr
    return results


def write_setup_config(env_path: Path, from_browser: str = "auto") -> bool:
    """把 SETUP_COMPLETE 与 FROM_BROWSER 写入 .env 文件。

    必要时创建文件与父目录。
    追加写入已有文件，不覆盖既有的键。

    Args:
        env_path: .env 文件路径（如 ~/.config/last30days/.env）
        from_browser: 要写入的浏览器抓取模式（默认 "auto"）

    Returns:
        写入成功返回 True，出错返回 False。
    """
    try:
        env_path = Path(env_path)
        env_path.parent.mkdir(parents=True, exist_ok=True)

        # 先读取已有内容，避免覆盖已有的键
        existing_keys: set = set()
        existing_content = ""
        if env_path.exists():
            existing_content = env_path.read_text(encoding="utf-8")
            for line in existing_content.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    key = stripped.split("=", 1)[0].strip()
                    existing_keys.add(key)

        lines_to_add = []
        if "SETUP_COMPLETE" not in existing_keys:
            lines_to_add.append("SETUP_COMPLETE=true")
        if "FROM_BROWSER" not in existing_keys:
            lines_to_add.append(f"FROM_BROWSER={from_browser}")

        if not lines_to_add:
            return True  # 无需写入，已配置完成

        # 追加前确保末尾有换行
        with open(env_path, "a", encoding="utf-8") as f:
            if existing_content and not existing_content.endswith("\n"):
                f.write("\n")
            f.write("\n".join(lines_to_add) + "\n")

        return True

    except OSError as exc:
        logger.error("Failed to write setup config to %s: %s", env_path, exc)
        return False


# 源名到中文展示名的映射（用于自动配置结果文案）
_SOURCE_DISPLAY = {
    "weibo": "微博",
    "zhihu": "知乎",
    "bilibili": "B站",
    "douyin": "抖音",
    "xiaohongshu": "小红书",
    "v2ex": "V2EX",
    "juejin": "掘金",
    "github": "GitHub",
    "xueqiu": "雪球",
}


def get_setup_status_text(results: Dict[str, Any]) -> str:
    """返回自动配置结果的可读摘要。

    Args:
        results: run_auto_setup() 返回的 dict

    Returns:
        多行状态文本。
    """
    lines = []
    lines.append("配置完成！我检测到这些：")
    lines.append("")

    cookies_found = results.get("cookies_found", {})
    if cookies_found:
        for source, browser in cookies_found.items():
            display = _SOURCE_DISPLAY.get(source, source)
            lines.append(f"  - 在 {browser} 中找到了 {display} 的登录态 cookie")
    else:
        lines.append("  - 未在浏览器中找到微博 / 知乎的登录态 cookie")

    ytdlp_action = results.get("ytdlp_action", "")
    if ytdlp_action == "installed":
        lines.append("  - 已通过 Homebrew 安装 yt-dlp（用于 B站等视频字幕）")
    elif ytdlp_action == "install_failed":
        lines.append("  - yt-dlp 安装失败 — 请手动执行 `brew install yt-dlp`")
    elif ytdlp_action == "no_homebrew":
        lines.append("  - 未找到 yt-dlp。请先安装 Homebrew，再执行：brew install yt-dlp")
    elif ytdlp_action == "already_installed":
        lines.append("  - yt-dlp 已安装")
    elif results.get("ytdlp_installed", False):
        lines.append("  - yt-dlp 已安装（视频字幕检索就绪）")
    else:
        lines.append("  - 未找到 yt-dlp（安装方式：brew install yt-dlp）")

    env_written = results.get("env_written", False)
    if env_written:
        lines.append("")
        lines.append("配置已保存。后续运行会自动从你的浏览器检测登录态。")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 服务端配置探测（无浏览器，JSON 输出）
# ---------------------------------------------------------------------------

_SERVER_KEY_NAMES = [
    "SCRAPECREATORS_API_KEY",
    "BRAVE_API_KEY",
    "EXA_API_KEY",
    "SERPER_API_KEY",
    "PARALLEL_API_KEY",
    "WEIBO_COOKIE",
    "ZHIHU_COOKIE",
    "GITHUB_TOKEN",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "MOONSHOT_API_KEY",
    "ZHIPU_API_KEY",
]


def run_server_setup(config: Dict[str, Any]) -> Dict[str, Any]:
    """服务端配置探测：不取 cookie，只检查工具与 key 是否就绪。

    返回一个适合作为 JSON 输出到 stdout 的 dict，便于 SKILL.md
    据此向用户呈现合适的选项。
    """
    yt_dlp = shutil.which("yt-dlp") is not None
    node = shutil.which("node") is not None
    python3 = shutil.which("python3") is not None

    keys: Dict[str, bool] = {}
    for key_name in _SERVER_KEY_NAMES:
        # 归一化键名：SCRAPECREATORS_API_KEY -> scrapecreators，WEIBO_COOKIE -> weibo
        short = (
            key_name.lower()
            .replace("_api_key", "")
            .replace("_key", "")
            .replace("_token", "")
            .replace("_cookie", "")
        )
        keys[short] = bool(config.get(key_name))

    # 判断需要登录态的社交源各自是否就绪（取代参考项目的 x_method）
    cookie_sources = {
        "weibo": bool(config.get("WEIBO_COOKIE") or config.get("SCRAPECREATORS_API_KEY")),
        "zhihu": bool(config.get("ZHIHU_COOKIE") or config.get("SCRAPECREATORS_API_KEY")),
        "douyin": bool(config.get("SCRAPECREATORS_API_KEY")),
    }

    return {
        "yt_dlp": yt_dlp,
        "node": node,
        "python3": python3,
        "keys": keys,
        "cookie_sources": cookie_sources,
    }


# 向后兼容别名：保留参考项目的函数/键名，转调到 CN 版实现，
# 避免尚未按 PORT_CONTRACT §10 改名的 CLI 调用报错。
_OPENCLAW_KEY_NAMES = _SERVER_KEY_NAMES


def run_openclaw_setup(config: Dict[str, Any]) -> Dict[str, Any]:
    """向后兼容别名 -> run_server_setup（见 PORT_CONTRACT §10）。"""
    return run_server_setup(config)


# ---------------------------------------------------------------------------
# PAT 授权流程（通过 ScrapeCreators 用 GitHub token 换 API key）
# ---------------------------------------------------------------------------

_PAT_BASE = "https://api.scrapecreators.com/v1/github/pat"


def auth_with_pat(github_token: str) -> Optional[Dict[str, Any]]:
    """用 GitHub PAT 向 ScrapeCreators 认证。

    把 token POST 到 PAT 认证端点。ScrapeCreators 会用 GitHub API 校验，
    创建/查找账户，并返回一个 API key。

    Returns:
        成功返回含 api_key、github_username 等字段的 dict，失败返回 None。
    """
    try:
        req = Request(f"{_PAT_BASE}/auth", data=b"", method="POST")
        req.add_header("Authorization", f"Bearer {github_token}")
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except HTTPError as exc:
        if exc.code == 422:
            logger.warning("PAT auth: insufficient scope — user needs user:email")
        else:
            logger.warning("PAT auth failed: %s", exc)
        return None
    except (URLError, OSError) as exc:
        logger.warning("PAT auth request failed: %s", exc)
        return None

    if not data.get("api_key"):
        logger.warning("PAT auth returned no api_key: %s", data)
        return None

    return data


# ---------------------------------------------------------------------------
# 设备授权流程（通过 ScrapeCreators 走 GitHub OAuth）
# ---------------------------------------------------------------------------

_DEVICE_BASE = "https://api.scrapecreators.com/v1/github/device"


def run_device_auth() -> Optional[Tuple[str, str, str, int]]:
    """启动设备授权流程。

    向 ScrapeCreators 的 device/code 端点发起 POST。

    Returns:
        成功返回 (device_code, user_code, verification_uri, interval)，
        失败返回 None。
    """
    try:
        body = json.dumps({}).encode()
        req = Request(f"{_DEVICE_BASE}/code", data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except (HTTPError, URLError, OSError) as exc:
        logger.warning("Device auth code request failed: %s", exc)
        return None

    device_code = data.get("device_code")
    user_code = data.get("user_code")
    verification_uri = data.get("verification_uri")
    interval = data.get("interval", 5)

    if not device_code or not user_code:
        logger.warning("Device auth returned incomplete response: %s", data)
        return None

    return (device_code, user_code, verification_uri or "", interval)


def poll_device_auth(
    device_code: str,
    interval: int,
    timeout: int = 300,
    user_code: str = "",
    clipboard_ok: bool = False,
) -> Optional[str]:
    """用户在设备上授权后，轮询获取 access token。

    Args:
        device_code: run_device_auth() 返回的 device_code。
        interval: 轮询间隔（秒）。
        timeout: 最长轮询时间（秒）。
        user_code: 等待期间用于反复提醒的用户码。
        clipboard_ok: 用户码是否已复制到剪贴板。

    Returns:
        成功返回 access_token，超时或失败返回 None。
    """
    import sys

    started_at = time.time()
    deadline = started_at + timeout
    last_reminder = started_at
    reminder_count = 0
    max_reminders = 4
    reminder_interval = 30  # 两次提醒之间的间隔（秒）

    while time.time() < deadline:
        time.sleep(interval)

        # 等待期间定期提醒用户码
        if (
            user_code
            and reminder_count < max_reminders
            and time.time() - last_reminder >= reminder_interval
        ):
            clipboard_hint = "（已在剪贴板中）" if clipboard_ok else ""
            print(
                f"  仍在等待... 你的验证码：{user_code}{clipboard_hint}",
                file=sys.stderr,
                flush=True,
            )
            last_reminder = time.time()
            reminder_count += 1

        try:
            body = json.dumps({"device_code": device_code}).encode()
            req = Request(f"{_DEVICE_BASE}/token", data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except HTTPError as exc:
            if exc.code in (400, 403, 428):
                continue
            logger.warning("Device auth poll error: %s", exc)
            return None
        except (URLError, OSError):
            continue

        if data.get("access_token"):
            return data["access_token"]

        error = data.get("error")
        if error == "slow_down":
            interval = min(interval + 2, 30)
            continue
        if error == "authorization_pending":
            continue
        if error in ("expired_token", "access_denied"):
            logger.warning("Device auth failed: %s", error)
            return None

    return None


def fetch_api_key(access_token: str) -> Optional[str]:
    """用 GitHub access token 向 ScrapeCreators 取回 API key。

    带 Bearer 认证 GET device/profile 端点。

    Returns:
        成功返回 api_key 字符串，失败返回 None。
    """
    try:
        req = Request(f"{_DEVICE_BASE}/profile")
        req.add_header("Authorization", f"Bearer {access_token}")
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except (HTTPError, URLError, OSError) as exc:
        logger.warning("Failed to fetch API key: %s", exc)
        return None

    return data.get("api_key")


def run_full_device_auth(timeout: int = 300) -> Dict[str, Any]:
    """跑完整的 GitHub 设备授权流程，返回可 JSON 序列化的结果。

    串联：启动设备流程 -> 打开浏览器 -> 轮询 -> 取回 API key。
    设计为由 CLI 调用，其 stdout 由大模型解析。

    Returns:
        含 status 及相关字段的 dict：
        - {"status": "success", "api_key": "sc_...", "user_code": "ABCD-1234"}
        - {"status": "error", "message": "..."}
        - {"status": "timeout", "user_code": "ABCD-1234"}
        - {"status": "denied"}
    """
    import webbrowser

    # 第 1 步：启动设备流程
    result = run_device_auth()
    if result is None:
        return {"status": "error", "message": "启动设备授权流程失败"}

    device_code, user_code, verification_uri, interval = result

    import sys

    # 第 2 步：打开浏览器前先把验证码复制到剪贴板
    clipboard_ok = False
    if sys.platform == "darwin":
        try:
            subprocess.run(
                ["pbcopy"], input=user_code.encode(), check=True, timeout=5,
            )
            clipboard_ok = True
        except Exception:
            pass  # pbcopy 不可用或失败，继续往下走

    # 第 3 步：醒目地展示验证码，再打开浏览器
    clipboard_hint = "  （已复制到剪贴板）" if clipboard_ok else ""
    code_line = f"  你的验证码：{user_code}{clipboard_hint}"
    action_line = "  把它粘贴到刚打开的 GitHub 页面上"
    width = max(len(code_line), len(action_line)) + 2
    border = "-" * width
    print(f"\n+{border}+", file=sys.stderr)
    print(f"|{code_line.ljust(width)}|", file=sys.stderr)
    print(f"|{action_line.ljust(width)}|", file=sys.stderr)
    print(f"+{border}+", file=sys.stderr)

    if verification_uri:
        try:
            webbrowser.open(verification_uri)
        except Exception:
            print(f"请打开：{verification_uri}", file=sys.stderr)

    print("等待授权中...", file=sys.stderr, flush=True)

    # 第 4 步：轮询获取 token（期间定期提醒验证码）
    access_token = poll_device_auth(
        device_code, interval, timeout=timeout,
        user_code=user_code, clipboard_ok=clipboard_ok,
    )
    if access_token is None:
        return {"status": "timeout", "user_code": user_code, "clipboard_ok": clipboard_ok}

    # 第 4 步：取回 API key
    api_key = fetch_api_key(access_token)
    if api_key is None:
        return {
            "status": "error",
            "message": "已授权但取回 API key 失败",
            "clipboard_ok": clipboard_ok,
        }

    return {"status": "success", "method": "device", "api_key": api_key, "user_code": user_code, "clipboard_ok": clipboard_ok}


# ---------------------------------------------------------------------------
# 统一的 GitHub 授权：先试 PAT，失败回退到设备流程
# ---------------------------------------------------------------------------


def run_github_auth(timeout: int = 300) -> Dict[str, Any]:
    """先用 gh CLI 试 PAT 授权，失败回退到设备流程。

    1. 检查是否有 gh CLI
    2. 有则运行 `gh auth token` 取 PAT
    3. 把 PAT POST 给 ScrapeCreators —— 成功即完成
    4. 若 PAT 因任何原因失败，回退到设备流程

    返回含 status、method、api_key 的可 JSON 序列化 dict。
    """
    import sys

    # 第 1 步：通过 gh CLI 试 PAT
    gh_path = shutil.which("gh")
    if gh_path:
        try:
            result = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                token = result.stdout.strip()
                print("检测到 gh CLI —— 正在尝试 PAT 授权...", file=sys.stderr)
                pat_result = auth_with_pat(token)
                if pat_result and pat_result.get("api_key"):
                    return {
                        "status": "success",
                        "method": "pat",
                        "api_key": pat_result["api_key"],
                        "github_username": pat_result.get("github_username", ""),
                    }
                # PAT 失败 —— 可能是权限范围不足
                print(
                    "PAT 授权未成功（可能是权限范围或端点问题）。"
                    "回退到 GitHub 设备授权流程...",
                    file=sys.stderr,
                )
        except Exception as exc:
            logger.debug("gh auth token failed: %s", exc)

    # 第 2 步：回退到设备流程
    if not gh_path:
        print("未找到 gh CLI —— 改用 GitHub 设备授权流程...", file=sys.stderr)

    return run_full_device_auth(timeout=timeout)
