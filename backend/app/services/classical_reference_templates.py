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

DEFAULT_REFERENCE_STYLE_GUIDE = """【固定参照译本字词校准范本】
- 当前默认参照范本用于辅助判断古文释义、术语译法、断句、称谓和语气准确性；系统会按当前原文自动召回最相关的参照片段，不再把整本参考书当作泛泛风格提示。
- 成稿最终只输出“原文一段、翻译一段”的对照结构；不得沿用参照译本里的注释、点评、讲解、圈号编号或全注全译排版。
- 默认范本是“菜根谭-中华书局参照范本”时，凡召回片段覆盖到当前原文，必须以中华书局版本的断句、关键字词释义、术语方向和语气密度为第一依据；古汉语词典只用于核对和补足召回片段未覆盖的字词。
- 遇到疑难字词、典故、制度、人名、地名和古今异义，必须先参考召回片段里的释义判断；如果中华书局召回片段与古汉语词典或模型直觉冲突，以中华书局召回片段为主，只有召回片段未覆盖时才按古汉语词典和原文上下文保守处理。
- 所有翻译落笔前，必须联网检索专业网站交叉验证：围绕疑难字词、古今异义、典故、职官制度、人名地名、虚词承接和关键句义，检索专业古籍网站、专业古汉语词典、权威注译资料、出版社/高校/学术类资料，确认古汉语释义和原本意思。
- 不得望文生义，不得按现代汉语常见意思拆字理解原文；不得用百科摘抄、论坛、营销号或现代口语直觉替代专业释义。
- 译文以中华书局释义为底座，最终要译成清楚、自然、有文气的现代白话；让普通读者一眼读懂，不要停留在古典直译腔，也不要改成现代励志文、鸡汤文或讲义文。
- 古典比喻原则上要保留核心意象和比喻方向，不要轻易抹掉。比如“精金美玉的人品”应优先写成“精金美玉般纯正坚实的人品”，“薄冰上履过”应保留“如履薄冰”的艰险与戒慎；只有现代读者确实难以理解时，才补充成更清楚的白话表达。释义、轻重、比喻方向必须贴住中华书局译文，不能把意象全部抹成抽象概念。
- 不要扩写成故事、评论、赏析或讲义，也不要按现代字面义硬翻。
- “便觉、始觉、方觉、乃觉、忽觉”等承接短语中的“觉”优先按“觉得、感到、发觉”处理，不得写成“导致、造成、引发、致使”等因果关系。
- 不允许模型自行重翻古文。遇到“便、乃、遂、始、方、则、即、须、假、行、觉、见、知、识、容、非、是”等虚词、承接词、判断词或古今异义词，先按中华书局召回片段锁定句内关系；召回片段没有覆盖时，再用古汉语词典补缺。
- 内部必须完成“原文断句 → 中华书局释义对齐 → 联网专业资料交叉验证 → 古汉语词典补缺 → 新白话表达”；成稿应像充分吃透中华书局译文后的自然复写，不得像模型自己望文生义的机翻稿。
- 参考范本的作用是校准字词和断句，不得复制、近似改写或沿用参考译本的连续表达。
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
