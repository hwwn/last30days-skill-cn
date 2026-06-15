# last30days-cn 移植契约 (PORT CONTRACT) — 所有 agent 必读

派生自 `mvanhorn/last30days-skill` 的中国市场完整移植版。**参考源码已克隆到 `/tmp/last30days-src/`**（`skills/last30days/scripts/lib/*` 等）。本契约固定所有跨文件接口，agent 必须严格遵守，保证并行产出的文件能无缝集成。

---

## 0. 路径与命名

- 新仓库根：`/Users/hwwn/Github/last30days-skill-cn`
- skill 目录：`skills/last30days-cn/`
- 引擎包：`skills/last30days-cn/scripts/lib/`（已从源码复制基线，西方 provider 已删）
- CLI 入口：`skills/last30days-cn/scripts/last30days_cn.py`（由源 `last30days.py` 移植，源 `last30days.py` 删除）
- 包内相对导入照旧（`from . import xxx`）。Python 3.12+，**仅标准库**（不得引入第三方依赖；jieba 等仅在 `try/except ImportError` 后可选使用，缺失时回退）。

## 1. 规范数据源名（canonical source names）

引擎全程只用这 10 个源名（小写）：

```
weibo  zhihu  bilibili  douyin  xiaohongshu  v2ex  juejin  github  xueqiu  grounding
```

源名映射（对应参考项目西方源 → 复用其 normalizer/pipeline 形态）：

| CN 源 | 西方原型 | normalizer 原型 | provider 文件 |
|---|---|---|---|
| `weibo` 微博 | x | `_normalize_x` (微博体) | `weibo.py` |
| `zhihu` 知乎 | reddit | `_normalize_reddit` | `zhihu.py` |
| `bilibili` B站 | youtube | `_normalize_youtube` | `bilibili.py` |
| `douyin` 抖音 | tiktok | `_normalize_shortform_video` | `douyin.py` |
| `xiaohongshu` 小红书 | instagram | `_normalize_shortform_video` | `xiaohongshu.py` |
| `v2ex` | hackernews | `_normalize_hackernews` | `v2ex.py` |
| `juejin` 掘金 | hackernews | `_normalize_hackernews` | `juejin.py` |
| `github` | github | `_normalize_github`（保留原样） | `github.py`（保留） |
| `xueqiu` 雪球 | polymarket | `_normalize_polymarket`（情绪体） | `xueqiu.py` |
| `grounding` 网页 | grounding | `_normalize_grounding`（保留原样） | `grounding.py`（保留，免改） |

别名（`pipeline.SEARCH_ALIAS`）：`wb→weibo, zh→zhihu, bili→bilibili, b站→bilibili, dy→douyin, xhs→xiaohongshu, 小红书→xiaohongshu, gh→github, xq→xueqiu, web→grounding`。

## 2. Provider 接口契约（每个 `lib/<source>.py`）

每个 provider 暴露：

```python
def search_<source>(query: str, from_date: str, to_date: str, depth: str = "default", **kw) -> dict | list:
    """打实际 API / 返回原始响应。失败抛 http.HTTPError 或返回空。"""

def parse_<source>_response(result, query: str = "") -> list[dict]:
    """把原始响应解析为 raw item dict 列表（key 见 §3）。"""
```

- 用 `from . import http, log`；`from .query import extract_core_subject`；`from .relevance import token_overlap_relevance`（打分用）。
- 日志：`log.source_log("微博", msg)` 之类。
- `depth` ∈ {`quick`,`default`,`deep`}，控制条数（参考 `DEPTH_CONFIG = {"quick":15,"default":30,"deep":60}`）。
- 日期窗口 `from_date`/`to_date` 为 `YYYY-MM-DD`。解析时把发布时间转成 `YYYY-MM-DD` 放进 `date` 字段。
- **取数分层**：能免 key 的走公开/半公开接口；需登录的（微博/知乎/抖音/小红书）先尝试 cookie/scraper key，**拿不到就返回 `[]`**（绝不伪造数据），由 SKILL.md 指挥宿主模型用 WebSearch 补充。每个 provider 顶部用注释写明取数方式与降级路径。

