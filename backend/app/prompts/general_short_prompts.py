"""Layered prompt routing for the general_short category.

The category used to load one 60,000-character markdown file that mixed writing
rules with two complete sample books, three sample reports and the similarity
audit spec.  That made the sample books the strongest style anchor in context,
so the writer reproduced their structure no matter what the rules said.  This
module loads only the writing rules for the draft stage and keeps the
structure-extraction and differentiation-audit specs in separate stages.

Substitution is marker-based (``str.replace``) rather than ``str.format`` so the
JSON schemas inside the stage instructions keep their literal braces.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any

CATEGORY = "general_short"
LABEL = "通用短篇"
BUNDLE = Path(__file__).resolve().parent / CATEGORY

#: Markers used by the stage instructions. None may collide with the literal
#: JSON braces in those files.
MARKERS = ("fingerprint", "raw_text", "source_text", "draft_text")

#: Brace-free slot for the injected structure fingerprint.  It survives any
#: number of ``str.format(raw_text=...)`` passes performed by the shared routing
#: layer, and is replaced by the general_short branch just before the call.
FINGERPRINT_TOKEN = "[[FINGERPRINT]]"


def _manifest() -> dict[str, Any]:
    return json.loads((BUNDLE / "manifest.json").read_text(encoding="utf-8"))


BUNDLE_VERSION = _manifest()["version"]


def get_manifest(version: str | None = None) -> dict[str, Any]:
    manifest = _manifest()
    if version not in (None, manifest["version"]):
        raise ValueError(f"未安装的通用短篇提示词版本：{version}")
    for section in manifest["sections"]:
        raw = (BUNDLE / section["file"]).read_bytes()
        if sha256(raw).hexdigest() != section["sha256"]:
            raise RuntimeError(f"通用短篇原文校验失败：{section['id']}")
    return manifest


def stage_source(stage: str, version: str | None = None) -> str:
    manifest = get_manifest(version)
    if stage not in manifest["stage_sections"]:
        raise ValueError(f"未支持的通用短篇提示词节点：{stage}")
    by_id = {item["id"]: item for item in manifest["sections"]}
    return "\n\n".join(
        (BUNDLE / by_id[key]["file"]).read_text(encoding="utf-8").rstrip()
        for key in manifest["stage_sections"][stage]
    )


def stage_instruction(stage: str) -> str:
    return (BUNDLE / "stages" / f"{stage}.md").read_text(encoding="utf-8")


def _substitute(text: str, values: dict[str, Any]) -> str:
    for key in MARKERS:
        token = "{" + key + "}"
        if token in text:
            text = text.replace(token, str(values.get(key, "")))
    return text


def render_stage(stage: str, values: dict[str, Any], version: str | None = None) -> str:
    body = stage_source(stage, version)
    instruction = stage_instruction(stage)
    parts = [part for part in (body, instruction) if part.strip()]
    return _substitute("\n\n".join(parts), values)


def writer_template(version: str | None = None) -> str:
    """Return the draft prompt as a ``{raw_text}`` template plus a fingerprint slot.

    The shared routing layer calls ``.format(raw_text=...)`` on the writer prompt
    for every category.  :data:`FINGERPRINT_TOKEN` is brace-free so it survives
    that call and can be replaced by the general_short branch afterwards.
    """
    return render_stage(
        "draft",
        {"fingerprint": FINGERPRINT_TOKEN, "raw_text": "{raw_text}"},
        version,
    )


def render_writer_prompt(fingerprint: str, raw_text: str, version: str | None = None) -> str:
    """Fill both the injected fingerprint and the raw transcript for the draft stage."""
    return (
        writer_template(version)
        .format(raw_text=raw_text)
        .replace(FINGERPRINT_TOKEN, fingerprint or "（本轮未提供外部灵感原文，无结构指纹）")
    )
