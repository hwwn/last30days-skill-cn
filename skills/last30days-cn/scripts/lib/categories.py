"""话题分类 -> 同类社区/话题映射（用于 Step 0.55 社区扩展）。

当一个话题属于某个已知品类（AI 图像生成、AI 编程助手、SaaS 录屏工具
等）时，WebSearch 返回的品牌专属话题/社区往往不足：跨产品的技术讨论
通常发生在“同类品类”话题里（知乎话题、社区标签等）。本模块通过把
小写化后的话题串与“复合词模式”做匹配，将话题归入某个品类，再返回该
品类按优先级排序的同类社区/话题列表（在 CN 移植里用于扩展知乎话题等
社区源）。

该映射刻意保持小、经人工策划、经代码评审。新增品类是一次代码改动；
不存在用户可编辑的覆盖入口。

误命中防护：每个模式要么是多词复合（如 "image generation"、
"图像生成"），要么是领域专属的单词/品牌名（如 "midjourney"、
"通义万相"）。像 "image"、"ai"、"模型"、"视频" 这类裸常见名词永远不
作为模式使用。

先匹配先得（first-match-wins）：品类按声明顺序求值。条目从最具体到最
宽泛排序，使更窄的品类先于更宽泛的品类认领话题。例如
`ai_image_generation` 排在 `ai_chat_model` 前面，让 "gpt image 2"
命中图像生成品类。
"""

from __future__ import annotations

from typing import List, Optional, TypedDict


class _CategoryEntry(TypedDict):
    patterns: List[str]
    peer_subs: List[str]


