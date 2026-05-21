"""Comic-series manuscript and illustration helpers."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable

from app.core.config import settings
from app.services.cover import ImageApiUncertainError, generate_image_file_from_prompt

COMIC_IMAGE_PROMPT = """根据主题与书名，先生成一张具有出版感的治愈系封面。
整体偏日韩治愈系与文艺插画风格，简洁、有留白、有情绪氛围。
可使用人物、动物、风景、城市、房间、街道、天气、植物、光影、食物、日常物件或抽象意象等元素，根据主题自由表达。
重点是“情绪感”与“意境”，而不是复杂剧情。
色彩有统一的情绪基调与高级感，可适当使用高识别度配色。
整体像豆瓣高分绘本、微信读书情绪封面、LOFT文创、ins治愈插画。
避免低龄儿童绘本感、避免商业鸡汤海报感、避免AI模板化插画感。"""

COMIC_SECTION_IMAGE_PROMPT = """生成封面后，再根据目录中的5-6个章节短语，分别创作对应的配图。
所有配图必须与封面保持统一视觉风格，包括：
配色体系
笔触质感
构图语言
情绪氛围
留白方式
光影风格
插画颗粒感

每张配图根据章节短语表达不同情绪与意境，但仍属于同一本书的视觉体系。
画面不必重复角色，也不必强行出现人物，可根据短语自由选择意象。
重点是“短句的情绪可视化”，像一本成年人阅读的治愈系情绪绘本。
避免重复构图、重复元素、重复情绪表达。
整体克制、安静、松弛、有生活感与余味。"""

COMIC_STORY_PROMPT = """根据文前短语与5-6个目录章节短语，创作简短温柔的成年人治愈系情绪绘本 / 对话漫画书稿。
文稿内容不根据封面主体生成，也不要假设封面里一定有人物或动物。
每一辑围绕对应短语展开，必须有新的互动逻辑、情绪切口或小反转，避免重复固定套路。
故事可偏孤独、幽默、误会、社恐、嘴硬、疲惫、笨拙、小聪明、小失落、小幸运等真实成年人情绪。
对白自然、克制、像朋友聊天，不过度鸡汤，不过度治愈。
画面感聚焦生活小事与情绪瞬间。
结尾保留一点余味、反差或轻微情绪回弹，不强行升华。
整体像日本成年人治愈漫画、LOFT文创插画、小红书高赞情绪漫画。
避免低龄化、避免儿童寓言感、避免网络段子感、避免AI重复套路感。
角色形象与情节表达需原创化处理，避免直接模仿已有IP或现成卡通形象。"""

COMIC_COPYRIGHT_RULE = (
    "全系列必须为原创角色与原创画面；禁止使用、模仿或暗示任何已有动漫、绘本、游戏、电影、品牌、"
    "文创 IP、商标、logo、角色造型、角色配色、角色名称或知名画师个人风格；不要出现水印、出版社标识、二维码。"
)

COMIC_SERIES_STYLE_BIBLE = (
    "统一系列风格：封面与所有章节配图必须像同一本成年人治愈系情绪绘本。"
    "全系列共享统一配色体系、笔触质感、构图语言、情绪氛围、留白方式、光影风格和插画颗粒感。"
    "每张图可根据短语自由选择人物、动物、风景、房间、街道、天气、植物、光影、食物、日常物件或抽象意象。"
    "不要重复构图、重复元素或重复情绪表达；整体克制、安静、松弛、有生活感与余味。"
)

from app.services.comic_prompts import (  # noqa: E402
    COMIC_COPYRIGHT_RULE,
    COMIC_IMAGE_PROMPT,
    COMIC_SECTION_IMAGE_PROMPT,
    COMIC_SERIES_STYLE_BIBLE,
    COMIC_STORY_PROMPT,
    HEALING_COMIC_REFERENCE,
)

_BULLET_RE = re.compile(r"^\s*(?:[-*+•·]|\d+[.、．)]|第[一二三四五六]辑[：:、.]?)\s*")
COMIC_PREFACE_IMAGE_PLACEHOLDER = "{{COMIC_PREFACE_IMAGE}}"
COMIC_COVER_IMAGE_FILENAME = "comic_cover.png"
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


def _comic_image_gate_wait_timeout_seconds() -> int:
    """Comic series image sets should queue instead of restarting the manuscript."""
    return max(
        14400,
        int(getattr(settings, "IMAGE_GATE_WAIT_TIMEOUT_SECONDS", 900) or 900),
        int(getattr(settings, "PAID_API_GATE_WAIT_TIMEOUT_SECONDS", 14400) or 14400),
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
        raise ValueError(f"插画需要输入 5 或 6 条短语，当前识别到 {len(phrases)} 条")


def parse_comic_preface_phrase(value: object) -> str:
    raw = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return ""
    first = raw.split("\n", 1)[0]
    first = re.sub(r"^\s*(?:文前|前言|序言|开场)\s*[：:、.．)]\s*", "", first).strip()
    return first.strip(" \t，,。；;")[:120]


def parse_comic_theme_requirement(value: object) -> str:
    raw = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return ""
    first = raw.split("\n", 1)[0]
    first = re.sub(r"^\s*(?:主题要求|动物主题|角色要求|主角要求|用户要求)\s*[：:、.．)]\s*", "", first).strip()
    return first.strip(" \t，,。；;")[:240]


def parse_comic_title(value: object) -> str:
    raw = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return ""
    first = raw.split("\n", 1)[0]
    first = re.sub(r"^\s*(?:书名|标题|漫画名|插画名)\s*[：:、.．)]\s*", "", first).strip()
    return first.strip(" \t，,。；;")


def parse_comic_input_parts(value: object) -> tuple[str, str, list[str]]:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    title = ""
    preface = ""
    body_lines: list[str] = []
    for raw in text.split("\n"):
        stripped = raw.strip()
        if not stripped:
            continue
        title_match = re.match(r"^(?:书名|标题|漫画名|插画名)\s*[：:]\s*(.+?)\s*$", stripped)
        if title_match and not title:
            title = parse_comic_title(title_match.group(1))
            continue
        requirement_match = re.match(r"^(?:主题要求|动物主题|角色要求|主角要求|用户要求)\s*[：:]\s*(.+?)\s*$", stripped)
        if requirement_match:
            continue
        preface_match = re.match(r"^(?:文前|前言|序言|开场)\s*(?:短语)?\s*[：:]\s*(.+?)\s*$", stripped)
        if preface_match and not preface:
            preface = parse_comic_preface_phrase(preface_match.group(1))
            continue
        body_lines.append(raw)
    return title, preface, parse_comic_phrases(body_lines)


def parse_comic_input(value: object) -> tuple[str, list[str]]:
    _, preface, phrases = parse_comic_input_parts(value)
    return preface, phrases


def comic_section_title(index: int) -> str:
    names = ("第一辑", "第二辑", "第三辑", "第四辑", "第五辑", "第六辑")
    return names[index - 1] if 1 <= index <= len(names) else f"第{index}辑"


def comic_raw_text_from_phrases(
    phrases: list[str],
    *,
    preface_phrase: str | None = None,
    title: str | None = None,
    theme_requirement: str | None = None,
) -> str:
    lines: list[str] = []
    cleaned_title = parse_comic_title(title)
    if cleaned_title:
        lines.append(f"书名：{cleaned_title}")
    cleaned_requirement = parse_comic_theme_requirement(theme_requirement)
    if cleaned_requirement:
        lines.append(f"主题要求：{cleaned_requirement}")
    if preface_phrase:
        lines.append(f"文前短语：{preface_phrase.strip()[:120]}")
    lines.extend(f"{comic_section_title(index)}：{phrase}" for index, phrase in enumerate(phrases, start=1))
    return "\n".join(lines)


def comic_theme_from_phrases(phrases: list[str], *, preface_phrase: str | None = None) -> str:
    parts: list[str] = []
    cleaned_preface = parse_comic_preface_phrase(preface_phrase)
    if cleaned_preface:
        parts.append(f"文前主题：{cleaned_preface}")
    parts.extend(
        f"{comic_section_title(index)}：{phrase.strip()[:120]}"
        for index, phrase in enumerate(phrases, start=1)
        if phrase.strip()
    )
    return "\n".join(parts).strip()


def _animal_for(index: int) -> tuple[str, str]:
    return _ANIMALS[(index - 1) % len(_ANIMALS)]


def build_comic_image_prompt(
    *,
    phrase: str,
    index: int,
    total: int,
    title: str | None = None,
    cover_theme: str | None = None,
    theme_requirement: str | None = None,
    include_healing_reference: bool = False,
) -> tuple[str, str, str]:
    visual_focus = "自由意象"
    subject = "根据章节短语自由选择人物、动物、风景、房间、街道、天气、植物、光影、食物、日常物件或抽象意象"
    display_title = (title or "未定书名").strip()
    theme = (cover_theme or phrase).strip()
    requirement = parse_comic_theme_requirement(theme_requirement)
    requirement_section = (
        f"\n【用户可选主题要求】\n- {requirement}\n"
        "- 只能转化为原创角色或原创动物主题；如果用户写了真实 IP、角色名或品牌名，只保留动物/情绪/关系的抽象方向，不能复刻或暗示原 IP。\n"
        if requirement
        else ""
    )
    reference_section = (
        f"\n【漫画端口长期参考与防抄袭规范】\n{HEALING_COMIC_REFERENCE}\n"
        if include_healing_reference and HEALING_COMIC_REFERENCE
        else ""
    )
    prompt = f"""{COMIC_SECTION_IMAGE_PROMPT}

