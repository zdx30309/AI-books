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
from app.prompts.goal_driven_templates import get_category_meta, normalize_category
from app.services.dangdang_crawler import collect_dangdang_reference_covers
from app.utils.distributed_limiter import DistributedLimitError, distributed_semaphore

logger = logging.getLogger(__name__)

ALLOWED_REFERENCE_SITE = "当当网"
ALLOWED_REFERENCE_BRANDS: tuple[str, ...] = ("酷威文化", "果麦文化", "时代华语")
_PLACEHOLDER_KEYS = {"", "sk-xxx", "sk-xxxx", "sk-xxxx..."}
_IMAGE_PROMPT_LIMIT = 5000


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

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def collect_reference_covers(cover_input: CoverInput, limit: int = 12) -> tuple[ReferenceCover, ...]:
    """Collect stable reference metadata from the three approved Dangdang brand pools."""
    cache_dir = _cover_reference_cache_dir()
    references = collect_dangdang_reference_covers(
        title=cover_input.title,
        intro=cover_input.intro,
        category_label=cover_input.category_label,
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
    normalized_category = normalize_category(category or "social_science")
    category_meta = get_category_meta(normalized_category)
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
    avoid_rules = (
        "只借鉴酷威文化、果麦文化、时代华语的抽象设计规律，不复刻任何真实封面",
        "不要出现出版社 logo、出版品牌 logo、原出版信息、二维码、价格、水印",
        "不要做成当当商品主图、3D 书模、白底商品照、赠品图或促销海报",
        "不要生成真实品牌背书、原书名残影、乱码中文或英文乱码",
    )
    return CoverStyleBrief(
        reference_site=ALLOWED_REFERENCE_SITE,
        reference_brands=ALLOWED_REFERENCE_BRANDS,
        matched_category_style=f"{cover_input.category_label}：{profile['style']}",
        brand_style_direction=f"优先参考 {profile['brand']} 在当当网同类书中的抽象审美规律",
        cover_type=str(profile["cover_type"]),
        color_palette=tuple(profile["colors"]),
        composition=str(profile["composition"]),
        typography_direction=str(profile["typography"]),
        white_space_ratio=str(profile["white_space"]),
        visual_metaphors=tuple(profile["metaphors"]),
        reference_samples=tuple(asdict(item) for item in collect_reference_covers(cover_input)),
        title_hierarchy="书名最大，作者名次之，副标题或极短封面文案更小；整体像正式出版物装帧",
        avoid_rules=avoid_rules,
        generation_prompt_addendum=str(profile["addendum"]),
    )


def _brief_json(brief: CoverStyleBrief) -> str:
    return json.dumps(asdict(brief), ensure_ascii=False, indent=2)


def build_draft_cover_prompt(cover_input: CoverInput, style_brief: CoverStyleBrief) -> str:
    return f"""你是一名资深中国出版封面设计师。请根据书籍信息和参考风格报告，生成一张可以直接作为图书封面的竖版正面封面设计图。

书籍信息：
- 书名：{cover_input.title}
- 作者：{cover_input.author}
- 简介：{cover_input.intro}
- 分类：{cover_input.category_label}

参考风格报告：
{_brief_json(style_brief)}

生成目标：
生成完整的正面封面本体，画面铺满整张竖版画布，推荐 2:3 或 3:4。它必须是一张可直接商用、具有出版质感的中文图书封面，而不是电商商品主图。

设计要求：
- 只学习当当网中酷威文化、果麦文化、时代华语三家品牌的抽象规律：配色、构图、标题层级、留白、视觉隐喻和出版气质。
- 不得复刻、拼贴、改写或近似复制任何真实封面。
- 书名是最大视觉层级，作者名次之；简介只可提炼成一句很短的副标题或封面文案，不要整段上封面。
- 封面应有成熟出版设计：构图稳定、色彩克制、字体高级、视觉隐喻准确、印刷质感自然。
- 中文文字必须尽量准确、清晰，像真实出版封面排版；不要生成乱码中文、伪中文、英文占位符或真实出版信息。

硬性禁止：
不要 3D 书模、不要书脊、不要白底商品照、不要赠品、不要贴纸角标、不要促销条、不要二维码、不要出版社 logo、不要出版品牌 logo、不要原出版信息、不要价格、不要水印、不要真实品牌背书、不要乱码文字、不要电影海报风。"""


def build_refine_cover_prompt(cover_input: CoverInput, style_brief: CoverStyleBrief) -> str:
    return f"""请精修这张封面，使它成为可直接商用，具有出版质感的中文图书封面。

书籍信息：
- 书名：{cover_input.title}
- 作者：{cover_input.author}
- 分类：{cover_input.category_label}

精修方向：
- 保留第二步封面的核心视觉、主色、中文标题层级和分类气质，但让构图更稳、字体区更清晰、画面更像正式出版物。
- 继续遵循参考风格：{style_brief.generation_prompt_addendum}
- 删除或避免所有出版社 logo、出版品牌 logo、原出版信息、二维码、价格、促销标签、真实书名残影、乱码文字、水印。
- 不要把画面改成电商商品图，不要出现 3D 书本样机、书脊、赠品、白底商品展示。
- 最终保留：书名、作者、可选短副标题、封面主体视觉、成熟出版排版。

输出要求：
竖版正面封面本体，高清，干净，有纸张或印刷质感，适合直接作为图书封面使用。"""


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
            return _post_image_request(http, endpoint, api_key, json_payload=payload)


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
            return _post_image_request(http, endpoint, api_key, data=data, files=files)


def _post_image_request(
    http: httpx.Client,
    endpoint: str,
    api_key: str,
    *,
    json_payload: dict[str, Any] | None = None,
    data: dict[str, str] | None = None,
    files: dict[str, tuple[str, bytes, str]] | None = None,
) -> bytes:
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
            return _decode_image_response(response.json())
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:1000]
            last_error = RuntimeError(f"图像 API 请求失败 {exc.response.status_code}: {body}")
            if exc.response.status_code < 500 or attempt >= max_attempts:
                raise last_error from exc
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt >= max_attempts:
                raise RuntimeError(f"图像 API 连接失败: {exc}") from exc
        time.sleep(2 * attempt)
    raise RuntimeError(f"图像 API 请求失败: {last_error}")


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
        with httpx.Client(timeout=120.0, proxy=_proxy_for_base_url(url) or None) as http:
            img = http.get(url)
            img.raise_for_status()
            return img.content
    raise RuntimeError("图像 API 返回值既无 b64_json 也无 url")


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
    except Exception as exc:
        logger.warning("封面精修失败，保留初稿但不降级为最终封面: %s", exc)
        (output_dir / "cover_refine_error.txt").write_text(str(exc), encoding="utf-8")
        raise RuntimeError("封面精修失败，已保留 draft_cover.png 供排查，未生成最终 cover.png") from exc

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
        return normalize_category(value)
    except ValueError:
        logger.warning("unknown cover category %r, fallback to social_science", value)
        return "social_science"


def generate_cover_file_from_metadata(metadata: dict[str, Any], output_path: Path) -> Path:
    """Generate one standalone cover from spreadsheet metadata.

    The standalone Excel entry intentionally reuses the same high-quality
    draft + refine cover workflow used by manuscript tasks. It writes to a
    per-item work directory first, then copies the final cover to output_path.
    """
    title = _metadata_lookup(metadata, _METADATA_TITLE_KEYS) or output_path.stem
    author = _metadata_lookup(metadata, _METADATA_AUTHOR_KEYS) or None
    intro = _metadata_lookup(metadata, _METADATA_INTRO_KEYS) or title
    category = _metadata_category(_metadata_lookup(metadata, _METADATA_CATEGORY_KEYS))
    markdown = f"@最终书名\n{title}\n\n@最终简介\n{intro}\n\n@正文\n{intro}\n"
    work_dir = output_path.parent / f".{output_path.stem}_work"
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
    "CoverStyleBrief",
    "build_cover_prompt",
    "build_cover_style_brief",
    "build_draft_cover_prompt",
    "build_refine_cover_prompt",
    "generate_cover_file",
    "generate_cover_file_from_metadata",
    "prepare_cover_input",
]
