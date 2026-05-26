"""Three-stage book cover generation."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import queue
import random
import re
import threading
import time
import zipfile
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

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
    "正统出版封面长期参考指令｜高仿执行版\n"
    "核心逻辑：先选母版，再抄版式，再换内容，再调细节，最后检查侵权元素。\n"
    "执行顺序必须固定：先选母版 → 再抄版式 → 再换内容 → 再调细节 → 最后检查是否保留侵权元素。\n"
    "本文件用于“正统出版”封面书架的长期参考。参考来源包括用户提供的正统出版长期素材库，以及微信读书、得到系列等成熟出版封面的设计规律。\n"
    "目标不是让 AI 自由发挥，也不是只抽象学习所谓“出版感”，而是让 AI 在不保留侵权元素的前提下，尽量高仿参考图库中成熟封面的排版、配色和书架效果。\n"
    "生成前必须先从素材库中选择一张最适合当前书名、简介、分类和读者期待的参考封面作为主要版式母版。不要泛泛学习“出版感”，不要自己重新发明一套构图，不要从多个封面里随意拼凑。\n"
    "优先选择与当前书籍题材、气质、目标读者、上架场景、标题长度和封面信息量接近的参考封面；如果没有完全对应的题材，也要选择版式最适合的一张作为主母版。\n"
    "高仿时重点模仿参考母版的标题所在区域、标题大小比例、标题分行方式、作者名位置、副标题位置、留白比例、主视觉区域、主视觉占比、主视觉与标题的相对关系、背景色块比例、色彩明度关系、冷暖对比方式、画面重心、手机书架缩略图效果。\n"
    "以用户当前提供的书名、作者、分类和简介为主。长期素材库不是普通灵感参考，而是主要版式参考来源。\n"
    "AI 应先从素材库中判断当前书更接近哪一类出版封面，哪种排版最适合当前书名，哪种标题层级最适合缩略图识别，哪种色彩关系最适合当前题材和读者期待，然后以素材库中相近封面的排版方式为母版，进行高相似度改写。\n"
    "允许高仿参考封面的标题所在区域、标题大小关系、标题分行节奏、作者名位置、副标题位置、留白比例、主视觉占比、主视觉与标题的相对关系、背景色块关系、整体版面重心、色彩明度关系、冷暖对比方式、手机书架缩略图效果；排版、配色、标题层级、留白节奏、主视觉位置和画面重心都可以高度相似。\n"
    "不要为了追求原创而刻意远离参考图库。如果某个参考封面的排版方式适合当前书，可以大胆贴近它。\n"
    "可以高仿的是版式骨架、标题层级、留白节奏、色彩气质、主视觉位置、图文关系、背景处理方式、整体出版审美。\n"
    "必须删除或替换真实书名、原作者信息、出版社信息、出版社或平台 logo、二维码、价格、水印、真实品牌元素、已有摄影作品、已有插画作品、可识别人物肖像、独特图形细节。\n"
    "可以像它的排版和气质，不能留下它的身份信息和具体视觉资产。\n"
    "主视觉的位置、比例、面积、与文字的关系可以高仿母版，但具体内容必须根据当前书名和简介重新生成；如果母版是人物、插画、摄影或独特图形，不能复制原姿态、原图像或原细节，只保留视觉位置和构图关系。\n"
    "配色可以接近参考母版，可以高仿主色、辅助色、明暗关系、冷暖关系和整体色彩气质，但不要完全复制到每个色块、每个比例、每个细节都一样；要微调色相、明度、饱和度、点缀色或局部比例。\n"
    "优先让封面看起来像真实书架上的成熟出版物。不要生成随机 AI 图、PPT 封面、知识付费课程海报、电商主图或网文封面。\n"
    "排版可以大胆贴近参考图。标题位置、字号比例、留白关系、主视觉区域、色块比例和配色气质，都可以高相似度参考。\n"
    "主视觉必须服务于当前书名、简介、题材和读者期待。可以借鉴参考封面的主视觉位置、面积比例、图文关系和整体氛围，但具体视觉元素要根据当前书重新设计。\n"
    "配色可以接近参考封面。允许使用相似的主色、辅助色、明度关系、冷暖关系和对比方式，但不要完全复制某一本封面的完整配色比例。\n"
    "不要灰蒙蒙、不要发黄做旧、不要低饱和寡淡。\n"
    "如果需要白底，必须是纯白 #FFFFFF 数字白；不要米白、奶油白、暖白、灰白或纸白。不要任何纸张纹理、纤维感、印刷颗粒、噪点、做旧感、扫描感、油墨渗透感；不要模拟实体纸书摄影效果，不要书页感、纸面肌理或胶版纸质感。提高整体明度与色彩纯度，避免灰蒙蒙和所谓高级感灰滤镜；文字区域必须纯净清晰。\n"
    "标题是封面的第一识别信息。标题必须清晰、有层级，适合手机书架缩略图识别。作者名、副标题、推荐语等信息要克制。除非用户明确要求，不要添加大段简介、营销金句、课程口号或无意义小字。\n"
    "不要随意添加无意义的横线、竖线、圆点、印章、编号等装饰符号。所有装饰元素都必须服务于标题层级、版面结构或主视觉关系。\n"
    "如果母版中有装饰线、圆点、编号或小符号，可以参考它的功能和位置关系，但不要原样照搬，也不要在不需要的位置强行添加。\n"
    "生成完成后必须自检：是否真的选择了一张参考母版；标题区域、主视觉区域、留白比例是否明显贴近母版；配色气质和书架效果是否接近母版；是否已经删除或替换真实书名、作者、出版社、logo、二维码、价格、水印；是否没有复制原摄影、原插画、原人物肖像、独特图形细节；是否像真正图书封面而不是课程页、广告页、说明页或 PPT。\n"
    "不要知识付费海报感、PPT 课程封面感、公众号长图感、电商商品图感。不要网文封面感、廉价 AI 插画感、过度 3D 感、促销广告感。\n"
    "不要为了“原创”而远离参考图库，导致封面不像真实出版物。不要为了“高级感”一律做成冷淡抽象风。不要为了“文学感”一律做成古风水彩、远山、飞鸟、太阳、泛黄纸张。不要为了“现代感”一律做成几何门洞、光束、人影、蓝黄对比、大标题模板。\n"
    "最终封面应当像以素材库中的成熟出版封面为母版，经过高相似度改写后生成的新书封面。排版像，配色像，出版气质像；真实书名、作者信息、出版社信息、logo、二维码、价格、水印、真实品牌元素、具体摄影作品、具体插画作品、可识别人物肖像、独特图形细节全部删除或替换。"
)


def _load_reference_markdown(filename: str) -> str:
    path = Path(__file__).with_name(filename)
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


_LITERATURE_COVER_REFERENCE = _load_reference_markdown("literature_cover_reference.md")
_LITERATURE_REFERENCE_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
_LITERATURE_REFERENCE_DIRECT_LIMIT = 16
_LITERATURE_REFERENCE_DIRECT_SEQUENCE_COUNT = 1
_LITERATURE_REFERENCE_BOARD_LIMIT = 12
_LITERATURE_REFERENCE_BOARD_COLUMNS = 4
_LITERATURE_REFERENCE_CELL_SIZE = (260, 380)
_SIMPLE_REFERENCE_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
_SIMPLE_REFERENCE_CATEGORY_PROFILES: dict[str, dict[str, Any]] = {
    "psychology_cognition": {
        "label": "心理认知类",
        "asset_dir": "psychology_cognition",
        "style": "亮色心理认知、自助成长、强标题、简洁插画",
        "cover_type": "心理认知类参考封面",
        "colors": ("明黄", "珊瑚红", "青蓝", "白色", "深色标题"),
        "composition": "亮色纯底或简洁色块，超大标题，少量心理认知相关插画或人物剪影，手机端强识别",
        "typography": "巨大中文标题，层级清楚，作者名和副标题克制",
        "white_space": "25%-45%",
        "metaphors": ("笑脸", "人物剪影", "手掌", "心形", "咖啡杯", "自我成长符号"),
        "prompt_hint": "参考样例中的亮色底、大标题、心理认知/自助成长气质、简洁插画和强缩略图识别度。",
        "style_dna": "亮色底、超大标题、心理认知/自助成长气质、少量符号化插画，整体像大众心理认知类畅销书。",
        "color_axes": (
            "明黄主轴 + 深咖/黑色标题 + 少量白色呼吸区，适合自信、意义、积极转向的主题。",
            "珊瑚红主轴 + 米白局部 + 灰蓝小面积插画，适合关系、人性、情绪秘密和亲密困境。",
            "青蓝主轴 + 深蓝标题 + 白色局部留白，适合勇气、成长课、安全感和认知升级。",
            "草绿主轴 + 浅黄辅助 + 深绿色标题，适合身体感受、需求、疗愈和自我接纳。",
            "淡紫/蓝紫主轴 + 墨黑标题 + 粉色点缀，适合自我价值、敏感、内耗和温柔心理主题。",
            "橙黄主轴 + 孔雀蓝点缀 + 白色高光，适合热量、行动力、改变和外向能量。",
        ),
        "layout_axes": (
            "顶部或左上超大标题占 45%-60%，主视觉放下半部或右下角，像样例那样手机端第一眼读标题。",
            "标题做成 3-5 行粗体堆叠，人物剪影或心理符号从标题空隙穿插，不要整齐居中模板。",
            "大色块铺底，标题在左侧竖向压版，右侧用一个简单心理符号形成平衡。",
            "主视觉居中偏下，标题从上方压下来，副标题只做一小行，留出明确呼吸区。",
            "用一个圆形/手势/心形/头像轮廓作为视觉锚点，标题环绕或贴边排版，避免四张都同一左上大标题。",
        ),
        "title_axes": (
            "书名拆成 2-4 行，最大字重，单行不要太长；关键词可放大，但不要每个字都一样大。",
            "主标题强，副标题弱；副标题不超过 12 个中文字符，放在标题下方或角落。",
            "标题可有一处错位、跨行或大小对比，让封面有设计节奏，不要全都同样齐左。",
            "标题使用现代粗黑体或圆角黑体，确保缩略图可读；不要仿书法。",
        ),
        "visual_axes": (
            "从简介提炼一个心理动作或身体感受，用人物剪影、手势、拥抱姿态或心形容器表达。",
            "从简介提炼一个抽象心理符号，例如温度计、钥匙、气泡、拼图、地图、种子或护盾。",
            "从简介提炼一个社交场景，只保留桌椅、杯子、侧影、对话气泡等极简元素。",
            "从简介提炼一个自我成长隐喻，例如打开的门、阶梯、光点、镜子、背包或植物，不要做复杂插画。",
            "用一个大面积单色剪影或扁平图形作为主视觉，少细节，和标题形成强对比。",
        ),
        "quote_axes": (
            "金句策略：不加金句，只保留书名、作者和一个很短副标题。",
            "金句策略：最多 1 句短句，不超过 10 个中文字符，放角落，不抢标题。",
            "金句策略：如果书名已经有情绪张力，不加任何额外金句。",
        ),
    },
    "literature_simple": {
        "label": "文学类",
        "asset_dir": "literature_simple",
        "style": "文学意象、书法大字、强情绪背景、出版质感",
        "cover_type": "文学类参考封面",
        "colors": ("绿色", "朱红", "深红", "米白", "墨黑"),
        "composition": "书法或手写感大标题压版，配合文学意象背景、纹理或单一主视觉，整体有纸书气质",
        "typography": "大号书法/手写标题，作者名小而稳，副标题少",
        "white_space": "20%-45%",
        "metaphors": ("水纹", "灯笼", "土地纹理", "山水纹理", "文学意象"),
        "prompt_hint": "参考样例中的书法大标题、文学意象、强色彩背景和真实出版物气质。",
        "style_dna": "书法/手写大标题、强文学意象、纹理背景、纸书出版感，情绪浓但画面不做网文海报。",
        "color_axes": (
            "水纹绿主轴 + 米白书法标题 + 深绿纹理，适合潮汐、记忆、乡土、水边和清冷文学。",
            "朱红主轴 + 墨黑标题/插画 + 米白小字，适合历史、命运、家族、时代感。",
            "深红/黑红主轴 + 米白粗书法 + 暗纹理，适合苦难、燃烧、土地、强烈人物命运。",
            "米白纸色主轴 + 墨黑书法 + 一处朱红印记，适合散文、回忆、温和文学。",
            "夜色蓝黑主轴 + 银白标题 + 微弱暖色光点，适合孤独、城市、冬夜、悬念文学。",
            "低饱和自然色主轴 + 深墨标题 + 局部植物/山水纹理，适合安静、自然、成长和疗愈文学。",
        ),
        "layout_axes": (
            "超大书法标题占画面 45%-70%，背景做一整块文学纹理，作者名贴边小号放置。",
            "标题竖向或半竖向排版，主视觉只保留一个象征物，不要把画面填满。",
            "标题在上半部强压版，底部留出一条简介/推荐语区，但文字极少。",
            "中心放一个文学意象，标题绕它展开或压在其上，形成像纸书封面的整体感。",
            "使用左右不对称：一侧大标题，一侧留白/纹理/小人物或物件，避免每张都红底满屏字。",
        ),
        "title_axes": (
            "标题使用书法、手写或粗宋变体；每本书可改变字势、行距和分行，不要统一红底白字。",
            "长标题拆成有呼吸的 2-5 行，关键词放大；短标题可以极大化并保留留白。",
            "作者名小而稳，靠边或靠下；不要加大段简介。",
            "标题可压过主视觉，但必须读得清，缩略图中第一眼能辨认。",
        ),
        "visual_axes": (
            "从简介提炼一个文学场景：水面、山路、灯、猫、窗、旧城、田野、船、雨夜或纸页。",
            "从简介提炼一个情绪物件：灯笼、伞、石头、树影、河岸、空椅子、门牌、信件或火光。",
            "用背景纹理讲故事：水纹、木刻、山形、土地裂纹、夜色颗粒或旧纸纤维。",
            "如果主题有强命运感，用单一高对比物件或人物剪影；不要出现写实人脸。",
            "如果主题安静，减少主视觉面积，让标题、纹理和留白承担情绪。",
        ),
        "quote_axes": (
            "金句策略：不加金句，只保留书名和作者。",
            "金句策略：最多 1 句短句，不超过 12 个中文字符，像腰封外的一行小字。",
            "金句策略：标题已经有文学感时，不加副标题和营销句。",
        ),
    },
    "social_science_simple": {
        "label": "社科类",
        "asset_dir": "social_science_simple",
        "style": "社科经管、大字标题、红黑白、高识别、极简版式",
        "cover_type": "社科类参考封面",
        "colors": ("红色", "白色", "黑色", "深红", "少量金色"),
        "composition": "极简背景，巨大标题占据主要画面，少量符号或图案辅助，严肃社科经管书架感",
        "typography": "超大粗体中文或数字标题，黑白红强对比，副标题和作者名清楚克制",
        "white_space": "15%-40%",
        "metaphors": ("数字", "经济符号", "倒计时", "投资", "中国经济", "趋势图案"),
        "prompt_hint": "参考样例中的红黑白大字标题、极简社科经管版式和强商业出版识别度。",
        "style_dna": "社科经管书架感、超大标题、少色高对比、极简符号或数字，像严肃商业出版物而不是课程海报。",
        "color_axes": (
            "红底 + 黑色超大标题 + 小面积白色圆形/标签，适合冲突、危机、强判断主题。",
            "白底 + 黑色超大标题 + 红色局部强调，适合预测、趋势、技术和理性分析。",
            "黑底 + 白色标题 + 红色数字/图形点缀，适合倒计时、未来、风险和硬核判断。",
            "深红主轴 + 米白标题 + 金色细线，适合中国经济、国运、长期主义和宏观主题。",
            "灰白主轴 + 黑字 + 红色数据符号，适合产业、AI、投资方法和分析型主题。",
            "红白分割主轴 + 黑字压版，适合观点鲜明、标题较长的社科书。",
        ),
        "layout_axes": (
            "超大标题占 60%-80%，只用一个圆形、数字或小符号辅助，不要堆插画。",
            "巨型数字或关键词做主视觉，其他文字围绕其建立层级。",
            "上下强分割：上方大标题，下方作者/副标题/小符号，整体像样例社科经管书。",
            "左右强分割：一侧标题压满，一侧留白或放一个数据符号，避免每张都纯红满版。",
            "标题斜切或错位堆叠，配一个极简几何/数据图形，保持严肃而有冲击。",
        ),
        "title_axes": (
            "标题使用超粗黑体或大号数字，字距紧，层级强；缩略图必须先读到标题。",
            "长标题拆成 3-5 行，关键词加大；副标题只保留一小行。",
            "标题可以极端放大到切边，但不能遮掉核心字义。",
            "作者名小号，靠上或靠下，像真实经管/社科书署名。",
        ),
        "visual_axes": (
            "从简介提炼一个数据/趋势符号：箭头、曲线、圆环、坐标、倒计时、芯片或货币轮廓。",
            "从简介提炼一个宏观概念：城市网格、产业链、市场波动、国运符号或结构图。",
            "只使用一个符号作为视觉锚点，保持极简，不要变成 PPT 信息图。",
            "如果书名有数字或年份，把数字做成主视觉；没有数字时抽取一个关键词做巨型图形。",
            "用留白和大字形成冲击，主视觉可小到只是一处红/白/黑几何标记。",
        ),
        "quote_axes": (
            "金句策略：不加金句，只保留必要副标题。",
            "金句策略：最多 1 句短判断，不超过 12 个中文字符，像出版封面的短副标。",
            "金句策略：如果标题本身就是观点句，不再添加任何额外金句。",
        ),
    },
}

_ORTHODOX_DIRECT_CATEGORY_IDS = {
    "literature",
    "psychology_cognition",
    "literature_simple",
    "social_science_simple",
}
_ORTHODOX_ELEMENT_REPLACEMENT_PROFILES: dict[str, dict[str, str]] = {
    "literature": {
        "label": "正统出版",
        "level": "主版本：平衡替换",
        "direct_rule": (
            "在主正统出版分类中，只允许抄母版的排版、配色和字体/标题排版设计语法。"
            "允许强参考的范围只有：版心结构、留白比例、标题字形气质、字重字号比例、分行节奏、横竖排关系、字距行距、标题块位置、主色/辅助色/明暗冷暖关系和色块面积。"
            "除此之外所有元素都必须更换：主体物件、人物/动物姿态、符号造型、图标、角标、边框、纹样、线条装饰、背景点缀、印章式标记、局部插画组合、摄影素材、抽象符号和任何可识别记忆点都不能照搬。"
            "替换后的主视觉和装饰要根据当前书名与简介重新设计；看起来仍像这张母版所属设计系统的新书，但不能像母版换皮。"
        ),
        "refine_rule": (
            "精修时执行主正统出版的最终复查：只能保留排版、配色和字体/标题排版；如果初稿里仍能认出母版的主体物件、人物/动物姿态、符号造型、角标、纹样、小图标、线条点缀、背景装饰、独特主体轮廓或任何装饰记忆点，必须重画成新的同功能元素。"
        ),
    },
    "psychology_cognition": {
        "label": "正统测试1",
        "level": "测试1：轻度替换",
        "direct_rule": (
            "这是正统测试1：保留最多的母版外形，只做轻度核心替换。"
            "标题排版、版式骨架、色块比例、主视觉占位和装饰数量要贴近母版；"
            "但可识别核心主体和所有装饰细节必须换新，尤其是角落小符号、边线、圆点、气泡、贴纸、印章、纹样和背景点缀不能照搬。"
            "目标是 70%-80% 像母版的设计语法，20%-30% 来自当前书的新主体和新装饰。"
        ),
        "refine_rule": (
            "精修时只做轻度原创化：保留母版式秩序，但把任何像母版原装饰的小符号、角标、线条、背景点缀替换成新图形。"
        ),
    },
    "literature_simple": {
        "label": "正统测试2",
        "level": "测试2：中度替换",
        "direct_rule": (
            "这是正统测试2：中度替换核心视觉和装饰系统。"
            "版心、标题比例、留白、主色关系继续明显像母版；"
            "主视觉类别、主体轮廓、装饰图形、边框/角标/纹样/小图标/背景点缀必须系统性重组。"
            "目标是 55%-70% 像母版的排版和色彩关系，30%-45% 来自当前书的新主体、新装饰和新叙事。"
        ),
        "refine_rule": (
            "精修时做中度原创化：保留标题和版式骨架，重新整理所有装饰关系，避免母版的图标组合、边框纹样、背景小元素被认出来。"
        ),
    },
    "social_science_simple": {
        "label": "正统测试3",
        "level": "测试3：强替换",
        "direct_rule": (
            "这是正统测试3：强替换核心视觉和装饰，只保留母版的骨架魂魄。"
            "标题排版、版心、留白比例、色彩明暗结构仍要强参考；"
            "但主视觉主体、主体类别、装饰体系、角标、纹样、小图标、线条、背景点缀、局部符号和所有独特记忆点必须彻底换掉。"
            "目标是 40%-55% 像母版的结构和标题设计，45%-60% 来自当前书的新视觉元素；绝不能像直接换皮。"
        ),
        "refine_rule": (
            "精修时做强原创化检查：除了标题版式、版心和色彩结构，任何可识别母版装饰、主体轮廓或图标组合都要替换。"
        ),
    },
}


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
    "psychology_cognition": {
        "brand": "封面分类ZIP参考库",
        "style": _SIMPLE_REFERENCE_CATEGORY_PROFILES["psychology_cognition"]["style"],
        "cover_type": _SIMPLE_REFERENCE_CATEGORY_PROFILES["psychology_cognition"]["cover_type"],
        "colors": _SIMPLE_REFERENCE_CATEGORY_PROFILES["psychology_cognition"]["colors"],
        "composition": _SIMPLE_REFERENCE_CATEGORY_PROFILES["psychology_cognition"]["composition"],
        "typography": _SIMPLE_REFERENCE_CATEGORY_PROFILES["psychology_cognition"]["typography"],
        "white_space": _SIMPLE_REFERENCE_CATEGORY_PROFILES["psychology_cognition"]["white_space"],
        "metaphors": _SIMPLE_REFERENCE_CATEGORY_PROFILES["psychology_cognition"]["metaphors"],
        "addendum": _SIMPLE_REFERENCE_CATEGORY_PROFILES["psychology_cognition"]["prompt_hint"],
    },
    "literature_simple": {
        "brand": "封面分类ZIP参考库",
        "style": _SIMPLE_REFERENCE_CATEGORY_PROFILES["literature_simple"]["style"],
        "cover_type": _SIMPLE_REFERENCE_CATEGORY_PROFILES["literature_simple"]["cover_type"],
        "colors": _SIMPLE_REFERENCE_CATEGORY_PROFILES["literature_simple"]["colors"],
        "composition": _SIMPLE_REFERENCE_CATEGORY_PROFILES["literature_simple"]["composition"],
        "typography": _SIMPLE_REFERENCE_CATEGORY_PROFILES["literature_simple"]["typography"],
        "white_space": _SIMPLE_REFERENCE_CATEGORY_PROFILES["literature_simple"]["white_space"],
        "metaphors": _SIMPLE_REFERENCE_CATEGORY_PROFILES["literature_simple"]["metaphors"],
        "addendum": _SIMPLE_REFERENCE_CATEGORY_PROFILES["literature_simple"]["prompt_hint"],
    },
    "social_science_simple": {
        "brand": "封面分类ZIP参考库",
        "style": _SIMPLE_REFERENCE_CATEGORY_PROFILES["social_science_simple"]["style"],
        "cover_type": _SIMPLE_REFERENCE_CATEGORY_PROFILES["social_science_simple"]["cover_type"],
        "colors": _SIMPLE_REFERENCE_CATEGORY_PROFILES["social_science_simple"]["colors"],
        "composition": _SIMPLE_REFERENCE_CATEGORY_PROFILES["social_science_simple"]["composition"],
        "typography": _SIMPLE_REFERENCE_CATEGORY_PROFILES["social_science_simple"]["typography"],
        "white_space": _SIMPLE_REFERENCE_CATEGORY_PROFILES["social_science_simple"]["white_space"],
        "metaphors": _SIMPLE_REFERENCE_CATEGORY_PROFILES["social_science_simple"]["metaphors"],
        "addendum": _SIMPLE_REFERENCE_CATEGORY_PROFILES["social_science_simple"]["prompt_hint"],
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
        return normalize_cover_category_id(category or "") in _ORTHODOX_DIRECT_CATEGORY_IDS
    except ValueError:
        return False


def _orthodox_direct_category_id(category: str | None) -> str:
    try:
        normalized = normalize_cover_category_id(category or "")
    except ValueError:
        return "literature"
    return normalized if normalized in _ORTHODOX_DIRECT_CATEGORY_IDS else "literature"


def _orthodox_element_replacement_profile(category: str | None) -> dict[str, str]:
    category_id = _orthodox_direct_category_id(category)
    return _ORTHODOX_ELEMENT_REPLACEMENT_PROFILES.get(category_id, _ORTHODOX_ELEMENT_REPLACEMENT_PROFILES["literature"])


def _simple_reference_category_id(category: str | None) -> str | None:
    try:
        normalized = normalize_cover_category_id(category or "")
    except ValueError:
        return None
    if normalized in _SIMPLE_REFERENCE_CATEGORY_PROFILES:
        return normalized
    return None


def _is_simple_reference_category(category: str | None) -> bool:
    return _simple_reference_category_id(category) is not None


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
    "版式方向E：参考物件特写型书封的主视觉占比和标题节奏，用本书相关物件、织物、玻璃、票据、光影或细节重组。",
    "版式方向F：参考色块承载标题、插画与背景融合、主体压在边角等成熟组织方式，保留出版秩序而重做具体内容。",
)
_LITERATURE_TEMPLATE_BAN = (
    "不要无依据地默认套用太阳、夕阳、日出圆盘、弯曲白路、绿色山坡、远山、飞鸟、小人走在路上、"
    "几何门洞、光束、人影、蓝黄对比、古风水彩、泛黄纸张等通用模板；如果书名或简介确实需要对应意象，必须按本书重新组织版式、配色和细节。"
)
_LITERATURE_COLOR_VARIANTS = (
    "背景色方向：纯白 #FFFFFF 数字白或明亮浅色底，加一个高识别点色",
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

_ORTHODOX_REFERENCE_STYLE_DNA = (
    "256 张正统出版参考封面的大图库 DNA：真实中文出版物质感、强标题层级、清楚书架缩略图识别、"
    "克制但明确的色彩主轴、清透数字底色、成熟留白、稳定图文关系、少而准的视觉锚点。"
)
_ORTHODOX_REFERENCE_COLOR_AXES = (
    "水绿/湖蓝/青绿色主轴，配纯白 #FFFFFF 或深墨标题，适合清冷文学、心理疗愈、记忆、水边和自然主题。",
    "朱红/橙红/珊瑚红主轴，配黑白强标题或小面积金色，适合命运、关系、社会观察和强观点主题。",
    "纯白 #FFFFFF 数字白主轴，配墨黑标题和一处红/蓝/绿高纯点色，适合散文、思想、温和叙事和文学书架感。",
    "黑白高对比主轴，配单一亮色点缀，适合社科、悬疑思辨、硬核观点和强标题书。",
    "夜色蓝黑/深绿黑主轴，配银白或暖黄标题，适合孤独、城市、悬疑、时间和复杂情绪。",
    "清爽自然色主轴，配深色标题和局部植物/人物/物件，适合成长、家庭、乡土和细腻叙事。",
    "明黄/草绿/淡紫等柔和心理色块主轴，配粗黑体或圆体标题，适合大众心理、自我认知和成长书。",
    "商业社科红黑白主轴，标题极强、图形极少，适合经济、决策、趋势、效率和方法论主题。",
)
_ORTHODOX_REFERENCE_LAYOUT_AXES = (
    "超大标题压版：标题占画面 45%-70%，主视觉只做一个清楚锚点，保留手机书架第一眼识别。",
    "大留白中心意象：用一个物件、人物局部或抽象符号居中/偏下，标题与留白形成成熟出版节奏。",
    "左右不对称：一侧强标题，一侧主视觉或纯留白，避免每本都做成同一张红底大字。",
    "上下分割：上方标题层级强，下方放主视觉/作者/短副标，整体像成熟出版封面而不是海报。",
    "竖排或半竖排标题：适合文学、历史、东方意象或长标题，必须清晰，不做古风模板。",
    "单色块承载标题：背景色块、局部几何或纯净数字底色承载文字，主视觉穿插但不抢标题。",
    "满版图像弱化：图像作为背景气质，文字层级稳定覆盖其上，避免脏乱和真实素材残留。",
    "极简符号版式：只用数字、线、圆、门、窗、树影、脸部轮廓等一个符号，靠比例和留白取胜。",
)
_ORTHODOX_REFERENCE_TITLE_AXES = (
    "标题拆成 2-5 行，关键词放大，其余字收小，形成真实出版物标题层级。",
    "标题极大但有呼吸，允许切边或贴边，但不能丢失可读性。",
    "标题使用现代宋体、粗黑体、手写/书法感标题或混排，但要按书的气质选择，不统一套模板。",
    "作者名小而稳，靠上、靠下或贴边；副标题最多一行，不能变成课程海报。",
    "长标题优先做层级与分行，不堆满多段解释；短标题可以极大化并配大留白。",
)
_ORTHODOX_REFERENCE_VISUAL_AXES = (
    "文学意象：从简介提炼水面、山路、灯、窗、树、猫、旧城、田野、船、雨夜、信件或光影。",
    "心理符号：从简介提炼心形、脸、手势、拥抱、镜子、气泡、地图、钥匙、温度计或身体轮廓。",
    "社科符号：从简介提炼数字、箭头、圆环、坐标、市场曲线、城市网格、结构图或时间刻度。",
    "人物局部：使用背影、侧脸轮廓、手、衣物、脚步或剪影关系，不使用可识别真实肖像。",
    "单一物件：用书、杯子、椅子、门牌、花、石头、车票、钟面、信封或玻璃反射作为叙事锚点。",
    "抽象图形：用水纹、木刻感线条、山形、裂缝、线圈、色块边界表达情绪，但必须干净克制。",
    "自然/城市场景：只保留必要轮廓和气氛，不做复杂写实插画，不做网文场景海报。",
)
_ORTHODOX_REFERENCE_TEXT_DENSITY_AXES = (
    "文字密度：除书名、作者和必要副标题外，不加金句。",
    "文字密度：最多 0 到 1 句短金句，不超过 12 个中文字符，放角落或腰封感小位置。",
    "文字密度：标题已足够有观点时，不添加任何营销文案。",
    "文字密度：副标题可以有，但必须短、稳、出版化；禁止多段营销文案、课程口号和密集小字。",
)


def _stable_variant(options: tuple[str, ...], seed: str, salt: str) -> str:
    digest = hashlib.sha256(f"{salt}:{seed}".encode("utf-8")).hexdigest()
    return options[int(digest[:8], 16) % len(options)]


def _simple_reference_visual_plan(cover_input: CoverInput) -> dict[str, str]:
    category_id = _simple_reference_category_id(cover_input.category)
    if category_id is None:
        raise ImageApiPermanentError(f"unsupported simple reference cover category: {cover_input.category}")
    profile = _SIMPLE_REFERENCE_CATEGORY_PROFILES[category_id]
    seed = f"{cover_input.title}|{cover_input.author}|{cover_input.intro}|{cover_input.category}"
    return {
        "semantic_anchor": (
            f"书名《{cover_input.title}》；作者：{cover_input.author}；"
            f"简介线索：{cover_input.intro[:220]}"
        ),
        "color_axis": _stable_variant(tuple(profile["color_axes"]), seed, "simple_color"),
        "layout_axis": _stable_variant(tuple(profile["layout_axes"]), seed, "simple_layout"),
        "title_axis": _stable_variant(tuple(profile["title_axes"]), seed, "simple_title"),
        "visual_axis": _stable_variant(tuple(profile["visual_axes"]), seed, "simple_visual"),
        "quote_axis": _stable_variant(tuple(profile["quote_axes"]), seed, "simple_quote"),
    }


def _simple_reference_visual_plan_text(cover_input: CoverInput) -> str:
    plan = _simple_reference_visual_plan(cover_input)
    return (
        f"语义锚点：{plan['semantic_anchor']}\n"
        f"- 配色主轴：{plan['color_axis']}\n"
        f"- 版式骨架：{plan['layout_axis']}\n"
        f"- 标题策略：{plan['title_axis']}\n"
        f"- 主视觉叙事：{plan['visual_axis']}\n"
        f"- 金句策略：{plan['quote_axis']}"
    )


def _orthodox_reference_visual_plan(cover_input: CoverInput) -> dict[str, str]:
    seed = f"{cover_input.title}|{cover_input.author}|{cover_input.intro}|{cover_input.category}"
    return {
        "semantic_anchor": (
            f"书名《{cover_input.title}》；作者：{cover_input.author}；"
            f"简介线索：{cover_input.intro[:260]}"
        ),
        "color_axis": _stable_variant(_ORTHODOX_REFERENCE_COLOR_AXES, seed, "orthodox_color"),
        "layout_axis": _stable_variant(_ORTHODOX_REFERENCE_LAYOUT_AXES, seed, "orthodox_layout"),
        "title_axis": _stable_variant(_ORTHODOX_REFERENCE_TITLE_AXES, seed, "orthodox_title"),
        "visual_axis": _stable_variant(_ORTHODOX_REFERENCE_VISUAL_AXES, seed, "orthodox_visual"),
        "text_density_axis": _stable_variant(_ORTHODOX_REFERENCE_TEXT_DENSITY_AXES, seed, "orthodox_text_density"),
    }


def _orthodox_reference_visual_plan_text(cover_input: CoverInput) -> str:
    plan = _orthodox_reference_visual_plan(cover_input)
    return (
        f"语义锚点：{plan['semantic_anchor']}\n"
        f"- 配色主轴：{plan['color_axis']}\n"
        f"- 版式骨架：{plan['layout_axis']}\n"
        f"- 标题策略：{plan['title_axis']}\n"
        f"- 主视觉叙事：{plan['visual_axis']}\n"
        f"- 文字密度：{plan['text_density_axis']}"
    )


def _literature_fallback_visual_plan(cover_input: CoverInput) -> str:
    seed = f"{cover_input.title}|{cover_input.author}|{cover_input.intro}|{cover_input.category_label}"
    layout = _stable_variant(_LITERATURE_LAYOUT_VARIANTS, seed, "layout")
    color = _stable_variant(_LITERATURE_COLOR_VARIANTS, seed, "color")
    element = _stable_variant(_LITERATURE_ELEMENT_VARIANTS, seed, "element")
    return (
        f"本书强参考视觉方案：{layout}；{color}；{element} "
        "先保证版式像成熟出版物，再把具体图形、标题分行、局部细节和背景质感改成当前书专属。"
        "不要任何纸张纹理、纤维感、印刷颗粒、噪点或做旧效果；如果需要白底，必须是纯白 #FFFFFF 数字白。"
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


def _literature_reference_records(zip_path: Path, members: Sequence[str]) -> tuple[dict[str, str], ...]:
    return tuple(
        asdict(
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
        )
        for member in members
    )


def _literature_member_from_reference_record(item: object) -> str | None:
    if not isinstance(item, dict):
        return None
    local_path = item.get("local_image_path")
    if isinstance(local_path, str) and "!" in local_path:
        return local_path.split("!", 1)[1].lstrip("/")
    for key in ("product_url", "image_url"):
        value = item.get(key)
        if isinstance(value, str) and "!/" in value:
            return value.split("!/", 1)[1].lstrip("/")
    return None


def _literature_cached_direct_reference_members(samples_path: Path, available_members: set[str]) -> tuple[str, ...]:
    if not samples_path.exists() or samples_path.stat().st_size <= 0:
        return ()
    try:
        payload = json.loads(samples_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    if not isinstance(payload, list):
        return ()
    selected: list[str] = []
    seen: set[str] = set()
    for item in payload:
        member = _literature_member_from_reference_record(item)
        if member and member in available_members and member not in seen:
            selected.append(member)
            seen.add(member)
    return tuple(selected)


def _positive_int(value: object) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _orthodox_reference_sequence_state_dir() -> Path:
    return _repo_root() / "backend" / "output" / "orthodox_reference_state"


def _orthodox_reference_sequence_state_path() -> Path:
    return _orthodox_reference_sequence_state_dir() / "sequence.json"


def _orthodox_reference_sequence_lock_path() -> Path:
    return _orthodox_reference_sequence_state_dir() / "sequence.lock"


@contextmanager
def _orthodox_reference_sequence_lock() -> Iterator[None]:
    lock_path = _orthodox_reference_sequence_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 30
    stale_after_seconds = 180
    fd: int | None = None
    while fd is None:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {time.time()}".encode("utf-8"))
            os.close(fd)
        except FileExistsError as exc:
            try:
                if time.time() - lock_path.stat().st_mtime > stale_after_seconds:
                    lock_path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise ImageApiTransientError("正统出版参考封面全局顺序锁等待超时，请稍后重试") from exc
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("正统出版参考封面全局顺序锁清理失败: %s", lock_path)


def _read_orthodox_reference_sequence_state() -> dict[str, Any]:
    state_path = _orthodox_reference_sequence_state_path()
    if not state_path.exists() or state_path.stat().st_size <= 0:
        return {}
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("正统出版参考封面全局顺序状态不可读，将从第 1 张重新建立状态: %s", state_path)
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_orthodox_reference_sequence_state(payload: dict[str, Any]) -> None:
    state_path = _orthodox_reference_sequence_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = state_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(state_path)


def _reserve_orthodox_reference_sequence_index(members_count: int) -> int:
    if members_count <= 0:
        raise ImageApiPermanentError("正统出版参考封面 ZIP 为空，无法分配全局顺序")
    with _orthodox_reference_sequence_lock():
        state = _read_orthodox_reference_sequence_state()
        next_index = _positive_int(state.get("next_sequence_index")) or 1
        selected_index = ((next_index - 1) % members_count) + 1
        following_index = (selected_index % members_count) + 1
        total_allocations = (_positive_int(state.get("total_allocations")) or 0) + 1
        _write_orthodox_reference_sequence_state(
            {
                "policy": "global_zip_order_cycle",
                "members_count": members_count,
                "last_sequence_index": selected_index,
                "next_sequence_index": following_index,
                "total_allocations": total_allocations,
                "updated_at_unix": time.time(),
            }
        )
        return selected_index


def _ordered_literature_direct_reference_members(
    members: Sequence[str],
    sequence_index: int | None,
    *,
    limit: int,
) -> tuple[str, ...]:
    if not members:
        return ()
    count = min(max(1, limit), len(members))
    start = 0 if sequence_index is None else (sequence_index - 1) % len(members)
    return tuple(members[(start + offset) % len(members)] for offset in range(count))


def _selected_literature_direct_reference_members(
    output_dir: Path,
    *,
    force: bool,
    limit: int = _LITERATURE_REFERENCE_DIRECT_SEQUENCE_COUNT,
) -> tuple[str, ...]:
    zip_path = _literature_reference_zip_path()
    members = _literature_reference_members(zip_path)
    if not zip_path.exists() or not members:
        raise ImageApiPermanentError(
            f"正统出版参考封面 ZIP 缺失或为空，无法按素材库样式生成: {zip_path}"
        )
    samples_path = output_dir / "orthodox_reference_samples.json"
    if not force:
        cached = _literature_cached_direct_reference_members(samples_path, set(members))
        if cached:
            return cached[: min(limit, len(cached))]
    sequence_index = _reserve_orthodox_reference_sequence_index(len(members))
    return _ordered_literature_direct_reference_members(members, sequence_index, limit=limit)


def _literature_reference_sequence_index(members: Sequence[str], selected: Sequence[str]) -> int | None:
    if not members or not selected:
        return None
    try:
        return members.index(selected[0]) + 1
    except ValueError:
        return None


def _extract_literature_reference_inputs(
    members: Sequence[str],
    output_dir: Path,
    should_abort: Callable[[], bool] | None = None,
) -> tuple[Path, ...]:
    zip_path = _literature_reference_zip_path()
    input_dir = output_dir / "orthodox_reference_inputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    with zipfile.ZipFile(zip_path) as archive:
        for index, member in enumerate(members):
            _abort_cover_generation_if_requested(should_abort, stage="extract orthodox reference image")
            suffix = Path(member).suffix.lower()
            if suffix not in _LITERATURE_REFERENCE_IMAGE_EXTENSIONS:
                suffix = ".png"
            digest = hashlib.sha1(member.encode("utf-8")).hexdigest()[:12]
            target = input_dir / f"{index + 1:02d}_{digest}{suffix}"
            if not target.exists() or target.stat().st_size <= 0:
                target.write_bytes(archive.read(member))
            extracted.append(target)
    return tuple(extracted)


def _simple_reference_asset_dir(category_id: str) -> Path:
    profile = _SIMPLE_REFERENCE_CATEGORY_PROFILES[category_id]
    return (
        Path(__file__).resolve().parent
        / "reference_assets"
        / "simple_cover_categories"
        / str(profile["asset_dir"])
    )


def _simple_reference_image_paths(category_id: str) -> tuple[Path, ...]:
    asset_dir = _simple_reference_asset_dir(category_id)
    if not asset_dir.exists():
        return ()
    return tuple(
        path
        for path in sorted(asset_dir.iterdir())
        if path.is_file()
        and path.suffix.lower() in _SIMPLE_REFERENCE_IMAGE_EXTENSIONS
        and path.stat().st_size > 0
    )


def _simple_reference_records(category_id: str, image_paths: Sequence[Path]) -> tuple[dict[str, str], ...]:
    profile = _SIMPLE_REFERENCE_CATEGORY_PROFILES[category_id]
    return tuple(
        {
            "brand": "封面分类ZIP参考库",
            "title": path.stem,
            "product_url": f"local://simple_cover_categories/{profile['asset_dir']}/{path.name}",
            "image_url": f"local://simple_cover_categories/{profile['asset_dir']}/{path.name}",
            "source": "local_reference_asset",
            "product_id": hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12],
            "source_page": "封面分类.zip",
            "local_image_path": str(path),
        }
        for path in image_paths
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
                "具体图形、书名信息、主视觉内容和局部细节必须按当前书重做；不要纸感、纸纹、噪点或做旧，文字和背景要干净清晰。"
            ),
            visual_plan=_literature_fallback_visual_plan(cover_input),
        )
    simple_category_id = _simple_reference_category_id(cover_input.category)
    if simple_category_id is not None:
        profile = _SIMPLE_REFERENCE_CATEGORY_PROFILES[simple_category_id]
        return CoverMarketMatch(
            category_label=cover_input.category_label,
            search_keywords=keywords[:240],
            analysis=f"按封面分类.zip 中「{profile['label']}」示例封面的基础方向生成：{profile['prompt_hint']}",
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
    return (
        "social_science=视觉冲击；illustration=饱和插画；illustration_crayon=萌系治愈；"
        "literature=正统出版；psychology_cognition=正统测试1；literature_simple=正统测试2；social_science_simple=正统测试3"
    )


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
    if _is_literature_publishing(cover_input.category) or _is_simple_reference_category(cover_input.category):
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
            "正统出版执行顺序固定：先选母版 → 再抄版式 → 再换内容 → 再调细节 → 最后检查是否保留侵权元素。",
            "强参考用户提供的正统出版长期封面库和成熟出版封面方法；不要引用固定平台系列作为风格来源；必须先从参考图库中选择一张最适合本书的封面作为主要版式母版，不要自行发明新构图。",
            "可以明显靠近参考图的标题所在区域、标题大小关系、标题分行方式、作者名位置、副标题位置、留白比例、主视觉占比、主视觉与标题的相对关系、背景色块关系和整体版面重心。",
            "不要把某一本真实封面做成换书名、换作者、换元素的版本；不要复制真实封面的完整构图组合；不要出现出版社 logo、出版品牌 logo、第三方平台 logo、原出版信息、二维码、价格、水印、真实品牌背书或真实书名残影。",
            "不要生成乱码中文、英文占位符、真实人物肖像、名人脸、可识别照片脸。",
            "no paper texture, fibers, print grain, noise, retro dusty filter, scan effect, ink bleeding, low-contrast, muddy colors, heavy shadows, fake logos, barcodes, price tags, gibberish text, watermark.",
            _NO_REAL_PORTRAIT_RULE,
            *style_brief_rules,
        )
        color_palette = tuple(profile["colors"])
        reference_site = "本地正统出版ZIP参考封面库 + 成熟出版版式迁移"
        brand_style_direction = (
            "生成封面只围绕正统出版长期素材库、成熟出版封面方法和版式迁移 prompt 完成："
            "严格按先选母版、再抄版式、再换内容、再调细节、最后查侵权元素执行。"
            "先选择一张最适合本书的素材库封面作为主要版式母版，真实迁移标题层级、稳定留白节奏、主视觉比例、色块关系和清透数字底色；最终只输出最佳一版。"
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
            "- 最高优先级执行顺序：先选母版 → 再抄版式 → 再换内容 → 再调细节 → 最后检查是否保留侵权元素。\n"
            "- 先选母版：必须先从参考图库中选择一张最适合本书的封面作为主要版式母版；题材、气质、目标读者、上架场景、标题长度和信息量越接近越优先，不要自行发明新构图。\n"
            "- 再抄版式：尽量贴近主要母版的排版质感；标题所在区域、标题大小比例、标题分行方式、作者名位置、副标题位置、留白比例、主视觉区域、主视觉占比、主视觉与标题关系、背景色块比例、色彩明度关系、冷暖对比方式、画面重心和手机书架缩略图效果可以明显靠近参考图。\n"
            "- 再换内容：保留母版的视觉位置、构图关系和书架效果，但真实书名、作者、出版社、logo、二维码、价格、水印、已有摄影/插画、可识别人物肖像和独特图形细节必须删除或重做。\n"
            "- 再调细节：标题分行、字距、色相、明度、饱和度、点缀色、局部比例、主视觉细节和装饰符号必须做小幅原创调整，避免逐像素换皮。\n"
            "- 风格试验：内部逐个尝试强标题留白型、色块标题型、主视觉边角型、物件特写型、抽象文学型、摄影感平面型，比较后只输出最适合本书的一版。\n"
            "- 安全边界：不要把某一本真实封面简单做成换书名、换作者、换元素的版本；不要复制真实封面的完整构图组合。\n"
            "- 目标不是随机好看的 AI 图，而是像真实书架里的成熟出版物：信息组织清楚、标题层级明确、留白服务版面节奏、手机书架缩略图可读。\n"
        )
        color_and_type_rules = (
            "- 排版：可以高相似度参考长期素材库中成熟封面的标题位置、标题大小关系、标题分行方式、作者名位置、副标题位置、留白比例、文字层级、主视觉占比、主视觉与标题的相对关系、整体版面重心。\n"
            "- 母版锁定：不要从多张封面里随意拼凑；必须以一张主要母版为骨架，其他参考图只能辅助判断色彩和出版质感。\n"
            "- 背景与配色：允许使用与参考图相近的色彩气质、明度关系、冷暖关系和对比方式；但不要完全照搬某一本封面的完整配色比例，主色、辅助色、点缀色中至少调整一处。\n"
            "- 背景纯净：如果需要白底，必须是纯白 #FFFFFF 数字白；不要米白、奶油白、暖白、灰白、纸白、纸张纹理、纤维感、印刷颗粒、噪点、做旧感、扫描感、油墨渗透感；不要模拟实体纸书摄影效果，不要书页感、纸面肌理或胶版纸质感；提高整体明度与色彩纯度，避免灰蒙蒙和高级感灰滤镜，文字区域保持纯净清晰。\n"
            "- 元素：主视觉必须根据当前书名和简介重新选择；可以沿用参考封面的主视觉位置、面积比例和图文关系，但具体内容要与当前书相关，不要照搬真实封面的具体图形、摄影、插画、人物姿态或符号组合。\n"
            "- 主视觉：优先保证封面看起来像素材库里的成熟出版物；如果参考图库中某种版式非常适合当前书，可以大胆靠近它，只在具体元素、细节和内容表达上做调整。\n"
            "- 文字与装饰：标题是第一识别信息，必须清晰、有层级，适合手机书架缩略图识别；不要大段简介、营销金句、课程口号、无意义小字，也不要为了填补留白或制造高级感而补线、补点、补符号。\n"
            "- 避免模板：同批封面不要无依据地默认套用太阳、夕阳、日出圆盘、弯曲白路、绿色山坡、远山、飞鸟、小人走在路上、几何门洞、光束、人影、蓝黄对比、大标题模板、古风水彩、泛黄纸张等通用模板；如果书名或简介确实需要对应意象，必须按本书重新组织版式、配色和细节。\n"
            "- 避免质感：不要知识付费海报感、PPT课程封面感、公众号长图感、电商商品图感、网文封面感、廉价AI插画感、过度3D感或促销广告感。\n"
            "- 版权：可以像某一类成熟出版封面，可以明显借鉴某种排版方式，但不要让人一眼认出是在改某一本具体封面；禁止真实品牌背书、真实书名残影。\n"
            "- 删除：所有出版社 logo、出版品牌 logo、第三方平台 logo、原出版信息、二维码、价格、促销标签、真实书名残影、乱码文字、水印。\n"
            "- 最终自检：确认是否真的选定一张母版、版式是否贴近母版、内容是否已替换为本书相关元素、细节是否已微调、侵权风险元素是否全部删除。\n"
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
        opening = "请精修这张封面，让它更像长期素材库和成熟出版封面方法下的真实正统出版封面；不要引用固定平台系列作为风格来源。必须按先选母版、再抄版式、再换内容、再调细节、最后查侵权元素的顺序精修。排版、配色、留白和标题层级可以明显靠近参考图，但具体内容必须按本书重做。"
        composition_rule = (
            "- 先判断这张封面应靠近素材库中的哪一张主要版式母版；不要从多张封面随意拼凑。保留并强化成熟出版封面的标题区域、字号层级、标题分行、作者名位置、副标题位置、留白比例、主视觉区域、主视觉占比、图文关系、背景色块比例、色彩明度关系、冷暖对比方式和整体版面重心；"
            "同时调整主视觉内容、具体图形、人物或物件关系、标题分行细节、局部装饰和背景细节，避免成为某一本真实封面的直接换皮。"
            "不要随意保留无意义的横线、竖线、圆点、印章、编号等装饰；装饰只服务标题层级、版面结构或主视觉关系，不要为了填补留白而补线、补点、补符号。"
        )
        market_rule = (
            "- 根据内容简介重新调整主视觉和局部细节；元素必须匹配简介里的主题、场景、情绪或核心意象。"
            "不要任何纸张纹理、纤维感、印刷颗粒、噪点、做旧感、扫描感、油墨渗透感；如果需要白底，必须是纯白 #FFFFFF 数字白。"
            "不要灰蒙蒙、不要发黄做旧、不要低饱和寡淡，不要高级感灰滤镜。"
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
            "- 如果文字或背景出现脏污、破损、坑洼、发霉、擦烂、旧化、纸纹、噪点或扫描感，必须恢复纯净清晰的数字封面质感。\n"
            "- 如果元素和内容简介不匹配，必须替换为与简介主题、场景、情绪或核心意象匹配的原创元素。\n"
            "- 如果画面只是把某一本真实封面换书名、换作者、换元素，必须改变主视觉内容、具体图形、标题分行、局部装饰和背景细节。\n"
            "- 如果没有体现主要母版的标题区域、主视觉区域、留白比例、配色气质和书架效果，必须回到母版重新贴近。\n"
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
    if _is_literature_publishing(cover_input.category):
        return _build_literature_direct_cover_prompt(
            cover_input,
            reference_count=_LITERATURE_REFERENCE_DIRECT_SEQUENCE_COUNT,
        )
    if _is_simple_reference_category(cover_input.category):
        reference_count = len(_simple_reference_image_paths(cover_input.category))
        return _build_simple_reference_cover_prompt(
            cover_input,
            reference_count=reference_count,
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
    files: Any | None = None,
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


def _image_mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "image/png"


def _coerce_image_edit_paths(image_path: Path | Sequence[Path]) -> tuple[Path, ...]:
    if isinstance(image_path, Path):
        paths = (image_path,)
    else:
        paths = tuple(Path(item) for item in image_path)
    if not paths:
        raise ImageApiPermanentError("image edit requires at least one input image")
    if len(paths) > _LITERATURE_REFERENCE_DIRECT_LIMIT:
        raise ImageApiPermanentError(
            f"image edit supports at most {_LITERATURE_REFERENCE_DIRECT_LIMIT} input images"
        )
    return paths


def _image_edit_files(
    image_paths: Sequence[Path],
) -> dict[str, tuple[str, bytes, str]] | list[tuple[str, tuple[str, bytes, str]]]:
    if len(image_paths) == 1:
        image_path = image_paths[0]
        return {
            "image": (
                image_path.name,
                image_path.read_bytes(),
                _image_mime_type(image_path),
            )
        }
    return [
        (
            "image[]",
            (
                image_path.name,
                image_path.read_bytes(),
                _image_mime_type(image_path),
            ),
        )
        for image_path in image_paths
    ]


def _request_image_edit_response(
    image_path: Path | Sequence[Path],
    prompt: str,
    should_abort: Callable[[], bool] | None = None,
    *,
    stage: str = "refine",
) -> Any:
    image_paths = _coerce_image_edit_paths(image_path)
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
        "image_count": len(image_paths),
    }
    _emit_image_request_event({"event": "start", **event_base})
    with httpx.Client(
        timeout=httpx.Timeout(float(request_timeout), connect=min(60.0, float(request_timeout))),
        proxy=proxy or None,
    ) as http:
        files = _image_edit_files(image_paths)
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
    image_path: Path | Sequence[Path],
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
    files: Any | None = None,
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


def _build_literature_direct_cover_prompt(
    cover_input: CoverInput,
    *,
    reference_count: int,
) -> str:
    replacement_profile = _orthodox_element_replacement_profile(cover_input.category)
    if reference_count == 1:
        title_reference_rule = (
            "本次只输入 1 张按全局 ZIP 顺序轮转选中的参考封面；它就是唯一母版、书名标题主参考、版式主参考和色彩主参考。"
            "不要再选择别的方向，不要泛泛学习大图库，不要混合重组，不要自由发明新构图。"
        )
    else:
        title_reference_rule = (
            f"本次输入 {reference_count} 张参考封面时，必须只选择其中 1 张作为唯一母版；其余参考图只能辅助理解真实出版质感。"
        )
    return f"""正统出版单封面 image-to-image 结构强参考工作流