【本书封面风格锚点】
- 书名：{display_title}
- 全书主题与目录短语：
{theme}

【本章节配图信息】
- 本图用途：同一漫画系列中的第 {index} 张章节配图；用途信息只供内部生成参考，不得写进画面。
- 章节短语：{phrase}
- 视觉表达：{subject}。
{requirement_section}

【统一系列要求】
{COMIC_SERIES_STYLE_BIBLE}
请让这张配图与已经生成的封面保持同一视觉系统，包括配色体系、笔触质感、构图语言、情绪氛围、留白方式、光影风格和插画颗粒感。
画面中绝对不要出现任何章节顺序、系列总数、编号、序号、图片说明或元信息文字。

【版权与安全】
{COMIC_COPYRIGHT_RULE}
{reference_section}
"""
    return prompt.strip(), visual_focus, subject


def build_comic_cover_image_prompt(
    *,
    phrase: str,
    title: str | None = None,
    theme_requirement: str | None = None,
    include_healing_reference: bool = False,
) -> tuple[str, str, str]:
    visual_focus = "出版感治愈封面"
    subject = "根据主题自由选择人物、动物、风景、城市、房间、街道、天气、植物、光影、食物、日常物件或抽象意象"
    display_title = (title or "未定书名").strip()
    requirement = parse_comic_theme_requirement(theme_requirement)
    requirement_section = (
        f"\n【用户可选主题要求】\n- {requirement}\n"
        "- 可以使用该动物或主题方向，但必须重新设计成原创角色；禁止使用、近似模仿或暗示任何真实 IP、品牌角色、知名卡通或现成绘本人物。\n"
        if requirement
        else ""
    )
    reference_section = (
        f"\n【漫画端口长期参考与防抄袭规范】\n{HEALING_COMIC_REFERENCE}\n"
        if include_healing_reference and HEALING_COMIC_REFERENCE
        else ""
    )
    prompt = f"""{COMIC_IMAGE_PROMPT}