CATEGORY_PEERS: dict[str, _CategoryEntry] = {
    "ai_image_generation": {
        "patterns": [
            "图像生成",
            "图片生成",
            "ai绘画",
            "ai 绘画",
            "ai作画",
            "文生图",
            "文本生成图像",
            "image generation",
            "text to image",
            "text-to-image",
            "gpt image",
            "gpt-image",
            "nano banana",
            "midjourney",
            "stable diffusion",
            "stablediffusion",
            "dall-e",
            "dalle",
            "可图",
            "通义万相",
            "文心一格",
            "即梦",
            "liblib",
            "ideogram",
            "recraft",
        ],
        "peer_subs": [
            "AI绘画",
            "Stable Diffusion",
            "Midjourney",
            "AIGC",
            "提示词工程",
            "数字艺术",
        ],
    },
    "ai_video_generation": {
        "patterns": [
            "视频生成",
            "ai视频",
            "ai 视频",
            "文生视频",
            "文本生成视频",
            "video generation",
            "text to video",
            "text-to-video",
            "sora",
            "veo 3",
            "veo3",
            "runway gen",
            "可灵",
            "kling",
            "pika labs",
            "海螺",
            "hailuo",
            "vidu",
        ],
        "peer_subs": [
            "AI视频",
            "Sora",
            "可灵",
            "AIGC",
            "短视频创作",
        ],
    },
    "ai_music_generation": {
        "patterns": [
            "音乐生成",
            "ai音乐",
            "ai 音乐",
            "ai作曲",
            "music generation",
            "ai music",
            "suno",
            "udio",
            "天工",
            "海绵音乐",
        ],
        "peer_subs": [
            "AI音乐",
            "Suno",
            "音乐制作",
            "人工智能",
        ],
    },
    "ai_coding_agent": {
        "patterns": [
            "编程助手",
            "编程智能体",
            "代码助手",
            "ai编程",
            "ai 编程",
            "ai写代码",
            "claude code",
            "cursor",
            "github copilot",
            "windsurf",
            "通义灵码",
            "文心快码",
            "豆包marscode",
            "marscode",
            "trae",
            "cline",
            "aider",
            "coding agent",
            "coding assistant",
        ],
        "peer_subs": [
            "编程",
            "人工智能",
            "大语言模型",
            "提示词工程",
        ],
    },
    "ai_agent_framework": {
        "patterns": [
            "智能体框架",
            "智能体开发",
            "agent框架",
            "agent framework",
            "agentic framework",
            "langchain",
            "langgraph",
            "crewai",
            "autogen",
            "llamaindex",
            "dify",
            "扣子",
            "coze",
            "metagpt",
        ],
        "peer_subs": [
            "LangChain",
            "大语言模型",
            "AI Agent",
            "机器学习",
        ],
    },
    "ai_chat_model": {
        "patterns": [
            "大语言模型",
            "大模型",
            "对话模型",
            "语言模型",
            "gpt-5",
            "gpt-4",
            "claude opus",
            "claude sonnet",
            "claude haiku",
            "gemini pro",
            "gemini flash",
            "deepseek",
            "通义千问",
            "qwen",
            "文心一言",
            "豆包",
            "kimi",
            "智谱",
            "glm",
            "讯飞星火",
            "grok",
        ],
        "peer_subs": [
            "大语言模型",
            "ChatGPT",
            "Claude",
            "人工智能",
            "DeepSeek",
        ],
    },
    "saas_screen_recording": {
        "patterns": [
            "录屏",
            "屏幕录制",
            "录屏软件",
            "录屏工具",
            "screen recording",
            "screen recorder",
            "loom video",
            "录课工具",
            "screen capture tool",
        ],
        "peer_subs": [
            "SaaS",
            "录屏",
            "效率工具",
            "创业",
        ],
    },
    "saas_productivity": {
        "patterns": [
            "笔记软件",
            "效率工具",
            "效率软件",
            "协作工具",
            "项目管理软件",
            "notion",
            "obsidian",
            "飞书",
            "语雀",
            "钉钉",
            "wolai",
            "flomo",
            "滴答清单",
            "productivity app",
        ],
        "peer_subs": [
            "效率工具",
            "SaaS",
            "笔记软件",
            "知识管理",
        ],
    },
    "prediction_markets": {
        "patterns": [
            "预测市场",
            "polymarket",
            "kalshi",
            "prediction market",
            "事件合约",
            "manifold markets",
        ],
        "peer_subs": [
            "预测市场",
            "Polymarket",
            "概率与统计",
        ],
    },
    "crypto_defi": {
        "patterns": [
            "去中心化金融",
            "defi",
            "流动性挖矿",
            "收益耕作",
            "稳定币",
            "yield farming",
            "liquidity pool",
            "以太坊layer",
            "layer 2",
            "l2 rollup",
            "二层网络",
        ],
        "peer_subs": [
            "DeFi",
            "区块链",
            "加密货币",
            "以太坊",
        ],
    },
    "dev_tool_cli": {
        "patterns": [
            "命令行工具",
            "命令行软件",
            "终端工具",
            "开发工具",
            "cli tool",
            "command line tool",
            "terminal app",
            "dev tool",
        ],
        "peer_subs": [
            "命令行",
            "编程",
            "前端开发",
        ],
    },
    "automotive": {
        "patterns": [
            "新能源汽车",
            "电动汽车",
            "电动车",
            "纯电",
            "插电混动",
            "智能驾驶",
            "自动驾驶",
            "辅助驾驶",
            "智能座舱",
            "比亚迪",
            "理想汽车",
            "蔚来",
            "小鹏",
            "问界",
            "小米su7",
            "小米汽车",
            "特斯拉",
            "tesla",
            "model y",
            "model 3",
        ],
        "peer_subs": [
            "新能源汽车",
            "电动汽车",
            "自动驾驶",
            "汽车",
            "智能驾驶",
        ],
    },
    "gaming": {
        "patterns": [
            "游戏发售",
            "单机游戏",
            "手游",
            "网游",
            "主机游戏",
            "游戏评测",
            "黑神话",
            "原神",
            "崩坏",
            "王者荣耀",
            "永劫无间",
            "steam新作",
            "ps5",
            "switch 2",
            "switch2",
        ],
        "peer_subs": [
            "电子游戏",
            "单机游戏",
            "Steam",
            "游戏",
            "游戏推荐",
        ],
    },
    "film_tv": {
        "patterns": [
            "电影上映",
            "院线电影",
            "新番",
            "国产剧",
            "电视剧",
            "综艺",
            "剧集",
            "影视剧",
            "票房",
            "院线",
            "netflix",
            "网飞",
            "迪士尼",
        ],
        "peer_subs": [
            "电影",
            "电视剧",
            "影视评论",
            "动画",
            "综艺",
        ],
    },
    "consumer_electronics": {
        "patterns": [
            "智能手机",
            "旗舰手机",
            "新手机",
            "笔记本电脑",
            "智能手表",
            "无线耳机",
            "数码评测",
            "iphone",
            "华为mate",
            "小米手机",
            "vivo",
            "oppo",
            "一加",
            "macbook",
            "ipad",
            "apple watch",
        ],
        "peer_subs": [
            "数码",
            "智能手机",
            "笔记本电脑",
            "数码评测",
            "消费电子",
        ],
    },
}


def detect_category(topic: Optional[str]) -> Optional[str]:
    """通过复合词匹配把话题归入某个已知品类。

    返回品类 id（如 "ai_image_generation"），若没有任何品类的模式命中
    则返回 None。匹配是对小写化话题串做大小写不敏感的子串匹配（对中文
    无影响，对英文品牌名生效）。声明顺序优先（first-match-wins），故该
    映射从最具体到最宽泛排序。

    None 或空话题返回 None。对正常字符串输入分类绝不抛异常；调用方在
    典型路径上无需包 try/except，防御性调用方可自行决定。
    """
    if not topic:
        return None
    lowered = topic.lower()
    for category_id, entry in CATEGORY_PEERS.items():
        for pattern in entry["patterns"]:
            if pattern in lowered:
                return category_id
    return None


def peer_subs_for(category_id: Optional[str]) -> List[str]:
    """返回某个品类按优先级排序的同类社区/话题列表。

    对 None 或未知品类 id 返回空列表。返回的是全新副本；调用方可安全地
    就地修改它。
    """
    if not category_id:
        return []
    entry = CATEGORY_PEERS.get(category_id)
    if not entry:
        return []
    return list(entry["peer_subs"])