当前新书信息：
- 书名：{cover_input.title}
- 作者：{cover_input.author}
- 分类：{cover_input.category_label}
- 测试/替换档位：{replacement_profile['level']}
- 简介：{cover_input.intro}

参考输入与轮转规则：
- 已随请求附上 {reference_count} 张来自本地“参考封面(1).zip / 正统出版ZIP参考封面库”的成熟出版封面。
- 正统出版参考图按 ZIP 内排序全局轮转，不按单个任务重置，不随机抽样：全局第 1 张封面生成模仿 ZIP 第 1 张，全局第 256 张模仿 ZIP 第 256 张，全局第 257 张从 ZIP 第 1 张重新开始。
- {title_reference_rule}
- 本步骤只做一件事：把输入参考封面当作 image-to-image 母版，学习它的设计骨架和出版气质，生成当前书的新封面初稿。不要输出分析文字，不要走传统 style_brief、draft、refine 旧链路，不要做自由发散的自主视觉方案；后续会有正统出版专用 image-to-image 精修步骤继续打磨排版细节。

分层模仿要求：
- 只输出一张竖版中文图书正面封面本体。
- 书名必须使用“{cover_input.title}”，作者必须使用“{cover_input.author or _AUTHOR_PLACEHOLDER}”；不要保留参考图里的真实书名、原作者、出版社、平台信息或任何残影。
- 封面主书名必须与“{cover_input.title}”逐字完全一致：不能自己添加前缀、后缀、副标题、系列名、解释性短语、营销词、标点变体或额外汉字；不能删字、换字、改词序、改标点、拆错词、合并词或把书名扩写成另一句话。书名本身包含的冒号、问号、顿号、空格、数字和英文必须原样保留。
- 书名标题排版必须 100% 抄到位：复制标题设计语法，而不是随便换一种普通字体。必须贴着母版模仿标题字形气质、字重、字号比例、分行节奏、横排/竖排/半竖排关系、字距行距、标题块位置、标题与留白/主视觉的关系；标题区要像参考图里成熟设计师做出的同系列新书标题。
- 版式骨架必须贴着母版：版心结构、标题区域、作者名位置、副标题位置、留白比例、主视觉位置、主视觉面积、主视觉与标题的相对关系、背景色块比例、装饰点线的位置功能、整体画面重心和手机书架缩略图效果，都要明显像母版；这部分保留抄袭的魂魄和大部分外形。
- 色彩结构必须贴着母版：主色、辅助色、明暗关系、冷暖关系、色块面积关系和整体明度纯度要接近母版；可以对色相、局部比例和饱和度做轻微迁移，避免像同一张封面直接改字。
- 主视觉必须替换，不能直接沿用母版里的具体视觉元素。动物、人物、物件、植物、建筑、图标、抽象符号、照片、插画主体、独特轮廓、纹样、局部装饰组合，都必须根据当前书名和简介重新设计成新的视觉资产。
- 核心元素和装饰替换是硬约束：母版里的主视觉主体、配角、背景点缀、角标、边栏、贴纸、徽章、印章、圆点、箭头、气泡、线框、边框、纹样、图标、符号、局部小插画、装饰线、重复小图案、底纹和任何有记忆点的细节，都不能原样搬到新封面。可以模仿它们在版式里的“功能、位置、数量级、疏密和节奏”，但必须换成新的形状、新的组合、新的局部语义。
- {replacement_profile['direct_rule']}
- 替换主视觉时只保留“视觉角色”，不保留“具体身份”：可以保留母版中主视觉的位置、面积、重量、疏密、朝向关系、与标题的距离和画面节奏，但要更换物件类别、人物姿态、动物种类、符号造型、插画线条和细节组合。最终不能让人一眼认出原参考封面里的猫、椅子、头像、人物姿态、图标组合、摄影图或独特图形。
- 如果母版主视觉是猫、狗、鸟、椅子、杯子、人物头像、钥匙孔、手机、书堆、山水、圆形色块或任何强记忆点，不要原样保留；除非当前书名或简介明确需要同类元素，也必须重画成不同姿态、不同轮廓、不同细节、不同组合的原创元素。
- 当前书的特色只来自书名、作者、简介语义和重画后的主视觉内容；不能通过改变母版标题排版、版式骨架、色彩结构、图文比例来制造差异，也不能通过复制母版具体元素来偷懒。
- 如果最终看不出它借鉴了母版的标题排版、版式骨架、色彩结构和图文比例，视为失败；如果能被认出是某张参考封面的换皮、描摹或套壳，也视为失败。最终效果要像同一设计系统下的新书封面：魂魄像、外形大体像，但具体视觉元素已经彻底换掉。
- 版权替换规则：真实书名、原作者、出版社、平台标识、出版社 logo、二维码、价格、水印、真实品牌资产、真实摄影作品、已有插画作品、可识别人物肖像、独特图形细节和可识别主视觉元素，都必须删除或重画替换；只学习版式方法、比例关系、标题设计和出版气质。
- 封面上的金句可以少一点：除书名、作者和必要副标题外，最多 0 到 1 句短金句；标题已经足够清楚时不要加金句；禁止多段营销文案、课程口号、长清单和密集小字。
- 如果需要白底，必须是纯白 #FFFFFF 数字白；不要米白、奶油白、暖白、灰白或纸白。不要任何纸张纹理、纤维感、印刷颗粒、噪点、做旧感、扫描感、油墨渗透感；不要模拟实体纸书摄影效果，不要书页感、纸面肌理或胶版纸质感。提高整体明度与色彩纯度，避免灰蒙蒙和所谓高级感灰滤镜。文字区域必须纯净清晰，不要脏点、坑洼、破损、斑驳、划痕、掉墨、褶皱、旧纸感或做旧效果。
- 不要做成随机 AI 图、PPT 课程封面、知识付费海报、公众号长图、电商主图、网文封面、3D 书模或促销广告。
- 不要为了原创而远离输入母版的结构；最终效果要像成熟出版封面的同系列新作，而不是抽象地“有出版感”。但不要把母版中的具体视觉元素、独特图形和可识别插画资产带到新封面里。
"""


def _build_literature_direct_refine_prompt(cover_input: CoverInput) -> str:
    replacement_profile = _orthodox_element_replacement_profile(cover_input.category)
    return f"""正统出版封面第二次 image-to-image 精修工作流

