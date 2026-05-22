"""Three-stage book cover generation."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import queue
import random
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

import httpx

from app.core.config import settings
from app.services.cover_categories import (
    get_cover_category_meta,
    list_cover_categories,
    normalize_cover_category_id,
)
from app.services.dangdang_crawler import collect_dangdang_reference_covers
from app.utils.distributed_limiter import DistributedLimitError, distributed_semaphore
from app.utils.official_api import official_openai_base_url, official_proxy

logger = logging.getLogger(__name__)
_IMAGE_REQUEST_OBSERVER: ContextVar[Callable[[dict[str, Any]], None] | None] = ContextVar(
    "cover_image_request_observer",
    default=None,
)

ALLOWED_REFERENCE_SITE = "当当网"
ALLOWED_REFERENCE_BRANDS: tuple[str, ...] = ("酷威文化", "果麦文化", "时代华语")
_PLACEHOLDER_KEYS = {"", "sk-xxx", "sk-xxxx", "sk-xxxx..."}
_IMAGE_PROMPT_LIMIT = 32000
_AUTHOR_PLACEHOLDER = "作者名留白"
_SATURATED_COLOR_DIRECTION = (
    "以高饱和、明亮、强识别的出版色彩为主，颜色组合要清楚、有情绪、有手机端识别度。",
    "深色只能作为文字、线条、阴影或小面积压重，不允许大面积黑色、深蓝、暗红、深灰铺底。",
    "整体接近微信读书/得到系知识爆款封面：手机端缩略图也醒目，标题强、色块干净、排版有商业出版感。",
)
_NO_REAL_PORTRAIT_RULE = (
    "禁止真实人物肖像、名人脸、写实人像、可识别照片脸；人物只能用抽象剪影、背影、符号化小人或非写实插画表达。"
)
_WECHAT_DEDAO_STYLE_RULE = (
    "参考畅销书的配色与版式趋势，但只学习抽象规律：高饱和色块、明确标题层级、少而准的视觉隐喻、强留白或强对比；不得复刻任何真实封面。"
)
_VISUAL_IMPACT_STYLE_RULE = (
    "视觉冲击类封面：回到早期封面 workflow 的强视觉方案，先用一个强主视觉或强隐喻抓住现实痛点，"
    "再用巨大清晰标题压住画面。颜色必须高饱和、鲜艳、强对比，优先亮黄、玫红、电光蓝、亮青绿、珊瑚橙与干净白色留白。"
    "构图要像手机端第一眼能点开的出版封面：大色块、少元素、强图形，不做灰暗社科封面、PPT信息图、电商主图或电影海报。"
)
_CRAYON_ILLUSTRATION_STYLE_RULE = (
    "治愈蜡笔：日韩治愈系绘本封面，手绘感，像私人日记。"
    "高饱和纯色背景，背景颜色不要渐变，简洁构图，大留白。"
    "动物或人物插画，线条松弛笨拙，保留真实手绘颗粒感。"
    "超大手写标题，像随手写在画面上。"
    "整体温柔、孤独、可爱、有陪伴感。"
    "避免商业心理书、PPT感、UI插画风、过度精致和AI感。"
)
_CRAYON_QUALITY_CONTROL_RULE = (
    "萌系治愈硬约束：私人日记是情绪气质，不是书桌、日记本、杯子、台灯、便签的固定道具组合。"
    "背景必须是高饱和纯色，不要渐变背景、光效渐变、网格渐变或多色晕染；深暗色面积低于10%。"
    "金句每句不超过10个中文字符，只能像手写旁注放在角落或留白处；"
    "禁止清单、段落、整页日记文字和多个便签文案。"
)
_LITERATURE_PUBLISHING_STYLE_RULE = (
    "文艺文学感书封，像豆瓣高分文学与微信读书精选封面。"
    "现代出版设计风格，简洁、克制、有留白，不要古风小说海报感。"
    "弱化剧情感与人物主体，强化氛围、平面构成与书籍气质。"
    "可使用自然、城市、旅行、人物、植物、建筑、光影等意象元素，少而精。"
    "平面插画或轻水彩质感，轻微颗粒与纸张肌理。"
    "配色不要灰蒙蒙与做旧感，使用更有识别度和情绪感的颜色，颜色克制但能第一眼吸引人。"
    "标题排版高级、稀疏、克制，像真正出版社的文学书设计，而不是AI书法字。"
    "整体安静、诗意、有孤独感与呼吸感，同时保留现代感与视觉吸引力。"
    "避免古风模板、避免影视海报感、避免晋江小说封面、避免厚重书法字、避免PPT感、避免廉价AI插画感。"
)


def _load_reference_markdown(filename: str) -> str:
    path = Path(__file__).with_name(filename)
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


_LITERATURE_COVER_REFERENCE = _load_reference_markdown("literature_cover_reference.md")


class ImageApiError(RuntimeError):
    """Base exception for image API failures."""


class ImageApiTransientError(ImageApiError):
    """Image API failure that should be retried by the cover job queue."""


class ImageApiPermanentError(ImageApiError):
    """Image API failure that should be surfaced without automatic retry."""


class ImageApiUncertainError(ImageApiPermanentError):
    """Image API request may have been accepted, so auto retry would risk double billing."""


class CoverGenerationCancelled(RuntimeError):
    """Raised when a cover item was deleted, paused, or superseded mid-generation."""


@contextmanager
def observe_image_requests(callback: Callable[[dict[str, Any]], None]) -> Iterator[None]:
    """Expose logical image request events to queue workers without changing prompts."""
    token = _IMAGE_REQUEST_OBSERVER.set(callback)
    try:
        yield
    finally:
        _IMAGE_REQUEST_OBSERVER.reset(token)


def _emit_image_request_event(event: dict[str, Any]) -> None:
    observer = _IMAGE_REQUEST_OBSERVER.get()
    if observer is None:
        return
    try:
        observer(event)
    except Exception:
        logger.warning("cover image request observer failed", exc_info=True)


@dataclass(frozen=True)
class CoverInput:
    title: str
    author: str
    intro: str
    category: str
    category_label: str


@dataclass(frozen=True)
class CoverStyleBrief:
    reference_site: str
    reference_brands: tuple[str, ...]
    matched_category_style: str
    brand_style_direction: str
    cover_type: str
    color_palette: tuple[str, ...]
    composition: str
    typography_direction: str
    white_space_ratio: str
    visual_metaphors: tuple[str, ...]
    reference_samples: tuple[dict[str, str], ...]
    market_style_analysis: str
    title_hierarchy: str
    avoid_rules: tuple[str, ...]
    generation_prompt_addendum: str
    visual_variation_directive: str = ""


@dataclass(frozen=True)
class ReferenceCover:
    brand: str
    title: str
    product_url: str
    image_url: str
    source: str
    product_id: str = ""
    source_page: str = ""
    local_image_path: str = ""


@dataclass(frozen=True)
class CoverMarketMatch:
    category_label: str
    search_keywords: str
    analysis: str
    visual_plan: str = ""


_CATEGORY_STYLE_PROFILES: dict[str, dict[str, Any]] = {
    "social_science": {
        "brand": "时代华语",
        "style": "视觉冲击类现实议题书，强主视觉、高饱和、强标题、第一眼抓人",
        "cover_type": "视觉冲击 / 高饱和现实议题出版封面",
        "colors": ("亮黄", "玫红", "电光蓝", "亮青绿", "珊瑚橙", "干净白色留白"),
        "composition": "一个强主视觉或强隐喻居中，大色块和强对比铺陈，标题巨大清晰，少元素但冲击力强",
        "typography": "超大现代黑体或强识别标题字，字重足、对比强，手机缩略图也必须可读",
        "white_space": "25%-45%",
        "metaphors": ("巨大标题", "强色块", "城市压力", "裂缝", "阶梯", "被挤压的空间", "人物剪影", "现实痛点符号"),
        "addendum": _VISUAL_IMPACT_STYLE_RULE,
    },
    "life_philosophy": {
        "brand": "果麦文化",
        "style": "温和、清醒、留白多，有人生余味但不鸡汤",
        "cover_type": "人生哲学 / 成长认知出版封面",
        "colors": ("米白", "浅绿", "浅蓝", "淡金", "柔灰"),
        "composition": "大面积留白，一处安静隐喻物件或小人物，标题轻而稳",
        "typography": "清雅宋体、手写感标题或克制黑体，层级少",
        "white_space": "45%-65%",
        "metaphors": ("窗", "路", "树", "月光", "手写线条", "孤独人物", "小物件"),
        "addendum": "借鉴果麦文化的克制文学感，封面要安静、耐看、有复读价值。",
    },
    "literature": {
        "brand": "果麦文化",
        "style": "现代文学出版封面，安静、诗意、克制，有留白和呼吸感",
        "cover_type": "正统出版 / 文艺文学书封",
        "colors": ("高识别克制色", "清透蓝绿", "暖橙或玫红点睛", "奶白留白", "轻水彩纸色"),
        "composition": "少而精的自然、城市、旅行、人物、植物、建筑或光影意象，弱剧情、强氛围与平面构成",
        "typography": "高级、稀疏、克制的现代出版标题排版，避免AI书法字和厚重书法字",
        "white_space": "45%-70%",
        "metaphors": ("自然意象", "城市角落", "旅行片段", "植物", "建筑光影", "孤独背影", "留白"),
        "addendum": _LITERATURE_PUBLISHING_STYLE_RULE,
    },
    "popular_science": {
        "brand": "时代华语",
        "style": "清爽、理性、知识密度高，把复杂知识讲清楚",
        "cover_type": "科普 / 商业知识出版封面",
        "colors": ("蓝白", "青绿", "银灰", "橙黄点缀"),
        "composition": "标题醒目，几何结构或信息图式视觉居中，背景干净",
        "typography": "现代无衬线中文字体，信息层级清晰，副标题短",
        "white_space": "30%-50%",
        "metaphors": ("数据线", "芯片", "宇宙", "脑科学轮廓", "图表", "信息卡片"),
        "addendum": "做成时代华语式知识书封面，理性、明亮、有方法感。",
    },
    "wisdom_stories": {
        "brand": "果麦文化",
        "style": "东方审美、温润、有余味，以故事承载哲理",
        "cover_type": "智慧故事 / 东方哲思出版封面",
        "colors": ("宣纸米白", "墨黑", "浅褐", "竹青", "淡金"),
        "composition": "纸张纹理铺底，单一东方意象，标题竖排或松散横排",
        "typography": "书法感标题或雅致宋体，少量印章感点缀",
        "white_space": "45%-70%",
        "metaphors": ("山水", "茶盏", "灯", "竹", "石", "人物剪影"),
        "addendum": "借鉴果麦文化东方经典类封面的雅致留白，不做宗教宣传图。",
    },
    "biography": {
        "brand": "时代华语",
        "style": "人物命运感、可信档案感、时代背景清楚",
        "cover_type": "人物传记出版封面",
        "colors": ("黑白灰", "米黄", "深蓝", "酒红", "旧照片色"),
        "composition": "肖像或剪影为主，叠加旧照片、手稿、时代纹理",
        "typography": "标题稳重，作者和副标题像正式传记装帧",
        "white_space": "25%-45%",
        "metaphors": ("肖像", "剪影", "旧照片", "档案纸", "手稿", "强光影"),
        "addendum": "参考时代华语人物传记的可信商业出版感，突出命运转折。",
    },
    "history_culture": {
        "brand": "果麦文化",
        "style": "案卷感历史叙事，史实可信，时代暗流明显",
        "cover_type": "历史小说 / 历史叙事出版封面",
        "colors": ("米黄", "墨黑", "朱红", "青铜", "深绿"),
        "composition": "古籍纸或案卷底，历史器物居中，人物背影或地图作暗纹",
        "typography": "有碑帖感或宋体骨架，标题沉稳有历史重量",
        "white_space": "30%-55%",
        "metaphors": ("案卷", "古籍纸", "印章", "地图", "宫墙", "器物", "书信", "墨迹"),
        "addendum": "按果麦文化历史叙事书的出版气质处理，不做游戏海报。",
    },
    "emotion_relationship": {
        "brand": "酷威文化",
        "style": "柔和、日常、治愈或清醒，有情绪但不廉价",
        "cover_type": "情感关系 / 大众文学出版封面",
        "colors": ("粉白", "奶油色", "浅蓝", "淡紫", "暖橙"),
        "composition": "日常物件或双人距离隐喻，标题亲近但有边界",
        "typography": "圆润黑体或柔和宋体，标题清晰，副标题很短",
        "white_space": "35%-55%",
        "metaphors": ("信件", "餐桌", "窗边", "花", "双人距离", "暖光"),
        "addendum": "借鉴酷威文化大众情感书的亲近感，但避免网感廉价。",
    },
    "psychology_self_help": {
        "brand": "时代华语",
        "style": "专业、干净、方法感强，解决具体情绪问题",
        "cover_type": "心理自助 / 方法型出版封面",
        "colors": ("白", "浅黄", "浅蓝", "薄荷绿", "橙色点缀"),
        "composition": "大标题加一个清晰心理隐喻，画面明亮可信",
        "typography": "醒目现代黑体，信息区清楚，避免过度装饰",
        "white_space": "35%-55%",
        "metaphors": ("脑袋轮廓", "线团", "阶梯", "开门", "睡眠元素", "几何符号"),
        "addendum": "做成时代华语式心理自助书封面，可信、清楚、有方法感。",
    },
    "illustration_crayon": {
        "brand": "果麦文化",
        "style": "日韩治愈系绘本封面，手绘感，私人日记气质，高饱和纯色背景，温柔孤独可爱",
        "cover_type": "萌系治愈 / 治愈系绘本封面",
        "colors": ("高饱和纯色背景", "明亮通透绘画色", "纸张颗粒色", "温柔亮色"),
        "composition": "简洁构图，大留白，动物或人物插画，线条松弛笨拙，避免渐变背景和桌面日记模板",
        "typography": "超大手写标题，像随手写在画面上，但书名必须清晰可读",
        "white_space": "45%-65%",
        "metaphors": ("真实手绘颗粒感", "高饱和纯色背景", "超大手写标题", "动物或人物插画", "大留白", "陪伴感"),
        "addendum": _CRAYON_ILLUSTRATION_STYLE_RULE,
    },
    "suspense_micro_reasoning": {
        "brand": "酷威文化",
        "style": "类型小说张力强，悬念明确，但不血腥低俗",
        "cover_type": "悬疑 / 微推理小说封面",
        "colors": ("黑", "深蓝", "暗红", "冷白光", "灰绿"),
        "composition": "暗背景，一束局部强光或关键物件，标题强识别",
        "typography": "粗黑体、手写裂纹感或压迫感标题，层级强",
        "white_space": "20%-40%",
        "metaphors": ("门缝", "影子", "红线", "档案", "雨夜", "楼道", "破碎物"),
        "addendum": "借鉴酷威文化类型书张力，像成熟悬疑书封，不像电影海报。",
    },
    "classical_chinese": {
        "brand": "果麦文化",
        "style": "古籍新编、国学普及，准确雅致，易读不艰深",
        "cover_type": "古文白话 / 国学普及出版封面",
        "colors": ("米白", "墨黑", "朱砂", "青灰", "淡褐"),
        "composition": "宣纸纹理、碑拓或竹简意象，标题稳重，可竖排",
        "typography": "宋体、碑帖感或书法感标题，作者信息极简",
        "white_space": "45%-65%",
        "metaphors": ("宣纸", "碑拓", "竹简", "毛笔", "印章", "淡墨山水"),
        "addendum": "按果麦文化经典普及类封面处理，雅致、准确、不过度玄学化。",
    },
    "health_wellness": {
        "brand": "时代华语",
        "style": "轻科普、明亮、可信，可立即行动，不恐吓",
        "cover_type": "健康养生 / 生活科普出版封面",
        "colors": ("白", "绿色", "蓝色", "浅黄", "自然木色"),
        "composition": "干净底色，身体或习惯符号居中，标题清楚",
        "typography": "现代黑体，副标题像健康科普书而不是广告",
        "white_space": "35%-55%",
        "metaphors": ("身体轮廓", "食物", "睡眠", "植物", "运动线条", "习惯符号"),
        "addendum": "做成时代华语生活健康书封面，可靠、轻盈、不制造焦虑。",
    },
    "classic_interpretation": {
        "brand": "果麦文化",
        "style": "高级文化解读，降低门槛但保持经典气质",
        "cover_type": "经典解读 / 文艺评论出版封面",
        "colors": ("黑白灰", "深蓝", "酒红", "米金", "旧纸色"),
        "composition": "艺术局部或旧书页为主体，批注、画框或剧场感作辅助",
        "typography": "雅致宋体或稳重衬线感，标题像文化类畅销书",
        "white_space": "35%-60%",
        "metaphors": ("艺术局部", "剧场", "画框", "批注", "经典符号", "旧书页"),
        "addendum": "借鉴果麦文化经典解读书的高级文化气质，避免学院论文感。",
    },
}

_CATEGORY_STYLE_PROFILES["web_short_story"] = _CATEGORY_STYLE_PROFILES["history_culture"]
_CATEGORY_STYLE_PROFILES["social_science_test"] = _CATEGORY_STYLE_PROFILES["social_science"]
_CATEGORY_STYLE_PROFILES["illustration"] = _CATEGORY_STYLE_PROFILES["psychology_self_help"]

_COVER_PROMPT_ADJUSTMENT_KEYS = ("style_brief_rules", "draft_rules", "refine_rules")


def _empty_cover_prompt_adjustment() -> dict[str, tuple[str, ...]]:
    return {key: () for key in _COVER_PROMPT_ADJUSTMENT_KEYS}


def _cover_category_ids() -> tuple[str, ...]:
    try:
        return tuple(str(item["id"]) for item in list_cover_categories())
    except Exception:
        return tuple(_CATEGORY_STYLE_PROFILES.keys())


_CATEGORY_COVER_PROMPT_ADJUSTMENTS: dict[str, dict[str, tuple[str, ...]]] = {
    category_id: _empty_cover_prompt_adjustment() for category_id in _cover_category_ids()
}
_CATEGORY_COVER_PROMPT_ADJUSTMENTS["social_science"] = {
    "style_brief_rules": (_VISUAL_IMPACT_STYLE_RULE, *_SATURATED_COLOR_DIRECTION, _WECHAT_DEDAO_STYLE_RULE),
    "draft_rules": (_VISUAL_IMPACT_STYLE_RULE, *_SATURATED_COLOR_DIRECTION, _WECHAT_DEDAO_STYLE_RULE),
    "refine_rules": (
        f"继续保持并强化：{_VISUAL_IMPACT_STYLE_RULE}",
        *_SATURATED_COLOR_DIRECTION,
        _WECHAT_DEDAO_STYLE_RULE,
    ),
}
_CATEGORY_COVER_PROMPT_ADJUSTMENTS["illustration_crayon"] = {
    "style_brief_rules": (_CRAYON_ILLUSTRATION_STYLE_RULE, _CRAYON_QUALITY_CONTROL_RULE),
    "draft_rules": (_CRAYON_ILLUSTRATION_STYLE_RULE, _CRAYON_QUALITY_CONTROL_RULE),
    "refine_rules": (
        f"继续保持并强化：{_CRAYON_ILLUSTRATION_STYLE_RULE}",
        _CRAYON_QUALITY_CONTROL_RULE,
    ),
}
_CATEGORY_COVER_PROMPT_ADJUSTMENTS["literature"] = {
    "style_brief_rules": (_LITERATURE_PUBLISHING_STYLE_RULE,),
    "draft_rules": (_LITERATURE_PUBLISHING_STYLE_RULE,),
    "refine_rules": (f"继续保持并强化：{_LITERATURE_PUBLISHING_STYLE_RULE}",),
}


def _is_crayon_illustration(category: str | None) -> bool:
    try:
        return normalize_cover_category_id(category or "") == "illustration_crayon"
    except ValueError:
        return False


def _is_visual_impact(category: str | None) -> bool:
    try:
        return normalize_cover_category_id(category or "") == "social_science"
    except ValueError:
        return False


def _is_literature_publishing(category: str | None) -> bool:
    try:
        return normalize_cover_category_id(category or "") == "literature"
    except ValueError:
        return False


def _has_real_author(cover_input: CoverInput) -> bool:
    author = (cover_input.author or "").strip()
    return bool(author) and author != _AUTHOR_PLACEHOLDER


def _author_presence_rule(cover_input: CoverInput, *, handwritten: bool) -> str:
    if not _has_real_author(cover_input):
        return "- Excel 未提供作者名时，不要凭空编造作者名，也不要输出「作者名留白」。"
    if handwritten:
        return (
            f"- 必须把作者名「{cover_input.author}」放到封面上，不可省略；"
            "作者名作为书名下方或角落的小号手写署名，清晰准确，与插画融合。"
        )
    return (
        f"- 必须把作者名「{cover_input.author}」放到封面上，不可省略；"
        "作者名次于书名，小号但清晰可读，像真实出版封面署名。"
    )


def _crayon_title_author_rule(cover_input: CoverInput) -> str:
    title_rule = (
        f"- 书名「{cover_input.title}」必须是封面最大、最显眼的文字，"
        "比作者名和任何副标题都大；像随手写在画面上的超大手写标题，同时在手机缩略图里一眼读清。"
    )
    return f"{title_rule}\n{_author_presence_rule(cover_input, handwritten=True)}"


def _crayon_fallback_visual_plan(cover_input: CoverInput) -> str:
    seed_text = "|".join((cover_input.title, cover_input.author, cover_input.intro))
    seed = hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:10]
    intro = cover_input.intro[:220]
    return (
        f"本书语义锚点：书名《{cover_input.title}》；作者：{cover_input.author}；简介线索：{intro}；差异化种子：{seed}。\n"
        "创作方式：不要从固定备选主体里挑选，也不要照搬任何预设模板；请像插画师读完这本书后，"
        "重新发明一个只属于这本书的治愈意象、空间和画面叙事。\n"
        "生成前必须在内部自行决定：主体意象、构图方式、季节、空间、视角、画面叙事、纯色背景颜色；"
        "这些决定要来自书名和简介，而不是来自通用治愈素材库。\n"
        "色彩方向必须是高饱和纯色背景，不要渐变、光效晕染或复杂纹理；插画可以使用少量明亮绘画色，"
        "核心是干净、透亮、温暖、有陪伴感。\n"
        "金句策略：除书名和作者外，只在封面上写1到2句很短的氛围金句，每句不超过10个中文字符，"
        "像手写在角落、画面边缘或大面积留白处的轻旁注；金句内容要从本书情绪里来，不能变成清单、段落或商业副标题。"
    )


def _crayon_visual_variation_directive(cover_input: CoverInput, ai_visual_plan: str | None = None) -> str:
    visual_plan = (ai_visual_plan or "").strip() or _crayon_fallback_visual_plan(cover_input)
    return (
        "本书差异化视觉判断（核心专属prompt原文不变；以下内容只决定这本书如何与其他蜡笔封面不同）：\n"
        "- 这不是固定备选库，也不是让模型在几个模板里抽签；必须给模型保留想象空间，先根据书名、简介、作者自行发明这本书专属方案。\n"
        "- 生成前必须先根据书名和简介自行判断：主体意象、构图方式、季节、空间、视角、画面叙事、纯色背景颜色。\n"
        f"{visual_plan}\n"
        "- 如果上面的视觉方案仍显得普通、像桌面日记模板或与书名关系弱，必须二次改造，换成更贴合本书的原创治愈意象和构图。\n"
        "- 必须从本书书名和简介里提炼一个专属物件、场景或情绪动作来替换通用模板；"
        "不要默认套用孤独小人、蓝天色块、植物边框、居中工整大字或同一种治愈系安全构图。"
        "尤其不要把不同书都画成桌面、摊开日记本、杯子、台灯、便签、碎纸和窗边静物。"
    )


def get_cover_category_prompt_adjustment(category: str | None) -> dict[str, tuple[str, ...]]:
    """Return per-category cover prompt addenda.

    Every current category has an entry. Empty tuples intentionally mean the
    original three-step cover workflow and base prompt remain unchanged.
    """
    normalized = normalize_cover_category_id(category or "social_science")
    adjustment = _CATEGORY_COVER_PROMPT_ADJUSTMENTS.get(normalized)
    if adjustment is None:
        adjustment = _empty_cover_prompt_adjustment()
    return {
        key: tuple(adjustment.get(key, ()))
        for key in _COVER_PROMPT_ADJUSTMENT_KEYS
    }


def _prompt_rule_section(rules: tuple[str, ...]) -> str:
    if not rules:
        return ""
    return "".join(f"- {rule}\n" for rule in rules)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def collect_reference_covers(
    cover_input: CoverInput,
    limit: int = 12,
    *,
    market_match: CoverMarketMatch | None = None,
) -> tuple[ReferenceCover, ...]:
    """Collect stable reference metadata from the three approved Dangdang brand pools."""
    cache_dir = _cover_reference_cache_dir()
    reference_intro = cover_input.intro
    reference_category_label = cover_input.category_label
    if market_match is not None:
        reference_category_label = market_match.category_label or reference_category_label
        reference_intro = " ".join(
            item for item in (cover_input.intro, market_match.search_keywords) if item
        )
    references = collect_dangdang_reference_covers(
        title=cover_input.title,
        intro=reference_intro,
        category_label=reference_category_label,
        allowed_brands=ALLOWED_REFERENCE_BRANDS,
        cache_dir=cache_dir,
        limit=limit,
        fetch_enabled=settings.COVER_REFERENCE_FETCH_ENABLED,
        download_images=settings.COVER_REFERENCE_DOWNLOAD_IMAGES,
        cache_enabled=settings.COVER_REFERENCE_CACHE_ENABLED,
        timeout_seconds=settings.COVER_REFERENCE_TIMEOUT_SECONDS,
    )
    return tuple(
        ReferenceCover(
            brand=item.brand,
            title=item.title,
            product_url=item.product_url,
            image_url=item.image_url,
            product_id=item.product_id,
            source=item.source,
            source_page=item.source_page,
            local_image_path=item.local_image_path,
        )
        for item in references
    )


def _fallback_market_match(cover_input: CoverInput) -> CoverMarketMatch:
    keywords = " ".join(
        item
        for item in (
            cover_input.title,
            cover_input.category_label,
            cover_input.intro[:120],
        )
        if item
    )
    if _is_visual_impact(cover_input.category):
        return CoverMarketMatch(
            category_label=cover_input.category_label,
            search_keywords=keywords[:240],
            analysis=(
                "参考早期封面 workflow 与微信读书/得到系爆款封面：高饱和鲜艳配色、强对比大色块、"
                "巨大清晰标题、少而准的现实痛点视觉隐喻，手机端缩略图第一眼抓人。"
                "避免灰暗社科感、PPT信息图、电商主图、电影海报和真实人物肖像。"
            ),
        )
    if _is_crayon_illustration(cover_input.category):
        return CoverMarketMatch(
            category_label=cover_input.category_label,
            search_keywords=keywords[:240],
            analysis=(
                "参考日韩治愈系绘本封面：手绘感、私人日记气质、高饱和纯色背景、背景不要渐变、"
                "简洁构图、大留白、动物或人物插画、松弛笨拙线条、真实手绘颗粒感、超大手写标题。"
                "每本书必须先根据书名和简介判断不同主体意象、构图、纯色背景、空间、视角和叙事。"
                "避免商业心理书、PPT感、UI插画风、过度精致和AI感。"
            ),
            visual_plan=_crayon_fallback_visual_plan(cover_input),
        )
    if _is_literature_publishing(cover_input.category):
        return CoverMarketMatch(
            category_label=cover_input.category_label,
            search_keywords=keywords[:240],
            analysis=(
                "参考用户提供的正统出版封面长期文件、微信读书精选与豆瓣高分文学封面：现代出版设计、简洁克制、有留白，"
                "弱化剧情与人物主体，强化氛围、平面构成、纸张肌理和书籍气质。"
                "配色克制但有识别度，避免灰蒙蒙做旧、古风模板、影视海报感、晋江小说封面和厚重书法字。"
            ),
        )
    return CoverMarketMatch(
        category_label=cover_input.category_label,
        search_keywords=keywords[:240],
        analysis=(
            f"按 Excel 分类「{cover_input.category_label}」检索同类畅销书。"
            "采用微信读书/得到系爆款风：高饱和明亮配色、标题强识别、少量精准隐喻、成熟出版排版。"
            "禁止真实人物肖像，不复刻真实封面。"
        ),
    )


def _category_options_for_market_prompt() -> str:
    try:
        return "；".join(f"{item['id']}={item['label']}" for item in list_cover_categories())
    except Exception:
        return cover_categories_fallback()


def cover_categories_fallback() -> str:
    return "social_science=视觉冲击；illustration=饱和插画；illustration_crayon=萌系治愈；literature=正统出版"


def _json_object_from_text(text: str) -> dict[str, Any]:
    stripped = (text or "").strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        stripped = stripped[start : end + 1]
    data = json.loads(stripped)
    if not isinstance(data, dict):
        raise ValueError("DeepSeek market analysis did not return a JSON object")
    return data


def analyze_cover_market_match(cover_input: CoverInput) -> CoverMarketMatch:
    if _is_literature_publishing(cover_input.category):
        return _fallback_market_match(cover_input)
    if not settings.COVER_DEEPSEEK_STYLE_ANALYSIS_ENABLED or not (settings.DEEPSEEK_API_KEY or "").strip():
        return _fallback_market_match(cover_input)

    if _is_visual_impact(cover_input.category):
        prompt = f"""你是中文出版封面市场分析师。请根据 Excel 中的书名、简介、作者、分类判断这本书的现实痛点和第一眼主视觉，再给当当/微信读书/得到系强视觉封面检索提供关键词。