### 各 provider 取数方式（务实，标准库 urllib）

- `bilibili.py`：B站 Web 搜索 `https://api.bilibili.com/x/web-interface/wbi/search/type`（需 wbi 签名 + buvid cookie；签名复杂时降级到 `https://api.bilibili.com/x/web-interface/search/type` 带普通 header，仍失败则返回 `[]`）。解析 `data.result` 视频项。
- `v2ex.py`：用第三方全文搜索 `https://www.sov2ex.com/api/search?q={q}&sort=created&size={n}`（免 key）。解析 `hits[]._source`（title/content/replies/node/created/id）。
- `juejin.py`：`POST https://api.juejin.cn/search_api/v1/search`（免 key，body `{"key_word":q,"id_type":2,"limit":n,"cursor":"0","search_type":0}`）。失败返回 `[]`。
- `github.py`：**保留源文件**，免改（已用 GitHub 公开 API，`GITHUB_TOKEN` 可选提限额）。
- `xueqiu.py`：先 `GET https://xueqiu.com/` 取 `xq_a_token` cookie，再打热门讨论/股票讨论 `https://xueqiu.com/query/v1/symbol/search/status.json` 或话题接口；拿不到 cookie 返回 `[]`。情绪/讨论量映射到 polymarket 形态。
- `weibo.py`：`https://m.weibo.cn/api/container/getIndex?containerid=100103type%3D1%26q%3D{q}`（移动端半公开，建议带 `WEIBO_COOKIE`）。解析 `data.cards[].mblog`。无 cookie 时尽力尝试，失败返回 `[]`。
- `zhihu.py`：`https://www.zhihu.com/api/v4/search_v3?t=general&q={q}`（需 `ZHIHU_COOKIE`/`d_c0`）。拿不到返回 `[]`。
- `douyin.py`：若有 `SCRAPECREATORS_API_KEY` 走 ScrapeCreators 抖音端点（参考源 `tiktok.py` 的 header `http.scrapecreators_headers(token)`）；否则返回 `[]`。
- `xiaohongshu.py`：若有 `XIAOHONGSHU_API_BASE` 本地服务走其 `/api/v1/...` 搜索（参考 §env），否则若有 `SCRAPECREATORS_API_KEY` 走爬虫，否则 `[]`。

## 3. raw item dict 的 key（喂给 normalizer）

每个 provider 的 `parse_*` 必须产出下列形状之一（与 §1 normalizer 原型一致）。所有项都要带 `relevance`(0~1 float) 和 `why_relevant`(中文短句)，`date` 为 `YYYY-MM-DD` 或 None。

- **微博体（weibo）**：`{id, text, url, author_handle, date, engagement:{reposts,comments,attitudes}, relevance, why_relevant}`
- **知乎体（zhihu）**：`{id, title, selftext, url, subreddit(→话题/专栏名), date, engagement:{score(赞同),num_comments}, top_comments:[{excerpt,score}], comment_insights:[], relevance, why_relevant}`
- **B站体（bilibili）**：`{id|video_id, title, description, transcript_snippet(简介/字幕), url, channel_name(UP主), date, engagement:{view,like,coin,danmaku,reply}, top_comments:[{text,likes}], relevance, why_relevant}`
- **短视频体（douyin/xiaohongshu）**：`{id, text, caption_snippet, url, author_name, date, engagement:{digg_count/likes,comment,share/collected}, hashtags:[], top_comments:[], relevance, why_relevant}`
- **论坛体（v2ex/juejin）**：`{id, title, text, url, hn_url(可空), author, date, engagement:{points(赞),comments}, top_comments:[{text,points}], comment_insights:[], relevance, why_relevant}`
- **情绪体（xueqiu）**：`{id, title, question(讨论摘要), url, date, volume1mo|volume24hr, liquidity, price_movement(涨跌/情绪描述), end_date, outcome_prices:[], relevance, why_relevant}`

