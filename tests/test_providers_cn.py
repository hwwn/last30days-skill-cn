"""providers.py — Chinese OpenAI-compatible client selection (PORT_CONTRACT §5).

Covers:
- the four new OpenAI-compatible clients (DeepSeek / DashScope / Moonshot / Zhipu)
  exist with the contracted endpoints, default models, and names;
- ``auto`` provider detection appends the CN providers in the contracted order
  after google/openai/xai/openrouter;
- ``resolve_runtime`` returns the right client instance per provider;
- ``mock_runtime`` resolves CN providers without live creds.

INTEGRATION RISK (surfaced by test_resolve_runtime_missing_get_x_source): every
``resolve_runtime``/``mock_runtime`` path flows through
``providers._resolve_x_backend`` which calls ``env.get_x_source`` — a symbol the
CN port deleted from env.py. Live calls therefore raise AttributeError. The
client-selection tests monkeypatch ``env.get_x_source`` so the selection logic
under test can run; the dedicated test below pins the unpatched failure so the
regression is visible until the lib is fixed (e.g. drop the X backend entirely).
"""

import pytest

from lib import env, providers, schema


@pytest.fixture
def patch_x_backend(monkeypatch):
    """Provide the missing env.get_x_source so runtime resolution can proceed.

    Without this, resolve_runtime/mock_runtime raise AttributeError before they
    finish selecting a client (see test_resolve_runtime_missing_get_x_source).
    """
    monkeypatch.setattr(env, "get_x_source", lambda config: None, raising=False)


# --------------------------------------------------------------------------- #
# Client catalog (static — no network, no get_x_source)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("cls, name, endpoint, default_model", [
    (providers.DeepSeekClient, "deepseek",
     "https://api.deepseek.com/chat/completions", "deepseek-chat"),
    (providers.DashScopeClient, "dashscope",
     "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", "qwen-plus"),
    (providers.MoonshotClient, "moonshot",
     "https://api.moonshot.cn/v1/chat/completions", "moonshot-v1-8k"),
    (providers.ZhipuClient, "zhipu",
     "https://open.bigmodel.cn/api/paas/v4/chat/completions", "glm-4-flash"),
])
def test_cn_client_endpoints_and_defaults(cls, name, endpoint, default_model):
    client = cls("sk-test")
    assert client.name == name
    assert client.endpoint == endpoint
    assert isinstance(client, providers._OpenAICompatClient)
    planner_default, rerank_default = providers._MODEL_DEFAULTS[name]
    assert planner_default == default_model
    assert rerank_default == default_model


def test_western_reasoning_clients_retained():
    # PORT_CONTRACT §5: Gemini/OpenAI/xAI/OpenRouter are kept.
    assert providers.GeminiClient("k").name == "gemini"
    assert providers.OpenAIClient("k", env.AUTH_SOURCE_API_KEY, None).name == "openai"
    assert providers.XAIClient("k").name == "xai"
    assert providers.OpenRouterClient("k").name == "openrouter"


# --------------------------------------------------------------------------- #
# auto detection order (PORT_CONTRACT §5)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("config, expected", [
    ({"DEEPSEEK_API_KEY": "k"}, "deepseek"),
    ({"DASHSCOPE_API_KEY": "k"}, "dashscope"),
    ({"MOONSHOT_API_KEY": "k"}, "moonshot"),
    ({"ZHIPU_API_KEY": "k"}, "zhipu"),
])
def test_auto_selects_cn_provider_when_only_one_present(patch_x_backend, config, expected):
    runtime, client = providers.resolve_runtime(config, "default")
    assert runtime.reasoning_provider == expected
    assert client is not None
    assert client.name == expected


