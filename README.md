**简体中文** · [English](README.en.md)

# /last30days-cn · 最近30天（中国版）

**一个由 AI agent 主导的搜索引擎：按点赞、转发、弹幕、讨论量和真金白银排序，而不是按编辑的喜好。**

> 🌐 **English speaker researching the China market?** `last30days-cn` pulls the last-30-days, real-engagement conversation from **Weibo, Zhihu, Bilibili, Douyin, Xiaohongshu, V2EX, Juejin, GitHub & Xueqiu**, then synthesizes a cited, **bilingual** brief. The China market is the hardest to read from the outside — this is built for exactly that. → **Read the full [English README »](README.en.md)**

> 🇨🇳 **本项目参考 [`mvanhorn/last30days-skill`](https://github.com/mvanhorn/last30days-skill)，是针对中国市场做出适应的「中国市场版 last30days-skill」。** 它保留原项目与数据源无关的流水线（计划 → 并行检索 → 去重 → 聚类 → 重排 → 综述），把西方平台（Reddit / X / YouTube / TikTok / Polymarket …）整体替换为中国主流平台（微博 / 知乎 / B站 / 抖音 / 小红书 / V2EX / 掘金 / 雪球 …），并把综述改为中英双语输出。完整署名见文末。

运行时契约（语气规则、planner 协议、LAWs）以 [`skills/last30days-cn/SKILL.md`](skills/last30days-cn/SKILL.md) 为唯一真相。

---

## 安装

**Claude Code（推荐，经市场自动更新）:**
```
/plugin marketplace add hwwn/last30days-skill-cn
/plugin install last30days-cn
```

**Codex / Cursor / Copilot / Gemini CLI 等 50+ [Agent Skills](https://agentskills.io) 宿主:**
```
npx skills add hwwn/last30days-skill-cn -g
```
（`-g` 全局安装到用户目录，跨项目可用；去掉则按项目安装。）

**手动安装（开发者）:**
```bash
git clone https://github.com/hwwn/last30days-skill-cn.git
ln -sfn "$(pwd)/last30days-skill-cn/skills/last30days-cn" ~/.claude/skills/last30days-cn
```

零配置即可用：**V2EX、掘金、GitHub、B站、雪球**开箱即用（全部尝试免 key）。跑一次，首次向导会带你在浏览器登录后解锁微博、知乎。

---

微博转发。知乎赞同。B站弹幕与播放。抖音/小红书的种草。雪球的多空情绪，背后是真金白银。这是每天数以亿计的人用注意力和钱包投票的结果。`/last30days-cn` 把这些平台**并行**搜一遍，按真实用户的参与度互相打分，再由一个 AI agent judge 综述成一份简报。

**百度聚合的是 SEO 和广告，`/last30days-cn` 搜索的是人。**

每个中国平台都是一座围墙花园，有自己的 API、自己的登录态、自己的 cookie。没有任何一个 AI 能同时访问全部。但你可以带上自己的 key 和浏览器会话，一个 agent 就能同时搜遍所有平台、互相比对、告诉你什么才真正重要。

```
/last30days-cn 国产大模型怎么选
/last30days-cn 比亚迪
/last30days-cn DeepSeek vs Kimi vs 通义千问
/last30days-cn React 用户最近在抱怨什么
```

## 为什么存在

AI 圈每天都在变，知乎、微博、B站上的人总是最先吃到瓜。训练数据永远落后社区几个月。我需要知道**最近 30 天**社区真正在聊什么——开会前、调研竞品前、做决定前。

如果你明天要见一个 CEO，你读过他最近 30 天的所有微博和 B站访谈吗？这个工具读过。

## 数据源（由人来打分）

| 源 | emoji | 西方原型 | 它告诉你什么 | 凭据 |
|---|---|---|---|---|
| 微博 weibo | 🔴 | X/Twitter | 热点的第一反应、大V观点、转评赞 | `WEIBO_COOKIE`（可选）|
| 知乎 zhihu | 🔵 | Reddit | 没滤镜的深度讨论、高赞回答与评论 | `ZHIHU_COOKIE`（z_c0/d_c0）|
| B站 bilibili | 📺 | YouTube | 几十分钟硬核解读 + 弹幕民意 | 免 key |
| 抖音 douyin | 🎵 | TikTok | 触达千万人的短视频观点 | `SCRAPECREATORS_API_KEY` |
| 小红书 xiaohongshu | 📕 | Instagram | 种草、测评、生活方式信号 | `XIAOHONGSHU_API_BASE` / SC |
| V2EX v2ex | 💻 | Hacker News | 技术人的真实争论 | 免 key |
| 掘金 juejin | ⛏️ | Hacker News | 开发者文章与技术风向 | 免 key |
| GitHub github | 🐙 | GitHub | PR 速率、star、release、issue | `GITHUB_TOKEN`（可选）|
| 雪球 xueqiu | 📈 | Polymarket | 不是观点，是带仓位的多空情绪 | 自动取 cookie |
| 网页 grounding | 🌐 | Web | 媒体报道、博客对比，多信号之一 | web search key 或宿主 WebSearch |

一条 1500 赞同的知乎回答，比一篇没人看的博客更有信号。一个 300 万播放的 B站视频，比一份通稿更能说明什么在文化上重要。综述按**真实参与度**排序——社交相关性，而不是 SEO 相关性。

> 需登录的平台拿不到凭据时**返回空、绝不伪造数据**，并交由宿主模型用 WebSearch（`site:zhihu.com` / `site:weibo.com` …）补充。

## 抗操纵：SEO / GEO / 水军

排序的可信度取决于能不能抗住操纵。本工具把攻击面从「骗排序算法」搬到「伪造真实互动」，并对后者主动降权（见 [`signals.py`](skills/last30days-cn/scripts/lib/signals.py) 的 `manipulation_signals`）。

- **SEO（搜索引擎优化）—— 结构性免疫。** 引擎从不把搜索引擎的排序结果当主信号，而是直接打平台 API、按**真实互动**排序。backlink、关键词密度、页面权重这些 SEO 杠杆进不了候选池。
- **GEO（生成引擎优化，刷 AI 答案里的引用）—— 多重削弱。** ①**互动闸门**：没有真实互动的内容根本进不了池；②**跨源印证**：同一件事需跨多平台出现才进 top 簇，单源扎堆触发警告；③**注入沙箱**：抓来的文本一律「当数据、不当指令」，挡住提示词注入；④对**关键词堆砌 / 模板化 / 推广话术 / 链接与话题标签堆砌**这类「讨好 LLM」的文案在重排前降权。
- **水军 / 刷量（买赞、控评、刷播放）—— 主动降权。** 对微博 / 知乎 / 抖音 / 小红书等易被刷的平台，检测**互动结构异常**（高互动却零评论、评论占比极低=典型刷量特征）并降权；叠加**每作者上限 3 条**、**源集中度警告**。一条 9000 赞、0 评论的微博会被排到一条 2000 赞、300 评论的真实帖之后。

所有降权都**保守、可解释、只降序不删除**：命中原因记在 `metadata.manipulation_flags` 里供综述参考。它能挡掉低成本单源操纵，但不声称能完全免疫有预算的多平台协同刷量——没有按互动排序的系统能做到。

## 推理模型（含国产）

保留 Gemini / OpenAI / xAI / OpenRouter，并新增 4 个国产 OpenAI 兼容模型。`auto` 探测顺序：google → openai → xai → openrouter → **deepseek → dashscope → moonshot → zhipu** → local。

| 模型 | 默认 | key |
|---|---|---|
| DeepSeek 深度求索 | `deepseek-chat` | `DEEPSEEK_API_KEY` |
| 通义千问 Qwen (DashScope) | `qwen-plus` | `DASHSCOPE_API_KEY` |
| Kimi (Moonshot) | `moonshot-v1-8k` | `MOONSHOT_API_KEY` |
| 智谱 GLM | `glm-4-flash` | `ZHIPU_API_KEY` |

从 Claude Code / Codex / Gemini 等宿主调用时，宿主模型本身就是 planner，无需上面任何 key。

## 大家拿它来干什么

- **开会前** — `/last30days-cn 王慧文` 把他这个月在做什么、最近发言和访谈拉出来，而不是三年前的百科。
- **竞品 / 热点** — `/last30days-cn 小米 SU7` 最近口碑、提车反馈、争议，跨微博/知乎/B站/小红书。
- **选型** — `/last30days-cn DeepSeek vs Kimi vs 通义千问` 一次并行三条流水线，社区怎么说、GitHub 实时 star。
- **学一个东西** — `/last30days-cn Cursor 工作流` 社区实测的用法，再让它给你写一份可直接用的实践。
- **然后它成为你的专家** — 一次检索后，会话已掌握社区所知，可继续追问、写文案、做决策。

## 工作原理

1. **你输入一个话题。** 人、公司、产品、技术、"X vs Y"，都行。
2. **agent 解析谁重要。** 微博/知乎话题、GitHub repo、B站 UP 主、抖音话题标签。
3. **所有源并行检索。** 多查询扩展，按参与度 / 相关性 / 新鲜度打分。
4. **同一件事，合并。** 微博、知乎、B站上的同一事件聚成一簇，而非三条独立项。
5. **综述成一份简报。** 以具体数据为锚、按来源内联引用、按真实参与度排序。**中英双语**。
6. **然后它成为你的专家。** 继续追问即可。

## 自带凭据

| 源 | 需要 | 成本 |
|---|---|---|
| V2EX + 掘金 + GitHub + B站 + 雪球 | 无 | 免费 |
| 微博 weibo | 浏览器登录后取 `WEIBO_COOKIE` | 免费 |
| 知乎 zhihu | 浏览器登录后取 `ZHIHU_COOKIE` | 免费 |
| 抖音 + 小红书（爬虫）| ScrapeCreators key | PAYG |
| 小红书（本地）| 本地服务 `XIAOHONGSHU_API_BASE` | 自建 |
| 网页搜索 | Brave / Exa / Serper / Parallel 任一 | Brave 有免费额度 |

macOS 可把 key 存进系统 Keychain（service 前缀 `last30days-`），作为最低优先级凭据源自动读取。完整矩阵见 [CONFIGURATION.md](CONFIGURATION.md)。

## 中文分词

中文无空格，原引擎的空格切分会失效。本移植在 `relevance.tokenize()` / `query.extract_core_subject()` 中优先用 `jieba`（缺失时回退到 CJK 字符 bigram），加入中文停用词，英文逻辑全部保留。**jieba 可选**——没装也能跑。

## 开源

MIT license。无追踪、无埋点，研究数据留在你的机器上。Python 3.12+，**仅标准库**（jieba 等第三方仅在可用时增强）。版本历史见 [CHANGELOG.md](CHANGELOG.md)。

## 署名

本项目派生自 **Matt Van Horn ([@mvanhorn](https://github.com/mvanhorn))** 的 [`mvanhorn/last30days-skill`](https://github.com/mvanhorn/last30days-skill)，遵循其 MIT 许可。原作者保留对上游代码的著作权；感谢其构建了与数据源无关的流水线架构，使本次中国市场适配成为可能。本移植的改动同样以 MIT 发布。详见 [NOTICE](NOTICE)。
