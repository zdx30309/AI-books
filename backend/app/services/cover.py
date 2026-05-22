"""Three-stage book cover generation."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import queue
import random
import threading
import time
import zipfile
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
    "正统出版版式迁移：生成封面时，以用户当前提供的书名、作者、分类和简介为主；封面的版式结构应优先参考素材库中相近题材、相近气质、相近商业位置的成熟封面，不要绑定任何固定平台系列的气质。\n"
    "不要只抽象学习“出版感”。生成前必须先从参考图库中选择一张最适合本书的封面作为主要版式参考，不要自行发明新构图；内部可以逐个尝试不同正统出版 prompt 方向，比较后只输出最像真实出版物、最适合本书的一版。\n"
    "可以高相似度参考：标题所在区域、标题大小关系、标题分行方式、作者名位置、副标题位置、留白比例、主视觉占比、主视觉与标题的相对关系、背景色块关系、整体版面重心、手机书架缩略图识别度。\n"
    "允许排版和配色与参考封面接近，尤其标题位置、留白节奏、主视觉比例、色块关系和整体封面气质可以尽量贴近素材库中的成熟案例。\n"
    "主视觉必须根据当前书名和简介重新选择；可以沿用参考封面的主视觉位置、面积比例和图文关系，但具体内容要与当前书相关，不能为了原创而完全脱离参考图库。\n"
    "配色可以参考素材库中的成熟封面，允许使用相近的色彩气质、明度关系、冷暖关系和对比方式；但不要完全照搬某一本封面的完整配色比例，主色、辅助色、点缀色中至少调整一处来适配当前书。\n"
    "标题是第一识别信息，必须清晰、有层级，适合手机书架缩略图识别；不要添加大段简介、营销金句、课程口号或无意义小字。\n"
    "不要随意添加无意义的横线、竖线、圆点、印章、编号等装饰符号；所有装饰元素都必须服务于标题层级、版面结构或主视觉关系，不要为了填补留白或制造高级感而补线、补点、补符号。\n"
    "允许有纸感，但纸张质感必须干净、平整、细腻；不要让文字和背景变脏、破损、坑洼，不要斑驳、脏点、划痕、掉墨、褶皱、旧纸感或做旧效果，文字区域保持纯净清晰，不要让肌理干扰阅读。\n"
    "版权边界：可以明显借鉴某个参考封面的排版，甚至像同一类封面；但不能让人觉得只是把某一本封面换了书名、换了作者、换了一个相似图案，不能复制真实书名、出版社信息、logo、二维码、价格、水印、已有摄影作品、已有插画作品、可识别真实人物肖像或某一本封面的完整构图组合。\n"
    "删除所有出版社 logo、出版品牌 logo、第三方平台 logo、原出版信息、二维码、价格、促销标签、真实书名残影、乱码文字、水印。\n"
    "禁止真实品牌背书、真实书名残影。\n"
    "不要灰蒙蒙、不要发黄做旧、不要低饱和寡淡；最终效果应像从素材库中选了一张成熟出版封面作为版式母版，再根据当前书名和简介重新设计出的新封面。"
)


def _load_reference_markdown(filename: str) -> str:
    path = Path(__file__).with_name(filename)
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


_LITERATURE_COVER_REFERENCE = _load_reference_markdown("literature_cover_reference.md")
_LITERATURE_REFERENCE_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
_LITERATURE_REFERENCE_BOARD_LIMIT = 12
_LITERATURE_REFERENCE_BOARD_COLUMNS = 4
_LITERATURE_REFERENCE_CELL_SIZE = (260, 380)


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
        "brand": "正统出版长期素材库",
        "style": "modern publishing-grade, premium, bright, clean, vivid, thumbnail-readable",
        "cover_type": "正统出版 / 现代高级出版封面",
        "colors": ("mature publishing color mood", "clean paper texture", "vivid but not muddy", "thumbnail-readable contrast"),
        "composition": "strong reference to mature publishing layouts；clear title hierarchy；adapted visual content for this book",
        "typography": "large crisp elegant modern Chinese typography；subtitles and author names minimalist and restrained",
        "white_space": "reference mature cover whitespace rhythm and title-image relationship",
        "metaphors": ("book-specific object", "adapted mature cover structure", "publishing-grade visual relationship", "clean paper texture"),
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


_LITERATURE_LAYOUT_VARIANTS = (
    "版式方向A：明显参考成熟封面的上方大标题、下方主视觉和大留白关系，具体图形按本书内容重做。",
    "版式方向B：参考成熟封面的侧边标题秩序和另一侧图像留白，文字位置可以大胆靠近参考版式。",
    "版式方向C：让超大标题成为画面主体，主视觉用局部穿插、边角压入或色块承载来形成出版感。",
    "版式方向D：参考真实书封里空间、窗、房间、站台、门框、建筑局部与标题的相对关系，但替换成符合本书的场景。",
    "版式方向E：参考物件特写型书封的主视觉占比和标题节奏，用本书相关物件、纸页、织物、玻璃、票据或细节重组。",
    "版式方向F：参考色块承载标题、插画与背景融合、主体压在边角等成熟组织方式，保留出版秩序而重做具体内容。",
)
_LITERATURE_TEMPLATE_BAN = (
    "不要无依据地默认套用太阳、夕阳、日出圆盘、弯曲白路、绿色山坡、远山、飞鸟、小人走在路上、"
    "几何门洞、光束、人影、蓝黄对比、古风水彩、泛黄纸张等通用模板；如果书名或简介确实需要对应意象，必须按本书重新组织版式、配色和细节。"
)
_LITERATURE_COLOR_VARIANTS = (
    "背景色方向：成熟封面常见的暖白、纸白或浅色底，加一个高识别点色",
    "背景色方向：清透蓝、冷白和少量深色文字，保持缩略图清楚",
    "背景色方向：鲜明黄、金色或暖色底，但不要发黄做旧",
    "背景色方向：干净玫红、酒红或珊瑚色，配克制深色标题",
    "背景色方向：薄荷绿、青绿色或孔雀绿，保持出版级干净度",
    "背景色方向：黑白高对比加一个小面积亮色，文字层级必须稳定",
    "背景色方向：淡紫、蓝紫或灰粉，但必须清透，不要灰蒙蒙",
)
_LITERATURE_ELEMENT_VARIANTS = (
    "元素方向：从简介里提炼室内物件或生活细节，放入成熟出版版式中重新组合。",
    "元素方向：从简介里提炼城市、建筑、窗口、门、楼梯或站台局部，主视觉占比可参考成熟书封。",
    "元素方向：从简介里提炼手势、背影、衣物、票据或纸页等人物相关局部，不使用真实人脸。",
    "元素方向：从简介里提炼植物、花、叶、树影或自然局部，避免变成泛泛古风风景。",
    "元素方向：从简介里提炼抽象关系物，例如线、圆环、裂缝、折痕、玻璃反射或空椅子，必须具体可识别。",
    "元素方向：从简介里提炼旅行或时间物件，例如车窗、站牌、地图折痕、行李牌、钟面或车票，不做通用换皮模板。",
)


def _stable_variant(options: tuple[str, ...], seed: str, salt: str) -> str:
    digest = hashlib.sha256(f"{salt}:{seed}".encode("utf-8")).hexdigest()
    return options[int(digest[:8], 16) % len(options)]


def _literature_fallback_visual_plan(cover_input: CoverInput) -> str:
    seed = f"{cover_input.title}|{cover_input.author}|{cover_input.intro}|{cover_input.category_label}"
    layout = _stable_variant(_LITERATURE_LAYOUT_VARIANTS, seed, "layout")
    color = _stable_variant(_LITERATURE_COLOR_VARIANTS, seed, "color")
    element = _stable_variant(_LITERATURE_ELEMENT_VARIANTS, seed, "element")
    return (
        f"本书强参考视觉方案：{layout}；{color}；{element} "
        "先保证版式像成熟出版物，再把具体图形、标题分行、局部细节和背景质感改成当前书专属。"
        "可以有干净轻微纸感，但文字和背景不能脏、破损、坑洼或旧化到影响阅读。"
        f"{_LITERATURE_TEMPLATE_BAN}"
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


def _literature_reference_zip_path() -> Path:
    service_dir = Path(__file__).resolve().parent
    candidates = (
        service_dir / "reference_assets" / "orthodox_publishing" / "reference_covers.zip",
        service_dir / "reference_assets" / "healing_comic" / "reference_covers.zip",
    )
    for path in candidates:
        if path.exists() and path.stat().st_size > 0:
            return path
    return candidates[0]


def _literature_reference_members(zip_path: Path) -> tuple[str, ...]:
    if not zip_path.exists():
        return ()
    try:
        with zipfile.ZipFile(zip_path) as archive:
            return tuple(
                sorted(
                    info.filename
                    for info in archive.infolist()
                    if not info.is_dir()
                    and Path(info.filename).suffix.lower() in _LITERATURE_REFERENCE_IMAGE_EXTENSIONS
                    and info.file_size > 0
                )
            )
    except (OSError, zipfile.BadZipFile) as exc:
        logger.warning("正统出版参考封面 ZIP 不可读: %s", exc)
        return ()


def _selected_literature_reference_members(
    cover_input: CoverInput,
    *,
    limit: int = _LITERATURE_REFERENCE_BOARD_LIMIT,
) -> tuple[str, ...]:
    members = _literature_reference_members(_literature_reference_zip_path())
    if not members:
        return ()
    seed = f"{cover_input.title}|{cover_input.author}|{cover_input.intro}|{cover_input.category_label}"
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    start = int.from_bytes(digest[:4], "big") % len(members)
    step = (int.from_bytes(digest[4:8], "big") % max(1, len(members) - 1)) + 1
    selected: list[str] = []
    seen: set[str] = set()
    index = start
    while len(selected) < min(limit, len(members)):
        item = members[index % len(members)]
        if item not in seen:
            selected.append(item)
            seen.add(item)
        index += step
    return tuple(selected)


def collect_literature_reference_covers(
    cover_input: CoverInput,
    limit: int = _LITERATURE_REFERENCE_BOARD_LIMIT,
) -> tuple[ReferenceCover, ...]:
    zip_path = _literature_reference_zip_path()
    members = _selected_literature_reference_members(cover_input, limit=limit)
    return tuple(
        ReferenceCover(
            brand="正统出版ZIP参考封面库",
            title=Path(member).stem,
            product_url=f"zip://{zip_path.name}!/{member}",
            image_url=f"zip://{zip_path.name}!/{member}",
            source="local_zip",
            product_id=hashlib.sha1(member.encode("utf-8")).hexdigest()[:12],
            source_page=zip_path.name,
            local_image_path=f"{zip_path}!{member}",
        )
        for member in members
    )


def _build_literature_reference_board(
    cover_input: CoverInput,
    output_dir: Path,
    should_abort: Callable[[], bool] | None = None,
) -> Path:
    zip_path = _literature_reference_zip_path()
    members = _selected_literature_reference_members(cover_input)
    if not zip_path.exists() or not members:
        raise ImageApiPermanentError(
            f"正统出版参考封面 ZIP 缺失或为空，无法按素材库样式生成: {zip_path}"
        )

    try:
        from PIL import Image, ImageOps
    except ImportError as exc:  # pragma: no cover - protected by requirements
        raise ImageApiPermanentError("Pillow is required to build orthodox reference board") from exc

    columns = _LITERATURE_REFERENCE_BOARD_COLUMNS
    cell_w, cell_h = _LITERATURE_REFERENCE_CELL_SIZE
    rows = (len(members) + columns - 1) // columns
    board = Image.new("RGB", (columns * cell_w, rows * cell_h), "white")
    with zipfile.ZipFile(zip_path) as archive:
        for index, member in enumerate(members):
            _abort_cover_generation_if_requested(should_abort, stage="read orthodox reference zip")
            with archive.open(member) as fp:
                image = Image.open(io.BytesIO(fp.read()))
                image.load()
            thumb = ImageOps.contain(image.convert("RGB"), (cell_w - 16, cell_h - 16))
            x = (index % columns) * cell_w + (cell_w - thumb.width) // 2
            y = (index // columns) * cell_h + (cell_h - thumb.height) // 2
            board.paste(thumb, (x, y))

    output_dir.mkdir(parents=True, exist_ok=True)
    board_path = output_dir / "orthodox_reference_board.png"
    _abort_cover_generation_if_requested(should_abort, stage="write orthodox reference board")
    board.save(board_path, format="PNG", optimize=True)
    return board_path


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
                "强参考本地正统出版长期封面库和成熟出版封面方法，不再引用固定平台系列风格："
                "先从参考图库中选择一张最适合本书的封面作为主要版式参考，不要自行发明新构图；"
                "标题区域、字号层级、留白比例、主视觉占比、图文关系、配色气质和手机书架缩略图识别度可以明显靠近参考图；"
                "生成前逐个尝试强标题、色块标题、边角主视觉、物件特写、抽象文学和摄影感平面等 prompt 方向，最终只保留最佳一版；"
                "具体图形、书名信息、主视觉内容和局部细节必须按当前书重做，纸感允许但文字和背景要干净清晰。"
            ),
            visual_plan=_literature_fallback_visual_plan(cover_input),
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
        collect_literature_reference_covers(cover_input)
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
        visual_variation_directive = market_match.visual_plan.strip()
        avoid_rules = (
            "强参考用户提供的正统出版长期封面库和成熟出版封面方法；不要引用固定平台系列作为风格来源；必须先从参考图库中选择一张最适合本书的封面作为主要版式参考，不要自行发明新构图。",
            "可以明显靠近参考图的标题所在区域、标题大小关系、标题分行方式、作者名位置、副标题位置、留白比例、主视觉占比、主视觉与标题的相对关系、背景色块关系和整体版面重心。",
            "不要把某一本真实封面做成换书名、换作者、换元素的版本；不要复制真实封面的完整构图组合；不要出现出版社 logo、出版品牌 logo、第三方平台 logo、原出版信息、二维码、价格、水印、真实品牌背书或真实书名残影。",
            "不要生成乱码中文、英文占位符、真实人物肖像、名人脸、可识别照片脸。",
            "paper texture is allowed only if clean and readable; avoid dirty paper, broken type, pitted background, moldy texture, retro dusty filter, low-contrast, muddy colors, heavy shadows, fake logos, barcodes, price tags, gibberish text, watermark.",
            _NO_REAL_PORTRAIT_RULE,
            *style_brief_rules,
        )
        color_palette = tuple(profile["colors"])
        reference_site = "本地正统出版ZIP参考封面库 + 成熟出版版式迁移"
        brand_style_direction = (
            "生成封面只围绕正统出版长期素材库、成熟出版封面方法和版式迁移 prompt 完成："
            "先选择一张最适合本书的素材库封面作为主要版式母版，真实迁移标题层级、稳定留白节奏、主视觉比例、色块关系和干净纸感；最终只输出最佳一版。"
        )
        title_hierarchy = (
            "标题使用大而清晰、优雅、结构稳定的现代中文出版字体；"
            "副标题和作者名极简克制，缩略图中仍能读清书名。"
        )
        generation_prompt_addendum = (
            f"{profile['addendum']}\n"
            f"参考来源：本地正统出版ZIP参考封面库 + 成熟出版版式迁移。\n"
            f"长期参考文件摘要：{_LITERATURE_COVER_REFERENCE[:900]}\n"
            f"本书视觉判断：{visual_variation_directive}\n"
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
        reference_brands=("正统出版ZIP参考封面库",) if is_literature else ALLOWED_REFERENCE_BRANDS,
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
    if is_literature:
        style_report = json.dumps(
            {
                "reference_site": style_brief.reference_site,
                "matched_category_style": style_brief.matched_category_style,
                "brand_style_direction": style_brief.brand_style_direction,
                "visual_variation_directive": style_brief.visual_variation_directive,
            },
            ensure_ascii=False,
            indent=2,
        )
    category_rule_section = _prompt_rule_section(
        get_cover_category_prompt_adjustment(cover_input.category)["draft_rules"]
    )
    if is_literature:
        category_rule_section = ""
    style_addendum_limit = 2200 if is_literature else 1400
    style_addendum_section = style_brief.generation_prompt_addendum[:style_addendum_limit].rstrip()
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
        visual_variation_section = f"{style_brief.visual_variation_directive}\n"
        market_rule_section = (
            "- 工作流只参考本地正统出版长期素材库、成熟出版封面方法，以及下面的正统出版版式迁移 prompt；不要再引用固定平台系列作为风格来源。\n"
            "- 最高优先级：不要只抽象学习出版感；必须先从参考图库中选择一张最适合本书的封面作为主要版式参考，不要自行发明新构图。\n"
            "- 版式迁移：尽量贴近真实出版封面的排版质感；标题所在区域、标题大小关系、标题分行方式、作者名位置、副标题位置、留白比例、主视觉占比、主视觉与标题关系、背景色块关系、整体版面重心可以明显靠近参考图。\n"
            "- 风格试验：内部逐个尝试强标题留白型、色块标题型、主视觉边角型、物件特写型、抽象文学型、摄影感平面型，比较后只输出最适合本书的一版。\n"
            "- 安全边界：不要把某一本真实封面简单做成换书名、换作者、换元素的版本；不要复制真实封面的完整构图组合。\n"
            "- 目标不是随机好看的 AI 图，而是像真实书架里的成熟出版物：信息组织清楚、标题层级明确、留白服务版面节奏、手机书架缩略图可读。\n"
        )
        color_and_type_rules = (
            "- 排版：可以高相似度参考长期素材库中成熟封面的标题位置、标题大小关系、标题分行方式、作者名位置、副标题位置、留白比例、文字层级、主视觉占比、主视觉与标题的相对关系、整体版面重心。\n"
            "- 背景与配色：允许使用与参考图相近的色彩气质、明度关系、冷暖关系和对比方式；但不要完全照搬某一本封面的完整配色比例，主色、辅助色、点缀色中至少调整一处。\n"
            "- 纸感：允许轻微纸感、纸张纹理和出版印刷质感，但必须干净、平整、细腻；不要坑洼、斑驳、脏点、划痕、掉墨、褶皱、旧纸感或做旧效果，文字区域保持纯净清晰。\n"
            "- 元素：主视觉必须根据当前书名和简介重新选择；可以沿用参考封面的主视觉位置、面积比例和图文关系，但具体内容要与当前书相关，不要照搬真实封面的具体图形、摄影、插画、人物姿态或符号组合。\n"
            "- 主视觉：优先保证封面看起来像素材库里的成熟出版物；如果参考图库中某种版式非常适合当前书，可以大胆靠近它，只在具体元素、细节和内容表达上做调整。\n"
            "- 文字与装饰：标题是第一识别信息，必须清晰、有层级，适合手机书架缩略图识别；不要大段简介、营销金句、课程口号、无意义小字，也不要为了填补留白或制造高级感而补线、补点、补符号。\n"
            "- 避免模板：同批封面不要无依据地默认套用太阳、夕阳、日出圆盘、弯曲白路、绿色山坡、远山、飞鸟、小人走在路上、几何门洞、光束、人影、蓝黄对比、大标题模板、古风水彩、泛黄纸张等通用模板；如果书名或简介确实需要对应意象，必须按本书重新组织版式、配色和细节。\n"
            "- 避免质感：不要知识付费海报感、PPT课程封面感、公众号长图感、电商商品图感、网文封面感、廉价AI插画感、过度3D感或促销广告感。\n"
            "- 版权：可以像某一类成熟出版封面，可以明显借鉴某种排版方式，但不要让人一眼认出是在改某一本具体封面；禁止真实品牌背书、真实书名残影。\n"
            "- 删除：所有出版社 logo、出版品牌 logo、第三方平台 logo、原出版信息、二维码、价格、促销标签、真实书名残影、乱码文字、水印。\n"
        )
        text_rule = "- 中文书名必须准确、清晰、现代、有出版级结构；字体可参考但必须重新设计，不要乱码中文、伪中文或英文占位符。\n"
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
        "- 不要出现出版社 logo、出版品牌 logo、第三方平台 logo、原出版信息、二维码、价格、促销标签、真实品牌背书、真实书名残影、乱码文字、水印。"
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
        visual_variation_section = f"{style_brief.visual_variation_directive}\n"
        opening = "请精修这张封面，让它更像长期素材库和成熟出版封面方法下的真实正统出版封面；不要引用固定平台系列作为风格来源。排版、配色、留白和标题层级可以明显靠近参考图，但具体内容必须按本书重做。"
        composition_rule = (
            "- 先判断这张封面应靠近素材库中的哪一张主要版式母版；保留并强化成熟出版封面的标题区域、字号层级、留白比例、主视觉占比、图文关系、背景色块关系和整体版面重心；"
            "同时调整主视觉内容、具体图形、人物或物件关系、标题分行细节、局部装饰和背景纹理，避免成为某一本真实封面的直接换皮。"
            "不要随意保留无意义的横线、竖线、圆点、印章、编号等装饰；装饰只服务标题层级、版面结构或主视觉关系，不要为了填补留白而补线、补点、补符号。"
        )
        market_rule = (
            "- 根据内容简介重新调整主视觉和局部细节；元素必须匹配简介里的主题、场景、情绪或核心意象。"
            "可以保留干净轻微纸感，但文字和背景不能脏、破损、坑洼、发霉、擦烂或旧化到影响阅读。"
            "不要灰蒙蒙、不要发黄做旧、不要低饱和寡淡。"
            "不要把文学感一律做成古风、水彩、远山、飞鸟、太阳、泛黄纸张；"
            "不要把现代出版感一律做成几何门洞、光束、人影、蓝黄对比或通用大标题模板；"
            "不要知识付费海报感、PPT课程封面感、公众号长图感、电商商品图感、网文封面感、廉价AI插画感、过度3D感或促销广告感。"
            "删除所有出版社 logo、出版品牌 logo、第三方平台 logo、原出版信息、二维码、价格、促销标签、真实书名残影、乱码文字、水印。"
            "禁止真实品牌背书、真实书名残影。"
        )
        deletion_rule = "- 删除所有出版社 logo、出版品牌 logo、第三方平台 logo、原出版信息、二维码、价格、促销标签、真实书名残影、乱码文字、水印；禁止真实品牌背书、真实书名残影。"
    else:
        title_author_rule_section = ""
        visual_variation_section = ""
        opening = "请精修这张封面，让它成为可直接商用、具有成熟出版质感的中文图书封面。"
        composition_rule = "- 保留草稿的核心视觉和书籍气质，但让构图更稳、标题更清晰、字体层级更专业。"
        market_rule = "- 强化微信读书/得到系爆款风：高饱和色块、强标题识别、明确留白、少量精准视觉隐喻，手机端缩略图也醒目。"
        deletion_rule = "- 删除所有出版社 logo、出版品牌 logo、微信读书/得到/当当 logo、原出版信息、二维码、价格、促销标签、真实书名残影、乱码文字、水印。"
    if _is_visual_impact(cover_input.category) or _is_crayon_illustration(cover_input.category):
        deletion_rule = "- 删除所有出版社 logo、出版品牌 logo、微信读书/得到/当当 logo、原出版信息、二维码、价格、促销标签、真实书名残影、乱码文字、水印。"
    if _is_literature_publishing(cover_input.category):
        refinement_quality_rules = (
            "- 如果画面灰蒙蒙、发黄做旧、低饱和寡淡，必须重新调整背景颜色，让画面更干净、有出版识别度。\n"
            "- 如果文字或背景出现脏污、破损、坑洼、发霉、擦烂、旧化过重，必须保留出版纸感但恢复干净清晰。\n"
            "- 如果元素和内容简介不匹配，必须替换为与简介主题、场景、情绪或核心意象匹配的原创元素。\n"
            "- 如果画面只是把某一本真实封面换书名、换作者、换元素，必须改变主视觉内容、具体图形、标题分行、局部装饰和背景细节。\n"
            "- 如果画面无依据套用了太阳道路、古风水彩、泛黄纸张、几何门洞、光束人影、蓝黄对比或通用大标题模板，必须按本书内容重构。\n"
        )
    else:
        refinement_quality_rules = (
            "- 如果画面偏暗，必须提亮并提升饱和度；灰紫、灰褐、灰蓝、脏米色、夜景暗色、大面积黑色/深蓝/暗红/深灰必须减少到 10% 以下，并改成明亮通透绘画色。\n"
            "- 参考出版社版本时也不能做得过黑过暗；深色只做局部压重，整体保持清楚、明亮、有出版质感。\n"
            "- 如果是蜡笔分类，背景必须是高饱和纯色，不能使用渐变背景、光效渐变、网格渐变或多色晕染。\n"
            "- 如果草稿是桌面、摊开的日记本、杯子、台灯、便签、碎纸和窗边静物的通用组合，必须重构为本书专属主意象、空间、视角和纯色背景；私人日记只保留为情绪气质。\n"
        )
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
{title_author_rule_section}{refinement_quality_rules.rstrip()}
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
    *,
    stage: str = "refine",
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
        "stage": stage,
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
                abort_exception_factory=lambda: _cover_cancelled(f"wait {stage} image gate"),
            ):
                response_data = _post_image_request_with_cancellation(
                    http,
                    endpoint,
                    api_key,
                    data=data,
                    files=files,
                    should_abort=should_abort,
                    stage=f"{stage} image request",
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
    _abort_cover_generation_if_requested(should_abort, stage=f"after {stage} image request")
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
    literature_reference_board_path: Path | None = None
    if _is_literature_publishing(cover_input.category):
        literature_reference_board_path = _build_literature_reference_board(
            cover_input,
            output_dir,
            should_abort=should_abort,
        )
        _write_cover_text_file(
            output_dir / "orthodox_reference_samples.json",
            json.dumps(style_brief.reference_samples, ensure_ascii=False, indent=2),
            should_abort,
        )
        draft_prompt = (
            f"{draft_prompt}\n\n"
            "ZIP参考封面图像输入规则：已随请求附上一张 orthodox_reference_board.png，"
            "它由参考封面(1).zip 中按本书稳定抽样的真实正统出版封面组成。"
            "必须把这张图当作最主要的版式和风格参考板，先从其中选择一张最适合本书的封面作为版式母版，"
            "迁移标题区域、字号层级、留白比例、主视觉占比、图文关系、背景色块关系和整体书架气质。"
            "最终只生成一张新的竖版中文图书正面封面，不要输出拼贴板、九宫格、多张小封面或参考图合集；"
            "不要复制参考图中的真实书名、logo、人物、摄影、插画和完整细节组合。"
        )
    _write_cover_text_file(draft_prompt_path, draft_prompt, should_abort)
    if force or not draft_path.exists() or draft_path.stat().st_size <= 0:
        logger.info("开始生成封面初稿: model=%s size=%s title=%s", settings.IMAGE_MODEL, settings.IMAGE_SIZE, title)
        _notify_cover_progress(on_progress, "draft")
        _abort_cover_generation_if_requested(should_abort, stage="before draft image request")
        if literature_reference_board_path is not None:
            draft_request = lambda: _request_image_edit_response(
                literature_reference_board_path,
                draft_prompt,
                should_abort=should_abort,
                stage="draft_reference",
            )
        else:
            draft_request = lambda: _request_image_generation_response(
                draft_prompt,
                should_abort=should_abort,
            )
        draft_bytes = _request_or_reuse_image_response(
            draft_response_path,
            draft_request,
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
