"""Comic-series manuscript and illustration helpers."""
from __future__ import annotations

import json
import re
import zipfile
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable

from app.core.config import settings
from app.services.cover import ImageApiPermanentError, ImageApiUncertainError, generate_image_file_from_prompt

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
重点是“短句的情绪可视化”，像一本普通读者也能轻松阅读的治愈系情绪绘本。
避免重复构图、重复元素、重复情绪表达。
整体克制、安静、松弛、有生活感与余味。"""

COMIC_STORY_PROMPT = """根据文前短语与5-6个目录章节短语，创作简短温柔的日常治愈系情绪绘本 / 对话漫画书稿。
文稿内容不根据封面主体生成，也不要假设封面里一定有人物或动物。
每一辑围绕对应短语展开，必须有新的互动逻辑、情绪切口或小反转，避免重复固定套路。
故事可偏孤独、幽默、误会、社恐、嘴硬、疲惫、笨拙、小聪明、小失落、小幸运等真实日常情绪。
对白自然、克制、像朋友聊天，不过度鸡汤，不过度治愈。
画面感聚焦生活小事与情绪瞬间。
结尾保留一点余味、反差或轻微情绪回弹，不强行升华。
整体像温暖治愈漫画、LOFT文创插画、小红书高赞情绪漫画。
避免低龄化、避免儿童寓言感、避免网络段子感、避免AI重复套路感。
角色形象与情节表达需原创化处理，避免直接模仿已有IP或现成卡通形象。"""

COMIC_COPYRIGHT_RULE = (
    "全系列必须为原创角色与原创画面；禁止使用、模仿或暗示任何已有动漫、绘本、游戏、电影、品牌、"
    "文创 IP、商标、logo、角色造型、角色配色、角色名称或知名画师个人风格；不要出现水印、出版社标识、二维码。"
)

COMIC_SERIES_STYLE_BIBLE = (
    "统一系列风格：封面与所有章节配图必须像同一本日常治愈系情绪绘本。"
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

HEALING_COMIC_STYLE_GUARD = """- 漫画端必须保留治愈插画链路中已经验证有效的视觉质量基座：出版感、温暖感、白底留白、低压力阅读、轻水彩或蜡笔颗粒、统一主色、统一线条、统一角色气质。
- 这里的治愈插画只作为视觉语言和稳定质量基座；最终产物必须是原创治愈漫画图文书，不得变成单张插画合集。
- 每一章相当于一个温暖分类，章节内至少 6 张图，但 6 只是硬性下限，不是固定数量；具体张数由模型根据本章分类、短句密度和可展开的小故事自然决定，最多不超过 20 张；不能全书每章都只有 6 张；每张图都要是同一主角参与的独立小故事；多张图合在一起是本章分类下的小故事汇总，不能只是同一画面换角度。
- 全书封面、章节图片、图上短句、正文短语必须属于同一套治愈系列，温暖、克制、柔和、有日常生活感。
- 避免鸡汤、口号、儿童寓言、平台段子、功能化标题和模板化安慰。"""

_BULLET_RE = re.compile(r"^\s*(?:[-*+•·]|\d+[.、．)]|第[一二三四五六]辑[：:、.]?)\s*")
COMIC_PREFACE_IMAGE_PLACEHOLDER = "{{COMIC_PREFACE_IMAGE}}"
COMIC_COVER_IMAGE_FILENAME = "comic_cover.png"
COMIC_PREFACE_IMAGE_FILENAME = "comic_preface.png"

_IMAGE_MARKDOWN_RE = re.compile(
    r"!\[([^\]]*)\]\((?:comic_images/)?(comic_preface\.png|comic_section_\d+(?:_\d+)?\.png)\)"
)
_PLACEHOLDER_RE = re.compile(r"\{\{COMIC_IMAGE_(\d+)\}\}")
_MULTI_IMAGE_PLACEHOLDER_RE = re.compile(r"\{\{COMIC_IMAGE_(\d+)_(\d+)\}\}")
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
HEALING_COMIC_DEFAULT_PANEL_COUNT = 32
HEALING_COMIC_PANEL_COUNT = HEALING_COMIC_DEFAULT_PANEL_COUNT
HEALING_COMIC_MIN_PANEL_COUNT = 1
HEALING_COMIC_MAX_PANEL_COUNT: int | None = None
HEALING_COMIC_MIN_IMAGES_PER_CHAPTER = 6
HEALING_COMIC_MAX_IMAGES_PER_CHAPTER = 20
REFERENCE_ASSET_DIR = Path(__file__).with_name("reference_assets") / "healing_comic"
HEALING_COMIC_REFERENCE_EPUB = REFERENCE_ASSET_DIR / "kan_ni_yi_yan_jiu_hui_xiao.epub"
HEALING_COMIC_REFERENCE_COVERS_ZIP = REFERENCE_ASSET_DIR / "reference_covers.zip"
HEALING_COMIC_STORY_ROLES = (
    "清晨小故事",
    "路上小故事",
    "停顿小故事",
    "误会小故事",
    "松口气小故事",
    "收住光小故事",
    "窗边小故事",
    "晚风小故事",
    "整理小故事",
    "等候小故事",
    "轻声小故事",
    "回家小故事",
)


def _comic_image_gate_wait_timeout_seconds() -> int:
    """Comic series image sets should queue instead of restarting the manuscript."""
    return max(
        14400,
        int(getattr(settings, "IMAGE_GATE_WAIT_TIMEOUT_SECONDS", 900) or 900),
        int(getattr(settings, "PAID_API_GATE_WAIT_TIMEOUT_SECONDS", 14400) or 14400),
    )


class ComicGenerationPaused(RuntimeError):
    """Raised when a healing comic task is paused between image requests."""


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
    sub_index: int = 1
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
        phrase = phrase.strip()
        if phrase:
            phrases.append(phrase)
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
    return first.strip()


def parse_comic_theme_requirement(value: object) -> str:
    raw = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return ""
    first = raw.split("\n", 1)[0]
    first = re.sub(r"^\s*(?:主题要求|动物主题|角色要求|主角要求|用户要求)\s*[：:、.．)]\s*", "", first).strip()
    return first.strip()


def parse_healing_comic_requirement(value: object) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    text = re.sub(r"^\s*(?:漫画要求|内容要求|生成要求|主题要求)\s*[：:]\s*", "", text)
    return text


def parse_healing_comic_source_content(value: object) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    text = re.sub(
        r"^\s*(?:自定义内容|文章内容|参考文章|原文内容|素材正文|内容来源|用户文章|用户提供内容)\s*[：:]\s*",
        "",
        text,
    ).strip()
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text


def parse_healing_comic_character_prompt(value: object) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    text = re.sub(r"^\s*(?:主角形象|主角设定|角色形象|角色设定|主角要求|角色要求|主题要求|用户要求)\s*[：:]\s*", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def parse_healing_comic_title(value: object) -> str:
    raw = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return ""
    first = raw.split("\n", 1)[0]
    first = re.sub(r"^\s*(?:书名|标题|漫画名|漫画书名)\s*[：:、.．)]\s*", "", first).strip()
    return first.strip()


def parse_healing_comic_panel_count(value: object, *, default: int = HEALING_COMIC_DEFAULT_PANEL_COUNT) -> int:
    try:
        if isinstance(value, str):
            match = re.search(r"\d+", value)
            raw = int(match.group(0)) if match else default
        else:
            raw = int(value or default)
    except (TypeError, ValueError):
        raw = default
    return max(HEALING_COMIC_MIN_PANEL_COUNT, raw)


def _read_epub_toc_titles(limit: int = 180) -> list[str]:
    if not HEALING_COMIC_REFERENCE_EPUB.exists():
        return []
    try:
        with zipfile.ZipFile(HEALING_COMIC_REFERENCE_EPUB) as archive:
            if "toc.ncx" in archive.namelist():
                toc = archive.read("toc.ncx").decode("utf-8", errors="ignore")
                titles = re.findall(r"<text>(.*?)</text>", toc, flags=re.S)
            else:
                titles = []
            cleaned: list[str] = []
            skip = {"版权信息", "目录", "序言"}
            for title in titles:
                text = re.sub(r"<[^>]+>", "", title).strip()
                text = text.replace("（Buddy Gator盖朵短尾鳄漫画暖心上市！创意脑洞+可爱疗愈，每一页漫画都能将你暖到融化！）", "")
                if not text or text in skip or text.startswith("看你一眼就会笑"):
                    continue
                if text not in cleaned:
                    cleaned.append(text[:40])
                if len(cleaned) >= limit:
                    break
            return cleaned
    except Exception:
        return []


def _reference_asset_summary() -> str:
    epub_state = "已安装" if HEALING_COMIC_REFERENCE_EPUB.exists() else "缺失"
    cover_state = "已安装" if HEALING_COMIC_REFERENCE_COVERS_ZIP.exists() else "缺失"
    epub_count = 0
    cover_count = 0
    try:
        with zipfile.ZipFile(HEALING_COMIC_REFERENCE_EPUB) as archive:
            epub_count = len([name for name in archive.namelist() if name.lower().endswith((".jpg", ".jpeg", ".png"))])
    except Exception:
        epub_count = 0
    try:
        with zipfile.ZipFile(HEALING_COMIC_REFERENCE_COVERS_ZIP) as archive:
            cover_count = len([name for name in archive.namelist() if name.lower().endswith((".jpg", ".jpeg", ".png"))])
    except Exception:
        cover_count = 0
    return (
        f"原始参考资产：EPUB={epub_state}（约 {epub_count} 张漫画页图），"
        f"参考封面ZIP={cover_state}（约 {cover_count} 张封面图）。"
    )


def healing_comic_reference_context(title: str) -> str:
    toc_titles = _read_epub_toc_titles()
    samples = "、".join(toc_titles[:96])
    return f"""漫画参考数据库已装入服务端。
{_reference_asset_summary()}

