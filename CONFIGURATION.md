# Configuration / 配置

`/last30days-cn` 不改引擎源码就能调的所有旋钮。三层，按你触碰的频率排序：

Everything you can tune in `/last30days-cn` without editing the engine source.
Three layers, in order of how often you'll touch them:

1. **Per-run flags / 单次运行的命令行参数** — what you pass on the command line.
2. **Environment variables and `.env` / 环境变量与 `.env`** — what's enabled across all runs.
3. **Optional trend-monitoring stack / 可选的趋势监控栈** — SQLite store, watchlist, briefings.

> 运行时合约（语气规则、planner 协议、模型遵循的 LAWs）以 [`skills/last30days-cn/SKILL.md`](skills/last30days-cn/SKILL.md) 为准——两处冲突时它优先。本文件只负责把每个旋钮集中列出并与代码保持同步。引擎新增配置项应在同一 PR 中同步到本文件。
>
> The runtime contract lives in [`skills/last30days-cn/SKILL.md`](skills/last30days-cn/SKILL.md) — authoritative when the two differ. This file's job is narrower: surface every knob in one place, kept current with the code.

---

## 输出保存位置 / Where output is saved

| 平台 / Platform | 默认路径 / Default path | 覆盖 / Override |
|---|---|---|
| Linux / macOS | `LAST30DAYS_MEMORY_DIR` 默认 `~/Documents/Last30Days/` | 设 `LAST30DAYS_MEMORY_DIR=/path` |
| Windows | `LAST30DAYS_MEMORY_DIR` 默认 `C:\Users\<you>\Documents\Last30Days\` | 设 `LAST30DAYS_MEMORY_DIR=C:\path` |

每次运行按 slug 产出一个文件：`<slug>-raw[-suffix].md`。同话题 + 同 suffix 同一天覆盖，不同天追加日期戳。

**单次运行覆盖 / Per-run overrides:**
- `--save-dir <path>` — 一次性输出位置 / one-off output location.
- `--save-suffix <name>` — 区分同一话题的不同运行（如按客户 `--save-suffix=acme`）。

---

## API 密钥 / API keys (`.env`)

skill 按以下优先级从 `.env` 读取密钥（高优先级覆盖低优先级）：

The skill reads keys from `.env` in this priority order (highest wins):

1. **进程环境变量 / Process environment** (`os.environ`).
2. **`.claude/last30days.env`** — 当前项目目录（向上递归查找），per-project，命中即优先。
3. **`~/.config/last30days/.env`** — 用户级全局默认。用 `LAST30DAYS_CONFIG_DIR=/path` 改位置，`LAST30DAYS_CONFIG_DIR=""` 关闭文件模式。
4. **macOS Keychain** — service 名前缀 `last30days-` 的条目（仅 Darwin，最低优先级，加性）。

POSIX 上文件权限应为 `600`，否则引擎每次运行都告警。After editing: `chmod 600 ~/.config/last30days/.env`.

项目级文件是 **per-client 设置**的最干净模式：往每个客户目录丢一个 `.claude/last30days.env`，`cd` 进去即自动生效，无需包装脚本。

### 数据源密钥逐项说明 / Source-by-source keys

| 源 / Source | 凭据 / Key(s) | 必需于 / Required for | 免费层 / Free tier |
|---|---|---|---|
| V2EX `v2ex` | 无 / none | 始终可用 / always on（sov2ex 免 key 全文搜索） | yes |
| 掘金 `juejin` | 无 / none | 始终可用 / always on（掘金搜索 API 免 key） | yes |
| B站 `bilibili` | 无 / none | 始终可用 / always on（Web 搜索 API，wbi 签名 / buvid 降级） | yes |
| 雪球 `xueqiu` | 无 / none | 始终可用 / always on（自举 `xq_a_token` cookie） | yes |
| GitHub `github` | `GITHUB_TOKEN`（可选 / optional） | 始终可用；token 仅提高公开 API 限额 | yes |
| 微博 `weibo` | `WEIBO_COOKIE`（建议）或 `SCRAPECREATORS_API_KEY` | 结果中出现微博项 / Weibo items | cookie 免费 |
| 知乎 `zhihu` | `ZHIHU_COOKIE`（z_c0 / d_c0）或 `SCRAPECREATORS_API_KEY` | 结果中出现知乎项 / Zhihu items | cookie 免费 |
| 抖音 `douyin` | `SCRAPECREATORS_API_KEY` | 结果中出现抖音项 / Douyin items | 100 免费额度后 PAYG |
| 小红书 `xiaohongshu` | `XIAOHONGSHU_API_BASE`（本地服务）或 `SCRAPECREATORS_API_KEY` | 结果中出现小红书项；per-query opt-in | 本地自建免费 / SC PAYG |
| 网页 `grounding` | `BRAVE_API_KEY` / `EXA_API_KEY` / `SERPER_API_KEY` / `PARALLEL_API_KEY` 任一 | `--auto-resolve`、Step 2 补充搜索 | Brave 有免费层；宿主原生 WebSearch 可兜底 |

> **分层取数原则（PORT_CONTRACT §2）：** 能免 key 的走公开 / 半公开接口；需登录的（微博 / 知乎 / 抖音 / 小红书）拿不到凭据时 **返回空、绝不伪造数据**，由 SKILL.md 指挥宿主模型用 WebSearch 补充。
>
> **Tiered access:** key-free sources are always on; login-walled sources return **empty (never fabricated) data** when credentials are missing.

### 各源凭据获取方式 / How to obtain credentials

- **`WEIBO_COOKIE`** — 浏览器登录 `m.weibo.cn`，从开发者工具复制 Cookie（主要是 `SUB`）。也可设 `FROM_BROWSER=auto` 让引擎从本地浏览器（Firefox / Safari，`auto` 还含 Chrome）自动提取 `.weibo.cn` 域的 cookie。
- **`ZHIHU_COOKIE`** — 浏览器登录 `zhihu.com`，复制 `z_c0`（必要，登录态）与 `d_c0`。同样可经 `FROM_BROWSER` 从 `.zhihu.com` 域自动提取。
- **`GITHUB_TOKEN`** — GitHub Settings → Developer settings → Personal access tokens；可选，仅用于提高公开 API 限额。
- **`SCRAPECREATORS_API_KEY`** — scrapecreators.com 注册；抖音与小红书爬虫路径共用。支持逗号分隔多 key，每次运行随机轮询。也兼容上游 `SCRAPE_CREATORS_API_KEY` 拼写。
- **`XIAOHONGSHU_API_BASE`** — 本地小红书 HTTP 服务的基址，默认 `http://host.docker.internal:18060`（便于 Docker 内访问宿主服务）。引擎通过 `/health` 与 `/api/v1/login/status` 探活，未登录则该源不可用。