书名：{cover_input.title}
作者：{cover_input.author}
简介：{cover_input.intro}
Excel 分类：{cover_input.category_label}
可选分类：{_category_options_for_market_prompt()}

只输出 JSON，不要输出解释文字：
{{
  "category_label": "最适合用于检索同类封面的中文分类名，优先贴近 Excel 分类",
  "search_keywords": "用于当当网、微信读书和得到系高饱和视觉冲击封面检索的中文关键词，80字以内",
  "analysis": "用120字以内总结早期封面 workflow 的强视觉方向：高饱和鲜艳、强对比、大色块、巨大标题、少而准的现实痛点隐喻，避免灰暗社科封面、PPT信息图、电商主图、电影海报和真实人物肖像",
  "visual_plan": "用160字以内为这本书单独判断一个强主视觉或强隐喻、主色组合、构图焦点和标题压版方式；必须来自书名和简介，颜色必须高饱和鲜艳，画面少元素但冲击力强"
}}"""
    elif _is_crayon_illustration(cover_input.category):
        prompt = f"""你是中文出版封面市场分析师。请先根据 Excel 中的书名、简介、作者、分类判断这本书应参考哪类日韩治愈系绘本封面，再给当当/微信读书同类封面检索提供关键词。

书名：{cover_input.title}
作者：{cover_input.author}
简介：{cover_input.intro}
Excel 分类：{cover_input.category_label}
可选分类：{_category_options_for_market_prompt()}