用户书名：{parse_healing_comic_title(title) or "未命名漫画"}

EPUB 目录抽象节奏样本：
{samples}

从参考库归纳出的创作方法：
- 章节很多但每章很轻，每页只服务一个日常小瞬间或一个小反差。
- 标题偏短，常用动作、物件、天气、关系、小状况命名；不解释大道理。
- 每页文字极少，适合直接写进画面，通常是 1 到 3 句短句。
- 最终成品应像微信读书同系列治愈漫画：白底大留白，一页一个小场景，多页摊开看时风格一脉相承。
- 画面要有留白、原创角色动作和轻微幽默，不靠复杂剧情推进。
- 可以使用原创固定角色贯穿全书，但绝对不要使用绿色鳄鱼、短尾鳄或任何近似 Buddy Gator 的角色设定。
- 参考封面包体现出版书封方法：高识别度配色、标题醒目、构图简洁、平面插画或纸张颗粒感，避免信息堆砌。

长期参考规范：
{HEALING_COMIC_REFERENCE}
"""


def parse_comic_title(value: object) -> str:
    raw = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return ""
    first = raw.split("\n", 1)[0]
    first = re.sub(r"^\s*(?:书名|标题|漫画名|插画名)\s*[：:、.．)]\s*", "", first).strip()
    return first.strip()


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
        lines.append(f"文前短语：{preface_phrase.strip()}")
    lines.extend(f"{comic_section_title(index)}：{phrase}" for index, phrase in enumerate(phrases, start=1))
    return "\n".join(lines)


def comic_theme_from_phrases(phrases: list[str], *, preface_phrase: str | None = None) -> str:
    parts: list[str] = []
    cleaned_preface = parse_comic_preface_phrase(preface_phrase)
    if cleaned_preface:
        parts.append(f"文前主题：{cleaned_preface}")
    parts.extend(
        f"{comic_section_title(index)}：{phrase.strip()}"
        for index, phrase in enumerate(phrases, start=1)
        if phrase.strip()
    )
    return "\n".join(parts).strip()


def _animal_for(index: int) -> tuple[str, str]:
    return _ANIMALS[(index - 1) % len(_ANIMALS)]


_GENERIC_HEALING_TITLES = (
    "慢慢醒来",
    "今天轻一点",
    "抱抱自己",
    "小事也发光",
    "风会等你",
    "先坐一会儿",
    "雨停之前",
    "心事有座位",
    "晚安不着急",
    "明天也温柔",
    "把灯留小",
    "云朵在等",
)
_GENERIC_TITLE_RE = re.compile(r"^(?:第?\d+\s*[张章页]|章节标题|未命名|标题)$")


def _clean_healing_comic_title(value: object, index: int, *, fallback_lines: object = None) -> str:
    raw = str(value or "").strip()
    raw = re.sub(r"\s+", "", raw).strip("，,。；;：:")
    if raw and not _GENERIC_TITLE_RE.fullmatch(raw):
        return raw[:14]
    fallback = _short_text_lines(fallback_lines, fallback=_GENERIC_HEALING_TITLES[(index - 1) % len(_GENERIC_HEALING_TITLES)])[0]
    if fallback and not _GENERIC_TITLE_RE.fullmatch(fallback):
        return fallback[:14]
    return _GENERIC_HEALING_TITLES[(index - 1) % len(_GENERIC_HEALING_TITLES)]


def _clamp_healing_image_count(count: int) -> int:
    count = max(HEALING_COMIC_MIN_IMAGES_PER_CHAPTER, count)
    if HEALING_COMIC_MAX_IMAGES_PER_CHAPTER is not None:
        count = min(HEALING_COMIC_MAX_IMAGES_PER_CHAPTER, count)
    return count


def _healing_image_count_for_chapter(panel: dict[str, object], index: int = 1) -> int:
    raw_images = panel.get("images") or panel.get("pictures") or panel.get("image_sequence")
    requested = panel.get("image_count") or panel.get("images_count") or panel.get("picture_count")
    if requested is not None and str(requested).strip():
        try:
            requested_count = int(requested)
        except (TypeError, ValueError):
            requested_count = HEALING_COMIC_MIN_IMAGES_PER_CHAPTER
        return _clamp_healing_image_count(requested_count)
    raw_count = len(raw_images) if isinstance(raw_images, list) else 0
    return _clamp_healing_image_count(raw_count)


def _normalize_healing_chapter_images(panel: dict[str, object], index: int) -> list[dict[str, object]]:
    title = str(panel.get("title") or _GENERIC_HEALING_TITLES[(index - 1) % len(_GENERIC_HEALING_TITLES)])
    phrases = _short_text_lines(panel.get("phrases") or panel.get("image_text") or panel.get("dialogue"), fallback=title)
    base_visual = str(panel.get("visual") or panel.get("caption") or title).strip()
    raw_images = panel.get("images") or panel.get("pictures") or panel.get("image_sequence")
    images: list[dict[str, object]] = []
    max_count = HEALING_COMIC_MAX_IMAGES_PER_CHAPTER
    if isinstance(raw_images, list):
        for sub_index, item in enumerate(raw_images, start=1):
            if max_count is not None and len(images) >= max_count:
                break
            if not isinstance(item, dict):
                continue
            image_text = _short_text_lines(
                item.get("image_text") or item.get("caption") or item.get("dialogue") or phrases,
                fallback=phrases[0],
            )
            visual = str(item.get("visual") or item.get("scene") or item.get("narrative") or base_visual).strip()
            role = str(item.get("scene_role") or item.get("role") or f"第{sub_index}张").strip()
            images.append(
                {
                    "sub_index": sub_index,
                    "scene_role": role[:30],
                    "image_text": image_text,
                    "visual": visual[:420],
                    "narrative": str(item.get("narrative") or visual or base_visual).strip()[:420],
                }
            )
    target_count = max(_healing_image_count_for_chapter(panel, index), len(images))
    if max_count is not None:
        target_count = min(max_count, target_count)
    while len(images) < target_count:
        sub_index = len(images) + 1
        role = HEALING_COMIC_STORY_ROLES[(sub_index - 1) % len(HEALING_COMIC_STORY_ROLES)]
        text_seed = phrases[(sub_index - 1) % len(phrases)]
        text_lines = [text_seed] if text_seed else phrases[:1]
        images.append(
            {
                "sub_index": sub_index,
                "scene_role": role,
                "image_text": text_lines[:3],
                "visual": f"{base_visual}；本章是一个分类，本图是分类下第{sub_index}个完整小故事，主题为“{role}”，必须换成新的生活场景、新动作和新情绪落点，不要复用上一张构图。",
                "narrative": f"本图单独讲完一个很短的小故事：{title}分类下的{role}，有起因、动作和轻微落点，但保持单页治愈漫画的克制。",
            }
        )
    return images


def build_healing_comic_character_bible(
    character_prompt: object = "",
    series_meta: dict[str, str] | None = None,
) -> str:
    user_prompt = parse_healing_comic_character_prompt(character_prompt)
    generated_profile = str((series_meta or {}).get("protagonist_profile") or "").strip()
    if user_prompt:
        source = f"用户自定义主角形象：\n{user_prompt}"
    elif generated_profile:
        source = f"模型规划的原创主角形象：\n{generated_profile}"
    else:
        source = "未提供主角形象时，必须先设计一个完全原创、温柔、适合治愈漫画的固定主角。"
    return f"""{source}

