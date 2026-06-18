from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.prompts.goal_driven_templates import get_prompts
from app.services.content_engine import normalize_final_markdown


def test_normalize_final_markdown_backfills_blank_catalog_titles() -> None:
    manuscript = """@最终书名
测试书名
@最终简介
测试简介
@目录
1.
2.
@导言
测试导言。
@正文
@1. 关键危机里的转身
第一章正文。
@2. 旧账翻出后的余波
第二章正文。
"""

    result = normalize_final_markdown(manuscript)

    assert "1. 关键危机里的转身" in result
    assert "2. 旧账翻出后的余波" in result
    assert "\n1.\n" not in result
    assert "\n2.\n" not in result


def test_biography_prompt_requires_complete_catalog_titles() -> None:
    prompt = get_prompts("biography")["full_book_writer"]

    assert "你是一名成熟的传记文章写作者" in prompt
    assert "负责说明传记主人公的人生切入点和核心概括" in prompt
    assert "传记类：抓住人物的核心特质与核心事件" in prompt
    assert "不要用“按照多篇报道的说法”“有报道记载”“公开报道中”“据多篇报道”“公开资料多记载”之类的语言" in prompt
    assert "最终正文长度控制在约12000字" in prompt
    assert "用细节破题、用悬念推进、用时代和制度解释命运" not in prompt
    assert "默认 8000字以上，无上限" not in prompt


def test_history_prompt_includes_docx_revision_rules() -> None:
    prompt = get_prompts("history_culture")["full_book_writer"]

    assert "@目录必须列出完整章节标题，不得只写数字" in prompt
    assert "导言和正文开头不要重复" in prompt
    assert "全文压缩重复观点" in prompt
    assert "核心意象和关键词要克制使用" in prompt
    assert "结尾应停在一个有余味的具体画面里" in prompt
