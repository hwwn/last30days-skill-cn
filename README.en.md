<!-- 语言 / Language: --> [简体中文](README.md) · **English**

# /last30days-cn · Last 30 Days (China edition)

**An AI agent-led search engine scored by likes, reposts, danmaku, engagement, and real money — not editors.**

> 🌐 **Reading the Chinese market from the outside is hard.** The highest-signal Chinese conversation lives behind login walls (Weibo, Zhihu) and inside apps Western tools never touch (Bilibili, Douyin, Xiaohongshu). `last30days-cn` searches **Weibo, Zhihu, Bilibili, Douyin, Xiaohongshu, V2EX, Juejin, GitHub & Xueqiu** in parallel, scores results by real engagement, and synthesizes a cited, **bilingual (中文 + EN)** brief.

> 🇨🇳 **This project is a China-market adaptation of [`mvanhorn/last30days-skill`](https://github.com/mvanhorn/last30days-skill).** It keeps the original's source-agnostic pipeline (plan → parallel retrieve → dedupe → cluster → rerank → synthesize) and swaps Western platforms (Reddit / X / YouTube / TikTok / Polymarket …) for the Chinese ecosystem (Weibo / Zhihu / Bilibili / Douyin / Xiaohongshu / V2EX / Juejin / Xueqiu …), with bilingual synthesis output. Full attribution at the bottom.

The runtime contract (voice rules, planner protocol, LAWs) lives in [`skills/last30days-cn/SKILL.md`](skills/last30days-cn/SKILL.md), the source of truth.

---

## Install

**Claude Code (recommended — auto-updates via marketplace):**
```
/plugin marketplace add hwwn/last30days-skill-cn
/plugin install last30days-cn
```

**Codex / Cursor / Copilot / Gemini CLI, or any of 50+ [Agent Skills](https://agentskills.io) hosts:**
```
npx skills add hwwn/last30days-skill-cn -g
```
(`-g` installs globally for your user, available across all projects. Drop it to scope per-project.)

**Manual (developer):**
```bash
git clone https://github.com/hwwn/last30days-skill-cn.git
ln -sfn "$(pwd)/last30days-skill-cn/skills/last30days-cn" ~/.claude/skills/last30days-cn
```

Zero config — **V2EX, Juejin, GitHub, Bilibili, Xueqiu** work immediately (all attempt keyless access). Run it once and the setup wizard walks you through unlocking Weibo & Zhihu after a browser login.

---

Weibo reposts. Zhihu upvotes. Bilibili danmaku and view counts. Douyin & Xiaohongshu recommendations. Xueqiu bull/bear sentiment backed by real money. That's hundreds of millions of people voting with their attention and their wallets every day. `/last30days-cn` searches all of it in parallel, scores it by what real people actually engage with, and an AI agent judge synthesizes it into one brief.

**Baidu aggregates SEO and ads. `/last30days-cn` searches people.**

Each Chinese platform is a walled garden with its own API, its own auth, its own cookies. No single AI reaches all of it. But bring your own keys and browser sessions, and an agent can search all of them at once, score them against each other, and tell you what actually matters.

```
/last30days-cn how to choose a Chinese LLM
/last30days-cn BYD
/last30days-cn DeepSeek vs Kimi vs Qwen
/last30days-cn what React users are complaining about
```

## Why this exists

China's tech and consumer landscape moves every day, and the Zhihu / Weibo / Bilibili crowd is always first to know. Training data is always months behind what the community already figured out — and for the China market, English-language coverage lags even further. You need the **last 30 days** truth — before a meeting, before sizing up a competitor, before launching anything into the market.

Meeting a Chinese founder tomorrow? Have you read all their Weibo posts and Bilibili interviews from the last 30 days? This has.

## Sources, scored by the people

| Source | emoji | Western analog | What the people tell you | Credential |
|---|---|---|---|---|
| Weibo 微博 | 🔴 | X/Twitter | The hot take, the breaking reaction, reposts/likes | `WEIBO_COOKIE` (optional) |
| Zhihu 知乎 | 🔵 | Reddit | The unfiltered deep-dive, top answers & comments | `ZHIHU_COOKIE` (z_c0/d_c0) |
| Bilibili B站 | 📺 | YouTube | The long-form deep dive + danmaku sentiment | keyless |
| Douyin 抖音 | 🎵 | TikTok | The short-video take reaching millions | `SCRAPECREATORS_API_KEY` |
| Xiaohongshu 小红书 | 📕 | Instagram | Product reviews & lifestyle signal | `XIAOHONGSHU_API_BASE` / SC |
| V2EX | 💻 | Hacker News | Where technical people actually argue | keyless |
| Juejin 掘金 | ⛏️ | Hacker News | Developer articles & technical trends | keyless |
| GitHub | 🐙 | GitHub | PR velocity, stars, releases, issues | `GITHUB_TOKEN` (optional) |
| Xueqiu 雪球 | 📈 | Polymarket | Not opinions — bull/bear sentiment with skin in the game | auto-fetched cookie |
| Web grounding | 🌐 | Web | Editorial coverage, blog comparisons — one signal of many | web search key or host WebSearch |

A 1,500-upvote Zhihu answer is a stronger signal than a blog post nobody read. A 3M-view Bilibili video tells you more about what's culturally relevant than a press release. Synthesis ranks by **real engagement** — social relevancy, not SEO relevancy.

> Login-walled sources return **empty (never fabricated) data** when credentials are missing; the host model supplements via WebSearch (`site:zhihu.com` / `site:weibo.com` …).

## Reasoning providers (incl. Chinese models)

Retains Gemini / OpenAI / xAI / OpenRouter and adds 4 Chinese OpenAI-compatible providers. `auto` probe order: google → openai → xai → openrouter → **deepseek → dashscope → moonshot → zhipu** → local.

| Provider | Default model | Key |
|---|---|---|
| DeepSeek | `deepseek-chat` | `DEEPSEEK_API_KEY` |
| Qwen / Tongyi (DashScope) | `qwen-plus` | `DASHSCOPE_API_KEY` |
| Kimi (Moonshot) | `moonshot-v1-8k` | `MOONSHOT_API_KEY` |
| Zhipu GLM | `glm-4-flash` | `ZHIPU_API_KEY` |

When invoked from Claude Code / Codex / Gemini, the host model **is** the planner — no keys needed unless you run the script headlessly.

## What people use it for

- **Before a meeting** — `/last30days-cn Wang Huiwen` surfaces what they're doing this month, recent statements and interviews — not a 3-year-old bio.
- **Competitors & news** — `/last30days-cn Xiaomi SU7` for recent reception, delivery feedback, and controversies across Weibo / Zhihu / Bilibili / Xiaohongshu.
- **Compare tools** — `/last30days-cn DeepSeek vs Kimi vs Qwen` runs three pipelines in parallel: what the community says, plus live GitHub stars.
- **Learn fast** — `/last30days-cn Cursor workflow` pulls community-tested usage, then writes you a ready-to-use practice guide.
- **Then it becomes your expert** — after one run, your session knows what the community knows. Ask follow-ups, draft copy, make the call.

## How it works

1. **You type a topic.** Person, company, product, technology, "X vs Y." Anything.
2. **The agent resolves who matters.** Weibo/Zhihu topics, GitHub repos, Bilibili creators, Douyin hashtags.
3. **All sources searched in parallel.** Multi-query expansion, scored by engagement / relevance / freshness.
4. **Same story, merged.** One event across Weibo, Zhihu and Bilibili becomes one cluster, not three separate items.
5. **Synthesized into one brief.** Grounded in specific data, cited inline by source, ranked by real engagement. **Bilingual (中文 + EN).**
6. **Then it becomes your expert.** Just keep asking.

## Bring your own keys

| Sources | What you need | Cost |
|---|---|---|
| V2EX + Juejin + GitHub + Bilibili + Xueqiu | Nothing | Free |
| Weibo | `WEIBO_COOKIE` after a browser login | Free |
| Zhihu | `ZHIHU_COOKIE` after a browser login | Free |
| Douyin + Xiaohongshu (scraper path) | ScrapeCreators key | PAYG |
| Xiaohongshu (local path) | Local service `XIAOHONGSHU_API_BASE` | Self-hosted |
| Web search | Brave / Exa / Serper / Parallel (any one) | Brave has a free tier |

On macOS, store keys in the system Keychain under the `last30days-` service prefix — picked up automatically as the lowest-priority source. Full matrix in [CONFIGURATION.md](CONFIGURATION.md).

## Chinese tokenization

Chinese has no spaces, so the original engine's whitespace splitting breaks. The port tokenizes via `jieba` when available in `relevance.tokenize()` / `query.extract_core_subject()`, falling back to CJK character bigrams, with Chinese stopwords added and all English logic preserved. **jieba is optional** — the skill runs without it.

## Open source

MIT license. No tracking, no analytics. Your research stays on your machine. Python 3.12+, **standard library only** (third-party packages like jieba are optional enhancements with graceful fallback). See [CHANGELOG.md](CHANGELOG.md).

## Attribution

This project is a China-market port of [`mvanhorn/last30days-skill`](https://github.com/mvanhorn/last30days-skill) by **Matt Van Horn ([@mvanhorn](https://github.com/mvanhorn))**, used under its MIT License. The original author retains copyright over upstream code; port-specific changes are released under the same MIT terms. Thanks to the original author for the source-agnostic pipeline architecture that made this adaptation possible. See [NOTICE](NOTICE).