主角一致性硬约束：
- 后续封面和所有章节的所有图片都必须使用同一个主角，不得中途换物种、换年龄感、换体型、换主色、换服装核心元素或换标志性配件。
- 如果用户只给出方向，需要把它转成可执行的视觉锚点：物种或身份、体型轮廓、主色、面部特征、服装、配件、表情气质、线条质感、常见动作。
- 每张图可以改变动作、姿势、距离、场景和情绪，但主角的可识别外观必须一致。
- 不得使用真实 IP、品牌角色、知名动漫角色、绿色鳄鱼、短尾鳄或任何可被识别为现成角色的造型。"""


def build_healing_comic_writer_prompt(
    title: str,
    research_context: str,
    *,
    panel_count: int = HEALING_COMIC_DEFAULT_PANEL_COUNT,
    character_prompt: str | None = None,
    source_content: str | None = None,
) -> str:
    book_title = parse_healing_comic_title(title) or "今天也不用很厉害"
    panel_count = parse_healing_comic_panel_count(panel_count)
    character_bible = build_healing_comic_character_bible(character_prompt)
    source_text = parse_healing_comic_source_content(source_content)
    source_block = ""
    source_rule = "只根据书名自动推导一类适合的漫画系列，不需要用户再输入短语、角色或剧情。"
    if source_text:
        source_block = f"""
【用户自定义内容】
本次用户提供了一篇文章或已有内容。请在同一次脚本规划中完成提取和改编，不要新增单独提取阶段。

必须先从这篇内容里提取可漫画化的主题、情绪、生活场景、人物关系、细节物件、转折和余味，再改造成原创温暖治愈漫画系列。
- 这篇内容只能作为参照材料，不得逐段改写，不得复刻原文结构，不得照搬原句，不得把原文做成摘要。
- 每个章节必须能回到原内容里的某个情绪、场景、问题或细节，但表达必须是原创日常治愈小故事。
- 如果内容信息密度高，可以把同一主题拆成多个温暖分类；如果内容很短，不要硬凑无关内容。
- 不得出现“文章里提到”“原文说”“素材显示”“用户提供的内容中”等元叙述。
- 仍然保持治愈系漫画图文书：主角一致、风格统一、每章多图、每张图都是独立小故事。

用户自定义内容全文：
{source_text}
"""
        source_rule = "如果存在“用户自定义内容”，必须围绕其中提取出的主题、情绪、场景、人物关系和细节物件进行漫画化改编；不要只按书名自由发挥，也不要复述原文。"
    return f"""你是原创温暖治愈系漫画主编。请根据用户给定书名，参考服务端漫画数据库的抽象规律，创作一本 {panel_count} 章的原创温暖治愈漫画图文书。

用户书名：
{book_title}

【主角形象圣经】
{character_bible}

服务端参考数据库摘要：
{research_context}
{source_block}

【治愈系列风格基座】
{HEALING_COMIC_STYLE_GUARD}