只输出 JSON，不要输出解释文字：
{{
  "category_label": "最适合用于检索同类封面的中文分类名，优先贴近 Excel 分类",
  "search_keywords": "用于当当网与微信读书治愈绘本封面检索的中文关键词，80字以内",
  "analysis": "用120字以内总结日韩治愈系绘本封面的私人日记感、高饱和纯色背景、简洁构图、大留白、松弛笨拙线条、真实手绘颗粒感和超大手写标题；必须强调背景不要渐变，避免商业心理书、PPT感、UI插画风、过度精致和AI感",
  "visual_plan": "用160字以内先为这本书单独判断动物或人物主体、构图方式、纯色背景颜色、季节、空间、视角、画面叙事和1到2句短金句策略；必须来自书名和简介，不要套用孤独小人、蓝天色块、植物边框、桌面日记本、杯子、台灯、便签或同一种治愈模板；背景必须高饱和纯色且不要渐变；线条要松弛笨拙，留白要多；金句只能做手写旁注"
}}"""
    else:
        prompt = f"""你是中文出版封面市场分析师。请先根据 Excel 中的书名、简介、作者、分类判断这本书应参考哪类畅销书封面，再给当当/微信读书/得到风格检索提供关键词。

书名：{cover_input.title}
作者：{cover_input.author}
简介：{cover_input.intro}
Excel 分类：{cover_input.category_label}
可选分类：{_category_options_for_market_prompt()}

