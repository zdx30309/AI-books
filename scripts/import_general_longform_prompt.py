"""Losslessly package the supplied general-longform DOCX; no LLM or network."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET
from zipfile import ZipFile

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEST = ROOT / "backend/app/prompts/general_longform"
SECTIONS = [
    ("identity", "一 执行身份与材料边界"),
    ("stages", "二 自动识别当前阶段"),
    ("taxonomy", "三 选题分类与内容定位"),
    ("research", "四 联网研究与证据规则"),
    ("packaging", "五 书名与简介策划"),
    ("outline", "六 大纲与十万字分配"),
    ("chapter", "七 跨对话交接与章节写作"),
    ("style", "八 人工表达、自然节奏与检测反馈"),
    ("continuity", "九 防重复与全书一致性"),
    ("export", "十 Word 交付与文件规范"),
    ("acceptance", "十一 交付前验收"),
    ("appendix_boundary", "附录阅读边界"),
    ("topic_list", "附录一 微信读书选题清单"),
    ("reference_sample", "附录二 参考样稿全文"),
    ("reference_report", "附录三 官方检测报告原文"),
    ("editing_examples", "附录四 人工表达写作方法与对照范例"),
    ("packaging_examples", "附录五 书名与简介风格参考"),
    ("closing", "参考资料结束"),
]
COMMON = ["preamble", "identity", "stages", "appendix_boundary", "closing"]
STAGE_SECTIONS = {
    "reference_analysis": COMMON + ["style", "continuity", "reference_sample", "reference_report"],
    "research": COMMON + ["taxonomy", "research", "topic_list"],
    "evidence": COMMON + ["research", "acceptance"],
    "outline": COMMON + ["taxonomy", "research", "packaging", "outline", "chapter", "style", "continuity", "export", "acceptance", "topic_list", "packaging_examples"],
    "outline_revision": COMMON + ["taxonomy", "research", "packaging", "outline", "chapter", "style", "continuity", "export", "acceptance", "topic_list", "packaging_examples"],
    "materials": COMMON + ["outline", "chapter", "research", "export", "acceptance"],
    "import_bindings": COMMON + ["outline", "chapter", "research", "acceptance"],
    "draft": COMMON + ["research", "outline", "chapter", "style", "continuity", "export", "acceptance"],
    "continue": COMMON + ["research", "outline", "chapter", "style", "continuity", "export", "acceptance"],
    "polish": COMMON + ["research", "outline", "chapter", "style", "continuity", "export", "acceptance", "editing_examples"],
    "refine": COMMON + ["research", "outline", "chapter", "style", "continuity", "export", "acceptance", "editing_examples"],
    "acceptance": COMMON + ["research", "outline", "chapter", "style", "continuity", "export", "acceptance", "editing_examples"],
    "repair": COMMON + ["research", "outline", "chapter", "style", "continuity", "export", "acceptance", "editing_examples"],
    "final": COMMON + ["research", "outline", "chapter", "style", "continuity", "export", "acceptance", "editing_examples"],
    "format": COMMON + ["packaging", "outline", "chapter", "export", "acceptance"],
}
VERSIONS = {"2026-09-14-v16", "2026-09-14-v02"}
CONTRAST_SECTIONS = [
    ("reference_positive", "A 正面案例：", "14a"),
    ("reference_negative", "B 反面案例：", "14b"),
    ("report_positive", "A 正面案例检测报告：", "15a"),
    ("report_negative", "B 反面案例检测报告：", "15b"),
]


def digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def paragraph_text(p: ET.Element) -> str:
    return "".join(
        (node.text or "") if node.tag == W + "t" else
        "\t" if node.tag == W + "tab" else
        "\n" if node.tag in (W + "br", W + "cr") else ""
        for node in p.iter()
    )


def extract(path: Path) -> tuple[list[str], dict[str, list[str]]]:
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
        paragraphs = [paragraph_text(p) for p in root.findall(".//" + W + "body//" + W + "p")]
        extras = {}
        for name in archive.namelist():
            if name.startswith(("word/header", "word/footer", "word/footnotes", "word/endnotes")) and name.endswith(".xml"):
                extras[name] = [paragraph_text(p) for p in ET.fromstring(archive.read(name)).iter(W + "p")]
    return paragraphs, extras


def package(source: Path, dest: Path, version: str | None = None) -> dict:
    paragraphs, extras = extract(source)
    contrastive = "附录二 正反写作样稿全文" in paragraphs
    detected_version = "2026-09-14-v02" if contrastive else "2026-09-14-v16"
    if version is not None and version != detected_version:
        raise ValueError("Version does not match the document's section schema")
    version = detected_version
    headings = dict(SECTIONS)
    if contrastive:
        headings.update(reference_sample="附录二 正反写作样稿全文", reference_report="附录三 正反案例检测报告与判读")
    filenames = {key: f"source_sections/{index:02d}_{key}.txt" for index, key in enumerate(["preamble", *headings])}
    stage_sections = {stage: list(keys) for stage, keys in STAGE_SECTIONS.items()}
    starts = [("preamble", 0)]
    for key, heading in headings.items():
        matches = [i for i, text in enumerate(paragraphs) if text == heading]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one section heading {heading!r}; found {len(matches)}")
        starts.append((key, matches[0]))
    if starts != sorted(starts, key=lambda item: item[1]):
        raise ValueError("Section order differs from the supplied revision")
    if contrastive:
        for key, prefix, number in CONTRAST_SECTIONS:
            matches = [i for i, text in enumerate(paragraphs) if text.startswith(prefix)]
            if len(matches) != 1:
                raise ValueError(f"Expected exactly one role heading {prefix!r}; found {len(matches)}")
            starts.append((key, matches[0]))
            filenames[key] = f"source_sections/{number}_{key}.txt"
            stage_sections["reference_analysis"].append(key)
        starts.sort(key=lambda item: item[1])
    dest.mkdir(parents=True, exist_ok=True)
    section_dir = dest / "source_sections"
    section_dir.mkdir(exist_ok=True)
    records = []
    for index, (key, start) in enumerate(starts):
        end = starts[index + 1][1] if index + 1 < len(starts) else len(paragraphs)
        content = "\n".join(paragraphs[start:end])
        filename = filenames[key]
        (dest / filename).write_bytes(content.encode("utf-8"))
        records.append({"id": key, "file": filename, "start_paragraph": start,
                        "end_paragraph": end, "paragraph_count": end - start,
                        "sha256": digest(content.encode("utf-8")),
                        "role": "reference_only" if key in {"reference_sample", "reference_report", *(item[0] for item in CONTRAST_SECTIONS)} else "prompt",
                        "stages": [stage for stage, keys in stage_sections.items() if key in keys]})
    text = "\n".join(paragraphs)
    (dest / "source_text.txt").write_bytes(text.encode("utf-8"))
    (dest / "source_paragraphs.json").write_bytes(json.dumps(paragraphs, ensure_ascii=False, indent=2).encode("utf-8"))
    if source.resolve() != (dest / "source.docx").resolve():
        shutil.copyfile(source, dest / "source.docx")
    manifest = {"category": "general_longform", "label": "通用长篇", "version": version,
                "source_filename": source.name, "source_sha256": digest(source.read_bytes()),
                "text_sha256": digest(text.encode("utf-8")), "paragraph_count": len(paragraphs),
                "text_characters": len(text), "extra_parts": extras, "sections": records,
                "stage_sections": stage_sections, "raw_reference_generation_allowed": False}
    if contrastive:
        by_id = {item["id"]: item for item in records}
        examples = []
        for role in ("positive", "negative"):
            sample, report = by_id[f"reference_{role}"], by_id[f"report_{role}"]
            title = re.fullmatch(r"[AB] [正反]面案例：《(.+)》", paragraphs[sample["start_paragraph"]])
            if not title:
                raise ValueError("Reference example title is missing")
            report_text = "\n".join(paragraphs[report["start_paragraph"]:report["end_paragraph"]])
            words = re.search(r"稿件字数：(\d+)", report_text)
            risk = re.search(r"整体风险评分：(\d+)%", report_text)
            problems = re.search(r"检测到\s*(\d+)\s*类共性问题，涉及约\s*(\d+)\s*处", report_text)
            if not words or not risk or not problems:
                raise ValueError("Historical report fields are incomplete")
            examples.append({"role": role, "title": title[1], "sample_section_id": sample["id"], "report_section_id": report["id"],
                "historical_report": {"scope": "attachment_sample_only", "manuscript_words": int(words[1]),
                    "overall_risk_percent": int(risk[1]), "problem_categories": int(problems[1]), "approximate_occurrences": int(problems[2]),
                    "dimension_scores": {match[1]: int(match[2]) for match in re.finditer(r"([^\n｜]+)｜权重 \d+%｜评分 (\d+)", report_text)}}})
        manifest["reference_examples"] = examples
        manifest["reference_policy"] = {"mode": "contrastive", "ordinary_words_contextual": True,
            "whole_sample_roles_override_sentence_examples": True, "historical_reports_are_current_results": False,
            "raw_sample_facts_are_sources": False, "style_review_required": True}
    (dest / "manifest.json").write_bytes((json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    verify(dest)
    return manifest


def verify(dest: Path) -> dict:
    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    source = dest / "source.docx"
    paragraphs, extras = extract(source)
    assert digest(source.read_bytes()) == manifest["source_sha256"], "DOCX changed"
    assert paragraphs == json.loads((dest / "source_paragraphs.json").read_text(encoding="utf-8")), "Paragraphs changed"
    assert extras == manifest["extra_parts"], "Headers/footers/notes changed"
    assert len(paragraphs) == manifest["paragraph_count"]
    reconstructed = []
    cursor = 0
    for section in manifest["sections"]:
        assert section["start_paragraph"] == cursor, "Missing/overlapping paragraph span"
        end = section["end_paragraph"]
        data = (dest / section["file"]).read_bytes()
        assert data == "\n".join(paragraphs[cursor:end]).encode("utf-8"), section["id"]
        assert digest(data) == section["sha256"], section["id"]
        assert section["stages"], f"Unrouted section: {section['id']}"
        assert section["paragraph_count"] == end - cursor
        assert section["stages"] == [stage for stage, keys in manifest["stage_sections"].items() if section["id"] in keys]
        if section["role"] == "reference_only":
            assert section["stages"] == ["reference_analysis"], "Raw sample routed to generation"
        reconstructed.append(data.decode("utf-8"))
        cursor = end
    assert cursor == len(paragraphs)
    data = "\n".join(reconstructed).encode("utf-8")
    assert data == (dest / "source_text.txt").read_bytes(), "Reconstruction differs"
    assert digest(data) == manifest["text_sha256"]
    return {"paragraphs": cursor, "sections": len(reconstructed), "source_sha256": manifest["source_sha256"], "status": "pass"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--version", choices=sorted(VERSIONS))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        print(json.dumps(verify(args.dest), ensure_ascii=False))
    elif args.source:
        package(args.source, args.dest, args.version)
        print(json.dumps(verify(args.dest), ensure_ascii=False))
    else:
        parser.error("Provide --source or --check")