硬性要求：
- {source_rule}
- 需要正好输出 {panel_count} 条章节；不要固定成 32 条，也不要自行增减。
- {panel_count} 是本次用户指定章节数，不是系统上限；不得因为超过 32 或 64 就截断章节。
- 每条必须自拟章节标题，标题正文建议 4 到 12 个中文字符，必须温暖、治愈、柔和，有日常生活感；不要写成“第1张”“第2章”这类功能标题；禁止使用“把”字句、“被”字句等复杂句式。
- 每条必须给出 image_text：这是要直接写在配图上的中文短语，1 到 3 行，每行 4 到 12 个中文字符，短、轻、好读。
- 每条必须给出 phrases：章节正文使用的几句短语，必须与 image_text 一致或高度一致；正文只是短语，不要写故事长文。
- 每条必须给出 visual：画面设计必须先服务 image_text，让图片和短语天然绑定。
- 每条必须给出 images：这是该章节分类下的所有小故事图片，绝对不能只给 1 张；每章至少 {HEALING_COMIC_MIN_IMAGES_PER_CHAPTER} 张，6 张只是硬性下限，不是默认固定值；必须根据本章分类、短句密度、情绪层次和可展开的小故事数量，由模型自行决定 6 到 {HEALING_COMIC_MAX_IMAGES_PER_CHAPTER} 张之间的合适数量；如果全书每章都只有 6 张，视为失败；不要为了凑数量制造重复画面或空泛小故事。
- 章节本身相当于一个温暖分类，不是一张图的标题；该章节下的每一张图片都必须是一个独立小故事，所有图片合在一起构成本章分类的小故事汇总。
- images 里的每一张都必须有 scene_role、image_text、visual、narrative；每张图都要单独讲完一个很短的小故事，有起因、动作和轻微落点，不能只是同一画面换说法，也不能把同章多张图拆成同一个故事的机械分镜；同章新增的第 7 张、第 8 张、第 9 张或更多图片也必须继续属于本章分类，不能跑题。
- 每个章节的所有 images 都必须继续使用同一个主角形象，并在 visual 中写清楚主角正在做什么、场景如何变化、情绪如何推进。
- 全书所有章节和所有图片必须属于同一本书：统一主色、线条、颗粒感、留白、光影、主角形象、角色气质和出版感。
- 最终摊开效果要像同一本微信读书治愈漫画：白底大留白、单页小场景、少量角色动作、手写短句、轻幽默。
- 可以有原创固定角色，也可以用人物、动物、植物、物件、天气、房间、街道、光影等意象，但不要复刻参考书主角。
- 如果使用动物主角，必须避开绿色鳄鱼、短尾鳄、鳄鱼外形和参考书可识别角色；改成完全原创的动物或人物。
- 章节之间要有节奏变化：孤独、嘴硬、疲惫、误会、小幸运、社恐、幽默、笨拙、松弛、陪伴、自由感等轮换。
- 画面文字必须是 image_text，不要出现章节编号、页码、水印、logo 或多余说明。
- 只学习参考库的抽象气质：短句、留白、轻幽默、日常治愈、温柔反差。
- 禁止复刻、改写、近似模仿或暗示 Buddy Gator、盖朵短尾鳄、任何鳄鱼 IP、微信读书真实漫画、现成角色、现成台词或现成分镜。
- 不要使用真实 IP 名称、平台名、出版社名、作者名、品牌名。
- 不要鸡汤、不要说教、不要网络段子合集，不要儿童寓言感。