只输出 JSON，不要输出解释文字：
{{
  "category_label": "最适合用于检索同类封面的中文分类名，优先贴近 Excel 分类",
  "search_keywords": "用于当当网同类畅销书封面检索的中文关键词，80字以内",
  "analysis": "用120字以内总结微信读书/得到系爆款封面的配色、排版、视觉隐喻方向；必须强调高饱和、少大面积深色、禁真实人物肖像、不得抄袭真实封面"
}}"""
    try:
        from app.services.deepseek import generate_text

        raw = generate_text(
            prompt,
            temperature=0.0,
            max_output_tokens=settings.COVER_DEEPSEEK_STYLE_ANALYSIS_MAX_TOKENS,
            system_instruction="你只做分类匹配、市场风格分析和单本书视觉判断，不写完整封面生成提示词，不生成图片。",
        )
        data = _json_object_from_text(raw)
        return CoverMarketMatch(
            category_label=str(data.get("category_label") or cover_input.category_label)[:80],
            search_keywords=str(data.get("search_keywords") or cover_input.title)[:240],
            analysis=str(data.get("analysis") or raw)[:900],
            visual_plan=str(data.get("visual_plan") or "")[:900],
        )
    except Exception as exc:
        logger.warning("DeepSeek cover market analysis fallback: %s", exc)
        return _fallback_market_match(cover_input)


def _cover_reference_cache_dir() -> Path:
    configured = (settings.COVER_REFERENCE_CACHE_DIR or "").strip()
    path = Path(configured or "dangdang_book_covers")
    if path.is_absolute():
        return path
    return _repo_root() / path


def _resolve_image_api_credentials() -> tuple[str, str, str]:
    if settings.OFFICIAL_API_DIRECT_ONLY:
        openai_key = (settings.OPENAI_API_KEY or "").strip()
        image_key = (settings.IMAGE_API_KEY or "").strip()
        api_key = openai_key or image_key
        if not api_key:
            raise ValueError("IMAGE_API_KEY 或 OPENAI_API_KEY 未配置，无法生成封面")
        base_url = official_openai_base_url(settings.OPENAI_BASE_URL)
        proxy = official_proxy("openai", settings.OPENAI_HTTPS_PROXY)
        return api_key, base_url, proxy
    image_key = (settings.IMAGE_API_KEY or "").strip()
    if image_key.lower() not in _PLACEHOLDER_KEYS and "xxx" not in image_key.lower():
        base_url = official_openai_base_url(settings.IMAGE_BASE_URL or settings.OPENAI_BASE_URL)
        proxy = _proxy_for_base_url(base_url)
        return image_key, base_url, proxy
    openai_key = (settings.OPENAI_API_KEY or "").strip()
    if not openai_key:
        raise ValueError("IMAGE_API_KEY 未配置，且 OPENAI_API_KEY 也为空，无法生成封面")
    base_url = official_openai_base_url(settings.OPENAI_BASE_URL)
    proxy = official_proxy("openai", settings.OPENAI_HTTPS_PROXY)
    return openai_key, base_url, proxy


def _proxy_for_base_url(base_url: str) -> str:
    lowered = base_url.lower()
    if "openai.com" in lowered:
        return official_proxy("openai", settings.OPENAI_HTTPS_PROXY)
    if "googleapis.com" in lowered or "google.com" in lowered:
        return official_proxy("google", settings.GOOGLE_HTTPS_PROXY)
    return ""


def _normalize_text(value: str | None, fallback: str) -> str:
    clean = (value or "").strip()
    return clean or fallback


def _extract_section(markdown: str, headings: tuple[str, ...]) -> str:
    lines = (markdown or "").splitlines()
    for idx, line in enumerate(lines):
        if line.strip().lstrip("@").strip() not in headings:
            continue
        collected: list[str] = []
        for next_line in lines[idx + 1 :]:
            stripped = next_line.strip()
            if stripped.startswith("@") and stripped.lstrip("@").strip():
                break
            if stripped:
                collected.append(stripped)
            if collected:
                break
        return " ".join(collected).strip()
    return ""


def _extract_primary_intro(markdown: str) -> str:
    intro = _extract_section(
        markdown,
        (
            "最终简介",
            "三个备选简介",
            "鏈€缁堢畝浠?",
            "鏈€缁堢畝浠",
            "涓変釜澶囬€夌畝浠?",
            "涓変釜澶囬€夌畝浠",
        ),
    )
    return intro[:600]


def _extract_author(markdown: str) -> str:
    for line in (markdown or "").splitlines():
        stripped = line.strip()
        for prefix in ("作者：", "作者:", "Author:", "author:"):
            if stripped.startswith(prefix):
                return stripped[len(prefix) :].strip()[:80]
    return ""


def prepare_cover_input(
    title: str,
    markdown: str,
    *,
    author: str | None = None,
    intro: str | None = None,
    category: str | None = None,
) -> CoverInput:
    normalized_category = normalize_cover_category_id(category or "social_science")
    category_meta = get_cover_category_meta(normalized_category)
    return CoverInput(
        title=_normalize_text(title, "未命名书稿")[:120],
        author=_normalize_text(author or _extract_author(markdown), _AUTHOR_PLACEHOLDER)[:80],
        intro=_normalize_text(intro or _extract_primary_intro(markdown), title)[:600],
        category=normalized_category,
        category_label=category_meta["label"],
    )




def build_cover_style_brief(cover_input: CoverInput) -> CoverStyleBrief:
    profile = _CATEGORY_STYLE_PROFILES.get(
        cover_input.category,
        _CATEGORY_STYLE_PROFILES["social_science"],
    )
    is_visual = _is_visual_impact(cover_input.category)
    is_crayon = _is_crayon_illustration(cover_input.category)
    is_literature = _is_literature_publishing(cover_input.category)
    market_match = analyze_cover_market_match(cover_input)
    references = (
        ()
        if is_literature
        else collect_reference_covers(
            cover_input,
            limit=6 if is_crayon else 12,
            market_match=market_match,
        )
    )
    category_prompt_adjustment = get_cover_category_prompt_adjustment(cover_input.category)
    style_brief_rules = category_prompt_adjustment["style_brief_rules"]
    style_brief_addendum = "\n".join(style_brief_rules)
    if style_brief_addendum:
        style_brief_addendum += "\n"
    visual_variation_directive = ""
    if is_visual:
        avoid_rules = (
            "只学习早期封面 workflow、当当网、微信读书和得到系强视觉封面的抽象设计规律，不复刻、不拼贴、不近似复制任何真实封面。",
            "不要出现出版社 logo、出版品牌 logo、原出版信息、二维码、价格、水印、真实品牌背书或真实书名残影。",
            "不要做成电商商品主图、灰暗社科封面、PPT信息图、3D书模、白底商品照、赠品图、促销海报或电影海报。",
            "不要生成乱码中文、英文占位符、真实人物肖像、名人脸、可识别照片脸。",
            "视觉主体必须少而准：一个强主视觉或强隐喻即可，不要堆砌社会痛点符号。",
            _NO_REAL_PORTRAIT_RULE,
            _WECHAT_DEDAO_STYLE_RULE,
            *style_brief_rules,
            *_SATURATED_COLOR_DIRECTION,
        )
        color_palette = tuple(profile["colors"]) + _SATURATED_COLOR_DIRECTION
        reference_site = "当当网 + 早期封面 workflow + 微信读书/得到强视觉封面分析"
        brand_style_direction = (
            "以时代华语现实议题出版气质为底，回到早期封面 workflow 的强视觉策略：先判断本书最有冲突感的现实痛点，"
            "再做一个高饱和鲜艳、强对比、大色块、巨大标题的主视觉封面。"
        )
        title_hierarchy = (
            "书名必须是第一视觉焦点，巨大、清晰、强对比；作者名次之，副标题或封面文案只保留一句很短内容。"
        )
        visual_variation_directive = market_match.visual_plan.strip()
        generation_prompt_addendum = (
            f"{profile['addendum']}\n"
            f"{visual_variation_directive}\n"
            f"DeepSeek 分类/市场分析：{market_match.analysis}\n"
            f"{style_brief_addendum}"
            "必须使用高饱和鲜艳颜色、强对比大色块和强主视觉；标题必须巨大可读；"
            "不要灰暗社科封面、不要PPT信息图、不要商业模板感，画面少元素但冲击力强。"
        )
    elif is_crayon:
        crayon_title_author_rule = _crayon_title_author_rule(cover_input)
        visual_variation_directive = _crayon_visual_variation_directive(
            cover_input,
            ai_visual_plan=market_match.visual_plan,
        )
        avoid_rules = (
            "只学习日韩治愈系绘本与微信读书治愈插画封面的抽象审美，不复刻、不拼贴、不近似复制任何真实封面。",
            "不要出现出版社 logo、出版品牌 logo、原出版信息、二维码、价格、水印、真实品牌背书或真实书名残影。",
            "不要做成电商商品主图、3D 书模、白底商品照、赠品图、促销海报、心理学商业封面或电影海报。",
            "不要生成乱码中文、英文占位符、真实人物肖像、名人脸、可识别照片脸。",
            "不要信息图、PPT感、门和小路构图、UI插画风、过度精致和AI感。",
            "不要把同一批书做成同一种孤独小人、蓝天色块、植物边框或居中模板；每本都必须有自己的主意象和构图。",
            "不要把“私人日记感”字面化成同一种书桌、摊开的日记本、杯子、台灯、便签、碎纸和窗边静物。",
            "背景颜色必须是高饱和纯色，不要渐变背景；不要大面积灰紫、灰褐、灰蓝、脏米色、夜景暗色或昏暗灯光。",
            _NO_REAL_PORTRAIT_RULE,
            *style_brief_rules,
        )
        color_palette = tuple(profile["colors"])
        reference_site = "当当网 + 微信读书治愈绘本封面分析"
        brand_style_direction = (
            "优先参考日韩治愈系绘本和微信读书治愈插画封面的抽象审美规律：私人日记感、"
            "真实手绘颗粒感、高饱和纯色背景、简洁构图、大留白、松弛笨拙线条与陪伴感；"
            "同时每本书必须根据书名和简介生成不同主意象、构图和纯色背景，避免商业心理书封面。"
            "私人日记只是情绪气质，不能每张都落成桌面日记静物；必须避免同批封面的道具和视角重复。"
        )
        title_hierarchy = (
            "书名字像超大手写标题随手写在画面上，与插画融合，但必须是封面最大、最显眼的文字，"
            "手机缩略图里也要一眼读清；作者名必须保留为小号手写署名，可放在书名下方或角落。"
        )
        generation_prompt_addendum = (
            f"{profile['addendum']}\n"
            f"{visual_variation_directive}\n"
            f"DeepSeek 分类/市场分析：{market_match.analysis}\n"
            f"{style_brief_addendum}"
            f"{crayon_title_author_rule}\n"
            "必须强化日韩治愈系绘本封面、手绘感、私人日记感和真实手绘颗粒；"
            "必须优先执行本书差异化视觉方案，避免同质化、过度精致和AI感；"
            "必须使用高饱和纯色背景且不要渐变，避免桌面日记模板和低饱和灰暗画面。"
        )
    elif is_literature:
        avoid_rules = (
            "只学习用户提供的正统出版封面长期文件、微信读书精选和豆瓣高分文学书封的抽象出版规律，不复刻、不拼贴、不近似复制任何真实封面。",
            "不要出现出版社 logo、出版品牌 logo、原出版信息、二维码、价格、水印、真实品牌背书或真实书名残影。",
            "不要古风模板、影视海报感、晋江小说封面、厚重书法字、PPT感、廉价AI插画感、电商商品主图或3D书模。",
            "不要生成乱码中文、英文占位符、真实人物肖像、名人脸、可识别照片脸。",
            "不要灰蒙蒙与做旧感；颜色要克制但有识别度和情绪感，第一眼能吸引人。",
            _NO_REAL_PORTRAIT_RULE,
            *style_brief_rules,
        )
        color_palette = tuple(profile["colors"])
        reference_site = "本地正统出版参考文件 + 微信读书精选 + 豆瓣高分文学封面分析"
        brand_style_direction = (
            "优先参考用户提供的正统出版封面长期文件、微信读书精选和豆瓣高分文学的抽象审美：简洁、克制、有留白，"
            "弱剧情、弱人物主体，强化氛围、平面构成、纸张肌理和书籍气质。"
        )
        title_hierarchy = (
            "标题排版高级、稀疏、克制，像真正出版社的文学书设计；"
            "不要AI书法字、厚重书法字或网文感大字。"
        )
        generation_prompt_addendum = (
            f"{profile['addendum']}\n"
            f"长期参考文件摘要：{_LITERATURE_COVER_REFERENCE[:900]}\n"
            f"本地分类/市场分析：{market_match.analysis}\n"
            f"{style_brief_addendum}"
            "整体安静、诗意、有孤独感与呼吸感，同时保留现代感与视觉吸引力。"
        )
    else:
        avoid_rules = (
            "只学习当当网、微信读书、得到畅销书封面的抽象设计规律，不复刻、不拼贴、不近似复制任何真实封面。",
            "不要出现出版社 logo、出版品牌 logo、原出版信息、二维码、价格、水印、真实品牌背书或真实书名残影。",
            "不要做成电商商品主图、3D 书模、白底商品照、赠品图、促销海报或电影海报。",
            "不要生成乱码中文、英文占位符、真实人物肖像、名人脸、可识别照片脸。",
            _NO_REAL_PORTRAIT_RULE,
            _WECHAT_DEDAO_STYLE_RULE,
            *style_brief_rules,
            *_SATURATED_COLOR_DIRECTION,
        )
        color_palette = tuple(profile["colors"]) + _SATURATED_COLOR_DIRECTION
        reference_site = "当当网 + 微信读书/得到爆款风格分析"
        brand_style_direction = f"优先参考 {profile['brand']} 在当当网同类畅销书中的抽象审美规律，并叠加微信读书/得到系知识爆款风。"
        title_hierarchy = "书名最大，作者名次之，副标题或极短封面文案更小；手机端缩略图也必须一眼读清书名。"
        generation_prompt_addendum = (
            f"{profile['addendum']}\n"
            f"DeepSeek 分类/市场分析：{market_match.analysis}\n"
            f"{style_brief_addendum}"
            "必须强化微信读书/得到系爆款风：高饱和、强识别、清晰标题层级、少量精准隐喻。"
            "避免大面积深色，禁真实人物肖像，不复制真实封面。"
        )
    return CoverStyleBrief(
        reference_site=reference_site,
        reference_brands=() if is_literature else ALLOWED_REFERENCE_BRANDS,
        matched_category_style=f"{cover_input.category_label}: {profile['style']}",
        brand_style_direction=brand_style_direction,
        cover_type=str(profile["cover_type"]),
        color_palette=color_palette,
        composition=str(profile["composition"]),
        typography_direction=str(profile["typography"]),
        white_space_ratio=str(profile["white_space"]),
        visual_metaphors=tuple(profile["metaphors"]),
        reference_samples=tuple(asdict(item) for item in references),
        market_style_analysis=market_match.analysis,
        title_hierarchy=title_hierarchy,
        avoid_rules=avoid_rules,
        generation_prompt_addendum=generation_prompt_addendum,
        visual_variation_directive=visual_variation_directive,
    )


def _brief_json(brief: CoverStyleBrief) -> str:
    return json.dumps(asdict(brief), ensure_ascii=False, indent=2)










def build_draft_cover_prompt(cover_input: CoverInput, style_brief: CoverStyleBrief) -> str:
    is_visual = _is_visual_impact(cover_input.category)
    is_crayon = _is_crayon_illustration(cover_input.category)
    is_literature = _is_literature_publishing(cover_input.category)
    style_report = _brief_json(style_brief)[:2600 if is_crayon else 3200]
    category_rule_section = _prompt_rule_section(
        get_cover_category_prompt_adjustment(cover_input.category)["draft_rules"]
    )
    style_addendum_section = style_brief.generation_prompt_addendum[:1400].rstrip()
    if is_visual:
        title_author_rule_section = ""
        visual_variation_section = f"{style_brief.visual_variation_directive}\n"
        market_rule_section = (
            "- 工作流第一步已经做了 DeepSeek 分类/市场判断，并检索当当网同类畅销书，同时接回早期封面 workflow 的强视觉路线。"
            "你只学习抽象规律，不复刻、不拼贴、不近似复制任何真实封面。\n"
            "- 最高优先级：视觉冲击类封面，高饱和鲜艳、强对比、大色块、巨大标题、少而准的现实痛点隐喻，手机端第一眼抓人。\n"
        )
        color_and_type_rules = (
            "- 配色必须高饱和、鲜艳、强识别，优先亮黄、玫红、电光蓝、亮青绿、珊瑚橙、干净白色留白；不要灰蒙蒙、做旧或大面积黑暗铺底。\n"
            "- 主视觉只能少而准：一个强图形、一个现实痛点隐喻或一个符号化人物/物件即可，不要堆砌元素或做信息图。\n"
            "- 书名必须巨大、清晰、强对比，是第一视觉焦点；副标题或封面文案最多一句，很短。\n"
            "- 不要灰暗社科封面、PPT信息图、电商主图或电影海报。\n"
        )
        text_rule = "- 中文文字尽量准确清晰，像真实出版封面的强标题排版；不要乱码中文、伪中文、英文占位符。\n"
    elif is_crayon:
        title_author_rule_section = f"{_crayon_title_author_rule(cover_input)}\n"
        visual_variation_section = f"{style_brief.visual_variation_directive}\n"
        market_rule_section = (
            "- 工作流第一步已经做了 DeepSeek 分类/市场判断，并检索当当网与微信读书同类封面。"
            "你只学习日韩治愈系绘本与微信读书治愈插画封面的抽象审美，不复刻、不拼贴、不近似复制任何真实封面。\n"
            "- 最高优先级：做成日韩治愈系绘本封面，手绘感，像私人日记；"
            "画面意象必须按本书独立判断，避免过度精致和AI感。\n"
        )
        color_and_type_rules = (
            "- 背景必须是高饱和纯色，不要渐变背景、光效渐变、网格渐变或多色晕染；整体明亮通透，深暗色面积低于10%。\n"
            "- 动物或人物插画，线条松弛笨拙，保留真实手绘颗粒感；简洁构图，大留白。\n"
            "- 书名是第一视觉焦点：超大、清晰、有手写感，像随手写在画面上。\n"
            "- 画面元素少而准，留白多一点；不要堆满治愈符号，也不要套用桌面日记本、杯子、台灯、便签组合。\n"
            "- 除书名和作者外，只能有1到2句很短的氛围金句，每句不超过10个中文字符，作为角落或留白里的手写旁注。\n"
        )
        text_rule = "- 中文书名和作者署名都要尽量准确清晰，但必须像随手写进画面里的手写字；不要乱码中文、伪中文、英文占位符。\n"
    elif is_literature:
        title_author_rule_section = ""
        visual_variation_section = ""
        market_rule_section = (
            "- 工作流第一步已使用本地正统出版长期参考文件，并参考微信读书精选与豆瓣高分文学同类封面的抽象规律。"
            "你只学习现代文学出版封面的抽象规律，不复刻、不拼贴、不近似复制任何真实封面。\n"
            "- 文艺文学感书封：现代出版设计风格，简洁、克制、有留白；弱化剧情感与人物主体，强化氛围、平面构成与书籍气质。\n"
        )
        color_and_type_rules = (
            "- 可使用自然、城市、旅行、人物、植物、建筑、光影等意象元素，少而精；平面插画或轻水彩质感，轻微颗粒与纸张肌理。\n"
            "- 配色不要灰蒙蒙与做旧感，使用更有识别度和情绪感的颜色；颜色克制但能第一眼吸引人。\n"
            "- 标题排版高级、稀疏、克制，像真正出版社的文学书设计，而不是AI书法字或厚重书法字。\n"
        )
        text_rule = "- 中文文字尽量准确清晰，排版安静、有呼吸感、有现代文学书籍气质；不要乱码中文、伪中文、英文占位符。\n"
    else:
        title_author_rule_section = ""
        visual_variation_section = ""
        market_rule_section = (
            "- 工作流第一步已经做了 DeepSeek 分类/市场判断，并检索当当网同类畅销书，同时叠加微信读书/得到系爆款风格。"
            "你只学习抽象规律，不复刻、不拼贴、不近似复制任何真实封面。\n"
            "- 微信读书/得到系爆款风：高饱和、强识别、标题大而清晰、色块干净、手机端缩略图也醒目，排版要像成熟商业出版物。\n"
        )
        color_and_type_rules = (
            "- 配色要明亮且饱和，颜色组合要清楚、有情绪、有手机端识别度；不要大面积黑色、深蓝、暗红、深灰，深色面积尽量低于 20%。\n"
            "- 参考出版社版本时也不能做得过黑过暗；深色只做局部压重，整体要保持清楚、明亮、有出版质感。\n"
            "- 书名最大，作者名次之；简介只能提炼成一句很短副标题或封面文案，不要整段上封面。\n"
        )
        text_rule = "- 中文文字尽量准确清晰，像真实出版封面排版；不要乱码中文、伪中文、英文占位符。\n"
    platform_logo_rule = (
        "- 不要出现出版社 logo、出版品牌 logo、微信读书/豆瓣 logo、二维码、价格、水印、真实品牌背书、真实书名残影。"
        if is_literature
        else "- 不要出现出版社 logo、出版品牌 logo、微信读书/得到/当当 logo、二维码、价格、水印、真实品牌背书、真实书名残影。"
    )
    return f"""你是资深中文出版封面设计师。请严格根据 Excel 提供的书名、作者、简介、分类，生成一张竖版中文图书正面封面。