## 4. env（`env.py`）新增/调整

新增 config key（沿用 `get_config()` 的 `keys` 列表与 `KEYCHAIN_KEYS`）：
```
WEIBO_COOKIE  ZHIHU_COOKIE  GITHUB_TOKEN  XIAOHONGSHU_API_BASE  SCRAPECREATORS_API_KEY
DEEPSEEK_API_KEY  DASHSCOPE_API_KEY  MOONSHOT_API_KEY  ZHIPU_API_KEY
（保留：GOOGLE/GEMINI/OPENAI/XAI/OPENROUTER_API_KEY、BRAVE/EXA/SERPER/PARALLEL_API_KEY、LAST30DAYS_* 系列）
```
新增 helper：`is_bilibili_available()→True`、`is_v2ex_available()→True`、`is_juejin_available()→True`、`is_xueqiu_available()→True`、`is_weibo_available(config)`(有 WEIBO_COOKIE 或 SCRAPECREATORS)、`is_zhihu_available(config)`(有 ZHIHU_COOKIE 或 SCRAPECREATORS)、`is_douyin_available(config)`(有 SCRAPECREATORS)、保留 `is_xiaohongshu_available`。删除 X/reddit/bluesky/truthsocial/pinterest/xquik/tiktok/instagram 相关 helper。`XIAOHONGSHU_API_BASE` 默认 `http://host.docker.internal:18060`。cookie 域名表 `COOKIE_DOMAINS` 改为 `.weibo.cn`/`.zhihu.com`（映射到 WEIBO_COOKIE/ZHIHU_COOKIE）。

## 5. 推理 provider（`providers.py`）新增国产大模型

保留 Gemini/OpenAI/xAI/OpenRouter。新增 4 个 OpenAI 兼容 client（都用 `/chat/completions` 形态，复用 `OpenRouterClient` 模式）：
- **DeepSeek**：`https://api.deepseek.com/chat/completions`，默认模型 `deepseek-chat`，key `DEEPSEEK_API_KEY`
- **Qwen/通义（DashScope OpenAI 兼容）**：`https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`，默认 `qwen-plus`，key `DASHSCOPE_API_KEY`
- **Kimi/Moonshot**：`https://api.moonshot.cn/v1/chat/completions`，默认 `moonshot-v1-8k`，key `MOONSHOT_API_KEY`
- **智谱 GLM**：`https://open.bigmodel.cn/api/paas/v4/chat/completions`，默认 `glm-4-flash`，key `ZHIPU_API_KEY`

`resolve_runtime` 的 `auto` 探测顺序在 google/openai/xai/openrouter 之后追加：deepseek → dashscope → moonshot → zhipu。`_MODEL_DEFAULTS` 加这 4 个。`ProviderRuntime.reasoning_provider` 的 Literal 放宽为 `str`（schema.py 同步）。`_require_gemini_31` 仅对 gemini 生效（保留）。

## 6. CJK 分词（`relevance.py` + `query.py`）—— 关键

中文无空格，原 `tokenize()`/`extract_core_subject()` 的空格切分会把整句当一个 token，relevance 失效。改造：
- `relevance.tokenize()`：先尝试 `import jieba`（`try/except`），可用则 `jieba.lcut`；否则对 CJK 连续串做**字符 bigram**切分（如「人工智能」→{人工,工智,智能}），非 CJK 部分保留原英文空格切分逻辑。停用词加中文常见词（的/了/和/是/在/我/你/这/那/也/就/都/与/及…）。English 逻辑全部保留。
- `query.extract_core_subject()`：加中文 noise words（最近/最新/怎么/如何/什么/有哪些/推荐/对比/评测/教程/排行/盘点…）；CJK 串不按空格切，按上面分词去 noise 后重组；保留英文分支。`max_words` 对中文按「分词单元」计。
- 保留 `PreparedQuery`、`token_overlap_relevance` 的算法骨架不变，只换底层 tokenize。

