"""Three-stage book cover generation."""
from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.services.cover_categories import (
    get_cover_category_meta,
    list_cover_categories,
    normalize_cover_category_id,
)
from app.services.dangdang_crawler import collect_dangdang_reference_covers
from app.utils.distributed_limiter import DistributedLimitError, distributed_semaphore

logger = logging.getLogger(__name__)

ALLOWED_REFERENCE_SITE = "当当网"
ALLOWED_REFERENCE_BRANDS: tuple[str, ...] = ("酷威文化", "果麦文化", "时代华语")
_PLACEHOLDER_KEYS = {"", "sk-xxx", "sk-xxxx", "sk-xxxx..."}
_IMAGE_PROMPT_LIMIT = 5000
_SATURATED_COLOR_DIRECTION = (
    "以高饱和、明亮、强识别的出版色彩为主：电光蓝、亮青绿、柠檬黄、珊瑚橙、玫红、亮紫、奶白留白可组合使用。",
    "深色只能作为文字、线条、阴影或小面积压重，不允许大面积黑色、深蓝、暗红、深灰铺底。",
    "整体接近微信读书/得到系知识爆款封面：手机端缩略图也醒目，标题强、色块干净、排版有商业出版感。",
)
_NO_REAL_PORTRAIT_RULE = (
    "禁止真实人物肖像、名人脸、写实人像、可识别照片脸；人物只能用抽象剪影、背影、符号化小人或非写实插画表达。"
)
_WECHAT_DEDAO_STYLE_RULE = (
    "参考畅销书的配色与版式趋势，但只学习抽象规律：高饱和色块、明确标题层级、少而准的视觉隐喻、强留白或强对比；不得复刻任何真实封面。"
)
_HEALING_HANDDRAWN_STYLE_RULE = "治愈系手绘封面，日韩治愈插画风，图片避免抄袭和侵权。"
_CRAYON_ILLUSTRATION_STYLE_RULE = (
    "微信读书女性治愈系封面风格，日韩手绘插画，像情绪绘本与私人日记。"
    "油画棒、蜡笔、水粉质感，粗颗粒笔触，自然高饱和绘画色彩（草绿、橙花、天空蓝）。"
    "人物安静、脆弱、温柔，带一点孤独感。花朵、植物、风、云、猫等自然元素。"
    "书名字像手写在画上，与插画融合。避免心理学商业封面、避免得到系大标题、"
    "避免信息图、避免PPT感、避免门和小路构图、避免UI插画风。"
    "整体像一本被很多年轻女生收藏的治愈系绘本。"
)


class ImageApiError(RuntimeError):
    """Base exception for image API failures."""


class ImageApiTransientError(ImageApiError):
    """Image API failure that should be retried by the cover job queue."""


class ImageApiPermanentError(ImageApiError):
    """Image API failure that should be surfaced without automatic retry."""


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