书籍信息：
- 书名：{cover_input.title}
- 作者：{cover_input.author}
- 简介：{cover_input.intro}
- 分类：{cover_input.category_label}

必须优先遵守的硬规则：
{market_rule_section.rstrip()}
{category_rule_section.rstrip()}
{style_addendum_section}
{visual_variation_section.rstrip()}
{color_and_type_rules.rstrip()}
- 禁止真实人物肖像、名人脸、写实人像、可识别照片脸；如需人物，只能使用抽象剪影、背影、符号化小人或非写实插画。
{title_author_rule_section}{text_rule.rstrip()}
{platform_logo_rule}
- 不要 3D 书模、书脊、白底商品照、赠品、贴纸角标、促销条、电影海报风、电商主图风。

参考风格报告：
{style_report}

输出：
完整正面封面本体，画面铺满整张竖版画布，推荐 2:3 或 3:4，可直接作为中文图书封面使用。"""


def build_refine_cover_prompt(cover_input: CoverInput, style_brief: CoverStyleBrief) -> str:
    category_rule_section = _prompt_rule_section(
        get_cover_category_prompt_adjustment(cover_input.category)["refine_rules"]
    )
    if _is_visual_impact(cover_input.category):
        title_author_rule_section = ""
        visual_variation_section = f"{style_brief.visual_variation_directive}\n"
        opening = "请精修这张封面，让它成为高饱和鲜艳、第一眼抓人的视觉冲击类中文图书封面。"
        composition_rule = (
            "- 保留草稿中最强的现实痛点主视觉或隐喻，但进一步压缩元素、放大标题、提升色块对比；"
            "画面必须少而准，不要变成信息图、PPT页面或灰暗社科封面。"
        )
        market_rule = (
            "- 强化早期封面 workflow 的高饱和强视觉路线：鲜艳主色、强对比、大色块、巨大清晰标题、少量精准隐喻，"
            "手机端缩略图也要一眼抓人。"
            "避免灰暗、做旧、大面积黑底、真实人物肖像、电影海报感、电商主图感和廉价AI模板感。"
        )
    elif _is_crayon_illustration(cover_input.category):
        title_author_rule_section = f"{_crayon_title_author_rule(cover_input)}\n"
        visual_variation_section = f"{style_brief.visual_variation_directive}\n"
        opening = "请精修这张封面，让它成为有独特视觉叙事的日韩治愈系绘本封面。"
        composition_rule = (
            "- 保留草稿中来自本书差异化视觉判断的主体意象、构图、季节、空间、视角、叙事和纯色背景；"
            "不要把画面拉回通用治愈模板；让画面更松弛、更有人味；"
            "书名字像超大手写标题随手写在画面上，与插画自然融合；"
            "同时把书名放大成第一视觉焦点，作者名保留为小号手写署名。"
        )
        market_rule = (
            "- 强化日韩治愈系绘本封面风格：手绘感、像私人日记、真实手绘颗粒、"
            "高饱和纯色背景、简洁构图、大留白、动物或人物插画、松弛笨拙线条。"
            "书名层级清晰，手机端缩略图也能一眼读清。"
            "元素少而准，避免商业心理书、PPT感、UI插画风、门和小路构图、桌面日记模板、过度精致和AI感。"
            "除书名和作者外，只保留或补上1到2句很短的氛围金句，作为手写旁注融入画面。"
        )
    elif _is_literature_publishing(cover_input.category):
        title_author_rule_section = ""
        visual_variation_section = ""
        opening = "请精修这张封面，让它成为真正出版社质感的现代文艺文学书封。"
        composition_rule = (
            "- 保留草稿的现代文学气质，但进一步弱化剧情感与人物主体，强化氛围、平面构成、留白、纸张肌理和书籍气质；"
            "让画面安静、诗意、有孤独感与呼吸感，同时第一眼有视觉吸引力。"
        )
        market_rule = (
            "- 强化豆瓣高分文学与微信读书精选封面的抽象气质：简洁克制、有留白、少而精的自然/城市/旅行/植物/建筑/光影意象，"
            "平面插画或轻水彩质感，轻微颗粒与纸张肌理。"
            "避免古风模板、影视海报感、晋江小说封面、厚重书法字、PPT感和廉价AI插画感。"
        )
        deletion_rule = "- 删除所有出版社 logo、出版品牌 logo、微信读书/豆瓣 logo、原出版信息、二维码、价格、促销标签、真实书名残影、乱码文字、水印。"
    else:
        title_author_rule_section = ""
        visual_variation_section = ""
        opening = "请精修这张封面，让它成为可直接商用、具有成熟出版质感的中文图书封面。"
        composition_rule = "- 保留草稿的核心视觉和书籍气质，但让构图更稳、标题更清晰、字体层级更专业。"
        market_rule = "- 强化微信读书/得到系爆款风：高饱和色块、强标题识别、明确留白、少量精准视觉隐喻，手机端缩略图也醒目。"
        deletion_rule = "- 删除所有出版社 logo、出版品牌 logo、微信读书/得到/当当 logo、原出版信息、二维码、价格、促销标签、真实书名残影、乱码文字、水印。"
    if _is_visual_impact(cover_input.category) or _is_crayon_illustration(cover_input.category):
        deletion_rule = "- 删除所有出版社 logo、出版品牌 logo、微信读书/得到/当当 logo、原出版信息、二维码、价格、促销标签、真实书名残影、乱码文字、水印。"
    return f"""{opening}