【封面信息】
- 本图用途：整本漫画系列的书架封面与文前开场图；用途信息只供内部生成参考，不得写进画面。
- 书名：{display_title}
- 主题与目录短语：
{phrase}
- 视觉表达范围：{subject}。
{requirement_section}

【统一系列要求】
{COMIC_SERIES_STYLE_BIBLE}
这张图是后续章节配图的视觉基准：配色体系、笔触质感、构图语言、情绪氛围、留白方式、光影风格和插画颗粒感都要清晰、统一、有出版感。
画面中绝对不要出现任何章节顺序、系列总数、编号、序号、图片说明或元信息文字。

【版权与安全】
{COMIC_COPYRIGHT_RULE}
{reference_section}
"""
    return prompt.strip(), visual_focus, subject


def build_comic_preface_image_prompt(
    *,
    phrase: str,
    title: str | None = None,
    cover_theme: str | None = None,
    theme_requirement: str | None = None,
    include_healing_reference: bool = False,
) -> tuple[str, str, str]:
    visual_focus = "文前短语配图"
    subject = "根据文前短语自由选择人物、动物、风景、房间、街道、天气、植物、光影、食物、日常物件或抽象意象"
    display_title = (title or "未定书名").strip()
    theme = (cover_theme or phrase).strip()
    requirement = parse_comic_theme_requirement(theme_requirement)
    requirement_section = (
        f"\n【用户可选主题要求】\n- {requirement}\n"
        "- 只能转化为原创角色或原创动物主题；如果用户写了真实 IP、角色名或品牌名，只保留动物/情绪/关系的抽象方向，不能复刻或暗示原 IP。\n"
        if requirement
        else ""
    )
    reference_section = (
        f"\n【漫画端口长期参考与防抄袭规范】\n{HEALING_COMIC_REFERENCE}\n"
        if include_healing_reference and HEALING_COMIC_REFERENCE
        else ""
    )
    prompt = f"""{COMIC_SECTION_IMAGE_PROMPT}

