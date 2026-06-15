"""Environment and API key management for last30days-cn skill."""

from __future__ import annotations

import base64
import binascii
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

# Allow override via environment variable for testing
# Set LAST30DAYS_CONFIG_DIR="" for clean/no-config mode
# Set LAST30DAYS_CONFIG_DIR="/path/to/dir" for custom config location
_config_override = os.environ.get('LAST30DAYS_CONFIG_DIR')
if _config_override == "":
    # Empty string = no config file (clean mode)
    CONFIG_DIR = None
    CONFIG_FILE = None
elif _config_override:
    CONFIG_DIR = Path(_config_override)
    CONFIG_FILE = CONFIG_DIR / ".env"
else:
    CONFIG_DIR = Path.home() / ".config" / "last30days"
    CONFIG_FILE = CONFIG_DIR / ".env"

CODEX_AUTH_FILE = Path(os.environ.get("CODEX_AUTH_FILE", str(Path.home() / ".codex" / "auth.json")))

# macOS Keychain integration: items stored with this service prefix are picked
# up automatically on Darwin as the lowest-priority credential source.
# Example: `security add-generic-password -a "$USER" -s last30days-DEEPSEEK_API_KEY -w "sk-..."`.
KEYCHAIN_SERVICE_PREFIX = "last30days-"

# Single source of truth for which credentials the Keychain loader looks up.
# The setup-keychain.sh helper mirrors this list and is held in sync via
# tests/test_env_keychain.py::test_keychain_keys_match_setup_script.
KEYCHAIN_KEYS = (
    # Reasoning providers (Western, retained)
    "OPENAI_API_KEY", "XAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY",
    "GOOGLE_GENAI_API_KEY", "OPENROUTER_API_KEY",
    # Reasoning providers (Chinese LLMs)
    "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "MOONSHOT_API_KEY", "ZHIPU_API_KEY",
    # Scrapers / data sources
    "SCRAPECREATORS_API_KEY", "XIAOHONGSHU_API_BASE",
    "WEIBO_COOKIE", "ZHIHU_COOKIE", "GITHUB_TOKEN",
    # Web grounding search
    "BRAVE_API_KEY", "EXA_API_KEY", "SERPER_API_KEY", "PARALLEL_API_KEY",
)

AuthSource = Literal["api_key", "codex", "none"]
AuthStatus = Literal["ok", "missing", "expired", "missing_account_id"]

AUTH_SOURCE_API_KEY: AuthSource = "api_key"
AUTH_SOURCE_CODEX: AuthSource = "codex"
AUTH_SOURCE_NONE: AuthSource = "none"

AUTH_STATUS_OK: AuthStatus = "ok"
AUTH_STATUS_MISSING: AuthStatus = "missing"
AUTH_STATUS_EXPIRED: AuthStatus = "expired"
AUTH_STATUS_MISSING_ACCOUNT_ID: AuthStatus = "missing_account_id"


@dataclass(frozen=True)
class OpenAIAuth:
    token: str | None
    source: AuthSource
    status: AuthStatus
    account_id: str | None
    codex_auth_file: str


def _check_file_permissions(path: Path) -> None:
    """Warn to stderr if a secrets file has overly permissive permissions."""
    if os.name == "nt":
        # Windows reports synthesized POSIX mode bits that do not reflect NTFS ACLs.
        return

    try:
        mode = path.stat().st_mode
        # Check if group or other can read (bits 0o044)
        if mode & 0o044:
            sys.stderr.write(
                f"[last30days] WARNING: {path} is readable by other users. "
                f"Run: chmod 600 {path}\n"
            )
            sys.stderr.flush()
    except OSError as exc:
        sys.stderr.write(f"[last30days] WARNING: could not stat {path}: {exc}\n")
        sys.stderr.flush()