当前输入是一张已经按 ZIP 单张参考封面完成结构模仿、并替换过具体视觉元素的正统出版封面初稿。请只在这张初稿基础上做出版级精修，不要重新构图，不要换风格方向，不要改成另一张封面。

当前新书信息：
- 书名：{cover_input.title}
- 作者：{cover_input.author or _AUTHOR_PLACEHOLDER}
- 分类：{cover_input.category_label}
- 测试/替换档位：{replacement_profile['level']}
- 简介：{cover_input.intro}

精修目标：
- 保留初稿的大版式、标题位置、标题分行、横竖排关系、主色结构、主视觉位置、主视觉面积和整体外形；不要削弱第一步对参考封面的结构模仿。
- 保留第一步已经重画替换后的主视觉，不要把它改回参考封面的具体猫、椅子、头像、人物姿态、图标组合、摄影图、独特图形或其他可识别版权元素。
- 核心元素和装饰复查：母版中的角标、边栏、贴纸、徽章、印章、圆点、箭头、气泡、线框、边框、纹样、小图标、装饰线、重复小图案、底纹、背景点缀和局部小插画，不能在精修中被补回或描得更像原图；只能保留它们的排版功能，必须换成新的形状、组合、细节和语义。
- {replacement_profile['refine_rule']}
- 重点增加排版细节，让封面更像成熟出版设计师的终稿：微调字距、行距、基线、标题块边界、作者名位置、副标题位置、留白边距、对齐关系、点线装饰、局部小标识、色块边界和图文呼吸感。
- 中文主书名必须仍然是“{cover_input.title}”，逐字完全一致、清晰、准确、可读；不要改错字，不要漏字，不要多字，不要少字，不要改标点，不要自己添加前缀、后缀、副标题、系列名、解释性短语或营销词。作者必须仍然是“{cover_input.author or _AUTHOR_PLACEHOLDER}”，不要把作者名替换成英文作者或假出版社。
- 根据书名和简介，允许增加 0 到 1 处很小的英文辅助排版：可以是书名英文翻译、主题英文关键词或极短英文副标题，建议 1 到 4 个英文单词，例如放在标题旁、页眉/页脚、小色块、竖排边栏或装饰线附近。英文只做高级出版细节，不要喧宾夺主。
- 英文辅助信息只能是很小的装饰性副信息，不能替代、覆盖、扩写或改写中文主书名；不能让封面看起来像主书名变成了“{cover_input.title} + 额外信息”。
- 英文必须简短、准确、像真实书封上的设计元素；不要长句、不要英文段落、不要 fake praise、不要 ISBN、不要二维码、不要出版社 logo、不要价格、不要营销话术。
- 如果初稿有白底，保持纯白 #FFFFFF 数字白；不要米白、奶油白、暖白、灰白、纸白，不要纸张纹理、纤维、印刷颗粒、噪点、做旧、扫描感、油墨渗透感或高级感灰滤镜。
- 提高整体明度、色彩纯度和文字锐度；让封面在手机书架缩略图里更干净、更高级、更像正式出版封面。
- 只输出一张竖版中文图书正面封面本体；不要 3D 书模、不要展示场景、不要额外说明文字。
"""


def _literature_direct_style_payload(
    cover_input: CoverInput,
    reference_records: Sequence[dict[str, str]],
) -> dict[str, Any]:
    replacement_profile = _orthodox_element_replacement_profile(cover_input.category)
    return {
        "workflow": "orthodox_direct_single_reference_imitation",
        "reference_site": "本地正统出版ZIP参考封面库 + 全局顺序单母版结构强参考",
        "reference_brands": ["正统出版ZIP参考封面库"],
        "matched_category_style": "正统出版单封面 image-to-image 结构强参考",
        "brand_style_direction": (
            "按全局 ZIP 顺序选 1 张参考封面作为唯一母版，只抄排版、配色和字体/标题排版，"
            f"同时按「{replacement_profile['label']} / {replacement_profile['level']}」重画替换核心视觉元素和装饰元素。"
        ),
        "cover_type": "正统出版 / 单母版结构强参考成熟出版封面",
        "element_replacement_profile": dict(replacement_profile),
        "reference_samples": list(reference_records),
        "generation_prompt_addendum": "单张参考封面 image-to-image 结构强参考；标题排版和版式贴近母版，具体视觉元素重画替换；再用正统出版专用 image-to-image 精修排版细节和少量英文；不走旧 style_brief/draft/refine 链路。",
        "refine_step": {
            "workflow": "orthodox_direct_image_edit_refine",
            "input": "draft_cover.png",
            "output": "cover.png",
            "goal": "提升排版细节、出版高级感和少量英文辅助排版，同时保留第一步结构模仿和主视觉替换成果。",
        },
        "title": cover_input.title,
        "author": cover_input.author,
        "intro": cover_input.intro,
        "category": cover_input.category,
        "category_label": cover_input.category_label,
    }


def _generate_literature_cover_direct(
    cover_input: CoverInput,
    output_dir: Path,
    *,
    force: bool = False,
    should_abort: Callable[[], bool] | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> Path:
    cover_path = output_dir / "cover.png"
    draft_path = output_dir / "draft_cover.png"
    draft_response_path = output_dir / "cover_draft_response.json"
    draft_prompt_path = output_dir / "cover_draft_prompt.txt"
    refine_response_path = output_dir / "cover_refine_response.json"
    refine_prompt_path = output_dir / "cover_refine_prompt.txt"
    samples_path = output_dir / "orthodox_reference_samples.json"
    sequence_path = output_dir / "orthodox_reference_sequence.json"
    style_brief_path = output_dir / "cover_style_brief.json"

    if cover_path.exists() and cover_path.stat().st_size > 0 and not force:
        logger.info("复用已存在正统出版封面: %s", cover_path)
        return cover_path

    members = _selected_literature_direct_reference_members(output_dir, force=force)
    zip_path = _literature_reference_zip_path()
    all_members = _literature_reference_members(zip_path)
    sequence_index = _literature_reference_sequence_index(all_members, members)
    reference_records = _literature_reference_records(zip_path, members)
    _write_cover_text_file(
        samples_path,
        json.dumps(reference_records, ensure_ascii=False, indent=2),
        should_abort,
    )
    _write_cover_text_file(
        sequence_path,
        json.dumps(
            {
                "policy": "global_zip_order_single_reference",
                "state_path": str(_orthodox_reference_sequence_state_path()),
                "zip_path": str(zip_path),
                "members_count": len(all_members),
                "selected_sequence_index": sequence_index,
                "selected_members": list(members),
                "force": force,
            },
            ensure_ascii=False,
            indent=2,
        ),
        should_abort,
    )
    reference_images = _extract_literature_reference_inputs(members, output_dir, should_abort)
    direct_prompt = _build_literature_direct_cover_prompt(
        cover_input,
        reference_count=len(reference_images),
    )
    previous_direct_prompt = ""
    if draft_prompt_path.exists() and draft_prompt_path.stat().st_size > 0:
        try:
            previous_direct_prompt = draft_prompt_path.read_text(encoding="utf-8")
        except OSError:
            previous_direct_prompt = ""
    prompt_changed = bool(previous_direct_prompt and previous_direct_prompt != direct_prompt)
    _write_cover_text_file(draft_prompt_path, direct_prompt, should_abort)
    refine_prompt = _build_literature_direct_refine_prompt(cover_input)
    previous_refine_prompt = ""
    if refine_prompt_path.exists() and refine_prompt_path.stat().st_size > 0:
        try:
            previous_refine_prompt = refine_prompt_path.read_text(encoding="utf-8")
        except OSError:
            previous_refine_prompt = ""
    refine_prompt_changed = bool(previous_refine_prompt and previous_refine_prompt != refine_prompt)
    _write_cover_text_file(refine_prompt_path, refine_prompt, should_abort)
    _write_cover_text_file(
        style_brief_path,
        json.dumps(_literature_direct_style_payload(cover_input, reference_records), ensure_ascii=False, indent=2),
        should_abort,
    )

    logger.info(
        "开始正统出版直连参考封面生成: model=%s size=%s title=%s references=%d",
        settings.IMAGE_MODEL,
        settings.IMAGE_SIZE,
        cover_input.title,
        len(reference_images),
    )
    _notify_cover_progress(on_progress, "draft")
    _abort_cover_generation_if_requested(should_abort, stage="before orthodox direct image request")
    cover_bytes = _request_or_reuse_image_response(
        draft_response_path,
        lambda: _request_image_edit_response(
            reference_images,
            direct_prompt,
            should_abort=should_abort,
            stage="draft_reference",
        ),
        should_abort=should_abort,
        force=force or prompt_changed,
    )
    _write_cover_bytes_file(draft_path, cover_bytes, should_abort)
    logger.info("开始正统出版封面二次精修: model=%s size=%s title=%s", settings.IMAGE_MODEL, settings.IMAGE_SIZE, cover_input.title)
    try:
        _notify_cover_progress(on_progress, "refine")
        _abort_cover_generation_if_requested(should_abort, stage="before orthodox refinement image request")
        refined_cover_bytes = _request_or_reuse_image_response(
            refine_response_path,
            lambda: _request_image_edit_response(
                (draft_path,),
                refine_prompt,
                should_abort=should_abort,
                stage="orthodox_refine",
            ),
            should_abort=should_abort,
            force=force or prompt_changed or refine_prompt_changed,
        )
    except ImageApiUncertainError as exc:
        logger.warning("正统出版封面二次精修结果不确定，使用已生成初稿作为最终封面: %s", exc)
        _write_cover_text_file(
            output_dir / "cover_refine_error.txt",
            f"二次精修结果不确定，已使用第一步初稿作为最终封面：{exc}",
            should_abort,
        )
        _notify_cover_progress(on_progress, "copy")
        _write_cover_bytes_file(cover_path, cover_bytes, should_abort)
        return cover_path
    except ImageApiTransientError as exc:
        logger.warning("正统出版封面二次精修遇到临时错误，保留初稿等待自动重试: %s", exc)
        _write_cover_text_file(
            output_dir / "cover_refine_error.txt",
            f"临时错误，等待自动重试：{exc}",
            should_abort,
        )
        raise
    except ImageApiPermanentError as exc:
        logger.warning("正统出版封面二次精修遇到永久错误，保留初稿但不降级为最终封面: %s", exc)
        _write_cover_text_file(output_dir / "cover_refine_error.txt", str(exc), should_abort)
        raise
    except Exception as exc:
        logger.warning("正统出版封面二次精修失败，保留初稿但不降级为最终封面: %s", exc)
        _write_cover_text_file(output_dir / "cover_refine_error.txt", str(exc), should_abort)
        raise RuntimeError(
            f"正统出版封面二次精修失败：{exc}。已保留 draft_cover.png 供排查，未生成最终 cover.png"
        ) from exc

    _notify_cover_progress(on_progress, "copy")
    _write_cover_bytes_file(cover_path, refined_cover_bytes, should_abort)
    return cover_path


def _build_simple_reference_cover_prompt(
    cover_input: CoverInput,
    *,
    reference_count: int,
) -> str:
    category_id = _simple_reference_category_id(cover_input.category)
    if category_id is None:
        raise ImageApiPermanentError(f"unsupported simple reference cover category: {cover_input.category}")
    profile = _SIMPLE_REFERENCE_CATEGORY_PROFILES[category_id]
    visual_plan = _simple_reference_visual_plan_text(cover_input)
    return f"""基础封面生成 prompt