只输出 JSON，不要解释，不要代码块。格式如下：
{{
  "title": "{book_title}",
  "intro": "一句原创简介",
  "series_style": "统一视觉风格说明，包括主色、线条、颗粒、留白、角色气质",
  "protagonist_profile": "固定主角形象说明，必须写清楚物种或身份、体型、主色、服装、配件、表情气质和不可改变的视觉锚点",
  "cover_visual": "封面画面描述",
  "panels": [
    {{
      "title": "章节标题",
      "image_count": 7,
      "image_text": ["写在图上的短句1", "写在图上的短句2"],
      "phrases": ["正文短语1", "正文短语2"],
      "visual": "本章整体画面方向",
      "images": [
        {{
          "scene_role": "清晨小故事",
          "image_text": ["写在图上的短句1"],
          "visual": "第1张图的完整画面描述，必须包含同一个主角的动作、场景和文字融入方式",
          "narrative": "这张图单独讲完一个很短的小故事，有起因、动作和轻微落点"
        }},
        {{
          "scene_role": "路上小故事",
          "image_text": ["写在图上的短句2"],
          "visual": "第2张图的完整画面描述，必须与上一张不同，并继续保持同一个主角形象",
          "narrative": "这张图单独讲完另一个很短的小故事，不复用上一张构图"
        }},
        {{
          "scene_role": "停顿小故事",
          "image_text": ["写在图上的短句3"],
          "visual": "第3张图的完整画面描述，新的生活场景、新动作、新情绪落点",
          "narrative": "这张图是本章分类下的第三个独立小故事"
        }},
        {{
          "scene_role": "误会小故事",
          "image_text": ["写在图上的短句4"],
          "visual": "第4张图的完整画面描述，保持同一主角但更换场景和动作",
          "narrative": "这张图是本章分类下的第四个独立小故事"
        }},
        {{
          "scene_role": "松口气小故事",
          "image_text": ["写在图上的短句5"],
          "visual": "第5张图的完整画面描述，避免重复前面任何一张构图",
          "narrative": "这张图是本章分类下的第五个独立小故事"
        }},
        {{
          "scene_role": "收住光小故事",
          "image_text": ["写在图上的短句6"],
          "visual": "第6张图的完整画面描述，作为本章分类下的另一个完整小故事",
          "narrative": "这张图是本章分类下的第六个独立小故事"
        }},
        {{
          "scene_role": "窗边小故事",
          "image_text": ["写在图上的短句7"],
          "visual": "第7张图的完整画面描述，继续属于本章分类，但换成新的生活场景、新动作和新情绪落点",
          "narrative": "这张图是本章分类下额外展开的独立小故事，不能重复前6张"
        }}
      ]
    }}
  ]
}}"""


def _json_object_from_text(text: str) -> dict[str, object]:
    stripped = (text or "").strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        stripped = stripped[start : end + 1]
    data = json.loads(stripped)
    if not isinstance(data, dict):
        raise ValueError("comic writer did not return a JSON object")
    return data


def fallback_healing_comic_panels(
    title: str,
    *,
    panel_count: int = HEALING_COMIC_DEFAULT_PANEL_COUNT,
) -> tuple[str, str, list[dict[str, object]]]:
    final_title = parse_healing_comic_title(title) or "今天也不用很厉害"
    panel_count = parse_healing_comic_panel_count(panel_count)
    theme = final_title
    base_moments = [
        "醒来", "出门", "等车", "买早餐", "坐电梯", "回消息", "发呆", "下雨",
        "加班", "迷路", "便利店", "晾衣服", "找钥匙", "看云", "喝水", "午休",
        "道歉", "拥抱空气", "收拾桌面", "路过花店", "夜跑", "听歌", "写便签", "停电",
        "等月亮", "修小灯", "看旧照片", "不赶路", "说晚安", "做梦", "醒来一点", "明天",
        "开窗", "换床单", "等红灯", "买花", "煮面", "晒太阳", "躲雨", "整理包",
        "关电脑", "走小路", "吹风", "洗杯子", "丢旧票", "等朋友", "看路灯", "擦桌子",
        "听雨停", "慢慢走", "收伞", "看晚霞", "写名字", "醒得早", "不解释", "留一盏灯",
        "去散步", "看雪", "等电梯", "喝热水", "抱抱自己", "把门关轻", "早点睡", "下一页",
    ]
    moments = [
        base_moments[index] if index < len(base_moments) else f"第{index + 1}个小瞬间"
        for index in range(panel_count)
    ]
    panels: list[dict[str, object]] = []
    for index, moment in enumerate(moments, start=1):
        animal, subject = _animal_for(index)
        image_text = [f"{moment}慢一点", "也没关系"]
        fallback_image_count = _clamp_healing_image_count(
            HEALING_COMIC_MIN_IMAGES_PER_CHAPTER + ((index - 1) % 5)
        )
        panels.append(
            {
                "title": _clean_healing_comic_title(f"{moment}的时候", index, fallback_lines=image_text),
                "image_count": fallback_image_count,
                "image_text": image_text,
                "phrases": image_text,
                "caption": "，".join(image_text),
                "dialogue": image_text,
                "visual": f"{subject}，在{moment}的日常场景里做一个很小的动作；画面中以手写短句写入“{' / '.join(image_text)}”；主题气质：{theme}；留白、轻幽默、温柔反差。",
                "images": [
                    {
                        "scene_role": "开场停顿",
                        "image_text": [image_text[0]],
                        "visual": f"{subject}出现在{moment}的开场小场景里，动作很轻，画面留白充足，手写短句“{image_text[0]}”自然放在旁白位置。",
                        "narrative": f"用一个很小的日常动作打开“{moment}”这一章。",
                    },
                    {
                        "scene_role": "温柔收束",
                        "image_text": [image_text[-1]],
                        "visual": f"{subject}在同一日常场景里换一个动作或视角，情绪落到更松弛的位置，手写短句“{image_text[-1]}”自然融入画面。",
                        "narrative": f"让“{moment}”从紧绷落回一点点安稳。",
                    },
                ],
            }
        )
    return final_title, "一本原创日常情绪漫画，关于日常里的小小松弛。", panels


def _short_text_lines(value: object, *, fallback: str) -> list[str]:
    if isinstance(value, list):
        raw_items = [str(item or "") for item in value]
    else:
        raw_items = re.split(r"[\n/|]+", str(value or ""))
    lines: list[str] = []
    for raw in raw_items:
        line = re.sub(r"\s+", "", raw).strip("，,。；;：:")
        if line:
            lines.append(line[:16])
        if len(lines) >= 3:
            break
    return lines or [fallback[:16]]


def parse_healing_comic_writer_output(
    text: str,
    title: str,
    *,
    panel_count: int = HEALING_COMIC_DEFAULT_PANEL_COUNT,
) -> tuple[str, str, list[dict[str, object]], dict[str, str]]:
    requested_title = parse_healing_comic_title(title) or "治愈漫画"
    panel_count = parse_healing_comic_panel_count(panel_count)
    try:
        data = _json_object_from_text(text)
        raw_panels = data.get("panels")
        if not isinstance(raw_panels, list):
            raise ValueError("panels missing")
        panels: list[dict[str, object]] = []
        for index, item in enumerate(raw_panels[:panel_count], start=1):
            if not isinstance(item, dict):
                continue
            raw_title = str(item.get("title") or f"第{index}章")
            image_text = _short_text_lines(item.get("image_text") or item.get("caption") or item.get("dialogue"), fallback=raw_title)
            phrases = _short_text_lines(item.get("phrases") or image_text, fallback=image_text[0])
            title = _clean_healing_comic_title(item.get("title"), index, fallback_lines=phrases)
            caption = " / ".join(phrases)[:80]
            visual = str(item.get("visual") or caption or title).strip()[:260]
            normalized_panel = {
                "title": title,
                "image_text": image_text,
                "phrases": phrases,
                "caption": caption,
                "dialogue": phrases[:3],
                "visual": visual,
            }
            normalized_panel["images"] = _normalize_healing_chapter_images({**item, **normalized_panel}, index)
            panels.append(
                normalized_panel
            )
        min_required = max(1, min(panel_count, 24, (panel_count * 3 + 3) // 4))
        if len(panels) >= min_required:
            fallback_panels = fallback_healing_comic_panels(requested_title, panel_count=panel_count)[2]
            while len(panels) < panel_count:
                panels.append(fallback_panels[len(panels)])
            series = {
                "series_style": str(data.get("series_style") or "").strip()[:400],
                "protagonist_profile": str(data.get("protagonist_profile") or "").strip(),
                "cover_visual": str(data.get("cover_visual") or "").strip()[:260],
            }
            return (
                requested_title,
                str(data.get("intro") or "一本原创日常情绪短漫画。").strip()[:160],
                panels[:panel_count],
                series,
            )
    except Exception:
        pass
    fallback_title, intro, panels = fallback_healing_comic_panels(requested_title, panel_count=panel_count)
    return fallback_title, intro, panels, {"series_style": "", "cover_visual": ""}


def build_healing_comic_panel_prompt(
    *,
    panel: dict[str, object],
    image: dict[str, object] | None = None,
    index: int,
    sub_index: int = 1,
    total: int = HEALING_COMIC_PANEL_COUNT,
    series_context: str = "",
    character_bible: str = "",
) -> str:
    image_item = image or {}
    chapter_images = _normalize_healing_chapter_images(panel, index)
    image_text_lines = _short_text_lines(
        image_item.get("image_text") or panel.get("image_text") or panel.get("phrases") or panel.get("dialogue"),
        fallback=str(panel.get("title") or "慢一点"),
    )
    image_text = "\n".join(image_text_lines)
    phrase_text = " / ".join(_short_text_lines(panel.get("phrases") or image_text_lines, fallback=image_text_lines[0]))
    visual = str(image_item.get("visual") or panel.get("visual") or panel.get("caption") or panel.get("title") or "").strip()
    narrative = str(image_item.get("narrative") or visual).strip()
    scene_role = str(image_item.get("scene_role") or f"第{sub_index}张").strip()
    title = str(panel.get("title") or f"第{index}张").strip()
    chapter_sequence = "\n".join(
        f"- 图{sub.get('sub_index') or pos}：{sub.get('scene_role') or ''}；{sub.get('visual') or ''}"
        for pos, sub in enumerate(chapter_images, start=1)
    )
    return f"""生成一张原创温暖治愈系短漫画单页图，属于同一组 {total} 章漫画中的第 {index} 章分类下第 {sub_index} 个小故事。