书籍信息：
- 书名：{cover_input.title}
- 作者：{cover_input.author}
- 简介：{cover_input.intro}
- 分类：{cover_input.category_label}

精修硬规则：
{composition_rule}
{visual_variation_section.rstrip()}
- 继续遵循：{style_brief.generation_prompt_addendum[:1400]}
{category_rule_section.rstrip()}
{title_author_rule_section}- 如果画面偏暗，必须提亮并提升饱和度；灰紫、灰褐、灰蓝、脏米色、夜景暗色、大面积黑色/深蓝/暗红/深灰必须减少到 10% 以下，并改成明亮通透绘画色。
- 参考出版社版本时也不能做得过黑过暗；深色只做局部压重，整体保持清楚、明亮、有出版质感。
- 如果是蜡笔分类，背景必须是高饱和纯色，不能使用渐变背景、光效渐变、网格渐变或多色晕染。
- 如果草稿是桌面、摊开的日记本、杯子、台灯、便签、碎纸和窗边静物的通用组合，必须重构为本书专属主意象、空间、视角和纯色背景；私人日记只保留为情绪气质。
{market_rule}
{deletion_rule}
- 禁止真实人物肖像、名人脸、写实人像、可识别照片脸；如已有此类内容，改成抽象剪影、背影、符号化小人或非写实插画。
- 不要改成电商商品图，不要出现 3D 书本样机、书脊、赠品、白底商品展示。