你是中文图书封面设计师。请根据当前书名、作者和简介，参考随请求附上的 {reference_count} 张「{profile['label']}」示例封面，直接生成一张类似风格的新书封面。

书籍信息：
- 书名：{cover_input.title}
- 作者：{cover_input.author}
- 分类：{cover_input.category_label}
- 简介：{cover_input.intro}

分类风格 DNA：
- {profile['style_dna']}
- 示例图是这个分类的风格 DNA，不是每本书都复制同一配色、同一标题位置、同一人物剪影或同一红/橙色大字模板。
- 必须完整学习示例图的出版感、标题冲击力、色彩明确度、图文关系和书架识别度；但要根据当前书名和简介重组配色、版式、主视觉和文字密度。

本书差异化视觉方案：
{visual_plan}

参考方式：
- 完整参考这些示例封面的整体方向：{profile['prompt_hint']}
- 重点学习示例封面的构图、标题位置、标题大小关系、标题分行方式、配色、主视觉位置、图文关系和整体书架气质。
- 具体书名、作者、画面主体和细节要换成当前这本书；不要保留示例封面里的原书名、原作者或出版社文字。
- 必须执行“本书差异化视觉方案”中的配色主轴、版式骨架、标题策略、主视觉叙事和金句策略；不要把同一批书做成同一种颜色、同一种排版、同一种剪影或同一种大字模板。
- 封面上的金句可以少一点：除书名、作者和必要副标题外，最多只允许 0 到 1 句短金句；标题已经足够清楚时不要加金句；禁止多段营销文案、长清单和密集小字。