### `.env` 骨架示例 / Example skeleton（占位符，替换为你的真实值）

```bash
# 推理 provider（择一即可；优先级见下文 / one provider, see priority below）
DEEPSEEK_API_KEY=<your-deepseek-key>

# 网页搜索后端（择一即可 / one is enough）
BRAVE_API_KEY=<your-brave-key>

# 登录态数据源（可选 / optional login-walled sources）
WEIBO_COOKIE=<your-weibo-cookie>
ZHIHU_COOKIE=<your-zhihu-z_c0-cookie>

# 爬虫数据源（抖音 / 小红书 / optional scraper sources）
SCRAPECREATORS_API_KEY=<your-scrapecreators-key>

# 小红书本地服务（如有 / optional local service）
XIAOHONGSHU_API_BASE=http://host.docker.internal:18060

# GitHub 限额（可选 / optional rate-limit bump）
GITHUB_TOKEN=<your-github-token>
```

---

## 推理 provider 优先级 / Reasoning provider priority

不传 `--plan` 时，skill 需要一个推理模型做 planning + reranking。`auto` 探测顺序（用 `LAST30DAYS_REASONING_PROVIDER=<name>` 锁定一个）：

When you don't pass `--plan`, the skill needs one reasoning model. `auto` detection order (pin with `LAST30DAYS_REASONING_PROVIDER=<name>`):

1. **Gemini** — `GOOGLE_API_KEY` / `GEMINI_API_KEY` / `GOOGLE_GENAI_API_KEY`
2. **OpenAI** — `OPENAI_API_KEY`
3. **xAI** — `XAI_API_KEY`
4. **OpenRouter** — `OPENROUTER_API_KEY`
5. **DeepSeek 深度求索** — `DEEPSEEK_API_KEY`（默认模型 `deepseek-chat`）
6. **通义千问 Qwen (DashScope)** — `DASHSCOPE_API_KEY`（默认 `qwen-plus`）
7. **Kimi (Moonshot)** — `MOONSHOT_API_KEY`（默认 `moonshot-v1-8k`）
8. **智谱 GLM** — `ZHIPU_API_KEY`（默认 `glm-4-flash`）
9. **Local / 确定性** — 始终可用，质量最低 / always available, lowest quality

国产 4 家都是 OpenAI 兼容的 `/chat/completions` 形态。用 `LAST30DAYS_PLANNER_MODEL` / `LAST30DAYS_RERANK_MODEL` 覆盖具体模型名。

> 从 Claude Code / Codex / Gemini 调用本 skill 时，宿主模型本身就是推理 provider——上面的 key 仅在你无人值守直接跑脚本（cron / CI / watchlist）时才需要。
>
> When invoked from a host, the host model **is** the provider — these keys are only needed for headless runs.

---

## 网页搜索后端优先级 / Web search backend priority