最终输出：
竖版正面封面本体，高清干净，有纸张或印刷质感。"""


def build_cover_prompt(
    title: str,
    markdown: str,
    *,
    author: str | None = None,
    intro: str | None = None,
    category: str | None = None,
) -> str:
    cover_input = prepare_cover_input(
        title,
        markdown,
        author=author,
        intro=intro,
        category=category,
    )
    return build_draft_cover_prompt(cover_input, build_cover_style_brief(cover_input))


def _cover_cancelled(stage: str) -> CoverGenerationCancelled:
    return CoverGenerationCancelled(f"封面生成已取消：{stage}")


def _post_image_request_with_cancellation(
    http: httpx.Client,
    endpoint: str,
    api_key: str,
    *,
    should_abort: Callable[[], bool] | None = None,
    stage: str,
    json_payload: dict[str, Any] | None = None,
    data: dict[str, str] | None = None,
    files: dict[str, tuple[str, bytes, str]] | None = None,
) -> Any:
    result_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)
    hard_timeout = max(0, int(getattr(settings, "IMAGE_API_HARD_TIMEOUT_SECONDS", 0) or 0))
    hard_deadline = time.monotonic() + hard_timeout if hard_timeout > 0 else None

    def _send() -> None:
        try:
            result_queue.put(
                (
                    "ok",
                    _post_image_request(
                        http,
                        endpoint,
                        api_key,
                        json_payload=json_payload,
                        data=data,
                        files=files,
                    ),
                )
            )
        except Exception as exc:  # pragma: no cover - surfaced through result_queue
            result_queue.put(("error", exc))

    worker = threading.Thread(target=_send, name=f"cover-image-{stage}", daemon=True)
    worker.start()
    while True:
        try:
            kind, payload = result_queue.get(timeout=0.5)
        except queue.Empty:
            if should_abort is not None and should_abort():
                logger.info("取消封面图片请求并释放本地闸口: stage=%s", stage)
                http.close()
                raise _cover_cancelled(stage)
            if hard_deadline is not None and time.monotonic() >= hard_deadline:
                logger.warning(
                    "image API hard timeout stage=%s timeout=%ss endpoint=%s",
                    stage,
                    hard_timeout,
                    endpoint.rstrip("/").rsplit("/", 1)[-1],
                )
                http.close()
                raise ImageApiUncertainError(f"image API hard timeout after {hard_timeout}s: {stage}")
            continue
        if kind == "ok":
            return payload
        raise payload


def _request_image_generation_response(
    prompt: str,
    should_abort: Callable[[], bool] | None = None,
    *,
    gate_wait_timeout_seconds: int | None = None,
) -> Any:
    api_key, base_url, proxy = _resolve_image_api_credentials()
    endpoint = f"{base_url}/images/generations"
    payload: dict[str, Any] = {
        "model": settings.IMAGE_MODEL,
        "prompt": prompt[:_IMAGE_PROMPT_LIMIT],
        "size": settings.IMAGE_SIZE,
        "n": 1,
    }
    if settings.IMAGE_RESPONSE_FORMAT:
        payload["response_format"] = settings.IMAGE_RESPONSE_FORMAT
    if settings.IMAGE_OUTPUT_FORMAT:
        payload["output_format"] = settings.IMAGE_OUTPUT_FORMAT
    if settings.IMAGE_QUALITY:
        payload["quality"] = settings.IMAGE_QUALITY

    request_timeout = max(60, int(settings.IMAGE_API_REQUEST_TIMEOUT_SECONDS or 300))
    event_base = {
        "provider": "openai_image",
        "model": settings.IMAGE_MODEL,
        "stage": "draft",
        "endpoint": endpoint,
    }
    _emit_image_request_event({"event": "start", **event_base})
    with httpx.Client(
        timeout=httpx.Timeout(float(request_timeout), connect=min(60.0, float(request_timeout))),
        proxy=proxy or None,
    ) as http:
        try:
            with distributed_semaphore(
                "image-cover",
                limit=settings.IMAGE_MAX_CONCURRENT_CALLS,
                wait_timeout_seconds=gate_wait_timeout_seconds
                or settings.IMAGE_GATE_WAIT_TIMEOUT_SECONDS,
                should_abort=should_abort,
                abort_exception_factory=lambda: _cover_cancelled("wait draft image gate"),
            ):
                response_data = _post_image_request_with_cancellation(
                    http,
                    endpoint,
                    api_key,
                    json_payload=payload,
                    should_abort=should_abort,
                    stage="draft image request",
                )
        except ImageApiUncertainError as exc:
            _emit_image_request_event({"event": "error", "error_type": "timeout_uncertain", "billing_risk": True, "error": str(exc), **event_base})
            raise
        except ImageApiPermanentError as exc:
            _emit_image_request_event({"event": "error", "error_type": "permanent", "billing_risk": False, "error": str(exc), **event_base})
            raise
        except ImageApiTransientError as exc:
            _emit_image_request_event({"event": "error", "error_type": "transient", "billing_risk": False, "error": str(exc), **event_base})
            raise
        except Exception as exc:
            _emit_image_request_event({"event": "error", "error_type": exc.__class__.__name__, "billing_risk": False, "error": str(exc), **event_base})
            raise
    _emit_image_request_event({"event": "success", "response_summary": {"data_count": len(response_data.get("data") or []) if isinstance(response_data, dict) else None}, **event_base})
    _abort_cover_generation_if_requested(should_abort, stage="after draft image request")
    return response_data


def _request_image_generation(
    prompt: str,
    should_abort: Callable[[], bool] | None = None,
    *,
    gate_wait_timeout_seconds: int | None = None,
) -> bytes:
    response_data = _request_image_generation_response(
        prompt,
        should_abort=should_abort,
        gate_wait_timeout_seconds=gate_wait_timeout_seconds,
    )
    return _decode_image_response(response_data, should_abort=should_abort)


def _request_image_edit_response(
    image_path: Path,
    prompt: str,
    should_abort: Callable[[], bool] | None = None,
) -> Any:
    api_key, base_url, proxy = _resolve_image_api_credentials()
    endpoint = f"{base_url}/images/edits"
    data: dict[str, str] = {
        "model": settings.IMAGE_MODEL,
        "prompt": prompt[:_IMAGE_PROMPT_LIMIT],
        "size": settings.IMAGE_SIZE,
        "n": "1",
    }
    if settings.IMAGE_RESPONSE_FORMAT:
        data["response_format"] = settings.IMAGE_RESPONSE_FORMAT
    if settings.IMAGE_OUTPUT_FORMAT:
        data["output_format"] = settings.IMAGE_OUTPUT_FORMAT
    if settings.IMAGE_QUALITY:
        data["quality"] = settings.IMAGE_QUALITY

    request_timeout = max(60, int(settings.IMAGE_API_REQUEST_TIMEOUT_SECONDS or 300))
    event_base = {
        "provider": "openai_image",
        "model": settings.IMAGE_MODEL,
        "stage": "refine",
        "endpoint": endpoint,
    }
    _emit_image_request_event({"event": "start", **event_base})
    with httpx.Client(
        timeout=httpx.Timeout(float(request_timeout), connect=min(60.0, float(request_timeout))),
        proxy=proxy or None,
    ) as http:
        files = {
            "image": (
                image_path.name,
                image_path.read_bytes(),
                "image/png",
            )
        }
        try:
            with distributed_semaphore(
                "image-cover",
                limit=settings.IMAGE_MAX_CONCURRENT_CALLS,
                wait_timeout_seconds=settings.IMAGE_GATE_WAIT_TIMEOUT_SECONDS,
                should_abort=should_abort,
                abort_exception_factory=lambda: _cover_cancelled("wait refine image gate"),
            ):
                response_data = _post_image_request_with_cancellation(
                    http,
                    endpoint,
                    api_key,
                    data=data,
                    files=files,
                    should_abort=should_abort,
                    stage="refine image request",
                )
        except ImageApiUncertainError as exc:
            _emit_image_request_event({"event": "error", "error_type": "timeout_uncertain", "billing_risk": True, "error": str(exc), **event_base})
            raise
        except ImageApiPermanentError as exc:
            _emit_image_request_event({"event": "error", "error_type": "permanent", "billing_risk": False, "error": str(exc), **event_base})
            raise
        except ImageApiTransientError as exc:
            _emit_image_request_event({"event": "error", "error_type": "transient", "billing_risk": False, "error": str(exc), **event_base})
            raise
        except Exception as exc:
            _emit_image_request_event({"event": "error", "error_type": exc.__class__.__name__, "billing_risk": False, "error": str(exc), **event_base})
            raise
    _emit_image_request_event({"event": "success", "response_summary": {"data_count": len(response_data.get("data") or []) if isinstance(response_data, dict) else None}, **event_base})
    _abort_cover_generation_if_requested(should_abort, stage="after refine image request")
    return response_data


def _request_image_edit(
    image_path: Path,
    prompt: str,
    should_abort: Callable[[], bool] | None = None,
) -> bytes:
    response_data = _request_image_edit_response(image_path, prompt, should_abort=should_abort)
    return _decode_image_response(response_data, should_abort=should_abort)


def _post_image_request(
    http: httpx.Client,
    endpoint: str,
    api_key: str,
    *,
    json_payload: dict[str, Any] | None = None,
    data: dict[str, str] | None = None,
    files: dict[str, tuple[str, bytes, str]] | None = None,
) -> Any:
    last_error: Exception | None = None
    max_attempts = max(1, int(settings.IMAGE_API_MAX_RETRIES or 1))
    for attempt in range(1, max_attempts + 1):
        started = time.monotonic()
        try:
            response = http.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}"},
                json=json_payload,
                data=data,
                files=files,
            )
            response.raise_for_status()
            data = response.json()
            logger.info(
                "image API request completed endpoint=%s attempt=%d elapsed=%.1fs",
                endpoint.rstrip("/").rsplit("/", 1)[-1],
                attempt,
                time.monotonic() - started,
            )
            return data
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:1000]
            status_code = exc.response.status_code
            message = f"图像 API 请求失败 {status_code}: {body}"
            if status_code != 429 and status_code < 500:
                raise ImageApiPermanentError(message) from exc
            last_error = ImageApiTransientError(message)
            if attempt >= max_attempts:
                raise ImageApiTransientError(f"{message}，已重试 {attempt} 次") from exc
        except (httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
            raise ImageApiUncertainError(
                f"image API request result is uncertain; automatic retry stopped to avoid duplicate billing: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            last_error = ImageApiTransientError(f"图像 API 连接失败: {exc}")
            if attempt >= max_attempts:
                raise ImageApiTransientError(f"图像 API 连接失败: {exc}，已重试 {attempt} 次") from exc
        except (RuntimeError, ValueError) as exc:
            raise ImageApiUncertainError(
                f"image API returned an unreadable response; automatic retry stopped to avoid duplicate billing: {exc}"
            ) from exc
        sleep_for = min(45.0, (3.0 * attempt) + random.uniform(0.0, 2.0))
        logger.warning(
            "image API request retrying endpoint=%s attempt=%d/%d sleep=%.1fs error=%s",
            endpoint.rstrip("/").rsplit("/", 1)[-1],
            attempt,
            max_attempts,
            sleep_for,
            last_error,
        )
        time.sleep(sleep_for)
    raise ImageApiTransientError(f"图像 API 请求失败: {last_error}")


def _decode_image_response(data: Any, should_abort: Callable[[], bool] | None = None) -> bytes:
    _abort_cover_generation_if_requested(should_abort, stage="decode image response")
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        raise ImageApiPermanentError(
            f"image API returned no image data; automatic retry stopped to avoid duplicate billing: {str(data)[:1000]}"
        )
    item = items[0]
    b64 = item.get("b64_json") if isinstance(item, dict) else None
    if b64:
        try:
            return base64.b64decode(b64, validate=True)
        except (ValueError, TypeError) as exc:
            raise ImageApiPermanentError("image API returned invalid base64 image data") from exc

    url = item.get("url") if isinstance(item, dict) else None
    if url:
        return _download_image_result(url, should_abort=should_abort)
    raise ImageApiPermanentError("image API returned neither b64_json nor url; automatic retry stopped")


def _download_image_result(url: str, should_abort: Callable[[], bool] | None = None) -> bytes:
    last_error: Exception | None = None
    max_attempts = max(1, int(settings.IMAGE_RESULT_DOWNLOAD_MAX_RETRIES or settings.IMAGE_API_MAX_RETRIES or 1))
    read_timeout = max(15, int(settings.IMAGE_RESULT_DOWNLOAD_TIMEOUT_SECONDS or 60))
    timeout = httpx.Timeout(
        float(read_timeout),
        connect=min(20.0, float(read_timeout)),
        read=float(read_timeout),
        write=20.0,
        pool=20.0,
    )
    for attempt in range(1, max_attempts + 1):
        _abort_cover_generation_if_requested(should_abort, stage="download image result")
        try:
            with httpx.Client(
                timeout=timeout,
                proxy=_proxy_for_base_url(url) or None,
                follow_redirects=True,
            ) as http:
                img = http.get(url)
                img.raise_for_status()
                if not img.content:
                    raise RuntimeError("图像结果下载为空")
                _abort_cover_generation_if_requested(should_abort, stage="after download image result")
                return img.content
        except (httpx.HTTPError, RuntimeError) as exc:
            last_error = exc
            if attempt >= max_attempts:
                raise ImageApiTransientError(f"图像结果下载失败: {exc}，已重试 {attempt} 次") from exc
            sleep_until = time.monotonic() + min(10, 2 * attempt)
            while time.monotonic() < sleep_until:
                _abort_cover_generation_if_requested(should_abort, stage="wait retry image result download")
                time.sleep(min(0.2, max(0.0, sleep_until - time.monotonic())))
    raise ImageApiTransientError(f"图像结果下载失败: {last_error}")


def _abort_cover_generation_if_requested(
    should_abort: Callable[[], bool] | None,
    *,
    stage: str,
) -> None:
    if should_abort is not None and should_abort():
        raise CoverGenerationCancelled(f"封面生成已取消：{stage}")


def _write_cover_text_file(path: Path, text: str, should_abort: Callable[[], bool] | None = None) -> None:
    _abort_cover_generation_if_requested(should_abort, stage=f"write {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_cover_bytes_file(path: Path, data: bytes, should_abort: Callable[[], bool] | None = None) -> None:
    _abort_cover_generation_if_requested(should_abort, stage=f"write {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_bytes(data)
    except FileNotFoundError as exc:
        if should_abort is not None and should_abort():
            raise CoverGenerationCancelled(f"封面生成已取消：write {path.name}") from exc
        raise


def _read_image_response_cache(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImageApiPermanentError(f"cached image API response is unreadable: {path.name}") from exc


def _write_image_response_cache(
    path: Path,
    response_data: Any,
    should_abort: Callable[[], bool] | None = None,
) -> None:
    try:
        payload = json.dumps(response_data, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ImageApiPermanentError("image API response could not be cached safely") from exc
    _write_cover_text_file(path, payload, should_abort)


def _request_or_reuse_image_response(
    response_path: Path,
    request_response: Callable[[], Any],
    *,
    should_abort: Callable[[], bool] | None = None,
    force: bool = False,
) -> bytes:
    if not force and response_path.exists() and response_path.stat().st_size > 0:
        logger.info("reusing cached image API response: %s", response_path)
        response_data = _read_image_response_cache(response_path)
    else:
        response_data = request_response()
        _write_image_response_cache(response_path, response_data, should_abort)
    return _decode_image_response(response_data, should_abort=should_abort)


def generate_image_file_from_prompt(
    prompt: str,
    output_path: Path,
    *,
    response_cache_path: Path | None = None,
    force: bool = False,
    should_abort: Callable[[], bool] | None = None,
    gate_wait_timeout_seconds: int | None = None,
) -> Path:
    """Generate one image from a caller-supplied prompt with response caching."""
    if output_path.exists() and output_path.stat().st_size > 0 and not force:
        return output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    response_path = response_cache_path or output_path.with_suffix(".response.json")
    image_bytes = _request_or_reuse_image_response(
        response_path,
        lambda: _request_image_generation_response(
            prompt,
            should_abort=should_abort,
            gate_wait_timeout_seconds=gate_wait_timeout_seconds,
        ),
        should_abort=should_abort,
        force=force,
    )
    _write_cover_bytes_file(output_path, image_bytes, should_abort)
    return output_path


def generate_cover_file(
    title: str,
    markdown: str,
    output_dir: Path,
    *,
    category: str | None = None,
    author: str | None = None,
    intro: str | None = None,
    force: bool = False,
    should_abort: Callable[[], bool] | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> Path:
    """Generate draft and refined cover images under the task output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    _abort_cover_generation_if_requested(should_abort, stage="start")
    _notify_cover_progress(on_progress, "preparing")
    existing_cover_path = output_dir / "cover.png"
    if existing_cover_path.exists() and existing_cover_path.stat().st_size > 0 and not force:
        logger.info("复用已存在封面: %s", existing_cover_path)
        return existing_cover_path

    draft_path = output_dir / "draft_cover.png"
    refine_prompt_path = output_dir / "cover_refine_prompt.txt"
    cover_path = output_dir / "cover.png"
    draft_response_path = output_dir / "cover_draft_response.json"
    refine_response_path = output_dir / "cover_refine_response.json"
    if (
        not force
        and draft_path.exists()
        and draft_path.stat().st_size > 0
        and refine_prompt_path.exists()
        and refine_prompt_path.stat().st_size > 0
    ):
        refine_prompt = refine_prompt_path.read_text(encoding="utf-8")
        logger.info("reusing existing draft and refine prompt for cover retry: %s", draft_path)
        _notify_cover_progress(on_progress, "refine")
        _abort_cover_generation_if_requested(should_abort, stage="before refine image request")
        cover_bytes = _request_or_reuse_image_response(
            refine_response_path,
            lambda: _request_image_edit_response(draft_path, refine_prompt, should_abort=should_abort),
            should_abort=should_abort,
        )
        _write_cover_bytes_file(cover_path, cover_bytes, should_abort)
        return cover_path

    cover_input = prepare_cover_input(
        title,
        markdown,
        author=author,
        intro=intro,
        category=category,
    )
    style_brief = build_cover_style_brief(cover_input)

    style_brief_path = output_dir / "cover_style_brief.json"
    draft_prompt_path = output_dir / "cover_draft_prompt.txt"
    refine_prompt_path = output_dir / "cover_refine_prompt.txt"
    draft_path = output_dir / "draft_cover.png"
    cover_path = output_dir / "cover.png"

    if cover_path.exists() and cover_path.stat().st_size > 0 and not force:
        logger.info("复用已存在封面: %s", cover_path)
        return cover_path

    _write_cover_text_file(style_brief_path, _brief_json(style_brief), should_abort)

    draft_prompt = build_draft_cover_prompt(cover_input, style_brief)
    _write_cover_text_file(draft_prompt_path, draft_prompt, should_abort)
    if force or not draft_path.exists() or draft_path.stat().st_size <= 0:
        logger.info("开始生成封面初稿: model=%s size=%s title=%s", settings.IMAGE_MODEL, settings.IMAGE_SIZE, title)
        _notify_cover_progress(on_progress, "draft")
        _abort_cover_generation_if_requested(should_abort, stage="before draft image request")
        draft_bytes = _request_or_reuse_image_response(
            draft_response_path,
            lambda: _request_image_generation_response(draft_prompt, should_abort=should_abort),
            should_abort=should_abort,
            force=force,
        )
        _write_cover_bytes_file(draft_path, draft_bytes, should_abort)
    else:
        logger.info("复用已存在封面初稿，直接进入精修: %s", draft_path)

    refine_prompt = build_refine_cover_prompt(cover_input, style_brief)
    _write_cover_text_file(refine_prompt_path, refine_prompt, should_abort)
    logger.info("开始精修封面: model=%s size=%s title=%s", settings.IMAGE_MODEL, settings.IMAGE_SIZE, title)
    try:
        _notify_cover_progress(on_progress, "refine")
        _abort_cover_generation_if_requested(should_abort, stage="before refine image request")
        cover_bytes = _request_or_reuse_image_response(
            refine_response_path,
            lambda: _request_image_edit_response(draft_path, refine_prompt, should_abort=should_abort),
            should_abort=should_abort,
            force=force,
        )
        _write_cover_bytes_file(cover_path, cover_bytes, should_abort)
    except CoverGenerationCancelled:
        raise
    except DistributedLimitError:
        raise
    except ImageApiTransientError as exc:
        logger.warning("封面精修遇到临时错误，保留初稿等待自动重试: %s", exc)
        _write_cover_text_file(
            output_dir / "cover_refine_error.txt",
            f"临时错误，等待自动重试：{exc}",
            should_abort,
        )
        raise
    except ImageApiPermanentError as exc:
        logger.warning("封面精修遇到永久错误，保留初稿但不降级为最终封面: %s", exc)
        _write_cover_text_file(output_dir / "cover_refine_error.txt", str(exc), should_abort)
        raise
    except Exception as exc:
        logger.warning("封面精修失败，保留初稿但不降级为最终封面: %s", exc)
        _write_cover_text_file(output_dir / "cover_refine_error.txt", str(exc), should_abort)
        raise RuntimeError(
            f"封面精修失败：{exc}。已保留 draft_cover.png 供排查，未生成最终 cover.png"
        ) from exc

    return cover_path