最终输出：
只输出一张竖版中文图书正面封面本体，不要输出说明文字、拼贴图或多张候选图。
"""


def _simple_reference_style_payload(
    cover_input: CoverInput,
    reference_records: Sequence[dict[str, str]],
) -> dict[str, Any]:
    category_id = _simple_reference_category_id(cover_input.category)
    if category_id is None:
        raise ImageApiPermanentError(f"unsupported simple reference cover category: {cover_input.category}")
    profile = _SIMPLE_REFERENCE_CATEGORY_PROFILES[category_id]
    visual_plan = _simple_reference_visual_plan(cover_input)
    return {
        "workflow": "simple_reference_cover",
        "reference_site": f"封面分类.zip / {profile['label']}",
        "reference_brands": ["封面分类ZIP参考库"],
        "matched_category_style": f"{profile['label']}: {profile['style']}",
        "brand_style_direction": profile["prompt_hint"],
        "cover_type": profile["cover_type"],
        "reference_samples": list(reference_records),
        "generation_prompt_addendum": profile["prompt_hint"],
        "visual_plan": visual_plan,
        "title": cover_input.title,
        "author": cover_input.author,
        "intro": cover_input.intro,
        "category": cover_input.category,
        "category_label": cover_input.category_label,
    }


def _generate_simple_reference_cover_direct(
    cover_input: CoverInput,
    output_dir: Path,
    *,
    force: bool = False,
    should_abort: Callable[[], bool] | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> Path:
    category_id = _simple_reference_category_id(cover_input.category)
    if category_id is None:
        raise ImageApiPermanentError(f"unsupported simple reference cover category: {cover_input.category}")
    profile = _SIMPLE_REFERENCE_CATEGORY_PROFILES[category_id]
    cover_path = output_dir / "cover.png"
    draft_path = output_dir / "draft_cover.png"
    draft_response_path = output_dir / "cover_draft_response.json"
    draft_prompt_path = output_dir / "cover_draft_prompt.txt"
    samples_path = output_dir / "cover_reference_samples.json"
    style_brief_path = output_dir / "cover_style_brief.json"

    if cover_path.exists() and cover_path.stat().st_size > 0 and not force:
        logger.info("复用已存在%s封面: %s", profile["label"], cover_path)
        return cover_path

    reference_images = _simple_reference_image_paths(category_id)
    if not reference_images:
        raise ImageApiPermanentError(
            f"{profile['label']}参考封面缺失，无法按封面分类.zip样式生成: {_simple_reference_asset_dir(category_id)}"
        )
    reference_records = _simple_reference_records(category_id, reference_images)
    prompt = _build_simple_reference_cover_prompt(
        cover_input,
        reference_count=len(reference_images),
    )
    _write_cover_text_file(
        samples_path,
        json.dumps(reference_records, ensure_ascii=False, indent=2),
        should_abort,
    )
    _write_cover_text_file(draft_prompt_path, prompt, should_abort)
    _write_cover_text_file(
        style_brief_path,
        json.dumps(_simple_reference_style_payload(cover_input, reference_records), ensure_ascii=False, indent=2),
        should_abort,
    )

    logger.info(
        "开始%s轻量参考封面生成: model=%s size=%s title=%s references=%d",
        profile["label"],
        settings.IMAGE_MODEL,
        settings.IMAGE_SIZE,
        cover_input.title,
        len(reference_images),
    )
    _notify_cover_progress(on_progress, "draft")
    _abort_cover_generation_if_requested(should_abort, stage="before simple reference image request")
    image_bytes = _request_or_reuse_image_response(
        draft_response_path,
        lambda: _request_image_edit_response(
            reference_images,
            prompt,
            should_abort=should_abort,
            stage="draft_reference",
        ),
        should_abort=should_abort,
        force=force,
    )
    _write_cover_bytes_file(draft_path, image_bytes, should_abort)
    _notify_cover_progress(on_progress, "copy")
    _write_cover_bytes_file(cover_path, image_bytes, should_abort)
    return cover_path


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

    cover_input = prepare_cover_input(
        title,
        markdown,
        author=author,
        intro=intro,
        category=category,
    )
    if _is_literature_publishing(cover_input.category):
        return _generate_literature_cover_direct(
            cover_input,
            output_dir,
            force=force,
            should_abort=should_abort,
            on_progress=on_progress,
        )
    if _is_simple_reference_category(cover_input.category):
        return _generate_simple_reference_cover_direct(
            cover_input,
            output_dir,
            force=force,
            should_abort=should_abort,
            on_progress=on_progress,
        )

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
            "必须把这张图当作最主要的版式和风格参考板，严格按先选母版、再抄版式、再换内容、再调细节、最后查侵权元素执行。"
            "先从其中选择一张最适合本书的封面作为主要版式母版，不要从多张封面随意拼凑；"
            "迁移标题区域、字号层级、标题分行、作者名位置、副标题位置、留白比例、主视觉区域、主视觉占比、图文关系、背景色块比例、色彩明度关系、冷暖对比方式和整体书架气质。"
            "再替换真实书名、作者、出版社、logo、二维码、价格、水印、摄影、插画、人物肖像和独特图形细节，并微调局部比例、色相、字距、装饰和背景纹理。"
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
    force: bool = False,
    should_abort: Callable[[], bool] | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> Path:
    """Generate one standalone cover from spreadsheet metadata.

    The standalone Excel entry intentionally reuses the same high-quality
    draft + refine cover workflow used by manuscript tasks. It writes to a
    per-item work directory first, then copies the final cover to output_path.
    """
    _abort_cover_generation_if_requested(should_abort, stage="metadata start")
    if output_path.exists() and output_path.stat().st_size > 0 and not force:
        logger.info("复用已存在独立封面: %s", output_path)
        return output_path

    title = _metadata_lookup(metadata, _METADATA_TITLE_KEYS) or output_path.stem
    author = _metadata_lookup(metadata, _METADATA_AUTHOR_KEYS) or None
    intro = _metadata_lookup(metadata, _METADATA_INTRO_KEYS) or title
    category = _metadata_category(_metadata_lookup(metadata, _METADATA_CATEGORY_KEYS))
    metadata = dict(metadata)
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
        force=force,
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