【本书封面风格锚点】
- 书名：{display_title}
- 全书主题与目录短语：
{theme}

【文前短语配图信息】
- 本图用途：文前短语配图；用途信息只供内部生成参考，不得写进画面。
- 文前短语：{phrase}
- 视觉表达：{subject}。
{requirement_section}

【统一系列要求】
{COMIC_SERIES_STYLE_BIBLE}
请让这张文前短语配图与已经生成的封面保持同一视觉系统，包括配色体系、笔触质感、构图语言、情绪氛围、留白方式、光影风格和插画颗粒感。
画面中绝对不要出现任何章节顺序、系列总数、编号、序号、图片说明或元信息文字。

【版权与安全】
{COMIC_COPYRIGHT_RULE}
{reference_section}
"""
    return prompt.strip(), visual_focus, subject


def plan_comic_section_images(
    phrases: list[str],
    output_dir: Path,
    *,
    title: str | None = None,
    preface_phrase: str | None = None,
    theme_requirement: str | None = None,
    include_healing_reference: bool = False,
) -> list[ComicSectionImage]:
    validate_comic_phrases(phrases)
    image_dir = output_dir / "comic_images"
    image_dir.mkdir(parents=True, exist_ok=True)
    total = len(phrases)
    results: list[ComicSectionImage] = []
    cleaned_preface = parse_comic_preface_phrase(preface_phrase)
    cover_theme = comic_theme_from_phrases(phrases, preface_phrase=cleaned_preface)
    cover_prompt, cover_focus, cover_subject = build_comic_cover_image_prompt(
        phrase=cover_theme,
        title=title,
        theme_requirement=theme_requirement,
        include_healing_reference=include_healing_reference,
    )
    cover_path = image_dir / COMIC_COVER_IMAGE_FILENAME
    (image_dir / "comic_cover_prompt.txt").write_text(cover_prompt, encoding="utf-8")
    results.append(
        ComicSectionImage(
            index=-1,
            phrase=cleaned_preface or phrases[0],
            title="封面",
            animal=cover_focus,
            subject=cover_subject,
            prompt=cover_prompt,
            filename=COMIC_COVER_IMAGE_FILENAME,
            path=cover_path,
        )
    )
    preface_prompt, preface_focus, preface_subject = build_comic_preface_image_prompt(
        phrase=cleaned_preface,
        title=title,
        cover_theme=cover_theme,
        theme_requirement=theme_requirement,
        include_healing_reference=include_healing_reference,
    )
    preface_path = image_dir / COMIC_PREFACE_IMAGE_FILENAME
    (image_dir / "comic_preface_prompt.txt").write_text(preface_prompt, encoding="utf-8")
    results.append(
        ComicSectionImage(
            index=0,
            phrase=cleaned_preface,
            title="文前",
            animal=preface_focus,
            subject=preface_subject,
            prompt=preface_prompt,
            filename=COMIC_PREFACE_IMAGE_FILENAME,
            path=preface_path,
        )
    )
    for index, phrase in enumerate(phrases, start=1):
        prompt, animal, subject = build_comic_image_prompt(
            phrase=phrase,
            index=index,
            total=total,
            title=title,
            cover_theme=cover_theme,
            theme_requirement=theme_requirement,
            include_healing_reference=include_healing_reference,
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
    theme_requirement: str | None = None,
    include_healing_reference: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[ComicSectionImage]:
    image_dir = output_dir / "comic_images"
    results = plan_comic_section_images(
        phrases,
        output_dir,
        title=title,
        preface_phrase=preface_phrase,
        theme_requirement=theme_requirement,
        include_healing_reference=include_healing_reference,
    )
    total = len(results)

    def _mark_remaining_pending(start: int, reason: str) -> None:
        for rest in range(start, total):
            results[rest] = replace(results[rest], status="pending", error=reason)

    for offset, image in enumerate(results):
        if image.index < 0:
            stem = "comic_cover"
        elif image.index == 0:
            stem = "comic_preface"
        else:
            stem = f"comic_section_{image.index:02d}"
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
                gate_wait_timeout_seconds=_comic_image_gate_wait_timeout_seconds(),
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


def build_comic_writer_input(
    phrases: list[str],
    *,
    preface_phrase: str | None = None,
    title: str | None = None,
    theme_requirement: str | None = None,
) -> str:
    cleaned_title = parse_comic_title(title)
    cleaned_requirement = parse_comic_theme_requirement(theme_requirement)
    cleaned_preface = parse_comic_preface_phrase(preface_phrase)
    lines = [
        f"本漫画系列共 {len(phrases)} 辑。",
        "请严格使用创作者输入的书名，不要生成、改写或润色书名。",
        "请严格保留文前短语、目录章节短语和图像占位符。",
        "图像占位符必须独立成行；占位符前后不要添加“第一辑配图”“第几辑插画”“第几辑封面”“共几辑”等任何图片说明文字。",
        "文稿内容只根据短语创作，不要根据封面主体生成，也不要假设封面里一定有人物、动物或固定角色。",
        "",
    ]
    if cleaned_title:
        lines.extend(["书名", cleaned_title, ""])
    if cleaned_requirement:
        lines.extend(
            [
                "用户可选主题要求",
                cleaned_requirement,
                "如果这里出现真实 IP、角色名、品牌名或知名卡通，只保留动物类型、情绪方向和关系气质，必须改造成原创角色。",
                "",
            ]
        )
    if cleaned_preface:
        lines.extend(
            [
                "文前",
                f"文前短语：{cleaned_preface}",
                f"文前图像占位符：{COMIC_PREFACE_IMAGE_PLACEHOLDER}",
                "",
            ]
        )
    lines.append("目录章节短语")
    for index, phrase in enumerate(phrases, start=1):
        lines.extend(
            [
                f"{comic_section_title(index)}",
                f"短语：{phrase}",
                f"图像占位符：{{{{COMIC_IMAGE_{index}}}}}",
                "",
            ]
        )
    return "\n".join(lines).strip()


def comic_book_title_from_phrases(
    phrases: list[str],
    *,
    preface_phrase: str | None = None,
    title: str | None = None,
) -> str:
    explicit_title = parse_comic_title(title)
    if explicit_title:
        return explicit_title
    title = parse_comic_preface_phrase(preface_phrase) or (phrases[0].strip() if phrases else "")
    title = title.strip(" \t，,。；;")
    return title[:30] or "治愈漫画系列"


def assemble_comic_series_markdown(
    phrases: list[str],
    *,
    preface_phrase: str | None = None,
    title: str | None = None,
) -> str:
    validate_comic_phrases(phrases)
    cleaned_preface = parse_comic_preface_phrase(preface_phrase)
    final_title = comic_book_title_from_phrases(phrases, preface_phrase=cleaned_preface, title=title)
    lines: list[str] = [
        "@最终书名",
        final_title,
        "@目录",
    ]
    lines.extend(f"{index}. {phrase}" for index, phrase in enumerate(phrases, start=1))
    lines.append("@文前")
    if cleaned_preface:
        lines.append(f"文前短语：{cleaned_preface}")
    lines.append(COMIC_PREFACE_IMAGE_PLACEHOLDER)
    lines.append("@正文")
    for index, phrase in enumerate(phrases, start=1):
        lines.extend(
            [
                f"@{index}. {comic_section_title(index)}",
                f"短语：{phrase}",
                f"{{{{COMIC_IMAGE_{index}}}}}",
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
        if image.index < 0:
            continue
        if image.index == 0:
            result = _insert_preface_image(result, image)
        else:
            result = _insert_image_after_section_phrase(result, image)
    return result


def extract_comic_title(markdown: str) -> str:
    lines = (markdown or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    in_title = False
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        section = re.match(r"^@?\s*(最终书名|最终简介|目录|文前|正文)\s*$", line)
        if section:
            in_title = section.group(1) == "最终书名"
            continue
        if in_title:
            return line.lstrip("@").strip()[:60]
    return ""


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
    "COMIC_SECTION_IMAGE_PROMPT",
    "COMIC_SERIES_STYLE_BIBLE",
    "COMIC_STORY_PROMPT",
    "ComicSectionImage",
    "assemble_comic_series_markdown",
    "build_comic_cover_image_prompt",
    "build_comic_image_prompt",
    "build_comic_preface_image_prompt",
    "build_comic_writer_input",
    "comic_book_title_from_phrases",
    "comic_raw_text_from_phrases",
    "comic_theme_from_phrases",
    "extract_comic_title",
    "generate_comic_section_images",
    "image_filename_from_markdown",
    "inject_comic_images",
    "parse_comic_input",
    "parse_comic_input_parts",
    "parse_comic_phrases",
    "parse_comic_preface_phrase",
    "parse_comic_theme_requirement",
    "parse_comic_title",
    "plan_comic_section_images",
    "validate_comic_phrases",
]