def _notify_cover_progress(on_progress: Callable[[str], None] | None, stage: str) -> None:
    if on_progress is None:
        return
    try:
        on_progress(stage)
    except Exception:
        logger.warning("cover progress callback failed stage=%s", stage, exc_info=True)


_METADATA_TITLE_KEYS = (
    "title",
    "book_title",
    "book title",
    "name",
    "书名",
    "标题",
    "图书名",
)
_METADATA_AUTHOR_KEYS = ("author", "writer", "作者", "作家")
_METADATA_INTRO_KEYS = (
    "intro",
    "summary",
    "description",
    "synopsis",
    "subtitle",
    "卖点",
    "简介",
    "内容简介",
    "短简介",
)
_METADATA_CATEGORY_KEYS = (
    "category",
    "bookshelf",
    "type",
    "分类",
    "书架",
    "题材",
    "类型",
)


_METADATA_TITLE_KEYS = tuple(dict.fromkeys((*_METADATA_TITLE_KEYS, "书名", "标题", "图书名", "作品名")))
_METADATA_AUTHOR_KEYS = tuple(dict.fromkeys((*_METADATA_AUTHOR_KEYS, "作者", "作家", "笔名")))
_METADATA_INTRO_KEYS = tuple(
    dict.fromkeys(
        (
            *_METADATA_INTRO_KEYS,
            "简介",
            "内容简介",
            "书籍简介",
            "图书简介",
            "短简介",
            "卖点",
            "副标题",
            "brief",
            "book_intro",
        )
    )
)
_METADATA_CATEGORY_KEYS = tuple(dict.fromkeys((*_METADATA_CATEGORY_KEYS, "分类", "书架", "题材", "类型")))


def _metadata_lookup(metadata: dict[str, Any], keys: tuple[str, ...]) -> str:
    lowered = {
        str(key).strip().lower().replace(" ", "_"): str(value).strip()
        for key, value in metadata.items()
        if value is not None and str(value).strip()
    }
    for key in keys:
        value = lowered.get(key.strip().lower().replace(" ", "_"))
        if value:
            return value
    return ""


def _metadata_category(value: str) -> str:
    if not value:
        return "social_science"
    try:
        return normalize_cover_category_id(value)
    except ValueError:
        logger.warning("unknown cover category %r, fallback to social_science", value)
        return "social_science"


def generate_cover_file_from_metadata(
    metadata: dict[str, Any],
    output_path: Path,
    *,
    should_abort: Callable[[], bool] | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> Path:
    """Generate one standalone cover from spreadsheet metadata.

    The standalone Excel entry intentionally reuses the same high-quality
    draft + refine cover workflow used by manuscript tasks. It writes to a
    per-item work directory first, then copies the final cover to output_path.
    """
    _abort_cover_generation_if_requested(should_abort, stage="metadata start")
    if output_path.exists() and output_path.stat().st_size > 0:
        logger.info("复用已存在独立封面: %s", output_path)
        return output_path

    title = _metadata_lookup(metadata, _METADATA_TITLE_KEYS) or output_path.stem
    author = _metadata_lookup(metadata, _METADATA_AUTHOR_KEYS) or None
    intro = _metadata_lookup(metadata, _METADATA_INTRO_KEYS) or title
    category = _metadata_category(_metadata_lookup(metadata, _METADATA_CATEGORY_KEYS))
    markdown = f"@最终书名\n{title}\n\n@最终简介\n{intro}\n\n@正文\n{intro}\n"
    work_dir = output_path.parent / f".{output_path.stem}_work"
    markdown = f"@最终书名\n{title}\n\n@作者\n{author or ''}\n\n@最终简介\n{intro}\n\n@正文\n{intro}\n"
    _abort_cover_generation_if_requested(should_abort, stage="prepare work dir")
    work_dir.mkdir(parents=True, exist_ok=True)
    category_meta = get_cover_category_meta(category)
    _write_cover_text_file(
        work_dir / "metadata_used.json",
        json.dumps(
            {
                "title": title,
                "author": author or "",
                "intro": intro,
                "category": category,
                "category_label": category_meta["label"],
                "source_metadata": metadata,
            },
            ensure_ascii=False,
            indent=2,
        ),
        should_abort,
    )
    generated_path = generate_cover_file(
        title=title,
        markdown=markdown,
        output_dir=work_dir,
        category=category,
        author=author,
        intro=intro,
        force=False,
        should_abort=should_abort,
        on_progress=on_progress,
    )
    _notify_cover_progress(on_progress, "copy")
    _abort_cover_generation_if_requested(should_abort, stage="copy final cover")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_cover_bytes_file(output_path, generated_path.read_bytes(), should_abort)
    return output_path


__all__ = [
    "ALLOWED_REFERENCE_BRANDS",
    "ALLOWED_REFERENCE_SITE",
    "CoverInput",
    "CoverGenerationCancelled",
    "CoverMarketMatch",
    "CoverStyleBrief",
    "ImageApiError",
    "ImageApiPermanentError",
    "ImageApiTransientError",
    "ImageApiUncertainError",
    "analyze_cover_market_match",
    "build_cover_prompt",
    "build_cover_style_brief",
    "build_draft_cover_prompt",
    "build_refine_cover_prompt",
    "generate_cover_file",
    "generate_cover_file_from_metadata",
    "generate_image_file_from_prompt",
    "get_cover_category_prompt_adjustment",
    "observe_image_requests",
    "prepare_cover_input",
]