## 7. 双语输出契约（`render.py` / `html_render.py` / SKILL.md）

徽章（输出第一行，强制）：`🌐 最近30天 · last30days-cn v{VERSION} · 已同步 {YYYY-MM-DD}`

通用查询正文：徽章 → 空行 → `TL;DR (EN): <一段英文摘要>` → 空行 → `我了解到：` → 加粗起句中文段落 → `研究中的关键模式：` + 中文编号列表 → 引擎页脚（表情树，原样透传）→ 邀请语。对比查询用 `## 速判` / `## {实体}` / `## 逐项对比`(表格) / `## 结论` 模板。

源标签与 emoji（render 页脚表情树用）：
`微博 🔴` `知乎 🔵` `B站 📺` `抖音 🎵` `小红书 📕` `V2EX 💻` `掘金 ⛏️` `GitHub 🐙` `雪球 📈` `网页 🌐`

LAWs（全部移植并本地化，见 SKILL.md）：1 结尾无 `Sources:` 块；2 通用查询无自造标题（用 `我了解到：`，对比例外）；3 不用 `—`/`–` 用 ` - `；4 通用查询正文无 `##`（对比例外）；5 引擎页脚原样透传；6 不堆砌原始证据簇；7 宿主即 planner，命名实体话题必带 `--plan`；8 引用内联 `[名称](url)`。

`render.py`/`html_render.py`：把所有面向用户的英文标签/源名改为上面中文+emoji，保留所有结构标记（`<!-- PASS-THROUGH FOOTER -->`、`<!-- EVIDENCE FOR SYNTHESIS -->` 等注释边界）。badge 文案改中文。其余渲染逻辑保留。

## 8. `pipeline.py` 改造

- import 改为新 provider 模块（weibo/zhihu/bilibili/douyin/xiaohongshu/v2ex/juejin/github/xueqiu/grounding），删旧 import。
- `SEARCH_ALIAS`/`MOCK_AVAILABLE_SOURCES` 用新源名。
- `available_sources(config, requested)`：基线恒可用 `["v2ex","juejin","github","bilibili","xueqiu"]`（都尝试免 key）；`weibo`/`zhihu` 当 `is_weibo_available`/`is_zhihu_available` 为真时加入；`douyin` 当有 SCRAPECREATORS；`xiaohongshu` 当 requested 且 `is_xiaohongshu_available`；`grounding` 当有 BRAVE/EXA/SERPER/PARALLEL。`EXCLUDE_SOURCES` 逻辑保留。
- `_retrieve_stream` 的 if/elif 分发改为新源：每个源调 `<mod>.search_<source>(...)` + `<mod>.parse_<source>_response(...)`。github 分支保留原逻辑（person/project/keyword + enrich）。删 reddit/x/youtube/tiktok/... 旧分支。Phase2 补充搜索（`_run_supplemental_searches`）的 X-handle 逻辑改为 weibo-uid/handle 逻辑或直接简化为按 `--weibo-handle` 补搜 weibo；若复杂可保留骨架但仅对 weibo 生效。
- `_mock_stream_results`：给 v2ex/bilibili/weibo/zhihu/xueqiu/grounding 各造 1 条 mock（用 §3 的 key）。
- `_finalize_items_by_source`：删 polymarket/digg 特判，改为 xueqiu 的话题相关性过滤（可简化为按 topic token_overlap 过滤）。

## 9. `planner.py` / `categories.py` 改造

