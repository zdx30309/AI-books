import hashlib
import re
from pathlib import Path

from app.core.quality_metrics import (
    _STANDARD_LONGFORM_CHAPTER_CATEGORIES,
    _cjk_count,
    _detect_standard_longform_chapter_structure_errors,
)
from app.services.general_longform_runtime import atomic_json, atomic_text
from app.services.general_short_length import LengthHold


class ChapterStructureHold(LengthHold):
    pass


def applies(category):
    return category in _STANDARD_LONGFORM_CHAPTER_CATEGORIES and category != "general_short"


def writer_contract(prompt, category):
    if not applies(category):
        return prompt
    template = Path(__file__).parents[1] / "prompts/standard_chapter_contract.md"
    return prompt + "\n\n" + template.read_text().strip()


def structure_issues(markdown, category):
    if not applies(category):
        return {}
    return _detect_standard_longform_chapter_structure_errors(markdown, category=category)


def _split(markdown):
    body_marker = re.search(r"(?m)^@正文\s*\n", markdown)
    if not body_marker:
        raise ChapterStructureHold("章节结构需人工处理：缺少正文标记，原稿已保留")
    prefix = markdown[:body_marker.end()]
    body = markdown[body_marker.end():]
    ending = re.search(r"(?m)^@(?:参考|编辑|全书完成|附录).*$", body)
    suffix = body[ending.start():] if ending else ""
    body = body[:ending.start()] if ending else body
    headings = list(re.finditer(r"(?m)^@(\d+)[.．、]\s*([^\n]+)\n", body))
    if not headings or body[:headings[0].start()].strip():
        raise ChapterStructureHold("章节结构需人工处理：无法无损识别章节，原稿已保留")
    if [int(heading.group(1)) for heading in headings] != list(range(1, len(headings) + 1)):
        raise ChapterStructureHold("章节结构需人工处理：章节编号不连续，原稿已保留")
    groups = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        groups.append({"numbers": [index + 1], "titles": [heading.group(2).strip()],
                       "bodies": [body[heading.end():end].strip()]})
    return prefix, groups, suffix


def _render(prefix, groups, suffix):
    headings = [f"@{index}. {'、'.join(group['titles'])}" for index, group in enumerate(groups, 1)]
    contents = "\n\n".join(heading + "\n" + "\n\n".join(group["bodies"])
                              for heading, group in zip(headings, groups))
    toc = list(re.finditer(r"(?ms)^@目录\s*\n.*?(?=^@导言\s*$)", prefix))
    if len(toc) != 1:
        raise ChapterStructureHold("章节结构需人工处理：目录无法安全同步，原稿已保留")
    match = toc[0]
    prefix = prefix[:match.start()] + "@目录\n" + "\n".join(headings) + "\n\n" + prefix[match.end():]
    return prefix + contents + "\n\n" + suffix


def ensure_chapter_structure(markdown, category, output_dir=None, stage="writer"):
    before = structure_issues(markdown, category)
    if not before:
        return markdown
    source_sha256 = hashlib.sha256(markdown.encode()).hexdigest()
    signature = hashlib.sha256((category + "\0" + markdown).encode()).hexdigest()
    directory = Path(output_dir) / "chapter_structure" / stage / signature if output_dir else None
    if directory:
        atomic_text(directory / "source.md", markdown)
        atomic_json(directory / "report.json", {"status": "checking", "source_sha256": source_sha256,
                                                "category": category, "issues_before": before, "paid_calls": 0})
    try:
        prefix, groups, suffix = _split(markdown)
        if len(groups) < 4:
            raise ChapterStructureHold("章节标记无法完整识别，原稿已保留；不自动付费重写")
        if len(groups) > 40:
            raise ChapterStructureHold("章节过多，超出局部合并上限，原稿已保留；不自动付费重写")
        original_bodies = [body for group in groups for body in group["bodies"]]
        for attempt in range(len(groups)):
            counts = [_cjk_count("".join(group["bodies"])) for group in groups]
            short = next((index for index, count in enumerate(counts) if count < 600), None)
            adjacent = next((index for index in range(len(groups) - 1)
                             if counts[index] < 850 and counts[index + 1] < 850), None)
            if short is not None:
                neighbors = [index for index in (short - 1, short + 1) if 0 <= index < len(groups)]
                left = min(short, min(neighbors, key=lambda index: counts[index]))
            elif adjacent is not None:
                left = adjacent
            elif min(counts) < 700 and max(counts) >= 1800 and max(counts) >= 3 * min(counts):
                short = counts.index(min(counts))
                neighbors = [index for index in (short - 1, short + 1) if 0 <= index < len(groups)]
                left = min(short, min(neighbors, key=lambda index: counts[index]))
            else:
                break
            if len(groups) <= 4 or counts[left] + counts[left + 1] > 3000:
                raise ChapterStructureHold("章节结构无法在局部合并边界内修复；已保留原稿，需人工处理，不自动付费重写")
            first, second = groups[left:left + 2]
            groups[left:left + 2] = [{key: first[key] + second[key] for key in ("numbers", "titles", "bodies")}]
        result = _render(prefix, groups, suffix)
        if original_bodies != [body for group in groups for body in group["bodies"]]:
            raise ChapterStructureHold("局部合并正文校验失败，原稿已保留")
        remaining = structure_issues(result, category)
        if remaining:
            raise ChapterStructureHold("局部合并后仍有章节结构问题，已暂停，不自动付费重试")
        if directory:
            atomic_text(directory / "result.md", result)
            atomic_json(directory / "report.json", {"status": "repaired", "source_sha256": source_sha256,
                "result_sha256": hashlib.sha256(result.encode()).hexdigest(), "category": category,
                "issues_before": before, "issues_after": remaining, "groups": [group["numbers"] for group in groups],
                "paid_calls": 0, "attempts": attempt + 1})
        return result
    except ChapterStructureHold as exc:
        if directory:
            atomic_json(directory / "report.json", {"status": "manual_review", "source_sha256": source_sha256,
                "category": category, "issues_before": before, "error": str(exc), "paid_calls": 0})
        raise