【系列方向】
{series_context}

【主角形象圣经】
{character_bible}

【本章分类下的小故事汇总】
{chapter_sequence}

【本张内容】
- 标题：{title}
- 本张小故事类型：{scene_role}
- 本张叙事：{narrative}
- 画面：{visual}
- 必须写在图上的短句：
{image_text}
- 正文短语参考：{phrase_text}

【治愈系列风格基座】
{HEALING_COMIC_STYLE_GUARD}

【视觉要求】
- 画面必须把上面的短句自然写进图里，像手写小对白、旁白或气泡文字；不要漏字，不要换成英文。
- 只绘制“本张内容”；本章只是分类，本张图必须单独讲完一个小故事，有起因、动作和轻微落点，不要依赖上一张或下一张才能读懂。
- 同章至少 {HEALING_COMIC_MIN_IMAGES_PER_CHAPTER} 张图，6 张只是下限，不是固定数量；本章实际共有 {len(chapter_images)} 张小故事图，每张都必须是不同小故事；不要把它们画成同一故事的机械分镜，不要每张都画成同一构图。
- 主角必须严格遵守“主角形象圣经”；只能变化动作、角度、场景和情绪，不得改变主角的物种或身份、体型轮廓、主色、服装核心元素、标志性配件和面部特征。
- 画面像微信读书同系列治愈漫画摊开后的单页：白底或浅色大留白，细线条，轻水彩/蜡笔颗粒，低压力阅读节奏。
- 每张都是一个完整小故事场景，可以有一格或两三格小分镜，但不要复杂剧情。
- 可以是原创人物、原创动物、植物、物件、天气、房间、街道、光影等意象；若使用固定角色，全系列保持一致。
- 全系列保持统一角色气质、线条、配色、颗粒感、留白方式和日常治愈氛围，与封面像同一本书。
- 不要像儿童绘本、商业鸡汤海报、PPT插画或廉价AI模板。
- 不要出现章节编号、页码、水印、logo、出版社名或多余说明文字。

【版权与安全】
{COMIC_COPYRIGHT_RULE}
{HEALING_COMIC_REFERENCE}
禁止复刻、近似模仿或暗示 Buddy Gator、盖朵短尾鳄、鳄鱼 IP、微信读书真实漫画、现成角色、现成台词或现成分镜。"""


def build_healing_comic_cover_prompt(
    *,
    title: str,
    intro: str,
    panels: list[dict[str, object]],
    series_context: str = "",
    series_meta: dict[str, str] | None = None,
    character_bible: str = "",
) -> str:
    final_title = parse_healing_comic_title(title) or "治愈漫画"
    meta = series_meta or {}
    sample_titles = "、".join(str(panel.get("title") or "") for panel in panels[:12])
    sample_text = " / ".join(
        line
        for panel in panels[:8]
        for line in _short_text_lines(panel.get("image_text") or panel.get("phrases"), fallback=str(panel.get("title") or ""))
    )
    return f"""根据书名生成一张原创温暖治愈漫画封面，作为后续 {len(panels)} 张同系列漫画页的视觉母版。

【书名】
{final_title}

【简介】
{intro}

【系列参考】
{series_context}

【主角形象圣经】
{character_bible}

【OpenAI规划的统一风格】
{meta.get("series_style") or "白底大留白、细线条、轻水彩/蜡笔颗粒、原创角色、温柔轻幽默。"}

【封面画面方向】
{meta.get("cover_visual") or "原创角色站在大留白画面中，周围有少量日常物件或天气意象，安静、有陪伴感。"}

【章节气质样本】
标题：{sample_titles}
短句：{sample_text}

【治愈系列风格基座】
{HEALING_COMIC_STYLE_GUARD}

【封面要求】
- 必须出现书名“{final_title}”，字要清楚、像出版物封面标题。
- 封面要像微信读书同系列治愈漫画书封：白底或高识别度浅色底，大留白，简洁平面构图，轻微纸张颗粒。
- 画面建立全书统一视觉母版：原创角色、主色、线条、颗粒感、留白和情绪氛围后续都要沿用。
- 如果出现主角，必须严格遵守“主角形象圣经”，让封面成为后续所有章节图片的角色母版。
- 可以有原创动物或人物，但绝对不要绿色鳄鱼、短尾鳄、鳄鱼轮廓或 Buddy Gator 近似角色。
- 避免儿童绘本感、商业鸡汤海报感、PPT感、AI模板感、复杂背景。

【版权与安全】
{COMIC_COPYRIGHT_RULE}
禁止复刻、近似模仿或暗示 Buddy Gator、盖朵短尾鳄、鳄鱼 IP、微信读书真实漫画、现成角色、现成台词或现成分镜。"""


def plan_healing_comic_images(
    panels: list[dict[str, object]],
    output_dir: Path,
    *,
    series_context: str = "",
    title: str | None = None,
    intro: str = "",
    series_meta: dict[str, str] | None = None,
    character_bible: str = "",
) -> list[ComicSectionImage]:
    image_dir = output_dir / "comic_images"
    image_dir.mkdir(parents=True, exist_ok=True)
    results: list[ComicSectionImage] = []
    total = len(panels)
    cover_prompt = build_healing_comic_cover_prompt(
        title=title or "治愈漫画",
        intro=intro,
        panels=panels,
        series_context=series_context,
        series_meta=series_meta,
        character_bible=character_bible,
    )
    cover_path = image_dir / COMIC_COVER_IMAGE_FILENAME
    (image_dir / "comic_cover_prompt.txt").write_text(cover_prompt, encoding="utf-8")
    results.append(
        ComicSectionImage(
            index=-1,
            phrase=parse_healing_comic_title(title) or "治愈漫画",
            title="封面",
            animal="原创角色",
            subject=str((series_meta or {}).get("cover_visual") or ""),
            prompt=cover_prompt,
            filename=COMIC_COVER_IMAGE_FILENAME,
            path=cover_path,
        )
    )
    for index, panel in enumerate(panels, start=1):
        for sub_index, image_item in enumerate(_normalize_healing_chapter_images(panel, index), start=1):
            prompt = build_healing_comic_panel_prompt(
                panel=panel,
                image=image_item,
                index=index,
                sub_index=sub_index,
                total=total,
                series_context=series_context,
                character_bible=character_bible,
            )
            filename = f"comic_section_{index:02d}_{sub_index:02d}.png"
            path = image_dir / filename
            (image_dir / f"comic_section_{index:02d}_{sub_index:02d}_prompt.txt").write_text(prompt, encoding="utf-8")
            results.append(
                ComicSectionImage(
                    index=index,
                    phrase=str(panel.get("caption") or panel.get("title") or ""),
                    title=str(panel.get("title") or f"第{index}章"),
                    animal="原创角色",
                    subject=str(image_item.get("visual") or panel.get("visual") or ""),
                    prompt=prompt,
                    filename=filename,
                    path=path,
                    sub_index=sub_index,
                )
            )
    _write_manifest(image_dir, results)
    return results


def _read_image_error_marker(path: Path, fallback: str) -> str:
    try:
        marker = json.loads(path.read_text(encoding="utf-8"))
        return str(marker.get("error") or marker.get("fallback_error") or fallback)
    except Exception:
        return fallback


def _write_image_error_marker(
    path: Path,
    image: ComicSectionImage,
    *,
    error: str,
    prompt_path: str | None = None,
    fallback_error: str | None = None,
) -> None:
    payload: dict[str, object] = {
        "index": image.index,
        "sub_index": image.sub_index,
        "phrase": image.phrase,
        "filename": image.filename,
        "error": error,
    }
    if prompt_path:
        payload["prompt_path"] = prompt_path
    if fallback_error:
        payload["fallback_error"] = fallback_error
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _safe_comic_image_prompt(image: ComicSectionImage) -> str:
    if image.index < 0:
        purpose = "封面"
        content = f"书名：{image.phrase or image.title}"
    elif image.index == 0:
        purpose = "文前开场图"
        content = f"短句：{image.phrase}"
    else:
        purpose = f"第 {image.index} 章分类下第 {image.sub_index} 个独立小故事"
        content = f"章节：{image.title}\n短句：{image.phrase}"
    return f"""生成一张原创温暖治愈漫画{purpose}，竖版 2450x3500。