- `planner.py`：`QUICK_SOURCE_PRIORITY`/`SOURCE_PRIORITY`/`SOURCE_CAPABILITIES`/`INTENT_SOURCE_EXCLUSIONS` 用新源名（按能力映射：weibo/zhihu/v2ex/juejin/github=discussion+social/link，bilibili=video+discussion，douyin/xiaohongshu=video_shortform+social，xueqiu=market，grounding=web）。`_infer_intent` 增加中文正则（对比：`对比|vs|对决|哪个好`；预测：`预测|赔率|会不会|能不能`；how_to：`怎么|如何|教程|入门`；factual：`是什么|什么是|多少`；opinion：`值不值|怎么样|体验`；breaking_news：`最新|发布|上线|官宣|爆`）。`_build_prompt` 文案双语化，示例换中国话题。
- `categories.py`：分类关键词改中文（科技/财经/数码/游戏/影视/生活/汽车…），保留结构。

## 10. `last30days_cn.py`（CLI）改造

- 重命名旧 `--subreddits→--zhihu-topics`、`--x-handle→--weibo-handle`、`--x-related→--weibo-related`、`--github-user/--github-repo` 保留、`--tiktok-*→--douyin-*`、`--ig-creators→--xhs-creators`。`--competitors*`、`--plan`、`--emit`、`--save-dir`、`--depth`、`--mock`、`--agent` 等全部保留。
- `run()` 调 `pipeline.run(...)` 的参数名同步（subreddits→zhihu_topics 等，pipeline 形参也同步改名；或保留 pipeline 形参名但 CLI 映射过去——二选一，**以 pipeline.py §8 为准，CLI 适配 pipeline**）。
- 删旧 `last30days.py`。badge/版本读 `plugin.json` version。

## 11. SKILL.md（双语合约）

移植源 `skills/last30days/SKILL.md` 的结构与全部 LAWs/步骤，但：源名/flag 换中国版（§1/§10），输出语言双语（§7），平台门示例换中国平台，X-handle 解析步骤改 weibo/zhihu 话题解析，WebSearch 补充步骤改搜 `site:zhihu.com`/`site:weibo.com`/`哔哩哔哩`/`小红书` 等。frontmatter：`name: last30days-cn`，`description` 双语，`argument-hint` 中国话题示例，`allowed-tools: Bash, Read, Write, AskUserQuestion, WebSearch`。保留 STEP 0 自检、徽章强制、LAWs。可适当精简超长的事故复盘段落，但 LAWs 与步骤主干必须完整。

## 12. 复制免改的文件（不要动）

`schema.py`(仅 §5 放宽 reasoning_provider 类型) `http.py` `log.py` `subproc.py` `dates.py` `dedupe.py` `fusion.py` `cluster.py` `signals.py` `rerank.py` `entity_extract.py` `snippet.py` `skill_meta.py` `quality_nudge.py` `grounding.py` `cookie_extract.py`/`chrome_cookies.py`/`safari_cookies.py`（cookie 域名在 env 配）。`competitors.py`/`fanout.py`/`preflight.py` 若引用旧源名则同步改名，否则免改。

## 13. 仓库脚手架

`pyproject.toml`(name=last30days-cn, py>=3.12, 无运行期依赖) `.claude-plugin/plugin.json`(name last30days-cn, version 1.0.0) `.claude-plugin/marketplace.json` `README.md`(双语, 署名原作者 mvanhorn + 原仓库链接, MIT) `LICENSE`(MIT, 原作者+本移植) `CHANGELOG.md` `CONFIGURATION.md` `CONCEPTS.md` `AGENTS.md` `CLAUDE.md`。

## 14. 测试

`tests/` 移植关键测试 + 中文 fixtures（每个 provider 的 parse 用样本 JSON 验证产出 §3 形状）。至少覆盖：env、providers(国产 client 选择)、relevance CJK 分词、normalize 各 CN 源、planner CN intent、pipeline available_sources、render 双语徽章、每个 provider parse。pytest，`tests/conftest.py` 保留/适配。
