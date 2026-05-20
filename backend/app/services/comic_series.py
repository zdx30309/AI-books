"""Comic-series manuscript and illustration helpers."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable

from app.services.cover import ImageApiUncertainError, generate_image_file_from_prompt

COMIC_IMAGE_PROMPT = """日韩治愈系儿童绘本封面，超简洁构图，大面积高饱和纯色背景。
可爱动物主角，Q版手绘插画，线条干净简单，带轻微蜡笔与颗粒感。
角色表情温柔、呆萌、有陪伴感，像文创贴纸或绘本角色。
书名使用简洁大字与留白排版，整体轻松、温暖、童趣。
配色明亮鲜艳，以高识别度颜色为主。
整体像日本儿童绘本、LOFT文创、微信读书治愈系封面。
画面中不要出现任何章节顺序、系列总数、编号、序号、图片说明或元信息文字。
避免复杂背景、避免商业海报感、避免厚重设计、避免AI精致感。"""

COMIC_STORY_PROMPT = """根据封面主体动物或人物，创作简短温柔的治愈系小故事 / 双语对话绘本。
每篇故事必须有新的互动逻辑、情绪切口或小反转，避免重复“安慰”“陪伴”“夸奖”等固定套路。
不要重复相同梗、相同对白结构、相同结尾方式或类似情绪模板。
故事可偏幽默、孤独、笨拙、误会、反差、冷笑话、日常哲思、小聪明、小失落、小幸运等不同方向。
角色关系保持自然，像真实朋友之间的聊天，而不是标准鸡汤。
对白短句化、口语化、适合绘本与漫画排版，可加入中英双语。
画面聚焦生活小事。
结尾保留轻微余味与情绪回弹，不强行升华。
整体像日本治愈漫画、LOFT文创绘本、小红书高赞治愈漫画。
避免鸡汤、避免大道理、避免低龄化、避免网络段子感、避免AI重复套路感。"""

COMIC_COPYRIGHT_RULE = (
    "全系列必须为原创角色与原创画面；禁止使用、模仿或暗示任何已有动漫、绘本、游戏、电影、品牌、"
    "文创 IP、商标、logo、角色造型、角色配色、角色名称或知名画师个人风格；不要出现水印、出版社标识、二维码。"
)

COMIC_SERIES_STYLE_BIBLE = (
    "统一系列风格：全系列图片（含书架封面）必须像同一套日韩治愈系儿童绘本 / LOFT 文创贴纸系列；"
    "所有图片共享同一视觉系统：高饱和纯色背景、简洁留白、干净手绘线条、轻微蜡笔颗粒、柔软阴影、纸张肌理、原创 Q 版动物角色。"
    "角色可以每张不同，但必须像同一套绘本里的伙伴：圆润比例一致、五官复杂度一致、线条粗细一致、光影柔和度一致、表情温柔、没有真实品牌元素。"
    "只允许背景主色、少量小道具和情绪细节随短语变化；不要改变画风、质感、角色比例、排版密度和出版气质。"
)

from app.services.comic_prompts import (  # noqa: E402
    COMIC_COPYRIGHT_RULE,
    COMIC_IMAGE_PROMPT,
    COMIC_SERIES_STYLE_BIBLE,
    COMIC_STORY_PROMPT,
)

_BULLET_RE = re.compile(r"^\s*(?:[-*+•·]|\d+[.、．)]|第[一二三四五六]辑[：:、.]?)\s*")
COMIC_PREFACE_IMAGE_PLACEHOLDER = "{{COMIC_PREFACE_IMAGE}}"
COMIC_PREFACE_IMAGE_FILENAME = "comic_preface.png"

_IMAGE_MARKDOWN_RE = re.compile(
    r"!\[([^\]]*)\]\((?:comic_images/)?(comic_preface\.png|comic_section_\d{2}\.png)\)"
)
_PLACEHOLDER_RE = re.compile(r"\{\{COMIC_IMAGE_(\d{1,2})\}\}")
_PREFACE_PLACEHOLDER_RE = re.compile(r"\{\{COMIC_PREFACE_IMAGE\}\}")
_SECTION_RE = re.compile(r"(?m)^@\s*(\d+)\.\s*(.+?)\s*$")
_PREFACE_SECTION_RE = re.compile(r"(?m)^@\s*文前\s*$")

_ANIMALS = (
    ("小熊", "圆滚滚的小熊，背影和正面都很憨，适合表达慢慢来和陪伴感"),
    ("小猫", "白色小猫，表情安静，适合夜晚、窗台、月亮和自言自语"),
    ("小狗", "短耳小狗，眼神真诚，适合笨拙误会、朋友聊天和小幸运"),
    ("小兔", "软耳小兔，动作轻轻，适合孤独、勇气和温柔的小反转"),
    ("小企鹅", "圆肚小企鹅，略笨拙，适合冷笑话、反差和小聪明"),
    ("小水獭", "抱着小物件的小水獭，适合日常哲思、小失落和被生活接住"),
)


@dataclass(frozen=True)
class ComicSectionImage:
    index: int
    phrase: str
    title: str
    animal: str
    subject: str
    prompt: str
    filename: str
    path: Path
    status: str = "planned"
    error: str | None = None

    @property
    def markdown(self) -> str:
        return f"![配图](comic_images/{self.filename})"


def parse_comic_phrases(value: object) -> list[str]:
    if isinstance(value, list):
        raw_items = [str(item or "") for item in value]
    else:
        text = str(value or "")
        raw_items = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    phrases: list[str] = []
    for raw in raw_items:
        phrase = _BULLET_RE.sub("", raw).strip()
        phrase = phrase.strip(" \t，,。；;")
        if phrase:
            phrases.append(phrase[:120])
    return phrases


def validate_comic_phrases(phrases: list[str]) -> None:
    if len(phrases) not in {5, 6}:
        raise ValueError(f"漫画系列需要输入 5 或 6 条短语，当前识别到 {len(phrases)} 条")


def parse_comic_preface_phrase(value: object) -> str:
    raw = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return ""
    first = raw.split("\n", 1)[0]
    first = re.sub(r"^\s*(?:文前|前言|序言|开场)\s*[：:、.．)]\s*", "", first).strip()
    return first.strip(" \t，,。；;")[:120]


def parse_comic_input(value: object) -> tuple[str, list[str]]:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    preface = ""
    body_lines: list[str] = []
    for raw in text.split("\n"):
        stripped = raw.strip()
        if not stripped:
            continue
        preface_match = re.match(r"^(?:文前|前言|序言|开场)\s*(?:短语)?\s*[：:]\s*(.+?)\s*$", stripped)
        if preface_match and not preface:
            preface = parse_comic_preface_phrase(preface_match.group(1))
            continue
        body_lines.append(raw)
    return preface, parse_comic_phrases(body_lines)


def comic_section_title(index: int) -> str:
    names = ("第一辑", "第二辑", "第三辑", "第四辑", "第五辑", "第六辑")
    return names[index - 1] if 1 <= index <= len(names) else f"第{index}辑"


def comic_raw_text_from_phrases(phrases: list[str], *, preface_phrase: str | None = None) -> str:
    lines: list[str] = []
    if preface_phrase:
        lines.append(f"文前短语：{preface_phrase.strip()[:120]}")
    lines.extend(f"{comic_section_title(index)}：{phrase}" for index, phrase in enumerate(phrases, start=1))
    return "\n".join(lines)


def _animal_for(index: int) -> tuple[str, str]:
    return _ANIMALS[(index - 1) % len(_ANIMALS)]


def build_comic_image_prompt(
    *,
    phrase: str,
    index: int,
    total: int,
    title: str | None = None,
) -> tuple[str, str, str]:
    animal, subject = _animal_for(index)
    display_title = (title or phrase).strip()[:18]
    prompt = f"""{COMIC_IMAGE_PROMPT}