【内容】
{content}

【安全保守版本】
- 只画普通日常生活里的安静小场景：窗边、桌面、杯子、纸张、植物、路灯、云、门口、椅子、书页或一个完全原创的小角色。
- 如果出现主角，保持柔和、原创、非写实、非品牌、非现成 IP；不要出现真实人物、名人、商标、平台标识或可识别角色。
- 不出现暴力、恐惧、医疗、伤口、血迹、裸露、亲密暗示、政治符号、宗教符号、酒精、烟草、武器、危险动作或任何刺激性画面。
- 画面必须是白底或浅色大留白、轻水彩或蜡笔颗粒、细线条、低压力阅读、出版感、温暖克制。
- 图上可以自然写入短句，但不要写编号、页码、水印、logo、任务说明、提示词说明或多余文字。

【版权】
必须是原创角色和原创画面，不复刻、暗示或模仿任何已有漫画、绘本、动画、游戏、品牌角色、真实出版物页面或现成分镜。
"""


def _generate_image_with_comic_protection(
    image: ComicSectionImage,
    *,
    image_dir: Path,
    stem: str,
    response_path: Path,
    uncertain_path: Path,
    permanent_path: Path,
    force: bool,
    allow_uncertain: bool,
    retry_uncertain: bool = False,
) -> ComicSectionImage:
    if image.path.exists() and image.path.stat().st_size > 0 and not force:
        if uncertain_path.exists():
            uncertain_path.unlink()
        if permanent_path.exists():
            permanent_path.unlink()
        return replace(image, status="completed", error=None)

    if permanent_path.exists() and not image.path.exists() and not force:
        message = _read_image_error_marker(permanent_path, "previous permanent image request failure")
        if not allow_uncertain:
            raise ImageApiPermanentError(message)
        return replace(image, status="permanent_error", error=message)

    if uncertain_path.exists() and not image.path.exists() and not force and not retry_uncertain:
        message = _read_image_error_marker(uncertain_path, "previous uncertain image request")
        if not allow_uncertain:
            raise ImageApiUncertainError(message)
        return replace(image, status="uncertain", error=message)

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
        if permanent_path.exists():
            permanent_path.unlink()
        return replace(image, status="completed", error=None)
    except ImageApiUncertainError as exc:
        message = str(exc)
        _write_image_error_marker(
            uncertain_path,
            image,
            prompt_path=f"{stem}_prompt.txt",
            error=message,
        )
        if not allow_uncertain:
            raise
        return replace(image, status="uncertain", error=message)
    except ImageApiPermanentError as exc:
        original_message = str(exc)
        safe_prompt = _safe_comic_image_prompt(image)
        safe_prompt_path = image_dir / f"{stem}_safe_prompt.txt"
        safe_response_path = image_dir / f"{stem}_safe_response.json"
        safe_prompt_path.write_text(safe_prompt, encoding="utf-8")
        try:
            generate_image_file_from_prompt(
                safe_prompt,
                image.path,
                response_cache_path=safe_response_path,
                force=force,
                gate_wait_timeout_seconds=_comic_image_gate_wait_timeout_seconds(),
            )
            if uncertain_path.exists():
                uncertain_path.unlink()
            if permanent_path.exists():
                permanent_path.unlink()
            return replace(image, status="completed", error=None)
        except ImageApiUncertainError as fallback_exc:
            message = str(fallback_exc)
            _write_image_error_marker(
                uncertain_path,
                image,
                prompt_path=f"{stem}_safe_prompt.txt",
                error=message,
                fallback_error=original_message,
            )
            if not allow_uncertain:
                raise
            return replace(image, status="uncertain", error=message)
        except ImageApiPermanentError as fallback_exc:
            fallback_message = str(fallback_exc)
            _write_image_error_marker(
                permanent_path,
                image,
                prompt_path=f"{stem}_safe_prompt.txt",
                error=original_message,
                fallback_error=fallback_message,
            )
            if not allow_uncertain:
                raise ImageApiPermanentError(f"{original_message}; safe fallback also failed: {fallback_message}") from fallback_exc
            return replace(image, status="permanent_error", error=fallback_message)


def generate_healing_comic_images(
    panels: list[dict[str, object]],
    output_dir: Path,
    *,
    series_context: str = "",
    title: str | None = None,
    intro: str = "",
    series_meta: dict[str, str] | None = None,
    character_bible: str = "",
    force: bool = False,
    allow_uncertain: bool = False,
    retry_uncertain: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
    should_pause: Callable[[], bool] | None = None,
) -> list[ComicSectionImage]:
    image_dir = output_dir / "comic_images"
    results = plan_healing_comic_images(
        panels,
        output_dir,
        series_context=series_context,
        title=title,
        intro=intro,
        series_meta=series_meta,
        character_bible=character_bible,
    )
    total = len(results)

    def _raise_if_paused() -> None:
        if should_pause is not None and should_pause():
            _write_manifest(image_dir, results)
            raise ComicGenerationPaused("漫画生成已暂停")

    def _stem_for(image: ComicSectionImage) -> str:
        return image.path.stem

    def _generate_one(image: ComicSectionImage) -> ComicSectionImage:
        _raise_if_paused()
        stem = _stem_for(image)
        response_path = image_dir / f"{stem}_response.json"
        uncertain_path = image_dir / f"{stem}_uncertain.json"
        permanent_path = image_dir / f"{stem}_permanent.json"
        return _generate_image_with_comic_protection(
            image,
            image_dir=image_dir,
            stem=stem,
            response_path=response_path,
            uncertain_path=uncertain_path,
            permanent_path=permanent_path,
            force=force,
            allow_uncertain=allow_uncertain,
            retry_uncertain=retry_uncertain,
        )

    if results:
        _raise_if_paused()
        try:
            results[0] = _generate_one(results[0])
        except Exception:
            _write_manifest(image_dir, results)
            raise
        if on_progress is not None:
            on_progress(1, total)
        _raise_if_paused()

    max_workers = max(1, min(int(getattr(settings, "IMAGE_MAX_CONCURRENT_CALLS", 2) or 2), 2))
    pending = list(enumerate(results[1:], start=1))
    next_pending_index = 0
    completed = 1 if results else 0
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="healing-comic-image") as executor:
        futures = {}

        def submit_next() -> None:
            nonlocal next_pending_index
            _raise_if_paused()
            if next_pending_index >= len(pending):
                return
            offset, image = pending[next_pending_index]
            next_pending_index += 1
            futures[executor.submit(_generate_one, image)] = offset

        for _ in range(min(max_workers, len(pending))):
            submit_next()

        while futures:
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                offset = futures.pop(future)
                break
            try:
                results[offset] = future.result()
            except Exception:
                for pending_future in futures:
                    pending_future.cancel()
                _write_manifest(image_dir, results)
                raise
            completed += 1
            if on_progress is not None:
                on_progress(completed, total)
            _write_manifest(image_dir, results)
            _raise_if_paused()
            submit_next()
    _write_manifest(image_dir, results)
    return results


def assemble_healing_comic_markdown(title: str, intro: str, panels: list[dict[str, object]]) -> str:
    final_title = (title or "治愈漫画").strip() or "治愈漫画"
    final_intro = (intro or "一本原创日常情绪短漫画。").strip()[:180]
    lines = ["@最终书名", final_title, "@最终简介", final_intro, "@目录"]
    for index, panel in enumerate(panels, start=1):
        lines.append(f"{index}. {str(panel.get('title') or f'第{index}张')}")
    lines.append("@正文")
    for index, panel in enumerate(panels, start=1):
        lines.append(f"@{index}. {str(panel.get('title') or f'第{index}章')}")
        for sub_index, _ in enumerate(_normalize_healing_chapter_images(panel, index), start=1):
            lines.append(f"{{{{COMIC_IMAGE_{index}_{sub_index}}}}}")
        phrases = panel.get("phrases") or panel.get("image_text") or panel.get("dialogue")
        for line in _short_text_lines(phrases, fallback=str(panel.get("title") or f"第{index}章")):
            lines.append(line)
    return "\n".join(lines).strip()


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
        permanent_path = image_dir / f"{stem}_permanent.json"
        try:
            image = _generate_image_with_comic_protection(
                image,
                image_dir=image_dir,
                stem=stem,
                response_path=response_path,
                uncertain_path=uncertain_path,
                permanent_path=permanent_path,
                force=force,
                allow_uncertain=allow_uncertain,
            )
        except ImageApiUncertainError:
            results[offset] = replace(image, status="uncertain", error="image API request result is uncertain")
            _write_manifest(image_dir, results)
            raise
        except ImageApiPermanentError:
            results[offset] = replace(image, status="permanent_error", error="image API request failed permanently")
            _write_manifest(image_dir, results)
            raise
        if image.status == "uncertain" and allow_uncertain:
            results[offset] = image
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
    title = title.strip()
    return title or "治愈漫画系列"


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
    image_by_index: dict[int, ComicSectionImage] = {}
    image_by_position: dict[tuple[int, int], ComicSectionImage] = {}
    for image in images:
        if not image.path.exists() or image.path.stat().st_size <= 0:
            continue
        image_by_position[(image.index, image.sub_index)] = image
        image_by_index.setdefault(image.index, image)
    preface_image = image_by_index.get(0)

    if preface_image is not None:
        result = _PREFACE_PLACEHOLDER_RE.sub(preface_image.markdown, result)

    def _replace_multi_placeholder(match: re.Match[str]) -> str:
        index = int(match.group(1))
        sub_index = int(match.group(2))
        image = image_by_position.get((index, sub_index))
        return image.markdown if image else match.group(0)

    result = _MULTI_IMAGE_PLACEHOLDER_RE.sub(_replace_multi_placeholder, result)

    def _replace_placeholder(match: re.Match[str]) -> str:
        index = int(match.group(1))
        image = image_by_index.get(index)
        return image.markdown if image else match.group(0)

    result = _PLACEHOLDER_RE.sub(_replace_placeholder, result)
    result = _PREFACE_PLACEHOLDER_RE.sub("", result)
    result = _MULTI_IMAGE_PLACEHOLDER_RE.sub("", result)
    result = _PLACEHOLDER_RE.sub("", result)

    for image in images:
        if not image.path.exists() or image.path.stat().st_size <= 0:
            continue
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
            return line.lstrip("@").strip()
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
        insert_at = min(insert_at + max(0, image.sub_index - 1), len(lines))
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
    "ComicGenerationPaused",
    "ComicSectionImage",
    "HEALING_COMIC_PANEL_COUNT",
    "HEALING_COMIC_DEFAULT_PANEL_COUNT",
    "HEALING_COMIC_MAX_IMAGES_PER_CHAPTER",
    "HEALING_COMIC_MAX_PANEL_COUNT",
    "HEALING_COMIC_MIN_IMAGES_PER_CHAPTER",
    "HEALING_COMIC_MIN_PANEL_COUNT",
    "assemble_healing_comic_markdown",
    "assemble_comic_series_markdown",
    "build_healing_comic_cover_prompt",
    "build_healing_comic_character_bible",
    "build_healing_comic_panel_prompt",
    "build_healing_comic_writer_prompt",
    "build_comic_cover_image_prompt",
    "build_comic_image_prompt",
    "build_comic_preface_image_prompt",
    "build_comic_writer_input",
    "comic_book_title_from_phrases",
    "comic_raw_text_from_phrases",
    "comic_theme_from_phrases",
    "extract_comic_title",
    "fallback_healing_comic_panels",
    "generate_comic_section_images",
    "generate_healing_comic_images",
    "healing_comic_reference_context",
    "image_filename_from_markdown",
    "inject_comic_images",
    "parse_comic_input",
    "parse_comic_input_parts",
    "parse_comic_phrases",
    "parse_comic_preface_phrase",
    "parse_comic_theme_requirement",
    "parse_comic_title",
    "parse_healing_comic_requirement",
    "parse_healing_comic_character_prompt",
    "parse_healing_comic_panel_count",
    "parse_healing_comic_source_content",
    "parse_healing_comic_title",
    "parse_healing_comic_writer_output",
    "plan_healing_comic_images",
    "plan_comic_section_images",
    "validate_comic_phrases",
]
