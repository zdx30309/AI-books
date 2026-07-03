"""Persistent reference templates for classical Chinese rewrite tasks."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT


REFERENCE_TEMPLATE_DIR = PROJECT_ROOT / "backend" / "output" / "reference_templates" / "classical_reference_rewrite"
DEFAULT_REFERENCE_TEXT_PATH = REFERENCE_TEMPLATE_DIR / "default_reference.txt"
DEFAULT_REFERENCE_META_PATH = REFERENCE_TEMPLATE_DIR / "default_reference.json"

DEFAULT_REFERENCE_STYLE_GUIDE = """【固定参照译本体例范本】
- 当前默认参照范本采用《菜根谭》类“全本全注全译”普及读物体例：不是只给现代译文，而是围绕每个原文条目完成原文理解、必要注释、现代译文和点评阐发。
- 成稿的每个独立条目都必须像古籍普及读物中的一个完整条目：先抓住原文关键字词、对偶结构、比喻关系和义理判断，再写成连贯现代白话，最后补出克制的点评或现实理解。
- 对短篇寓言、格言、清言、小品、语录体文本，不能只改写成一段故事或一段翻译；至少要让读者看到“讲了什么、关键词怎么理解、为什么有这个道理、落在人情世故上是什么意思”。
- 可以显式使用“【注释】”“【译文】”“【点评】”这类条目层级；如果为了排版不显式列标题，也必须自然承担这三层功能。
- 注释只解释理解原文所必需的字词、典故、制度、人名、地名和古今异义，不堆砌资料；译文以忠实顺译为主，必要时补足省略关系；点评要贴着原文义理展开，不写空泛鸡汤。
- 参考范本的作用是学习体例、信息密度、注译点评组合方式和古籍普及读物的稳重语气；不得复制、近似改写或沿用参考译本的连续表达。
"""


@dataclass(frozen=True)
class ClassicalReferenceTemplateStatus:
    enabled: bool
    name: str = ""
    source_filename: str = ""
    chars: int = 0
    text_path: str = ""
    updated_at: str = ""


def _read_meta() -> dict[str, Any]:
    if not DEFAULT_REFERENCE_META_PATH.exists():
        return {}
    try:
        data = json.loads(DEFAULT_REFERENCE_META_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def default_reference_template_status() -> ClassicalReferenceTemplateStatus:
    if not DEFAULT_REFERENCE_TEXT_PATH.exists():
        return ClassicalReferenceTemplateStatus(enabled=False)
    try:
        chars = len(DEFAULT_REFERENCE_TEXT_PATH.read_text(encoding="utf-8").strip())
    except Exception:
        return ClassicalReferenceTemplateStatus(enabled=False)

    meta = _read_meta()
    updated_at = str(meta.get("updated_at") or "")
    if not updated_at:
        try:
            updated_at = datetime.fromtimestamp(DEFAULT_REFERENCE_TEXT_PATH.stat().st_mtime).isoformat()
        except OSError:
            updated_at = ""
    return ClassicalReferenceTemplateStatus(
        enabled=chars > 0,
        name=str(meta.get("name") or "菜根谭-中华书局参照范本"),
        source_filename=str(meta.get("source_filename") or "菜根谭-中华书局.docx"),
        chars=chars,
        text_path=str(DEFAULT_REFERENCE_TEXT_PATH.relative_to(PROJECT_ROOT)),
        updated_at=updated_at,
    )


def load_default_reference_template_text() -> str:
    status = default_reference_template_status()
    if not status.enabled:
        return ""
    return DEFAULT_REFERENCE_TEXT_PATH.read_text(encoding="utf-8").strip()


def build_default_reference_notes() -> tuple[str, str, str]:
    """Return style reference, full notes and source path for the default template."""
    status = default_reference_template_status()
    if not status.enabled:
        return "", "", ""
    full_text = load_default_reference_template_text()
    header = f"【固定参照译本：{status.name}】\n来源文件：{status.source_filename}\n全文字数：{status.chars}\n"
    return (
        status.name,
        f"{DEFAULT_REFERENCE_STYLE_GUIDE.strip()}\n\n{header}\n{full_text}".strip(),
        status.text_path,
    )