【本辑信息】
- 本图用途：同一漫画系列中的一张画面，也可作为书架封面候选；用途信息只供内部生成参考，不得写进画面。
- 创作者短语：{phrase}
- 画面标题文字：{display_title}
- 封面主体：原创 Q 版{animal}。{subject}。

【统一系列要求】
{COMIC_SERIES_STYLE_BIBLE}
请让这张图与同系列其他图片和书架封面保持统一画风、统一线条、统一颗粒质感、统一角色比例和统一童趣出版气质，但背景主色与小道具可根据短语变化。
画面中绝对不要出现任何章节顺序、系列总数、编号、序号、图片说明或元信息文字。

【版权与安全】
{COMIC_COPYRIGHT_RULE}
"""
    return prompt.strip(), animal, subject


def build_comic_preface_image_prompt(
    *,
    phrase: str,
    title: str | None = None,
) -> tuple[str, str, str]:
    animal = "小熊和小猫"
    subject = "原创 Q 版动物伙伴坐在窗边或安静房间里，适合作为整本漫画系列的开场画面"
    display_title = (title or phrase).strip()[:18]
    prompt = f"""{COMIC_IMAGE_PROMPT}

【文前开场信息】
- 本图用途：同一漫画系列的文前横图，也可作为书架封面候选；用途信息只供内部生成参考，不得写进画面。
- 创作者文前短语：{phrase}
- 画面标题文字：{display_title}
- 画面主体：原创 Q 版{animal}。{subject}。

【统一系列要求】
{COMIC_SERIES_STYLE_BIBLE}
请让这张文前横图与正文图片和书架封面保持同一视觉系统：统一画风、统一线条、统一颗粒质感、统一角色比例和统一童趣出版气质。
画面中绝对不要出现任何章节顺序、系列总数、编号、序号、图片说明或元信息文字。

