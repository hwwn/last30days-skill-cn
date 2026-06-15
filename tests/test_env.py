"""env.py — CN config keys, Keychain key list, and source-availability helpers.

Runs in clean mode (LAST30DAYS_CONFIG_DIR="" set in conftest), so get_config()
reads no .env file and only sees process-env overrides set per test.
"""

from lib import env


# --------------------------------------------------------------------------- #
# Config keys (PORT_CONTRACT §4)
# --------------------------------------------------------------------------- #

def test_clean_mode_has_no_config_file():
    # conftest set LAST30DAYS_CONFIG_DIR="" -> CONFIG_FILE is None (clean mode).
    assert env.CONFIG_FILE is None
    assert env.CONFIG_DIR is None
    assert env.config_exists() is False


def test_get_config_exposes_chinese_llm_keys():
    config = env.get_config()
    for key in ("DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "MOONSHOT_API_KEY", "ZHIPU_API_KEY"):
        assert key in config, f"{key} missing from config"


def test_get_config_exposes_cn_data_source_keys():
    config = env.get_config()
    for key in ("WEIBO_COOKIE", "ZHIHU_COOKIE", "GITHUB_TOKEN",
                "XIAOHONGSHU_API_BASE", "SCRAPECREATORS_API_KEY"):
        assert key in config, f"{key} missing from config"


def test_get_config_retains_western_reasoning_keys():
    config = env.get_config()
    for key in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY",
                "XAI_API_KEY", "OPENROUTER_API_KEY",
                "BRAVE_API_KEY", "EXA_API_KEY", "SERPER_API_KEY", "PARALLEL_API_KEY"):
        assert key in config, f"{key} missing from config"


def test_keychain_keys_cover_chinese_llms_and_cn_sources():
    keys = set(env.KEYCHAIN_KEYS)
    for key in ("DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "MOONSHOT_API_KEY", "ZHIPU_API_KEY",
                "WEIBO_COOKIE", "ZHIHU_COOKIE", "GITHUB_TOKEN",
                "XIAOHONGSHU_API_BASE", "SCRAPECREATORS_API_KEY"):
        assert key in keys, f"{key} not registered in KEYCHAIN_KEYS"


def test_no_western_only_source_keys_leaked_into_keychain():
    # The CN port dropped X/reddit/bluesky-style data-source creds.
    keys = set(env.KEYCHAIN_KEYS)
    for dropped in ("AUTH_TOKEN", "CT0", "TRUTHSOCIAL_API_KEY", "PINTEREST_TOKEN"):
        assert dropped not in keys


def test_cookie_domains_are_localized():
    assert set(env.COOKIE_DOMAINS) == {"weibo", "zhihu"}
    assert env.COOKIE_DOMAINS["weibo"]["domain"] == ".weibo.cn"
    assert env.COOKIE_DOMAINS["zhihu"]["domain"] == ".zhihu.com"
    assert env.COOKIE_DOMAINS["weibo"]["mapping"]["SUB"] == "WEIBO_COOKIE"
    assert env.COOKIE_DOMAINS["zhihu"]["mapping"]["z_c0"] == "ZHIHU_COOKIE"


# --------------------------------------------------------------------------- #
# Source-availability helpers (PORT_CONTRACT §4)
# --------------------------------------------------------------------------- #

def test_keyfree_sources_always_available():
    assert env.is_bilibili_available() is True
    assert env.is_v2ex_available() is True
    assert env.is_juejin_available() is True
    assert env.is_xueqiu_available() is True


def test_weibo_available_only_with_cookie_or_scraper():
    assert env.is_weibo_available({}) is False
    assert env.is_weibo_available({"WEIBO_COOKIE": "SUB=abc"}) is True
    assert env.is_weibo_available({"SCRAPECREATORS_API_KEY": "sc-1"}) is True


def test_zhihu_available_only_with_cookie_or_scraper():
    assert env.is_zhihu_available({}) is False
    assert env.is_zhihu_available({"ZHIHU_COOKIE": "z_c0=abc"}) is True
    assert env.is_zhihu_available({"SCRAPECREATORS_API_KEY": "sc-1"}) is True


def test_douyin_available_requires_scrapecreators():
    assert env.is_douyin_available({}) is False
    assert env.is_douyin_available({"SCRAPECREATORS_API_KEY": "sc-1"}) is True
    # A weibo cookie does not unlock douyin.
    assert env.is_douyin_available({"WEIBO_COOKIE": "SUB=abc"}) is False


def test_xiaohongshu_api_base_default():
    assert env.get_xiaohongshu_api_base({}) == "http://host.docker.internal:18060"
    assert env.get_xiaohongshu_api_base({"XIAOHONGSHU_API_BASE": "http://localhost:9999/"}) == "http://localhost:9999"


def test_token_accessors():
    assert env.get_scrapecreators_token({}) == ""
    assert env.get_scrapecreators_token({"SCRAPECREATORS_API_KEY": "sc-1"}) == "sc-1"
    assert env.get_github_token({}) == ""
    assert env.get_github_token({"GITHUB_TOKEN": "ghp_x"}) == "ghp_x"


def test_dropped_western_source_helpers_are_gone():
    # PORT_CONTRACT §4: X/reddit/bluesky/truthsocial/pinterest/tiktok/instagram
    # helpers were removed. (xiaohongshu's helper is retained, by contract.)
    for name in ("is_reddit_available", "is_x_available", "is_tiktok_available",
                 "is_instagram_available", "is_bluesky_available", "get_x_source"):
        assert not hasattr(env, name), f"{name} should have been removed from env"