def test_auto_prefers_western_then_cn_order(patch_x_backend):
    # Google wins over all CN providers.
    cfg = {"GOOGLE_API_KEY": "g", "DEEPSEEK_API_KEY": "d", "DASHSCOPE_API_KEY": "q"}
    runtime, _ = providers.resolve_runtime(cfg, "default")
    assert runtime.reasoning_provider == "gemini"

    # With no western keys, deepseek is probed before dashscope/moonshot/zhipu.
    cfg = {"DEEPSEEK_API_KEY": "d", "DASHSCOPE_API_KEY": "q",
           "MOONSHOT_API_KEY": "m", "ZHIPU_API_KEY": "z"}
    runtime, _ = providers.resolve_runtime(cfg, "default")
    assert runtime.reasoning_provider == "deepseek"

    # dashscope before moonshot before zhipu.
    cfg = {"DASHSCOPE_API_KEY": "q", "MOONSHOT_API_KEY": "m", "ZHIPU_API_KEY": "z"}
    assert providers.resolve_runtime(cfg, "default")[0].reasoning_provider == "dashscope"
    cfg = {"MOONSHOT_API_KEY": "m", "ZHIPU_API_KEY": "z"}
    assert providers.resolve_runtime(cfg, "default")[0].reasoning_provider == "moonshot"


def test_auto_with_no_keys_falls_back_to_local(patch_x_backend):
    runtime, client = providers.resolve_runtime({}, "default")
    assert runtime.reasoning_provider == "local"
    assert client is None


# --------------------------------------------------------------------------- #
# Explicit provider pin -> concrete client instance
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("provider, key_name, expected_cls", [
    ("deepseek", "DEEPSEEK_API_KEY", providers.DeepSeekClient),
    ("dashscope", "DASHSCOPE_API_KEY", providers.DashScopeClient),
    ("moonshot", "MOONSHOT_API_KEY", providers.MoonshotClient),
    ("zhipu", "ZHIPU_API_KEY", providers.ZhipuClient),
])
def test_explicit_pin_returns_matching_client(patch_x_backend, provider, key_name, expected_cls):
    cfg = {"LAST30DAYS_REASONING_PROVIDER": provider, key_name: "sk-test"}
    runtime, client = providers.resolve_runtime(cfg, "default")
    assert runtime.reasoning_provider == provider
    assert isinstance(client, expected_cls)


@pytest.mark.parametrize("provider", ["deepseek", "dashscope", "moonshot", "zhipu"])
def test_explicit_pin_without_key_raises(patch_x_backend, provider):
    cfg = {"LAST30DAYS_REASONING_PROVIDER": provider}
    with pytest.raises(RuntimeError):
        providers.resolve_runtime(cfg, "default")


def test_mock_runtime_resolves_cn_provider(patch_x_backend):
    runtime = providers.mock_runtime({"LAST30DAYS_REASONING_PROVIDER": "zhipu"}, "default")
    assert isinstance(runtime, schema.ProviderRuntime)
    assert runtime.reasoning_provider == "zhipu"
    assert runtime.planner_model == "glm-4-flash"


def test_gemini_31_guard_only_applies_to_gemini(patch_x_backend):
    # A non-gemini-3.1 planner pin must NOT raise for a CN provider.
    cfg = {"LAST30DAYS_REASONING_PROVIDER": "deepseek",
           "DEEPSEEK_API_KEY": "k",
           "LAST30DAYS_PLANNER_MODEL": "deepseek-reasoner"}
    runtime, _ = providers.resolve_runtime(cfg, "default")
    assert runtime.planner_model == "deepseek-reasoner"


# --------------------------------------------------------------------------- #
# Integration risk: env.get_x_source was deleted but providers still calls it.
# --------------------------------------------------------------------------- #

def test_resolve_runtime_no_x_backend():
    """CN 版无 X/Twitter 后端：get_x_source 已删，_resolve_x_backend 恒返回 None。

    resolve_runtime 必须正常工作，且 ProviderRuntime.x_search_backend 为 None。
    """
    assert not hasattr(env, "get_x_source")
    runtime, client = providers.resolve_runtime({"DEEPSEEK_API_KEY": "k"}, "default")
    assert runtime.x_search_backend is None
    assert runtime.reasoning_provider == "deepseek"