【版权与安全】
{COMIC_COPYRIGHT_RULE}
"""
    return prompt.strip(), animal, subject


def plan_comic_section_images(
    phrases: list[str],
    output_dir: Path,
    *,
    title: str | None = None,
    preface_phrase: str | None = None,
) -> list[ComicSectionImage]:
    validate_comic_phrases(phrases)
    image_dir = output_dir / "comic_images"
    image_dir.mkdir(parents=True, exist_ok=True)
    total = len(phrases)
    results: list[ComicSectionImage] = []
    cleaned_preface = parse_comic_preface_phrase(preface_phrase)
    if cleaned_preface:
        prompt, animal, subject = build_comic_preface_image_prompt(
            phrase=cleaned_preface,
            title=title,
        )
        path = image_dir / COMIC_PREFACE_IMAGE_FILENAME
        (image_dir / "comic_preface_prompt.txt").write_text(prompt, encoding="utf-8")
        results.append(
            ComicSectionImage(
                index=0,
                phrase=cleaned_preface,
                title="文前",
                animal=animal,
                subject=subject,
                prompt=prompt,
                filename=COMIC_PREFACE_IMAGE_FILENAME,
                path=path,
            )
        )
    for index, phrase in enumerate(phrases, start=1):
        prompt, animal, subject = build_comic_image_prompt(
            phrase=phrase,
            index=index,
            total=total,
            title=title,
        )
        filename = f"comic_section_{index:02d}.png"
        path = image_dir / filename
        prompt_path = image_dir / f"comic_section_{index:02d}_prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        image = ComicSectionImage(
            index=index,
            phrase=phrase,
            title=comic_section_title(index),
            animal=animal,
            subject=subject,
            prompt=prompt,
            filename=filename,
            path=path,
        )
        results.append(image)
    _write_manifest(image_dir, results)
    return results


def generate_comic_section_images(
    phrases: list[str],
    output_dir: Path,
    *,
    title: str | None = None,
    force: bool = False,
    allow_uncertain: bool = False,
    preface_phrase: str | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[ComicSectionImage]:
    image_dir = output_dir / "comic_images"
    results = plan_comic_section_images(
        phrases,
        output_dir,
        title=title,
        preface_phrase=preface_phrase,
    )
    total = len(results)

    def _mark_remaining_pending(start: int, reason: str) -> None:
        for rest in range(start, total):
            results[rest] = replace(results[rest], status="pending", error=reason)

    for offset, image in enumerate(results):
        stem = "comic_preface" if image.index == 0 else f"comic_section_{image.index:02d}"
        response_path = image_dir / f"{stem}_response.json"
        uncertain_path = image_dir / f"{stem}_uncertain.json"
        if uncertain_path.exists() and not image.path.exists() and not force:
            try:
                marker = json.loads(uncertain_path.read_text(encoding="utf-8"))
                message = str(marker.get("error") or "previous uncertain image request")
            except Exception:
                message = "previous uncertain image request"
            image = replace(image, status="uncertain", error=message)
            results[offset] = image
            if on_progress is not None:
                on_progress(offset + 1, total)
            if not allow_uncertain:
                _write_manifest(image_dir, results)
                raise ImageApiUncertainError(message)
            _mark_remaining_pending(
                offset + 1,
                "Skipped after an uncertain image request to avoid duplicate billing.",
            )
            break
        try:
            generate_image_file_from_prompt(
                image.prompt,
                image.path,
                response_cache_path=response_path,
                force=force,
            )
            if uncertain_path.exists():
                uncertain_path.unlink()
            image = replace(image, status="completed", error=None)
        except ImageApiUncertainError as exc:
            message = str(exc)
            uncertain_path.write_text(
                json.dumps(
                    {
                        "index": image.index,
                        "phrase": image.phrase,
                        "filename": image.filename,
                        "prompt_path": f"{stem}_prompt.txt",
                        "error": message,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            image = replace(image, status="uncertain", error=message)
            results[offset] = image
            if not allow_uncertain:
                _write_manifest(image_dir, results)
                raise
            if on_progress is not None:
                on_progress(offset + 1, total)
            _mark_remaining_pending(
                offset + 1,
                "Skipped after an uncertain image request to avoid duplicate billing.",
            )
            break
        results[offset] = image
        if on_progress is not None:
            on_progress(offset + 1, total)
    _write_manifest(image_dir, results)
    return results


def _write_manifest(image_dir: Path, results: list[ComicSectionImage]) -> None:
    (image_dir / "comic_images_manifest.json").write_text(
        json.dumps([_manifest_item(item) for item in results], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _manifest_item(image: ComicSectionImage) -> dict[str, object]:
    data = asdict(image)
    data["path"] = str(image.path)
    data["markdown"] = image.markdown
    data["exists"] = image.path.exists()
    return data


def build_comic_writer_input(phrases: list[str], images: list[ComicSectionImage]) -> str:
    preface_image = next((image for image in images if image.index == 0), None)
    section_images = [image for image in images if image.index > 0]
    lines = [
        f"本漫画系列共 {len(phrases)} 辑。",
        "请严格保留下列每一辑的短语、图像占位符和封面主体设定。",
        "图像占位符必须独立成行；占位符前后不要添加“第一辑配图”“第几辑插画”“第几辑封面”“共几辑”等任何图片说明文字。",
        "故事内容要默认正文配图和书架封面都属于同一套绘本系列，角色比例、线条、颗粒质感和温柔童趣气质保持一致。",
        "",
    ]
    if preface_image is not None:
        lines.extend(
            [
                "文前",
                f"文前短语：{preface_image.phrase}",
                f"文前图像占位符：{COMIC_PREFACE_IMAGE_PLACEHOLDER}",
                f"文前画面主体设定：原创 Q 版{preface_image.animal}，{preface_image.subject}",
                "",
            ]
        )
    for image in section_images:
        lines.extend(
            [
                f"{image.title}",
                f"短语：{image.phrase}",
                f"图像占位符：{{{{COMIC_IMAGE_{image.index}}}}}",
                f"封面主体设定：原创 Q 版{image.animal}，{image.subject}",
                "",
            ]
        )
    return "\n".join(lines).strip()


def inject_comic_images(markdown: str, images: list[ComicSectionImage]) -> str:
    result = markdown
    image_by_index = {image.index: image for image in images}
    preface_image = image_by_index.get(0)

    if preface_image is not None:
        result = _PREFACE_PLACEHOLDER_RE.sub(preface_image.markdown, result)

    def _replace_placeholder(match: re.Match[str]) -> str:
        index = int(match.group(1))
        image = image_by_index.get(index)
        return image.markdown if image else match.group(0)

    result = _PLACEHOLDER_RE.sub(_replace_placeholder, result)

    for image in images:
        if image.filename in result:
            continue
        if image.index == 0:
            result = _insert_preface_image(result, image)
        else:
            result = _insert_image_after_section_phrase(result, image)
    return result


def _insert_preface_image(markdown: str, image: ComicSectionImage) -> str:
    match = _PREFACE_SECTION_RE.search(markdown)
    if not match:
        return markdown.rstrip() + f"\n\n{image.markdown}\n"
    start = match.end()
    body_match = re.search(r"(?m)^@\s*正文\s*$", markdown[start:])
    end = start + body_match.start() if body_match else len(markdown)
    block = markdown[start:end]
    lines = block.splitlines()
    insert_at = 1
    for idx, line in enumerate(lines):
        if "短语" in line or image.phrase in line:
            insert_at = idx + 1
            break
    lines.insert(insert_at, image.markdown)
    return markdown[:start] + "\n".join(lines) + markdown[end:]


def _insert_image_after_section_phrase(markdown: str, image: ComicSectionImage) -> str:
    matches = list(_SECTION_RE.finditer(markdown))
    if not matches:
        return markdown.rstrip() + f"\n\n{image.markdown}\n"
    for offset, match in enumerate(matches):
        if int(match.group(1)) != image.index:
            continue
        start = match.end()
        end = matches[offset + 1].start() if offset + 1 < len(matches) else len(markdown)
        block = markdown[start:end]
        lines = block.splitlines()
        insert_at = 1
        for idx, line in enumerate(lines):
            if "短语" in line or image.phrase in line:
                insert_at = idx + 1
                break
        lines.insert(insert_at, image.markdown)
        return markdown[:start] + "\n".join(lines) + markdown[end:]
    return markdown.rstrip() + f"\n\n{image.markdown}\n"


def image_filename_from_markdown(line: str) -> str | None:
    match = _IMAGE_MARKDOWN_RE.search(line.strip())
    return match.group(2) if match else None


__all__ = [
    "COMIC_COPYRIGHT_RULE",
    "COMIC_IMAGE_PROMPT",
    "COMIC_PREFACE_IMAGE_FILENAME",
    "COMIC_PREFACE_IMAGE_PLACEHOLDER",
    "COMIC_SERIES_STYLE_BIBLE",
    "COMIC_STORY_PROMPT",
    "ComicSectionImage",
    "build_comic_image_prompt",
    "build_comic_preface_image_prompt",
    "build_comic_writer_input",
    "comic_raw_text_from_phrases",
    "generate_comic_section_images",
    "image_filename_from_markdown",
    "inject_comic_images",
    "parse_comic_input",
    "parse_comic_phrases",
    "parse_comic_preface_phrase",
    "plan_comic_section_images",
    "validate_comic_phrases",
]