def load_env_file(path: Path) -> dict[str, str]:
    """Load environment variables from a file."""
    env = {}
    if not path or not path.exists():
        return env
    _check_file_permissions(path)

    with open(path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                key, _, value = line.partition('=')
                key = key.strip()
                value = value.strip()
                # Remove quotes if present
                if value and value[0] in ('"', "'") and value[-1] == value[0]:
                    value = value[1:-1]
                if key and value:
                    env[key] = value
    return env


def _load_keychain(keys: list[str]) -> dict[str, str]:
    """Load credentials from macOS Keychain (no-op on other platforms).

    Each key is looked up as a generic password with service name
    ``f"{KEYCHAIN_SERVICE_PREFIX}{key}"`` for the current user. Missing items
    and lookup failures are silent — Keychain is the lowest-priority source
    and is meant to be additive over `.env` files and process environment.
    """
    import platform
    if platform.system() != "Darwin":
        return {}

    import shutil
    security = shutil.which("security")
    if not security:
        return {}

    import subprocess
    import pwd
    # USER can be unset under sudo, in Docker without --env USER, or in some CI
    # runners; fall back to the OS user record so lookups still match items
    # stored by setup-keychain.sh (which uses $USER).
    user = os.environ.get("USER") or pwd.getpwuid(os.getuid()).pw_name
    env: dict[str, str] = {}
    for key in keys:
        try:
            result = subprocess.run(
                [security, "find-generic-password",
                 "-a", user,
                 "-s", f"{KEYCHAIN_SERVICE_PREFIX}{key}",
                 "-w"],
                capture_output=True, text=True, timeout=5,
            )
        except (subprocess.TimeoutExpired, OSError):
            continue
        if result.returncode == 0 and result.stdout.strip():
            env[key] = result.stdout.strip()
    return env


def _decode_jwt_payload(token: str) -> dict[str, Any] | None:
    """Decode JWT payload without verification."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1]
        pad = "=" * (-len(payload_b64) % 4)
        decoded = base64.urlsafe_b64decode(payload_b64 + pad)
        return json.loads(decoded.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, binascii.Error, IndexError) as exc:
        sys.stderr.write(f"[last30days] WARNING: malformed JWT token: {exc}\n")
        sys.stderr.flush()
        return None


def _token_expired(token: str, leeway_seconds: int = 60) -> bool:
    """Check if JWT token is expired."""
    payload = _decode_jwt_payload(token)
    if not payload:
        return False
    exp = payload.get("exp")
    if not exp:
        return False
    return exp <= (time.time() + leeway_seconds)


def extract_chatgpt_account_id(access_token: str) -> str | None:
    """Extract chatgpt_account_id from JWT token."""
    payload = _decode_jwt_payload(access_token)
    if not payload:
        return None
    auth_claim = payload.get("https://api.openai.com/auth", {})
    if isinstance(auth_claim, dict):
        return auth_claim.get("chatgpt_account_id")
    return None


def load_codex_auth(path: Path = CODEX_AUTH_FILE) -> dict[str, Any]:
    """Load Codex auth JSON."""
    if not path.exists():
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except json.JSONDecodeError:
        sys.stderr.write(
            f"[last30days] WARNING: {path} exists but contains invalid JSON -- ignoring\n"
        )
        sys.stderr.flush()
        return {}


def get_codex_access_token() -> tuple[str | None, str]:
    """Get Codex access token from auth.json.

    Returns:
        (token, status) where status is 'ok', 'missing', or 'expired'
    """
    auth = load_codex_auth()
    token = None
    if isinstance(auth, dict):
        tokens = auth.get("tokens") or {}
        if isinstance(tokens, dict):
            token = tokens.get("access_token")
        if not token:
            token = auth.get("access_token")
    if not token:
        return None, AUTH_STATUS_MISSING
    if _token_expired(token):
        return None, AUTH_STATUS_EXPIRED
    return token, AUTH_STATUS_OK


def get_openai_auth(file_env: dict[str, str]) -> OpenAIAuth:
    """Resolve OpenAI auth from API key or Codex login."""
    api_key = os.environ.get('OPENAI_API_KEY') or file_env.get('OPENAI_API_KEY')
    if api_key:
        return OpenAIAuth(
            token=api_key,
            source=AUTH_SOURCE_API_KEY,
            status=AUTH_STATUS_OK,
            account_id=None,
            codex_auth_file=str(CODEX_AUTH_FILE),
        )

    # Codex auth (chatgpt.com backend) intentionally skipped.
    # The endpoint is unstable and causes crashes when the token expires.
    # Users who want OpenAI should set OPENAI_API_KEY explicitly.

    return OpenAIAuth(
        token=None,
        source=AUTH_SOURCE_NONE,
        status=AUTH_STATUS_MISSING,
        account_id=None,
        codex_auth_file=str(CODEX_AUTH_FILE),
    )


def _find_project_env() -> Path | None:
    """Find per-project .env by walking up from cwd.

    Searches for .claude/last30days.env in each parent directory,
    stopping at the user's home directory or filesystem root.
    """
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / '.claude' / 'last30days.env'
        if candidate.exists():
            return candidate
        # Stop at filesystem root or home
        if parent == Path.home() or parent == parent.parent:
            break
    return None


def get_config() -> dict[str, Any]:
    """Load configuration from multiple sources.

    Priority (highest wins):
      1. Environment variables (os.environ)
      2. .claude/last30days.env (per-project config)
      3. ~/.config/last30days/.env (global config)
      4. macOS Keychain items prefixed ``last30days-`` (Darwin only)
    """
    # Load from global config file
    file_env = load_env_file(CONFIG_FILE) if CONFIG_FILE else {}

    # Load from per-project config (overrides global)
    project_env_path = _find_project_env()
    project_env = load_env_file(project_env_path) if project_env_path else {}

    # Merge file sources: project > global
    merged_env = {**file_env, **project_env}

    # Keychain is the lowest-priority source (Darwin only; no-op elsewhere).
    # Loaded before openai_auth so OPENAI_API_KEY can come from Keychain too.
    keychain_env = _load_keychain(list(KEYCHAIN_KEYS))
    merged_env = {**keychain_env, **merged_env}

    openai_auth = get_openai_auth(merged_env)

    # Build config: Codex/OpenAI auth + process.env > project .env > global .env
    config = {
        'OPENAI_API_KEY': openai_auth.token,
        'OPENAI_AUTH_SOURCE': openai_auth.source,
        'OPENAI_AUTH_STATUS': openai_auth.status,
        'OPENAI_CHATGPT_ACCOUNT_ID': openai_auth.account_id,
        'CODEX_AUTH_FILE': openai_auth.codex_auth_file,
    }

    keys = [
        # Reasoning providers (Western, retained)
        ('XAI_API_KEY', None),
        ('GOOGLE_API_KEY', None),
        ('GEMINI_API_KEY', None),
        ('GOOGLE_GENAI_API_KEY', None),
        ('OPENROUTER_API_KEY', None),
        # Reasoning providers (Chinese LLMs, OpenAI-compatible)
        ('DEEPSEEK_API_KEY', None),
        ('DASHSCOPE_API_KEY', None),
        ('MOONSHOT_API_KEY', None),
        ('ZHIPU_API_KEY', None),
        # Data sources (CN)
        ('WEIBO_COOKIE', None),
        ('ZHIHU_COOKIE', None),
        ('GITHUB_TOKEN', None),
        ('XIAOHONGSHU_API_BASE', None),
        ('SCRAPECREATORS_API_KEY', None),
        # Web grounding search
        ('BRAVE_API_KEY', None),
        ('EXA_API_KEY', None),
        ('SERPER_API_KEY', None),
        ('PARALLEL_API_KEY', None),
        # Runtime / reasoning knobs
        ('LAST30DAYS_REASONING_PROVIDER', 'auto'),
        ('LAST30DAYS_PLANNER_MODEL', None),
        ('LAST30DAYS_RERANK_MODEL', None),
        ('LAST30DAYS_STORE', None),
        ('OPENAI_MODEL_PIN', None),
        ('XAI_MODEL_PIN', None),
        # Source selection / browser cookies
        ('FROM_BROWSER', None),
        ('SETUP_COMPLETE', None),
        ('INCLUDE_SOURCES', ''),
        ('EXCLUDE_SOURCES', ''),
    ]

    for key, default in keys:
        config[key] = os.environ.get(key) or merged_env.get(key, default)

    # Backward-compat: ScrapeCreators' own examples and tutorials use the
    # SCRAPE_CREATORS_API_KEY spelling (with underscore between SCRAPE and
    # CREATORS). Accept that form too so users who follow the vendor's docs
    # don't silently end up with has_scrapecreators=False. Canonical name
    # wins when both are set.
    if not config.get('SCRAPECREATORS_API_KEY'):
        legacy = os.environ.get('SCRAPE_CREATORS_API_KEY') or merged_env.get('SCRAPE_CREATORS_API_KEY')
        if legacy:
            config['SCRAPECREATORS_API_KEY'] = legacy

    # Multi-key rotation: comma-separated SCRAPECREATORS_API_KEY round-robins
    # via random.choice per run. Originally added in #268, accidentally dropped
    # in v3.0.6, restored here.
    sc_key_raw = config.get('SCRAPECREATORS_API_KEY') or ''
    if ',' in sc_key_raw:
        import random
        sc_keys = [k.strip() for k in sc_key_raw.split(',') if k.strip()]
        config['SCRAPECREATORS_API_KEY'] = random.choice(sc_keys) if sc_keys else ''

    # Track which config source was used (highest-priority file source wins
    # the label; keychain is only reported when nothing else is configured).
    if project_env_path:
        config['_CONFIG_SOURCE'] = f'project:{project_env_path}'
    elif CONFIG_FILE and CONFIG_FILE.exists():
        config['_CONFIG_SOURCE'] = f'global:{CONFIG_FILE}'
    elif keychain_env:
        config['_CONFIG_SOURCE'] = 'keychain'
    else:
        config['_CONFIG_SOURCE'] = 'env_only'

    # Extract browser credentials if configured
    browser_creds = extract_browser_credentials(config)
    for key, value in browser_creds.items():
        if not config.get(key):
            config[key] = value
            config[f"_{key}_SOURCE"] = "browser"

    return config


# ---------------------------------------------------------------------------
# Browser cookie extraction
# ---------------------------------------------------------------------------

# Maps each loginwalled CN source to the browser cookie(s) we harvest and the
# config key they feed. Extraction yields the raw cookie value under the mapped
# env key, which providers send as a Cookie header to the source's API.
COOKIE_DOMAINS: dict[str, dict[str, Any]] = {
    "weibo": {
        "domain": ".weibo.cn",
        "cookies": ["SUB", "SUBP"],
        "mapping": {"SUB": "WEIBO_COOKIE"},
    },
    "zhihu": {
        "domain": ".zhihu.com",
        "cookies": ["z_c0", "d_c0"],
        "mapping": {"z_c0": "ZHIHU_COOKIE"},
    },
}


def extract_browser_credentials(config: dict[str, Any]) -> dict[str, str]:
    """Extract auth cookies from local browsers.

    Default behavior (FROM_BROWSER unset): tries Firefox and Safari only.
    These read local files silently with no system dialogs.  Chrome is
    skipped because ``security find-generic-password`` triggers a macOS
    Keychain prompt that cannot be reliably suppressed.

    Set ``FROM_BROWSER=auto`` to also try Chrome (accepts the dialog),
    or ``FROM_BROWSER=off`` to disable extraction entirely.
    """
    from_browser = (config.get("FROM_BROWSER") or "").strip().lower()
    if from_browser == "off":
        return {}
    try:
        from . import cookie_extract
    except ImportError:
        return {}
    # Determine which browsers to try
    if from_browser in ("firefox", "chrome", "safari"):
        browsers = [from_browser]
    elif from_browser == "auto":
        browsers = ["firefox", "safari", "chrome"]
    else:
        # Default: silent browsers only (no Keychain dialog)
        browsers = ["firefox", "safari"]
    extracted: dict[str, str] = {}
    for _service, spec in COOKIE_DOMAINS.items():
        if all(config.get(env_key) for env_key in spec["mapping"].values()):
            continue
        for browser in browsers:
            try:
                cookies = cookie_extract.extract_cookies(browser, spec["domain"], spec["cookies"])
            except Exception:
                continue
            if cookies:
                for cookie_name, env_key in spec["mapping"].items():
                    if cookie_name in cookies and not config.get(env_key):
                        extracted[env_key] = cookies[cookie_name]
                break  # Found cookies for this service, stop trying browsers
    return extracted


def config_exists() -> bool:
    """Check if any configuration source exists."""
    if _find_project_env():
        return True
    if CONFIG_FILE:
        return CONFIG_FILE.exists()
    return False


# ---------------------------------------------------------------------------
# Source availability helpers
# ---------------------------------------------------------------------------
# Tiered access (PORT_CONTRACT §2): key-free public/semi-public sources are
# always available; loginwalled sources (weibo/zhihu/douyin/xiaohongshu) gate
# on a cookie / scraper key, and the provider returns [] when unavailable
# (never fabricates data).

def is_bilibili_available() -> bool:
    """Check if Bilibili source is available.

    Always returns True - the Web search API is reachable key-free (the
    provider handles wbi signature / buvid cookie fallbacks internally).
    """
    return True


def is_v2ex_available() -> bool:
    """Check if V2EX source is available.

    Always returns True - uses the key-free sov2ex full-text search API.
    """
    return True


def is_juejin_available() -> bool:
    """Check if Juejin source is available.

    Always returns True - the search API is key-free.
    """
    return True


def is_xueqiu_available() -> bool:
    """Check if Xueqiu source is available.

    Always returns True - the provider self-bootstraps an ``xq_a_token`` cookie
    via a GET to xueqiu.com before querying discussions.
    """
    return True


def is_weibo_available(config: dict[str, Any]) -> bool:
    """Check if Weibo source is available.

    True when WEIBO_COOKIE is configured or a ScrapeCreators key is present.
    The mobile container endpoint is best-effort even without a cookie, but we
    only mark it available when one of these credentials exists so the pipeline
    does not surface an empty source as configured.
    """
    return bool(config.get('WEIBO_COOKIE') or config.get('SCRAPECREATORS_API_KEY'))


def is_zhihu_available(config: dict[str, Any]) -> bool:
    """Check if Zhihu source is available.

    True when ZHIHU_COOKIE (z_c0 / d_c0) is configured or a ScrapeCreators key
    is present. The search_v3 endpoint requires login cookies.
    """
    return bool(config.get('ZHIHU_COOKIE') or config.get('SCRAPECREATORS_API_KEY'))


def is_douyin_available(config: dict[str, Any]) -> bool:
    """Check if Douyin source is available.

    Requires SCRAPECREATORS_API_KEY (ScrapeCreators Douyin endpoint). Without
    it the provider returns [].
    """
    return bool(config.get('SCRAPECREATORS_API_KEY'))


def get_xiaohongshu_api_base(config: dict[str, Any]) -> str:
    """Get Xiaohongshu HTTP API base URL.

    Defaults to host.docker.internal so OpenClaw Docker can reach host service.
    """
    return (config.get('XIAOHONGSHU_API_BASE') or "http://host.docker.internal:18060").rstrip("/")


def is_xiaohongshu_available(config: dict[str, Any]) -> bool:
    """Check whether Xiaohongshu HTTP API is reachable and logged in."""
    # Import here to avoid heavy imports at module load.
    from . import http

    base = get_xiaohongshu_api_base(config)
    try:
        # Keep health probe snappy, but allow one retry for transient hiccups.
        health = http.get(f"{base}/health", timeout=3, retries=2)
        if not isinstance(health, dict):
            return False
        if not health.get("success"):
            return False

        # Login probe can be slower on some deployments (browser/session checks),
        # so use a slightly longer timeout to avoid false negatives.
        login = http.get(f"{base}/api/v1/login/status", timeout=8, retries=2)
        is_logged_in = (
            login.get("data", {}).get("is_logged_in")
            if isinstance(login, dict) else False
        )
        return bool(is_logged_in)
    except (OSError, http.HTTPError):
        return False
    except Exception as exc:
        sys.stderr.write(
            f"[last30days] WARNING: unexpected error checking Xiaohongshu: "
            f"{type(exc).__name__}: {exc}\n"
        )
        sys.stderr.flush()
        return False


def get_scrapecreators_token(config: dict[str, Any]) -> str:
    """Get the ScrapeCreators API token (shared by douyin / xiaohongshu scrapers)."""
    return config.get('SCRAPECREATORS_API_KEY') or ''


def get_github_token(config: dict[str, Any]) -> str:
    """Get the optional GitHub token used to raise the public API rate limit."""
    return config.get('GITHUB_TOKEN') or ''
