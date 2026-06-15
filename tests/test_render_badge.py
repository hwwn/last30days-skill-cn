"""render.py — mandatory bilingual badge & localized source labels (PORT_CONTRACT §7).

Badge contract (first output line):
    🌐 最近30天 · last30days-cn v{VERSION} · 已同步 {YYYY-MM-DD}
"""

import re
from datetime import date

from lib import render


def test_badge_first_line_shape():
    lines = render._render_badge()
    assert lines[-1] == ""  # blank line follows the badge
    badge = lines[0]
    assert badge.startswith("🌐 最近30天 · last30days-cn v")
    assert " · 已同步 " in badge


def test_badge_contains_today_and_version():
    badge = render._render_badge()[0]
    today = date.today().strftime("%Y-%m-%d")
    assert today in badge
    # Version segment is "v<something>" and not the literal placeholder.
    m = re.search(r"last30days-cn v(\S+) · 已同步", badge)
    assert m is not None
    assert m.group(1) not in ("{version}", "{VERSION}", "")


def test_badge_full_format_regex():
    badge = render._render_badge()[0]
    assert re.fullmatch(
        r"🌐 最近30天 · last30days-cn v\S+ · 已同步 \d{4}-\d{2}-\d{2}",
        badge,
    )


def test_source_labels_are_localized_with_emoji():
    expected = {
        "weibo": "微博 🔴",
        "zhihu": "知乎 🔵",
        "bilibili": "B站 📺",
        "douyin": "抖音 🎵",
        "xiaohongshu": "小红书 📕",
        "v2ex": "V2EX 💻",
        "juejin": "掘金 ⛏️",
        "github": "GitHub 🐙",
        "xueqiu": "雪球 📈",
        "grounding": "网页 🌐",
    }
    assert render.SOURCE_LABELS == expected


def test_source_labels_cover_all_ten_canonical_sources():
    canonical = {
        "weibo", "zhihu", "bilibili", "douyin", "xiaohongshu",
        "v2ex", "juejin", "github", "xueqiu", "grounding",
    }
    assert set(render.SOURCE_LABELS) == canonical
