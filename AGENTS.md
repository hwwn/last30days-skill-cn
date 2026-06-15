# last30days-cn Skill

`mvanhorn/last30days-skill` 的中国市场移植。一个 Agent Skills 包，跨微博、知乎、B站、抖音、小红书、V2EX、掘金、GitHub、雪球、网页检索任意话题。可安装到 Claude Code（最常见宿主）、Codex、Cursor、GitHub Copilot、Gemini CLI 等 50+ [Agent Skills](https://agentskills.io) 宿主。纯 Python，多源检索聚合，**仅标准库**。

China-market port of `mvanhorn/last30days-skill`. An Agent Skills package for
researching any topic across Chinese platforms. Python scripts, multi-source
search aggregation, **standard library only**.

## 结构 / Structure
- `skills/last30days-cn/SKILL.md` — 规范 skill 定义 / 运行时 spec，slash command 触发时模型读它。
- `skills/last30days-cn/scripts/last30days_cn.py` — 主研究引擎（CLI 入口，由上游 `last30days.py` 移植）。
- `skills/last30days-cn/scripts/lib/` — 检索、富化、渲染模块。每个 `lib/<source>.py` 是一个 provider。
- `CONCEPTS.md` — 领域词汇（Skill / Engine / Harness / 规范源名 / 分层取数 / 情绪体 / CJK 分词回退）。
- `CONFIGURATION.md` — 用户可调旋钮（env vars、flags、per-host 安装模式）；按下面规则保持同步。
- `CHANGELOG.md` — 结构化发布历史。
- `docs/PORT_CONTRACT.md` — **移植契约，跨文件接口的唯一真相。改任何跨模块接口前必读。**

## 定位 / Orientation
- 这是一个 Agent Skills 包，不是 CLI 工具。产品是 slash-command 触发的 skill（多数宿主里是 `/last30days-cn <话题>`）；`scripts/last30days_cn.py` 是实现。功能必须在 skill 能安装进的每个宿主上工作。
- 功能设计从 slash-command UX 出发。一个没有 SKILL.md 集成的新引擎 flag 是不完整的——调用 skill 的模型根本不知道这个 flag 存在。
- README 与 PR 示例先展示 `/last30days-cn <话题>`。直接 CLI 调用（`python3 scripts/last30days_cn.py ...`）是脚本化 / cron / 开发期引擎测试的 fallback，要标注为 fallback，绝不当主路径。
- Slash command 不透传 shell 机制。`/last30days-cn 比亚迪 --emit=html | pbcopy` 在任何宿主里都非法——要么用 slash 形式（无 flag 无管道，让模型把用户意图翻译成引擎 flag），要么用直接 CLI 形式（完整 `python3 ...` + 显式 flag + 真实 shell）。

## 命令 / Commands
```bash
# 开发 / fallback：直接调引擎（仅用于脚本化、cron 或引擎测试）
python3 skills/last30days-cn/scripts/last30days_cn.py "测试查询" --emit=compact

# 安装到本机（拷贝，安装时冻结；改了工作树要重跑同步）
npx skills add . -g -y

# 测试 / Tests（pytest，配置在 pyproject.toml）
uv run pytest                                   # 全套
uv run pytest tests/test_relevance_cjk.py       # 单文件
uv run pytest tests/test_relevance_cjk.py -k some_case   # 单用例
uv run pytest --cov                             # 带覆盖率
```

Python 3.12+ 必需。用 `uv` 管理环境，venv 在 `.venv/`。

## 规则 / Rules
- **仅标准库。** jieba 等第三方仅在 `try/except ImportError` 后可选使用，缺失时必须有回退。不引入运行期依赖（`pyproject.toml` 的 `dependencies = []`）。
- **不伪造数据。** 需登录的源拿不到凭据就返回 `[]`，由 SKILL.md 指挥宿主用 WebSearch 补充。
- **规范源名。** 流水线内部只用 10 个小写源名（见 CONCEPTS.md / PORT_CONTRACT §1）；中文标签 + emoji 只在渲染层。
- `lib/__init__.py` 必须是裸包标记（仅注释，**不做 eager import**）。
- 改任何跨文件接口前先读 `docs/PORT_CONTRACT.md`——它固定所有跨模块契约。
- 保留与数据源无关的流水线逻辑与所有结构性注释边界（`<!-- PASS-THROUGH FOOTER -->`、`<!-- EVIDENCE FOR SYNTHESIS -->` 等）。

## 安全卫生 / Security hygiene
- 绝不提交真实 API key、浏览器 cookie、auth token、access token 或 `.env` 内容。
- 用 `skills/last30days-cn/scripts/lib/env.py` 的基于 env 的 auth 模式；测试与 fixture 只用明显的 dummy 值。
- 文档、fixture、测试数据里的密钥要脱敏，不留可复制粘贴的真实凭据。微博 / 知乎 cookie 尤其敏感。

## 维护 CONFIGURATION.md / Maintaining CONFIGURATION.md

`CONFIGURATION.md` 是面向用户的配置参考——保存路径、per-source key、web-search 后端优先级、趋势监控栈、per-client 安装模式。与 `SKILL.md`（运行时 spec）不同。

出现以下情况时更新它：
- 新增 env var（如 `LAST30DAYS_*`、`*_API_KEY`、`WEIBO_COOKIE`、`ZHIHU_COOKIE`）
- 新增影响配置的 CLI flag（如 `--store`、`--web-backend`）
- 新增 per-client 安装模式
- 新增需要自己凭据的可选源
- 改变配置层优先级（per-run flag > env > `.env` 文件 > 默认）

按各层被触碰的频率组织：per-run flags → env / `.env` → 可选趋势监控栈 → per-client 模式。新内容加进对应小节，而不是追加到末尾。

When a new config concept lands in `SKILL.md`, mirror the user-facing knob in
`CONFIGURATION.md` so non-agent readers can configure the skill without reverse-
engineering it from the runtime spec.

## 上游与署名 / Upstream & attribution
- 派生自 [`mvanhorn/last30days-skill`](https://github.com/mvanhorn/last30days-skill)（作者 Matt Van Horn），MIT 许可。
- 移植时保留上游与数据源无关的流水线架构与结构性注释；西方 provider 已删，换为中国平台 provider。
- 移植契约见 `docs/PORT_CONTRACT.md`。
