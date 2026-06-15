# Changelog

All notable changes to this project will be documented in this file.
本文件记录本项目所有重要变更。

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added / 新增

- **抗操纵打分层 / Anti-manipulation scoring layer** (`signals.manipulation_signals`) —
  对 SEO / GEO / 水军 的主动防御：检测刷量式互动结构异常（高互动零评论 / 评论占比极低）、
  推广话术、关键词与链接/话题标签堆砌，并在重排前保守降权（只降序不删除，命中原因记入
  `metadata.manipulation_flags`）。同时把 `signals.py` 的源质量与互动权重本地化为中国数据源。
  Active defense against SEO / GEO / astroturfing: down-weights farmed engagement
  shapes, promo/keyword/link/hashtag spam; demote-only and explainable. Also
  localizes source-quality and engagement weights to the China-market sources.

## [1.0.0] - 2026-06-14

首个发布版本：`mvanhorn/last30days-skill` 的中国市场完整移植。保留与数据源无关的流水线逻辑，替换数据源与推理 provider，输出改为中英双语。

Initial release: a complete China-market port of `mvanhorn/last30days-skill`. The
source-agnostic pipeline (plan → parallel retrieve → dedupe → cluster → rerank →
synthesize) is preserved; data sources and reasoning providers are swapped, and
synthesis output is bilingual (中文 + EN).

### Added / 新增

**数据源 / Sources** — 10 个规范源名替换西方平台：

- 微博 weibo (← X) — `m.weibo.cn` 移动端容器接口，建议配置 `WEIBO_COOKIE`，无凭据返回空。
- 知乎 zhihu (← Reddit) — `zhihu.com` search_v3，需 `ZHIHU_COOKIE`（z_c0 / d_c0），无凭据返回空。
- B站 bilibili (← YouTube) — B站 Web 搜索 API，免 key（wbi 签名 / buvid 降级）。
- 抖音 douyin (← TikTok) — ScrapeCreators 抖音端点，需 `SCRAPECREATORS_API_KEY`。
- 小红书 xiaohongshu (← Instagram) — 本地 HTTP 服务（`XIAOHONGSHU_API_BASE`）或 ScrapeCreators。
- V2EX v2ex (← Hacker News) — sov2ex 全文搜索 API，免 key。
- 掘金 juejin (← Hacker News) — 掘金搜索 API，免 key。
- GitHub github — 保留上游实现，`GITHUB_TOKEN` 可选提限额。
- 雪球 xueqiu (← Polymarket) — 自举 `xq_a_token` cookie 后查讨论 / 情绪，映射为情绪体。
- 网页 grounding — 保留上游实现（Brave / Exa / Serper / Parallel / 宿主 WebSearch）。

**推理 provider / Reasoning providers** — 新增 4 个国产 OpenAI 兼容模型，保留 Gemini / OpenAI / xAI / OpenRouter：

- DeepSeek 深度求索 — `deepseek-chat`，key `DEEPSEEK_API_KEY`。
- 通义千问 Qwen (DashScope OpenAI 兼容) — `qwen-plus`，key `DASHSCOPE_API_KEY`。
- Kimi (Moonshot) — `moonshot-v1-8k`，key `MOONSHOT_API_KEY`。
- 智谱 GLM — `glm-4-flash`，key `ZHIPU_API_KEY`。
- `auto` 探测顺序：google → openai → xai → openrouter → deepseek → dashscope → moonshot → zhipu → local。

**中文支持 / Chinese support**

- CJK 分词：`relevance.tokenize()` / `query.extract_core_subject()` 优先用 `jieba`（可选，缺失时回退到 CJK 字符 bigram 切分），加入中文停用词与 noise words；英文逻辑全部保留。
- 中文意图识别：planner 的 `_infer_intent` 增加中文正则（对比 / 预测 / how_to / factual / opinion / breaking_news）。
- 分类关键词本地化为中文（科技 / 财经 / 数码 / 游戏 / 影视 / 生活 / 汽车 …）。

**双语输出 / Bilingual output**

- 徽章固定首行：`🌐 最近30天 · last30days-cn v{VERSION} · 已同步 {YYYY-MM-DD}`。
- 通用查询正文：徽章 → `TL;DR (EN)` 英文摘要 → `我了解到：` 中文段落 → `研究中的关键模式：` 中文编号列表 → 引擎页脚（表情树原样透传）。
- 源标签与 emoji 中文化（微博 🔴 / 知乎 🔵 / B站 📺 / 抖音 🎵 / 小红书 📕 / V2EX 💻 / 掘金 ⛏️ / GitHub 🐙 / 雪球 📈 / 网页 🌐）。
- 全部 8 条 LAWs 移植并本地化。

**配置 / Configuration**

- 新增 env key：`WEIBO_COOKIE`、`ZHIHU_COOKIE`、`GITHUB_TOKEN`、`XIAOHONGSHU_API_BASE`、`DEEPSEEK_API_KEY`、`DASHSCOPE_API_KEY`、`MOONSHOT_API_KEY`、`ZHIPU_API_KEY`。
- 保留 `SCRAPECREATORS_API_KEY`、web search keys、`LAST30DAYS_*` 系列。
- cookie 域名表改为 `.weibo.cn` / `.zhihu.com`。
- `XIAOHONGSHU_API_BASE` 默认 `http://host.docker.internal:18060`。

### Changed / 变更

- 包名 / skill 名 / slash command 由 `last30days` 改为 `last30days-cn`。
- CLI flag 重命名：`--subreddits` → `--zhihu-topics`、`--x-handle` → `--weibo-handle`、`--x-related` → `--weibo-related`、`--tiktok-*` → `--douyin-*`、`--ig-creators` → `--xhs-creators`；`--github-user/--github-repo`、`--competitors*`、`--plan`、`--emit`、`--save-dir`、`--depth`、`--mock`、`--agent` 等保留。
- `ProviderRuntime.reasoning_provider` 的类型从 `Literal[...]` 放宽为 `str`，以容纳国产 provider。

### Removed / 移除

- 删除西方独有数据源 provider 与 helper：X / Reddit / YouTube / TikTok / Instagram / Bluesky / TruthSocial / Pinterest / Threads / Digg / Polymarket / Perplexity。

### Attribution / 署名

- 派生自 [`mvanhorn/last30days-skill`](https://github.com/mvanhorn/last30days-skill)（作者 Matt Van Horn），遵循 MIT 许可。