用于 `--auto-resolve`（宿主无 WebSearch 时）与 Step 2 补充搜索。`auto` 探测顺序（用 `--web-backend=<name>` 单次覆盖）：

1. **Brave** — `BRAVE_API_KEY`
2. **Exa** — `EXA_API_KEY`
3. **Serper** — `SERPER_API_KEY`
4. **Parallel** — `PARALLEL_API_KEY`
5. **宿主原生 WebSearch / Host's native WebSearch** — Claude Code / Codex / Gemini 内置

---

## 运行时旋钮 / Runtime knobs

| Env / Flag | 作用 / Effect | 默认 / Default |
|---|---|---|
| `LAST30DAYS_REASONING_PROVIDER` | 锁定推理 provider（`auto` / `gemini` / `openai` / `xai` / `openrouter` / `deepseek` / `dashscope` / `moonshot` / `zhipu` / `local`） | `auto` |
| `LAST30DAYS_PLANNER_MODEL` | 覆盖 planner 模型名 | 按 provider 默认 |
| `LAST30DAYS_RERANK_MODEL` | 覆盖 rerank 模型名 | 按 provider 默认 |
| `LAST30DAYS_MEMORY_DIR` | 研究输出目录 | `~/Documents/Last30Days/` |
| `LAST30DAYS_CONFIG_DIR` | `.env` 全局目录（`""` 关闭文件模式） | `~/.config/last30days/` |
| `LAST30DAYS_STORE` / `--store` | 持久化到 SQLite（趋势监控） | off |
| `INCLUDE_SOURCES` | 逗号分隔，显式纳入额外源 | 空 / empty |
| `EXCLUDE_SOURCES` | 逗号分隔，抑制指定源（`INCLUDE_SOURCES` 的反向） | 空 / empty |
| `FROM_BROWSER` | 浏览器 cookie 提取：`off` / `firefox` / `safari` / `chrome` / `auto` | 默认仅静默浏览器（firefox + safari） |
| `--depth` | 检索深度 `quick`(15) / `default`(30) / `deep`(60) 条 | `default` |
| `--zhihu-topics` | 知乎话题 / 专栏定向（原 `--subreddits`） | — |
| `--weibo-handle` / `--weibo-related` | 微博账号定向 / 相关补搜（原 `--x-handle` / `--x-related`） | — |
| `--douyin-*` | 抖音定向（原 `--tiktok-*`） | — |
| `--xhs-creators` | 小红书博主定向（原 `--ig-creators`） | — |
| `--github-user` / `--github-repo` | GitHub 人 / repo 定向 | — |
| `--competitors` / `--competitors-plan` | 竞品自动发现 / 预置竞品计划 JSON | — |
| `--plan` | 宿主作为 planner 传入预置计划 JSON | — |
| `--emit` | 输出格式（`compact` / `html` …） | — |
| `--mock` | 用 mock 数据（无网络，测试用） | off |

---

## 趋势监控 / Trend monitoring (`--store` + watchlist + briefings)

默认是快照模式（每话题一个文件，重跑覆盖）。持续监控用三件套：

- **`--store`**（或 `LAST30DAYS_STORE=1`）— 持久化每条 finding 到 SQLite（默认 `~/.local/share/last30days/research.db`），按 `source_url` 去重。
- **`scripts/watchlist.py`** — 管理定期检索的话题；子命令 `add` / `remove` / `list` / `run-one` / `run-all` / `config`；新 finding 出现时可投递到 Slack webhook 或任意 HTTPS 端点。实际的 cron / 定时调度由你负责。
- **`scripts/briefing.py`** — 读 SQLite 产出日 / 周摘要的结构化数据，交给 agent 综述。

---

## per-client 模式 / Per-client patterns

- **per-client `.claude/last30days.env`** — 每个客户目录一份，`cd` 进去自动生效（推荐）。
- **save dir + suffix 包装函数** — 不 `cd` 进客户目录时，用 shell 函数设 `LAST30DAYS_MEMORY_DIR` + `--save-suffix`。
- **自定义分类 peer** — [`scripts/lib/categories.py`](skills/last30days-cn/scripts/lib/categories.py) 是 `(category_id, 触发关键词, peer 来源)` 的纯数据表，可加行。
- **预置 `--competitors-plan` JSON** — 复用的竞品对比，写一份 JSON 骨架按 `--competitors-plan @path.json` 传入。

---

## 交叉引用 / Cross-references

- CLI flag 全集 / Full flag surface: `python3 skills/last30days-cn/scripts/last30days_cn.py --help`
- skill 合约（语气、LAWs、预检协议）/ Skill contract: [`skills/last30days-cn/SKILL.md`](skills/last30days-cn/SKILL.md)
- 领域词汇 / Domain vocabulary: [`CONCEPTS.md`](CONCEPTS.md)
- 贡献者 / agent 指引 / Contributor guidance: [`AGENTS.md`](AGENTS.md)