_CATEGORY_STYLE_PROFILES: dict[str, dict[str, Any]] = {
    "social_science": {
        "brand": "时代华语",
        "style": "现实议题社科书，冷静、有判断力、有压迫感但不煽动",
        "cover_type": "社会痛点 / 现实议题出版封面",
        "colors": ("白灰黑", "深蓝", "暗红", "橙色点缀"),
        "composition": "中央强标题或上标题下隐喻物件，视觉主体克制，空间秩序清楚",
        "typography": "大号现代黑体或稳重宋黑混排，字距从容，标题必须可读",
        "white_space": "35%-55%",
        "metaphors": ("城市边界", "文件与表格", "阶梯", "房屋轮廓", "裂缝", "人物背影"),
        "addendum": "做成冷静的现实社科封面，像时代华语同类议题书的市场气质。",
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
        "style": "微信读书女性治愈系封面，日韩手绘插画，像情绪绘本与私人日记",
        "cover_type": "治愈插画（蜡笔） / 年轻女生收藏感情绪绘本封面",
        "colors": ("草绿", "橙花", "天空蓝", "自然高饱和绘画色", "米白"),
        "composition": "安静孤独的人物与花朵、植物、风、云、猫等自然元素融合，避免门和小路构图",
        "typography": "书名字像手写在画上，与插画融合，不像设计字体，避免得到系大标题",
        "white_space": "50%-70%",
        "metaphors": ("安静人物", "花朵", "植物", "风", "云", "猫", "私人日记", "粗颗粒笔触"),
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
_CATEGORY_COVER_PROMPT_ADJUSTMENTS["psychology_self_help"] = {
    "style_brief_rules": (_HEALING_HANDDRAWN_STYLE_RULE,),
    "draft_rules": (_HEALING_HANDDRAWN_STYLE_RULE,),
    "refine_rules": (f"继续保持：{_HEALING_HANDDRAWN_STYLE_RULE}",),
}
_CATEGORY_COVER_PROMPT_ADJUSTMENTS["illustration"] = _CATEGORY_COVER_PROMPT_ADJUSTMENTS[
    "psychology_self_help"
]
_CATEGORY_COVER_PROMPT_ADJUSTMENTS["illustration_crayon"] = {
    "style_brief_rules": (_CRAYON_ILLUSTRATION_STYLE_RULE,),
    "draft_rules": (_CRAYON_ILLUSTRATION_STYLE_RULE,),
    "refine_rules": (f"继续保持并强化：{_CRAYON_ILLUSTRATION_STYLE_RULE}",),
}


def _is_crayon_illustration(category: str | None) -> bool:
    try:
        return normalize_cover_category_id(category or "") == "illustration_crayon"
    except ValueError:
        return False


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
    if _is_crayon_illustration(cover_input.category):
        return CoverMarketMatch(
            category_label=cover_input.category_label,
            search_keywords=keywords[:240],
            analysis=(
                "参考微信读书女性治愈系封面：日韩手绘插画、情绪绘本与私人日记感、"
                "油画棒/蜡笔/水粉粗颗粒笔触、自然高饱和绘画色。"
                "禁止心理学商业封面、得到系大标题、信息图、PPT感、门和小路构图、UI插画风。"
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
    return "social_science=社会痛点；social_science_test=社会痛点测试版；life_philosophy=人生哲学；popular_science=科普商业；psychology_self_help=心理自助；illustration=治愈插画；illustration_crayon=治愈插画（蜡笔）"


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
    if not settings.COVER_DEEPSEEK_STYLE_ANALYSIS_ENABLED or not (settings.DEEPSEEK_API_KEY or "").strip():
        return _fallback_market_match(cover_input)

    if _is_crayon_illustration(cover_input.category):
        prompt = f"""你是中文出版封面市场分析师。请先根据 Excel 中的书名、简介、作者、分类判断这本书应参考哪类女性治愈系封面，再给当当/微信读书同类封面检索提供关键词。

书名：{cover_input.title}
作者：{cover_input.author}
简介：{cover_input.intro}
Excel 分类：{cover_input.category_label}
可选分类：{_category_options_for_market_prompt()}

只输出 JSON，不要输出解释文字：
{{
  "category_label": "最适合用于检索同类封面的中文分类名，优先贴近 Excel 分类",
  "search_keywords": "用于当当网与微信读书女性治愈系封面检索的中文关键词，80字以内",
  "analysis": "用120字以内总结女性治愈系封面的插画质感、配色、人物情绪和自然元素；必须强调参考微信读书女性治愈系，禁止心理学商业封面、得到系大标题、信息图、PPT感、门和小路构图、UI插画风"
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
            system_instruction="你只做分类匹配和市场风格分析，不写封面生成提示词，不生成图片。",
        )
        data = _json_object_from_text(raw)
        return CoverMarketMatch(
            category_label=str(data.get("category_label") or cover_input.category_label)[:80],
            search_keywords=str(data.get("search_keywords") or cover_input.title)[:240],
            analysis=str(data.get("analysis") or raw)[:900],
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
    image_key = (settings.IMAGE_API_KEY or "").strip()
    if image_key.lower() not in _PLACEHOLDER_KEYS and "xxx" not in image_key.lower():
        base_url = (settings.IMAGE_BASE_URL or settings.OPENAI_BASE_URL).rstrip("/")
        proxy = _proxy_for_base_url(base_url)
        return image_key, base_url, proxy
    openai_key = (settings.OPENAI_API_KEY or "").strip()
    if not openai_key:
        raise ValueError("IMAGE_API_KEY 未配置，且 OPENAI_API_KEY 也为空，无法生成封面")
    base_url = (settings.OPENAI_BASE_URL or "https://api.openai.com/v1").rstrip("/")
    proxy = (settings.OPENAI_HTTPS_PROXY or "").strip()
    return openai_key, base_url, proxy


def _proxy_for_base_url(base_url: str) -> str:
    lowered = base_url.lower()
    if "openai.com" in lowered:
        return (settings.OPENAI_HTTPS_PROXY or "").strip()
    if "googleapis.com" in lowered or "google.com" in lowered:
        return (settings.GOOGLE_HTTPS_PROXY or "").strip()
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
        author=_normalize_text(author or _extract_author(markdown), "作者名留白")[:80],
        intro=_normalize_text(intro or _extract_primary_intro(markdown), title)[:600],
        category=normalized_category,
        category_label=category_meta["label"],
    )




def build_cover_style_brief(cover_input: CoverInput) -> CoverStyleBrief:
    profile = _CATEGORY_STYLE_PROFILES.get(
        cover_input.category,
        _CATEGORY_STYLE_PROFILES["social_science"],
    )
    is_crayon = _is_crayon_illustration(cover_input.category)
    market_match = analyze_cover_market_match(cover_input)
    references = collect_reference_covers(cover_input, market_match=market_match)
    category_prompt_adjustment = get_cover_category_prompt_adjustment(cover_input.category)
    style_brief_rules = category_prompt_adjustment["style_brief_rules"]
    style_brief_addendum = "\n".join(style_brief_rules)
    if style_brief_addendum:
        style_brief_addendum += "\n"
    if is_crayon:
        avoid_rules = (
            "只学习当当网、微信读书女性治愈系封面的抽象审美，不复刻、不拼贴、不近似复制任何真实封面。",
            "不要出现出版社 logo、出版品牌 logo、原出版信息、二维码、价格、水印、真实品牌背书或真实书名残影。",
            "不要做成电商商品主图、3D 书模、白底商品照、赠品图、促销海报、心理学商业封面或电影海报。",
            "不要生成乱码中文、英文占位符、真实人物肖像、名人脸、可识别照片脸。",
            "不要得到系大标题、信息图、PPT感、门和小路构图、UI插画风。",
            _NO_REAL_PORTRAIT_RULE,
            *style_brief_rules,
        )
        color_palette = tuple(profile["colors"])
        reference_site = "当当网 + 微信读书女性治愈系封面分析"
        brand_style_direction = (
            "优先参考微信读书女性治愈系封面的抽象审美规律：情绪绘本、私人日记感、"
            "手绘颗粒质感、自然元素与温柔孤独人物；禁止得到系商业大标题和心理学爆款封面。"
        )
        title_hierarchy = "书名字像手写在画上，与插画融合；不要像设计字体，不做得到系大标题。"
        generation_prompt_addendum = (
            f"{profile['addendum']}\n"
            f"DeepSeek 分类/市场分析：{market_match.analysis}\n"
            f"{style_brief_addendum}"
            "必须强化微信读书女性治愈系封面风格、情绪绘本与私人日记感。"
            "禁止心理学商业封面、得到系大标题、信息图、PPT感、门和小路构图、UI插画风。"
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
        reference_brands=ALLOWED_REFERENCE_BRANDS,
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
    )


def _brief_json(brief: CoverStyleBrief) -> str:
    return json.dumps(asdict(brief), ensure_ascii=False, indent=2)










def build_draft_cover_prompt(cover_input: CoverInput, style_brief: CoverStyleBrief) -> str:
    style_report = _brief_json(style_brief)[:3200]
    category_rule_section = _prompt_rule_section(
        get_cover_category_prompt_adjustment(cover_input.category)["draft_rules"]
    )
    if _is_crayon_illustration(cover_input.category):
        market_rule_section = (
            "- 工作流第一步已经做了 DeepSeek 分类/市场判断，并检索当当网与微信读书同类封面。"
            "你只学习微信读书女性治愈系封面的抽象审美，不复刻、不拼贴、不近似复制任何真实封面。\n"
            "- 最高优先级：参考微信读书女性治愈系封面风格，做成情绪绘本与私人日记感；"
            "禁止心理学商业封面、得到系大标题、信息图、PPT感、门和小路构图、UI插画风。\n"
        )
        color_and_type_rules = (
            "- 使用油画棒、蜡笔、水粉质感，粗颗粒笔触，自然高饱和绘画色彩，优先草绿、橙花、天空蓝等绘画色。\n"
            "- 人物安静、脆弱、温柔，带一点孤独感；花朵、植物、风、云、猫等自然元素可以进入画面。\n"
            "- 书名字像手写在画上，与插画融合；不要像设计字体，不要得到系大标题，不要商业书封排版。\n"
        )
        text_rule = "- 中文书名尽量准确清晰，但必须像随手写进画面里的手写字；不要乱码中文、伪中文、英文占位符。\n"
    else:
        market_rule_section = (
            "- 工作流第一步已经做了 DeepSeek 分类/市场判断，并检索当当网同类畅销书，同时叠加微信读书/得到系爆款风格。"
            "你只学习抽象规律，不复刻、不拼贴、不近似复制任何真实封面。\n"
            "- 微信读书/得到系爆款风：高饱和、强识别、标题大而清晰、色块干净、手机端缩略图也醒目，排版要像成熟商业出版物。\n"
        )
        color_and_type_rules = (
            "- 配色要明亮且饱和，优先电光蓝、亮青绿、柠檬黄、珊瑚橙、玫红、亮紫、奶白留白等组合；不要大面积黑色、深蓝、暗红、深灰，深色面积尽量低于 20%。\n"
            "- 书名最大，作者名次之；简介只能提炼成一句很短副标题或封面文案，不要整段上封面。\n"
        )
        text_rule = "- 中文文字尽量准确清晰，像真实出版封面排版；不要乱码中文、伪中文、英文占位符。\n"
    return f"""你是资深中文出版封面设计师。请严格根据 Excel 提供的书名、作者、简介、分类，生成一张竖版中文图书正面封面。

书籍信息：
- 书名：{cover_input.title}
- 作者：{cover_input.author}
- 简介：{cover_input.intro}
- 分类：{cover_input.category_label}

必须优先遵守的硬规则：
{market_rule_section.rstrip()}
{category_rule_section.rstrip()}
{color_and_type_rules.rstrip()}
- 禁止真实人物肖像、名人脸、写实人像、可识别照片脸；如需人物，只能使用抽象剪影、背影、符号化小人或非写实插画。
{text_rule.rstrip()}
- 不要出现出版社 logo、出版品牌 logo、微信读书/得到/当当 logo、二维码、价格、水印、真实品牌背书、真实书名残影。
- 不要 3D 书模、书脊、白底商品照、赠品、贴纸角标、促销条、电影海报风、电商主图风。

参考风格报告：
{style_report}

输出：
完整正面封面本体，画面铺满整张竖版画布，推荐 2:3 或 3:4，可直接作为中文图书封面使用。"""


def build_refine_cover_prompt(cover_input: CoverInput, style_brief: CoverStyleBrief) -> str:
    category_rule_section = _prompt_rule_section(
        get_cover_category_prompt_adjustment(cover_input.category)["refine_rules"]
    )
    if _is_crayon_illustration(cover_input.category):
        opening = "请精修这张封面，让它成为温柔、私人、安静的微信读书女性治愈系情绪绘本封面。"
        composition_rule = (
            "- 保留草稿的核心情绪和手绘气质，让画面更松弛、更有人味；"
            "书名字像手写在画上，与插画融合，不像设计字体。"
        )
        market_rule = (
            "- 强化微信读书女性治愈系封面风格：日韩手绘插画、油画棒/蜡笔/水粉质感、"
            "粗颗粒笔触、草绿/橙花/天空蓝等自然高饱和绘画色、安静孤独人物与自然元素。"
            "避免心理学商业封面、得到系大标题、信息图、PPT感、门和小路构图、UI插画风。"
        )
    else:
        opening = "请精修这张封面，让它成为可直接商用、具有成熟出版质感的中文图书封面。"
        composition_rule = "- 保留草稿的核心视觉和书籍气质，但让构图更稳、标题更清晰、字体层级更专业。"
        market_rule = "- 强化微信读书/得到系爆款风：高饱和色块、强标题识别、明确留白、少量精准视觉隐喻，手机端缩略图也醒目。"
    return f"""{opening}

书籍信息：
- 书名：{cover_input.title}
- 作者：{cover_input.author}
- 简介：{cover_input.intro}
- 分类：{cover_input.category_label}

精修硬规则：
{composition_rule}
- 继续遵循：{style_brief.generation_prompt_addendum[:1400]}
{category_rule_section.rstrip()}
- 如果画面偏暗，必须提亮并提升饱和度；大面积黑色、深蓝、暗红、深灰应减少到 20% 以下。
{market_rule}
- 删除所有出版社 logo、出版品牌 logo、微信读书/得到/当当 logo、原出版信息、二维码、价格、促销标签、真实书名残影、乱码文字、水印。
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


def _request_image_generation(prompt: str) -> bytes:
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

    with httpx.Client(timeout=httpx.Timeout(600.0, connect=60.0), proxy=proxy or None) as http:
        with distributed_semaphore(
            "image-cover",
            limit=settings.IMAGE_MAX_CONCURRENT_CALLS,
            wait_timeout_seconds=settings.IMAGE_GATE_WAIT_TIMEOUT_SECONDS,
        ):
            response_data = _post_image_request(http, endpoint, api_key, json_payload=payload)
    return _decode_image_response(response_data)


def _request_image_edit(image_path: Path, prompt: str) -> bytes:
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

    with httpx.Client(timeout=httpx.Timeout(600.0, connect=60.0), proxy=proxy or None) as http:
        files = {
            "image": (
                image_path.name,
                image_path.read_bytes(),
                "image/png",
            )
        }
        with distributed_semaphore(
            "image-cover",
            limit=settings.IMAGE_MAX_CONCURRENT_CALLS,
            wait_timeout_seconds=settings.IMAGE_GATE_WAIT_TIMEOUT_SECONDS,
        ):
            response_data = _post_image_request(http, endpoint, api_key, data=data, files=files)
    return _decode_image_response(response_data)


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
        try:
            response = http.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}"},
                json=json_payload,
                data=data,
                files=files,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:1000]
            status_code = exc.response.status_code
            message = f"图像 API 请求失败 {status_code}: {body}"
            if status_code != 429 and status_code < 500:
                raise ImageApiPermanentError(message) from exc
            last_error = ImageApiTransientError(message)
            if attempt >= max_attempts:
                raise ImageApiTransientError(f"{message}，已重试 {attempt} 次") from exc
        except httpx.HTTPError as exc:
            last_error = ImageApiTransientError(f"图像 API 连接失败: {exc}")
            if attempt >= max_attempts:
                raise ImageApiTransientError(f"图像 API 连接失败: {exc}，已重试 {attempt} 次") from exc
        except (RuntimeError, ValueError) as exc:
            last_error = ImageApiTransientError(f"图像 API 返回异常: {exc}")
            if attempt >= max_attempts:
                raise ImageApiTransientError(f"图像 API 返回异常: {exc}，已重试 {attempt} 次") from exc
        time.sleep(min(30, 3 * attempt))
    raise ImageApiTransientError(f"图像 API 请求失败: {last_error}")


def _decode_image_response(data: Any) -> bytes:
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        raise RuntimeError(f"图像 API 返回缺少 data: {str(data)[:1000]}")
    item = items[0]
    b64 = item.get("b64_json") if isinstance(item, dict) else None
    if b64:
        return base64.b64decode(b64)

    url = item.get("url") if isinstance(item, dict) else None
    if url:
        return _download_image_result(url)
    raise RuntimeError("图像 API 返回值既无 b64_json 也无 url")


def _download_image_result(url: str) -> bytes:
    last_error: Exception | None = None
    max_attempts = max(1, int(settings.IMAGE_API_MAX_RETRIES or 1))
    timeout = httpx.Timeout(180.0, connect=30.0, read=180.0, write=30.0, pool=30.0)
    for attempt in range(1, max_attempts + 1):
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
                return img.content
        except (httpx.HTTPError, RuntimeError) as exc:
            last_error = exc
            if attempt >= max_attempts:
                raise ImageApiTransientError(f"图像结果下载失败: {exc}，已重试 {attempt} 次") from exc
            time.sleep(min(20, 2 * attempt))
    raise ImageApiTransientError(f"图像结果下载失败: {last_error}")


def generate_cover_file(
    title: str,
    markdown: str,
    output_dir: Path,
    *,
    category: str | None = None,
    author: str | None = None,
    intro: str | None = None,
    force: bool = False,
) -> Path:
    """Generate draft and refined cover images under the task output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    existing_cover_path = output_dir / "cover.png"
    if existing_cover_path.exists() and existing_cover_path.stat().st_size > 0 and not force:
        logger.info("复用已存在封面: %s", existing_cover_path)
        return existing_cover_path

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

    style_brief_path.write_text(_brief_json(style_brief), encoding="utf-8")

    draft_prompt = build_draft_cover_prompt(cover_input, style_brief)
    draft_prompt_path.write_text(draft_prompt, encoding="utf-8")
    if force or not draft_path.exists() or draft_path.stat().st_size <= 0:
        logger.info("开始生成封面初稿: model=%s size=%s title=%s", settings.IMAGE_MODEL, settings.IMAGE_SIZE, title)
        draft_path.write_bytes(_request_image_generation(draft_prompt))
    else:
        logger.info("复用已存在封面初稿，直接进入精修: %s", draft_path)

    refine_prompt = build_refine_cover_prompt(cover_input, style_brief)
    refine_prompt_path.write_text(refine_prompt, encoding="utf-8")
    logger.info("开始精修封面: model=%s size=%s title=%s", settings.IMAGE_MODEL, settings.IMAGE_SIZE, title)
    try:
        cover_path.write_bytes(_request_image_edit(draft_path, refine_prompt))
    except DistributedLimitError:
        raise
    except ImageApiTransientError as exc:
        logger.warning("封面精修遇到临时错误，保留初稿等待自动重试: %s", exc)
        (output_dir / "cover_refine_error.txt").write_text(
            f"临时错误，等待自动重试：{exc}",
            encoding="utf-8",
        )
        raise
    except ImageApiPermanentError as exc:
        logger.warning("封面精修遇到永久错误，保留初稿但不降级为最终封面: %s", exc)
        (output_dir / "cover_refine_error.txt").write_text(str(exc), encoding="utf-8")
        raise
    except Exception as exc:
        logger.warning("封面精修失败，保留初稿但不降级为最终封面: %s", exc)
        (output_dir / "cover_refine_error.txt").write_text(str(exc), encoding="utf-8")
        raise RuntimeError(
            f"封面精修失败：{exc}。已保留 draft_cover.png 供排查，未生成最终 cover.png"
        ) from exc

    return cover_path


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


def generate_cover_file_from_metadata(metadata: dict[str, Any], output_path: Path) -> Path:
    """Generate one standalone cover from spreadsheet metadata.

    The standalone Excel entry intentionally reuses the same high-quality
    draft + refine cover workflow used by manuscript tasks. It writes to a
    per-item work directory first, then copies the final cover to output_path.
    """
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
    work_dir.mkdir(parents=True, exist_ok=True)
    category_meta = get_cover_category_meta(category)
    (work_dir / "metadata_used.json").write_text(
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
        encoding="utf-8",
    )
    generated_path = generate_cover_file(
        title=title,
        markdown=markdown,
        output_dir=work_dir,
        category=category,
        author=author,
        intro=intro,
        force=False,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(generated_path.read_bytes())
    return output_path


__all__ = [
    "ALLOWED_REFERENCE_BRANDS",
    "ALLOWED_REFERENCE_SITE",
    "CoverInput",
    "CoverMarketMatch",
    "CoverStyleBrief",
    "ImageApiError",
    "ImageApiPermanentError",
    "ImageApiTransientError",
    "analyze_cover_market_match",
    "build_cover_prompt",
    "build_cover_style_brief",
    "build_draft_cover_prompt",
    "build_refine_cover_prompt",
    "generate_cover_file",
    "generate_cover_file_from_metadata",
    "get_cover_category_prompt_adjustment",
    "prepare_cover_input",
]
