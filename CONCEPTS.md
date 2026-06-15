# Concepts / 概念

`last30days-cn` 的共享词汇。这里的术语都有精确的、项目特有的含义——与它们的一般技术含义足够不同，新贡献者需要看定义才能跟上对话、PR 描述或 SKILL.md 合约。

Shared vocabulary for `last30days-cn`. Terms here have a precise project-specific
meaning — distinct enough from their general technical sense that a new
contributor would need them defined.

## 这个包 / The package

### Skill

一个自包含的 agent 指令包：`SKILL.md` 散文合约 + 同级 `scripts/` 可执行目录。包遵循 [Agent Skills](https://agentskills.io) 开放格式，可经 `npx skills add`、宿主原生插件安装器或 per-host skill 目录安装到几乎所有主流宿主（Claude Code、Codex、Cursor、GitHub Copilot、Gemini CLI 等 50+）。Skill 是分发单元，也是产品本身。

A self-contained agent-instructions package: a `SKILL.md` prose contract plus a
sibling `scripts/` directory. The Skill is the unit of distribution; the Skill is
the product.

### Engine / 引擎

Skill 的 SKILL.md 调用、用来真正干检索活的 Python 脚本（`scripts/last30days_cn.py`）。Engine 与 SKILL.md 之间有合约：SKILL.md 告诉模型该传哪些 flag（`--plan`、`--competitors-plan`、`--weibo-handle`、`--zhihu-topics`、`--emit=compact` 等），Engine 产出固定形状的输出（徽章行、排序后的证据簇、表情树页脚），模型有合约义务原样透传。Engine 是实现，SKILL.md 散文是面向 agent 的表层。

The Python script (`scripts/last30days_cn.py`) the Skill's SKILL.md invokes. The
Engine and SKILL.md have a contract: SKILL.md says which flags to pass; the Engine
produces a fixed output shape the model is contractually required to pass through.

### Harness / 宿主

加载 Skills 并代用户调用它们的 agent 运行时。Claude Code 是本 Skill 最常见的宿主，但不是唯一——Codex、Cursor、GitHub Copilot、Gemini CLI 以及整个 Agent Skills 生态都算。"多宿主（multi-harness）"指一个 Skill 在它能安装进的每个宿主上都正确工作；写功能时若缺乏多宿主意识（如引擎 flag 没有 SKILL.md 集成，或路径硬编码到某一个宿主的安装布局），就会在其他宿主上回归。

The agent runtime that loads Skills. Claude Code is the most common Harness but
not the only one. "Multi-harness" describes a Skill that works correctly across
every Harness it installs into.

## 移植特有概念 / Port-specific concepts

### 规范源名 / Canonical source name

引擎全程只认 10 个小写源名：`weibo zhihu bilibili douyin xiaohongshu v2ex juejin github xueqiu grounding`。所有面向用户的中文标签 + emoji（微博 🔴、知乎 🔵 …）只在渲染层出现；流水线内部一律用规范源名。`pipeline.SEARCH_ALIAS` 提供别名（`wb→weibo`、`zh→zhihu`、`bili→bilibili`、`b站→bilibili`、`xhs→xiaohongshu` 等）。

The engine uses exactly 10 lowercase source names internally; Chinese labels +
emoji appear only at the render layer.

### 分层取数 / Tiered access

取数按凭据需求分层：能免 key 的（V2EX / 掘金 / B站 / 雪球 / GitHub）走公开或半公开接口，始终可用；需登录的（微博 / 知乎 / 抖音 / 小红书）先尝试 cookie 或 scraper key，**拿不到就返回空、绝不伪造数据**，由 SKILL.md 指挥宿主模型用 WebSearch 补充。"拿不到数据返回空"是硬约束，不是降级选项。

Sources are tiered by credential need. Login-walled sources return **empty (never
fabricated) data** when credentials are missing. "Return empty on no data" is a
hard constraint, not a soft fallback.

### 情绪体 / Sentiment shape

雪球（xueqiu）对应上游的 Polymarket。Polymarket 有真实赔率，雪球没有；移植把雪球的多空讨论量 / 涨跌情绪映射到原 Polymarket 的"情绪体" raw item 形状（`volume`、`price_movement`、`outcome_prices` 等），复用其归一化与渲染路径，但语义是讨论情绪而非下注赔率。

Xueqiu maps to upstream Polymarket. There are no real-money odds; the port maps
Xueqiu's bull/bear discussion volume and price sentiment onto the original
"sentiment shape" raw item, reusing the normalize/render path.

### CJK 分词回退 / CJK tokenization fallback

中文无空格，原引擎的空格切分会把整句当一个 token。移植在 `relevance.tokenize()` / `query.extract_core_subject()` 优先 `import jieba`；jieba 缺失时回退到对 CJK 连续串做**字符 bigram** 切分（「人工智能」→ {人工, 工智, 智能}），非 CJK 部分保留原英文空格切分。jieba 是可选增强，不是依赖。

Chinese has no spaces. The port tokenizes via `jieba` when importable, falling
back to CJK character bigrams. jieba is an optional enhancement, not a dependency.
