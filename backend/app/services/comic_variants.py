"""Independent story-comic and homophone-comic pipelines.

The established healing-comic pipeline stays the default and is not routed
through this module.  The two variants reuse its image protection, manifests,
pause semantics and markdown image injection while keeping separate prompt
contracts and copy validation.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Callable

from app.core.config import settings
from app.services.comic_series import (
    COMIC_COVER_IMAGE_FILENAME,
    ComicGenerationPaused,
    ComicSectionImage,
    _generate_image_with_comic_protection,
    _write_manifest,
    parse_healing_comic_panel_count,
    parse_healing_comic_title,
)

STORY_COMIC_CATEGORY = "story_comic"
HOMOPHONE_COMIC_CATEGORY = "homophone_comic"
COMIC_VARIANT_CATEGORIES = frozenset({STORY_COMIC_CATEGORY, HOMOPHONE_COMIC_CATEGORY})
COMIC_VARIANT_PIPELINE_VERSION = "carousel-v3-unified-paper"
HOMOPHONE_COMIC_PIPELINE_VERSION = "carousel-v4-handwritten-marker"
COMIC_VARIANT_MIN_CARDS = 6
COMIC_VARIANT_MAX_CARDS = 10
HEALING_COMIC_FAMILY_CATEGORIES = frozenset(
    {"healing_comic", STORY_COMIC_CATEGORY, HOMOPHONE_COMIC_CATEGORY}
)

_PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"
_FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
_TERMINAL_PUNCTUATION = "。！？"
_NON_TERMINAL_PUNCTUATION_RE = re.compile(r"[。！？!?]+")
_DASH_RE = re.compile(r"[—–﹣－]+")
_STALE_PUN_PAIRS = {
    frozenset(("压力", "鸭力")),
    frozenset(("惊喜", "鲸喜")),
    frozenset(("好事", "好柿")),
    frozenset(("期待", "脐带")),
    frozenset(("知足", "蜘蛛")),
    # Production samples proved that these pairs become soft illustrations,
    # not jokes with a real reversal.  Keep them out of future batches.
    frozenset(("晚点", "碗垫")),
    frozenset(("鼓励", "谷粒")),
    frozenset(("踏实", "踏石")),
}

_HOMOPHONE_ROAST_MODES = frozenset(
    {
        "self_roast",
        "upward_satire",
        "peer_banter",
        "boast_deflation",
        "excuse_exposure",
        "double_standard",
        "empty_promise",
        "literal_misread",
        "dry_absurdity",
        "delayed_reveal",
        "observational_satire",
    }
)
_HOMOPHONE_SCORE_KEYS = (
    "naturalness",
    "surprise",
    "visual_payoff",
    "freshness",
    "instant_clarity",
    "laugh_out_loud",
    "story_logic",
    "delayed_reveal",
    "phonetic_payoff",
    "human_rhythm",
    "forcedness",
    "harm_risk",
)
_HOMOPHONE_EXPLANATION_RE = re.compile(
    r"(?:这个梗|谐音梗|意思是|也就是说|换句话说|原来是|所谓的.+就是)"
)
_HOMOPHONE_SOFT_ENDING_RE = re.compile(
    r"(?:终于都有地方|慢慢变|一步一步|有了起飞的劲|温柔地|轻轻地|暖暖地)"
)
_HOMOPHONE_AI_CAPTION_RE = re.compile(
    r"(?:替[他她它]?(?:认罪|开口)|把(?:奖金|承诺|梦想|箭头).{0,8}(?:戳|扎|砸|压)成|"
    r"箭头.{0,8}扎向|笑不活|直接封神|狠狠拿捏)"
)

_VARIANT_BACKGROUND_NAMES = (
    "清亮天蓝色（约 #AEE8F4）",
    "明快奶油黄色（约 #FFE39A）",
    "清新薄荷绿色（约 #CBEFBE）",
    "柔亮珊瑚粉色（约 #FFC6BD）",
    "清透淡紫色（约 #DCCFF6）",
)


def _variant_background_name(category: str, seed: object) -> str:
    """Choose one stable full-page colour for every card in an episode."""

    digest = hashlib.sha256(f"{category}:{seed}".encode("utf-8")).digest()
    return _VARIANT_BACKGROUND_NAMES[digest[0] % len(_VARIANT_BACKGROUND_NAMES)]


def variant_pipeline_version(category: str) -> str:
    """Return a category-scoped cache version.

    Story comics intentionally stay on their established pipeline.  Only the
    homophone branch invalidates its image/script cache for the new art system.
    """

    if category == HOMOPHONE_COMIC_CATEGORY:
        return HOMOPHONE_COMIC_PIPELINE_VERSION
    return COMIC_VARIANT_PIPELINE_VERSION

_STORY_FALLBACKS = (
    {
        "caption": "天色暗下来以前，她终于把替邻居收好的衣服送回了门口。",
        "protagonist": "穿浅蓝外套、背帆布包的原创年轻邮差",
        "place": "老居民楼的露天走廊",
        "time_weather": "阵雨刚停的傍晚",
        "relationship": "新搬来的住户与独居邻居",
        "key_prop": "一篮差点被雨淋湿的衣服",
        "shot_design": "由走廊远景推进到门口近景",
        "story_setup": "风把无人照看的衣服吹到栏杆边",
        "story_change": "主角收好衣服并逐户询问主人",
        "story_ending": "门开时，屋里的人递出一碗刚煮好的汤",
    },
    {
        "caption": "末班车已经开走了，好在那盏修好的车灯还陪他们慢慢回家。",
        "protagonist": "戴圆框眼镜的原创修车摊青年与拄伞老人",
        "place": "河堤边的旧自行车修理摊",
        "time_weather": "有薄雾的夜晚",
        "relationship": "陌生修车人和错过末班车的老人",
        "key_prop": "一盏重新亮起的自行车灯",
        "shot_design": "俯视修理动作后切到河堤长镜头",
        "story_setup": "老人推着坏车停在已经收摊的棚前",
        "story_change": "修车人重新拉开工具箱修亮车灯",
        "story_ending": "两个人推车沿着河堤的光慢慢走远",
    },
    {
        "caption": "她原本只想借一把剪刀，离开时却抱走了窗台上新开的花。",
        "protagonist": "卷发的原创花店学徒与隔壁裁缝",
        "place": "老街花店和相邻裁缝铺的后门",
        "time_weather": "春日下午的斜阳",
        "relationship": "每天见面却很少说话的邻店伙伴",
        "key_prop": "一把旧剪刀和一盆刚开的雏菊",
        "shot_design": "门缝近景转为两间店铺的横向中景",
        "story_setup": "花店学徒临时找不到修枝剪",
        "story_change": "裁缝递来剪刀，又把窗台的雏菊推过去",
        "story_ending": "两扇后门都没有关，花被放在中间晒太阳",
    },
)

_HOMOPHONE_FALLBACKS = (
    {
        "source_word": "报销",
        "pun_word": "抱箱",
        "pinyin_pair": "bào xiāo / bào xiāng",
        "caption": "财务说月底报销，它却已经认真抱箱半个月了。",
        "protagonist": "戴深绿色邮差帽的原创小水獭和橘色狐狸同事",
        "place": "社区快递柜旁的办公室走廊",
        "relationship": "做事过分认真的同事和看不下去的邻座",
        "key_prop": "一个每天都在变大的空纸箱和一张发票",
        "target_behavior": "把一句普通流程通知执行得过分具体",
        "roast_mode": "literal_misread",
        "audience_expectation": "同事以为水獭抱着纸箱是在等一件重要快递",
        "misread_trigger": "狐狸发现水獭连续很多天把同一个空箱抱到工位",
        "literal_action": "水獭每天认真抱着纸箱去财务室门口排队",
        "status_reversal": "大家最后才发现它只是把报销听成了抱箱",
        "target_reaction": "狐狸看了看发票，又看了看已经压扁的纸箱",
        "base_roast": "过分认真的人把一句流程用语执行成了一项体力工作",
        "pun_advantage": "报销与抱箱让一项抽象流程变成连续半个月的可见动作",
        "delivery_mode": "dry_office_misread",
        "story_setup": "小水獭每天一上班就抱着同一个空纸箱",
        "story_change": "纸箱越换越大，狐狸终于问它到底在办什么",
        "story_ending": "水獭掏出发票，说财务答应月底报销",
        "layout": "four_panel",
        "panel_script": [
            {"panel": 1, "visual": "狐狸经理站在白板前郑重宣布消息，员工们从困倦变成期待"},
            {"panel": 2, "visual": "经理把一摞鼓鼓的信封逐一放到每个人桌上"},
            {"panel": 3, "visual": "员工同时拆开信封，里面各躺着一块夹心饼干"},
            {"panel": 4, "visual": "全屋沉默盯住仍在鼓掌的经理，经理的笑容僵在脸上"},
        ],
        "scores": {
            "naturalness": 9,
            "surprise": 9,
            "visual_payoff": 9,
            "freshness": 8,
            "instant_clarity": 10,
            "laugh_out_loud": 9,
            "roast_precision": 9,
            "status_reversal": 9,
            "memorability": 9,
            "base_comedy_strength": 9,
            "caption_punch": 9,
            "forcedness": 1,
            "harm_risk": 0
        },
    },
    {
        "source_word": "有才",
        "pun_word": "油菜",
        "pinyin_pair": "yǒu cái / yóu cài",
        "caption": "他刚夸自己全班最有才，下一秒就抱着一捆油菜上台了。",
        "protagonist": "总爱抢答的小孔雀和表情平静的山羊老师",
        "place": "手工课快结束的小学教室",
        "relationship": "爱自夸的学生和不拆穿人的老师",
        "key_prop": "一捆还沾着泥的油菜",
        "target_behavior": "还没展示本事就先把自己夸满的虚荣",
        "roast_mode": "boast_deflation",
        "audience_expectation": "小孔雀连续举手，自称是全班最有才的人",
        "misread_trigger": "老师微笑着请他把才华拿到讲台上给大家看看",
        "literal_action": "他冲出教室又抱回一大捆油菜",
        "status_reversal": "原本等着被崇拜的学生成了全班沉默注视的对象",
        "target_reaction": "小孔雀抱着油菜等掌声，老师只把菜篮递给他",
        "base_roast": "还没有展示能力就先自封第一的人，真正上台时只剩尴尬",
        "pun_advantage": "有才与油菜让空洞自夸变成沉重又可见的舞台道具",
        "delivery_mode": "boast_demolition",
        "story_setup": "小孔雀在手工课上不断抢答，夸自己全班最有才",
        "story_change": "老师请他上台展示，他兴奋地跑出教室搬东西",
        "story_ending": "他抱回一捆油菜站上讲台，全班和老师一起沉默",
        "layout": "three_panel",
        "panel_script": [
            {"panel": 1, "visual": "小孔雀高高举手自信发言，同学们回头看他"},
            {"panel": 2, "visual": "山羊老师伸手请他上台，小孔雀兴奋跑出教室"},
            {"panel": 3, "visual": "小孔雀抱着一捆带泥油菜等掌声，老师和全班安静看着他"},
        ],
        "scores": {
            "naturalness": 9,
            "surprise": 9,
            "visual_payoff": 10,
            "freshness": 8,
            "instant_clarity": 10,
            "laugh_out_loud": 9,
            "roast_precision": 8,
            "status_reversal": 9,
            "memorability": 9,
            "base_comedy_strength": 8,
            "caption_punch": 8,
            "forcedness": 1,
            "harm_risk": 0
        },
    },
    {
        "source_word": "不知所措",
        "pun_word": "不知锁错",
        "pinyin_pair": "bù zhī suǒ cuò / bù zhī suǒ cuò",
        "caption": "它在更衣室试了半天，最后承认自己不知锁错了哪一格。",
        "protagonist": "戴圆眼镜的原创猫头鹰和抱着毛巾的河狸朋友",
        "place": "游泳馆快关门的更衣室",
        "relationship": "把储物柜钥匙混在一起的两个朋友",
        "key_prop": "一串没有号码牌的旧储物柜钥匙",
        "target_behavior": "做事不留标记，出问题后只能挨个试",
        "roast_mode": "self_roast",
        "audience_expectation": "河狸以为猫头鹰只是忘了自己的柜子号码",
        "misread_trigger": "猫头鹰拿着一串钥匙，从第一排一直试到最后一排",
        "literal_action": "它承认自己不但忘了号码，还可能把包锁进了别人的柜子",
        "status_reversal": "平时最爱教别人整理的猫头鹰被自己的钥匙困住",
        "target_reaction": "河狸抱着毛巾坐下，决定先看它把整排试完",
        "base_roast": "最爱讲整理方法的人，最后被自己没有标记的钥匙难住",
        "pun_advantage": "不知所措与不知锁错让慌乱来自一个非常具体的错误",
        "delivery_mode": "self_roast_locker",
        "story_setup": "游泳馆快关门时，猫头鹰还在第一排储物柜前试钥匙",
        "story_change": "它从第一格一路试到最后一格，河狸终于问发生了什么",
        "story_ending": "猫头鹰说自己已经不知锁错了哪一格",
        "layout": "four_panel",
        "panel_script": [
            {"panel": 1, "visual": "兔子独坐在餐馆窗边，时钟走过约定时间，店员第三次添水"},
            {"panel": 2, "visual": "店员开始收椅子，橘猫终于推门进来并拍着胸口解释"},
            {"panel": 3, "visual": "橘猫得意转身，完整展示亮得扎眼的橙色外套"},
            {"panel": 4, "visual": "兔子面无表情把账单推给橘猫，旁边店员低头忍笑"},
        ],
        "scores": {
            "naturalness": 9,
            "surprise": 8,
            "visual_payoff": 9,
            "freshness": 8,
            "instant_clarity": 10,
            "laugh_out_loud": 8,
            "roast_precision": 9,
            "status_reversal": 9,
            "memorability": 8,
            "base_comedy_strength": 8,
            "caption_punch": 8,
            "forcedness": 1,
            "harm_risk": 0
        },
    },
)


def is_healing_comic_family(category: object) -> bool:
    return str(category or "").strip() in HEALING_COMIC_FAMILY_CATEGORIES


def _load_prompt(filename: str) -> str:
    return (_PROMPT_DIR / filename).read_text(encoding="utf-8").strip()


def _render_prompt(filename: str, **values: object) -> str:
    rendered = _load_prompt(filename)
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", str(value or ""))
    return rendered


def _json_object(text: str) -> dict[str, object]:
    stripped = str(text or "").strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        stripped = stripped[start : end + 1]
    payload = json.loads(stripped)
    if not isinstance(payload, dict):
        raise ValueError("comic variant writer did not return a JSON object")
    return payload


def _score_int(value: object) -> int:
    try:
        return max(0, min(10, int(float(str(value or "0")))))
    except (TypeError, ValueError):
        return 0


def _plain_pinyin(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").lower())
    return re.sub(
        r"[^a-z]",
        "",
        "".join(ch for ch in normalized if not unicodedata.combining(ch)),
    )


def _edit_distance_at_most_one(left: str, right: str) -> bool:
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) > len(right):
        left, right = right, left
    i = j = differences = 0
    while i < len(left) and j < len(right):
        if left[i] == right[j]:
            i += 1
            j += 1
            continue
        differences += 1
        if differences > 1:
            return False
        if len(left) == len(right):
            i += 1
        j += 1
    return True


def _credible_pinyin_pair(value: object) -> bool:
    parts = re.split(r"[/|｜]", str(value or ""), maxsplit=1)
    if len(parts) != 2:
        return False
    left, right = (_plain_pinyin(part) for part in parts)
    return len(left) >= 2 and len(right) >= 2 and _edit_distance_at_most_one(left, right)


def _homophone_caption_has_punch_shape(caption: str) -> bool:
    """Reject plot summaries and synthetic three-clause punchlines.

    A publishable one-liner gets one setup beat and one payoff beat.  This is
    deliberately structural; model-provided humour scores are never enough to
    pass a caption that reads like an AI synopsis.
    """

    body = str(caption or "").rstrip(_TERMINAL_PUNCTUATION)
    pause_count = sum(body.count(mark) for mark in ("，", ",", "；", ";", "：", ":"))
    return (
        18 <= len(body) <= 42
        and pause_count <= 1
        and not _HOMOPHONE_AI_CAPTION_RE.search(body)
    )


def build_homophone_idea_prompt(
    title: str,
    *,
    panel_count: int,
    character_prompt: str | None = None,
    source_content: str | None = None,
) -> str:
    episode_count = parse_healing_comic_panel_count(panel_count)
    # Comedy needs a wider writers-room funnel than ordinary copy.  The second
    # call still sees only a compact shortlist, so this raises quality without
    # multiplying image calls or adding retry loops.
    candidate_count = min(max(episode_count * 4, 12), 64)
    return _render_prompt(
        "comic_homophone_ideas.md",
        TITLE=parse_healing_comic_title(title) or "今天也有好笑的事",
        EPISODE_COUNT=episode_count,
        CANDIDATE_COUNT=candidate_count,
        CHARACTER_REQUIREMENT=_text(
            character_prompt,
            "未指定，由模型设计原创治愈系角色。",
            limit=2000,
        ),
        SOURCE_CONTENT=_prompt_block(source_content, "未提供，只根据书名原创。", limit=20000),
    )


def parse_homophone_idea_output(text: str, *, panel_count: int) -> list[dict[str, object]]:
    """Select story-first homophone premises from a single writers-room call.

    The first stage does not write final cards.  It proves that the ordinary
    story works before the sound-alike reveal and that the reveal can be held
    until the last few cards.  This avoids the former failure mode where a word
    pair was mistaken for a joke and then forced into a synthetic one-liner.
    """
    target = parse_healing_comic_panel_count(panel_count)
    try:
        payload = _json_object(text)
        raw_candidates = payload.get("candidates")
    except Exception:
        raw_candidates = []
    if not isinstance(raw_candidates, list):
        raw_candidates = []

    normalized: list[dict[str, object]] = []
    for raw in raw_candidates:
        if not isinstance(raw, dict):
            continue
        source_word = _text(raw.get("source_word"), "", limit=18)
        pun_word = _text(raw.get("pun_word"), "", limit=18)
        pinyin_pair = _text(raw.get("pinyin_pair"), "", limit=64)
        if (
            not source_word
            or not pun_word
            or source_word == pun_word
            or frozenset((source_word, pun_word)) in _STALE_PUN_PAIRS
            or not _credible_pinyin_pair(pinyin_pair)
        ):
            continue
        scores_raw = raw.get("scores") if isinstance(raw.get("scores"), dict) else {}
        scores = {key: _score_int(scores_raw.get(key)) for key in _HOMOPHONE_SCORE_KEYS}
        target_behavior = _text(raw.get("target_behavior"), "", limit=120)
        roast_mode = _text(raw.get("roast_mode"), "", limit=32)
        ordinary_story = _text(raw.get("ordinary_story") or raw.get("base_roast"), "", limit=240)
        audience_expectation = _text(raw.get("audience_expectation"), "", limit=180)
        odd_repetition = _text(raw.get("odd_repetition") or raw.get("literal_action"), "", limit=180)
        reveal_trigger = _text(raw.get("reveal_trigger") or raw.get("misread_trigger"), "", limit=180)
        reveal_line = _text(raw.get("reveal_line") or raw.get("payoff"), "", limit=80)
        final_callback = _text(raw.get("final_callback"), source_word, limit=80)
        delayed_reveal_plan = _text(raw.get("delayed_reveal_plan"), "", limit=220)
        human_reason = _text(raw.get("human_reason"), "", limit=180)
        delivery_mode = _text(raw.get("delivery_mode"), "delayed_reveal", limit=48)
        contract_complete = all(
            len(value) >= minimum
            for value, minimum in (
                (ordinary_story, 16),
                (audience_expectation, 8),
                (odd_repetition, 8),
                (reveal_trigger, 8),
                (reveal_line, 2),
                (final_callback, 2),
                (delayed_reveal_plan, 12),
                (human_reason, 8),
                (delivery_mode, 4),
            )
        )
        lexical_clarity = source_word in final_callback and pun_word in reveal_line
        no_explanation = not _HOMOPHONE_EXPLANATION_RE.search(reveal_line + final_callback)
        total = (
            scores["laugh_out_loud"] * 20
            + scores["phonetic_payoff"] * 20
            + scores["delayed_reveal"] * 18
            + scores["story_logic"] * 18
            + scores["instant_clarity"] * 16
            + scores["human_rhythm"] * 14
            + scores["surprise"] * 12
            + scores["visual_payoff"] * 10
            + scores["freshness"] * 8
            + scores["naturalness"] * 8
            - scores["forcedness"] * 30
            - scores["harm_risk"] * 30
        )
        strict_pass = (
            scores["naturalness"] >= 8
            and scores["surprise"] >= 7
            and scores["visual_payoff"] >= 7
            and scores["freshness"] >= 7
            and scores["instant_clarity"] >= 9
            and scores["laugh_out_loud"] >= 7
            and scores["story_logic"] >= 8
            and scores["delayed_reveal"] >= 8
            and scores["phonetic_payoff"] >= 9
            and scores["human_rhythm"] >= 8
            and scores["forcedness"] <= 2
            and scores["harm_risk"] <= 1
            and roast_mode in _HOMOPHONE_ROAST_MODES
            and contract_complete
            and lexical_clarity
            and no_explanation
        )
        normalized.append(
            {
                "candidate_id": _text(
                    raw.get("candidate_id"),
                    f"J{len(normalized) + 1:03d}",
                    limit=16,
                ),
                "source_word": source_word,
                "pun_word": pun_word,
                "pinyin_pair": pinyin_pair,
                "semantic_domain": _text(raw.get("semantic_domain"), "日常生活", limit=48),
                "protagonist": _text(raw.get("protagonist"), "原创治愈系角色", limit=120),
                "place": _text(raw.get("place"), "具体生活场所", limit=80),
                "relationship": _text(raw.get("relationship"), "彼此友善的伙伴", limit=80),
                "key_prop": _text(raw.get("key_prop"), "推动反转的日常物件", limit=80),
                "setup": _text(raw.get("setup"), "一个可信的日常开场", limit=180),
                "visual_turn": _text(raw.get("visual_turn"), "一个逐渐显得不对劲的小动作", limit=180),
                "payoff": _text(raw.get("payoff"), "最后两张卡片完成声音回扣", limit=180),
                "target_behavior": target_behavior,
                "roast_mode": roast_mode,
                "ordinary_story": ordinary_story,
                "audience_expectation": audience_expectation,
                "odd_repetition": odd_repetition,
                "reveal_trigger": reveal_trigger,
                "reveal_line": reveal_line,
                "final_callback": final_callback,
                "delayed_reveal_plan": delayed_reveal_plan,
                "human_reason": human_reason,
                "delivery_mode": delivery_mode,
                "scores": scores,
                "strict_pass": strict_pass,
                "quality_total": total,
            }
        )

    normalized.sort(
        key=lambda item: (bool(item["strict_pass"]), int(item["quality_total"])),
        reverse=True,
    )
    selected: list[dict[str, object]] = []
    used_puns: set[str] = set()
    used_places: set[str] = set()
    used_domains: set[str] = set()
    for candidate in normalized:
        if not candidate["strict_pass"]:
            continue
        pun_word = str(candidate["pun_word"])
        place = str(candidate["place"])
        domain = str(candidate["semantic_domain"])
        if pun_word in used_puns or place in used_places or domain in used_domains:
            continue
        selected.append(candidate)
        used_puns.add(pun_word)
        used_places.add(place)
        used_domains.add(domain)
        if len(selected) >= target:
            break
    if len(selected) < target:
        for candidate in normalized:
            if (
                not candidate["strict_pass"]
                or candidate in selected
                or str(candidate["pun_word"]) in used_puns
            ):
                continue
            selected.append(candidate)
            used_puns.add(str(candidate["pun_word"]))
            if len(selected) >= target:
                break
    return selected


def format_homophone_joke_bank(candidates: list[dict[str, object]]) -> str:
    public_candidates = []
    for candidate in candidates:
        public_candidates.append(
            {
                key: candidate.get(key)
                for key in (
                    "candidate_id",
                    "source_word",
                    "pun_word",
                    "pinyin_pair",
                    "semantic_domain",
                    "protagonist",
                    "place",
                    "relationship",
                    "key_prop",
                    "setup",
                    "visual_turn",
                    "payoff",
                    "target_behavior",
                    "roast_mode",
                    "ordinary_story",
                    "audience_expectation",
                    "odd_repetition",
                    "reveal_trigger",
                    "reveal_line",
                    "final_callback",
                    "delayed_reveal_plan",
                    "human_reason",
                    "delivery_mode",
                    "scores",
                )
            }
        )
    return json.dumps(public_candidates, ensure_ascii=False, indent=2)


def normalize_complete_caption(value: object, *, fallback: str) -> str:
    if isinstance(value, list):
        raw = "，".join(str(item or "").strip() for item in value if str(item or "").strip())
    else:
        raw = str(value or "").strip()
    raw = raw.replace("\r", "").replace("\n", "，").replace("/", "，").replace("|", "，")
    raw = _DASH_RE.sub("，", raw)
    raw = re.sub(r"^\s*(?:文案|短句|旁白|caption)\s*[：:]\s*", "", raw, flags=re.I)
    raw = raw.strip(" \t\"'“”‘’。！？!?，,；;：:")
    raw = _NON_TERMINAL_PUNCTUATION_RE.sub("，", raw)
    raw = re.sub(r"[，,；;：:]\s*[，,；;：:]+", "，", raw)
    raw = re.sub(r"\s+", "", raw).strip("，,；;：:")
    fallback_body = str(fallback or "今天发生的这件小事，后来替他们留住了一点好心情。")
    fallback_body = fallback_body.strip().rstrip(_TERMINAL_PUNCTUATION)
    if len(raw) < 16:
        raw = fallback_body
    if len(raw) > 52:
        cut = max(raw.rfind("，", 0, 49), raw.rfind("；", 0, 49))
        raw = raw[: cut if cut >= 24 else 48].rstrip("，；")
    return raw + "。"


def _text(value: object, fallback: str, *, limit: int = 220) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
    return (cleaned or fallback)[:limit]


def _prompt_block(value: object, fallback: str, *, limit: int) -> str:
    """Keep form fields legible in prompts without preserving noisy spacing."""

    raw = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.split("\n")]
    cleaned = "\n".join(line for line in lines if line).strip()
    return (cleaned or fallback)[:limit]


def _layout(value: object, *, variant: str, index: int) -> str:
    del value, variant, index
    return "carousel"


def _fallback_episode(variant: str, index: int) -> dict[str, object]:
    collection = _STORY_FALLBACKS if variant == STORY_COMIC_CATEGORY else _HOMOPHONE_FALLBACKS
    source = collection[(index - 1) % len(collection)]
    return {str(key): value for key, value in source.items()}


def _card_text(value: object, fallback: str, *, role: str = "story") -> str:
    raw = str(value or "").replace("\r", "").replace("\n", " ").strip()
    raw = _DASH_RE.sub("，", raw)
    raw = re.sub(r"\s+", " ", raw).strip(" \t")
    if not raw:
        raw = fallback
    raw = raw[:42].rstrip("，,；;：:")
    if role not in {"reveal", "callback"} and raw and raw[-1] not in "。！？?!…」”』》":
        raw += "。"
    return raw


def _fallback_cards(
    *,
    variant: str,
    fallback: dict[str, object],
    place: str,
    protagonist: str,
    key_prop: str,
    setup: str,
    change: str,
    ending: str,
) -> list[dict[str, object]]:
    if variant == HOMOPHONE_COMIC_CATEGORY:
        pair = (str(fallback.get("source_word") or ""), str(fallback.get("pun_word") or ""))
        if pair == ("报销", "抱箱"):
            raw_cards = [
                ("hook", "小水獭每天一上班，就抱着同一个纸箱。", "小水獭抱着空纸箱经过快递柜", "text_with_doodle"),
                ("setup", "它先去财务室门口站一会儿。", "小水獭抱箱站在关着的财务室门前", "text_with_doodle"),
                ("delay", "门没开，它就把箱子抱回工位。", "小水獭抱箱坐在电脑前", "text_with_doodle"),
                ("delay", "第二天，箱子换成了更大的。", "更大的空纸箱挡住小水獭半张脸", "doodle_focus"),
                ("question", "狐狸终于问它，到底在办什么。", "狐狸同事困惑地看着纸箱", "text_with_doodle"),
                ("answer", "小水獭从箱底掏出一张发票。", "小水獭从空箱底拿出发票", "doodle_focus"),
                ("reveal", "它说，财务月底会给它报销。", "狐狸看发票，又看被抱皱的纸箱", "text_with_doodle"),
                ("callback", "可它已经认真抱箱半个月了。", "", "text_only"),
            ]
        elif pair == ("有才", "油菜"):
            raw_cards = [
                ("hook", "手工课快结束时，小孔雀又举手了。", "小孔雀高高举手", "text_with_doodle"),
                ("setup", "它说，今天准备了一个大作品。", "同学们一起看向小孔雀", "text_with_doodle"),
                ("delay", "老师请它上台，它却先跑出了教室。", "小孔雀兴奋跑出教室", "text_with_doodle"),
                ("delay", "过了一会儿，它抱着一个麻袋回来。", "小孔雀吃力拖着麻袋", "doodle_focus"),
                ("question", "老师问，作品为什么还带着泥。", "山羊老师看着地上的泥点", "text_with_doodle"),
                ("answer", "小孔雀把麻袋当场打开。", "麻袋里露出一捆绿色蔬菜", "doodle_focus"),
                ("reveal", "它说，这是自己种的油菜。", "小孔雀抱着一捆油菜等掌声", "text_with_doodle"),
                ("callback", "全班点头，它确实很有才。", "", "text_only"),
            ]
        else:
            raw_cards = [
                ("hook", "游泳馆快关门时，猫头鹰还在试钥匙。", "猫头鹰站在一排储物柜前", "text_with_doodle"),
                ("setup", "第一把打不开，它就换了第二把。", "猫头鹰拿着一串钥匙", "doodle_focus"),
                ("delay", "第二排试完，它又走向第三排。", "猫头鹰沿着储物柜逐格尝试", "text_with_doodle"),
                ("delay", "河狸抱着毛巾，在长凳上坐下了。", "河狸抱毛巾坐在长凳上等待", "text_with_doodle"),
                ("question", "它问，难道连号码也忘了吗。", "河狸指着储物柜号码牌", "text_with_doodle"),
                ("answer", "猫头鹰看了看手里那串钥匙。", "猫头鹰低头看没有标签的钥匙", "doodle_focus"),
                ("reveal", "它说，自己已经不知锁错哪一格。", "猫头鹰对着整排柜子发愣", "text_with_doodle"),
                ("callback", "这回真有点不知所措。", "", "text_only"),
            ]
    else:
        raw_cards = [
            ("hook", f"那天，{protagonist}来到{place}。", f"{place}里的{protagonist}", "text_with_doodle"),
            ("setup", setup, f"{setup}，关键动作清楚可见", "text_with_doodle"),
            ("detail", f"{key_prop}一直放在旁边。", f"{key_prop}的简单近景", "doodle_focus"),
            ("pause", "中间有一小会儿，谁也没有说话。", "", "text_only"),
            ("turn", change, f"{change}，画出局面变化", "text_with_doodle"),
            ("ending", ending, f"{ending}，留下可见结果", "text_with_doodle"),
        ]
    return [
        {
            "sub_index": index,
            "card_role": role,
            "text": _card_text(text, text, role=role),
            "image_text": [_card_text(text, text, role=role)],
            "visual": visual,
            "render_mode": mode,
            "scene_role": role,
            "narrative": text,
            "layout": "single_card",
        }
        for index, (role, text, visual, mode) in enumerate(raw_cards, start=1)
    ]


def _homophone_sequence_passes(
    cards: list[dict[str, object]],
    *,
    source_word: str,
    pun_word: str,
    pinyin_pair: str,
) -> bool:
    if (
        len(cards) < 7
        or len(cards) > COMIC_VARIANT_MAX_CARDS
        or not source_word
        or not pun_word
        or source_word == pun_word
        or frozenset((source_word, pun_word)) in _STALE_PUN_PAIRS
        or not _credible_pinyin_pair(pinyin_pair)
    ):
        return False
    texts = [str(card.get("text") or "") for card in cards]
    early = "".join(texts[: max(2, int(len(texts) * 0.6))])
    late = "".join(texts[-3:])
    return (
        source_word not in early
        and pun_word not in early
        and source_word in late
        and pun_word in late
        and not _HOMOPHONE_EXPLANATION_RE.search("".join(texts))
        and len(set(texts)) == len(texts)
    )


def _homophone_item_sequence_passes(item: dict[str, object]) -> bool:
    raw_cards = item.get("cards")
    if not isinstance(raw_cards, list):
        return False
    cards = [
        {"text": str(raw.get("text") or raw.get("caption") or "").strip()}
        for raw in raw_cards
        if isinstance(raw, dict)
    ]
    if len(cards) != len(raw_cards):
        return False
    return _homophone_sequence_passes(
        cards,
        source_word=_text(item.get("source_word"), "", limit=18),
        pun_word=_text(item.get("pun_word"), "", limit=18),
        pinyin_pair=_text(item.get("pinyin_pair"), "", limit=64),
    )


def _normalize_cards(
    item: dict[str, object],
    *,
    variant: str,
    fallback: dict[str, object],
    place: str,
    protagonist: str,
    key_prop: str,
    setup: str,
    change: str,
    ending: str,
) -> list[dict[str, object]]:
    fallback_cards = _fallback_cards(
        variant=variant,
        fallback=fallback,
        place=place,
        protagonist=protagonist,
        key_prop=key_prop,
        setup=setup,
        change=change,
        ending=ending,
    )
    raw_cards = item.get("cards")
    if not isinstance(raw_cards, list) or not (
        COMIC_VARIANT_MIN_CARDS <= len(raw_cards) <= COMIC_VARIANT_MAX_CARDS
    ):
        return fallback_cards
    cards: list[dict[str, object]] = []
    allowed_modes = {"text_only", "text_with_doodle", "doodle_focus"}
    for position, raw in enumerate(raw_cards, start=1):
        if not isinstance(raw, dict):
            return fallback_cards
        role = _text(raw.get("card_role"), "story", limit=24).lower()
        text = _card_text(raw.get("text") or raw.get("caption"), "", role=role)
        if len(text) < 2 or _HOMOPHONE_EXPLANATION_RE.search(text):
            return fallback_cards
        mode = _text(raw.get("render_mode"), "text_with_doodle", limit=24).lower()
        if mode not in allowed_modes:
            mode = "text_with_doodle"
        visual = "" if mode == "text_only" else _text(raw.get("visual"), f"{protagonist}在{place}完成当前动作", limit=260)
        cards.append(
            {
                "sub_index": position,
                "card_role": role,
                "text": text,
                "image_text": [text],
                "visual": visual,
                "render_mode": mode,
                "scene_role": role,
                "narrative": _text(raw.get("narrative"), text, limit=260),
                "layout": "single_card",
            }
        )
    if len({str(card["text"]) for card in cards}) != len(cards):
        return fallback_cards
    if not any(card["render_mode"] == "text_only" for card in cards):
        cards[min(3, len(cards) - 1)]["render_mode"] = "text_only"
        cards[min(3, len(cards) - 1)]["visual"] = ""
    if variant == HOMOPHONE_COMIC_CATEGORY and not _homophone_item_sequence_passes(item):
        return fallback_cards
    return cards


def _normalize_episode(
    item: dict[str, object],
    *,
    variant: str,
    index: int,
) -> dict[str, object]:
    fallback = _fallback_episode(variant, index)
    layout = _layout(item.get("layout"), variant=variant, index=index)
    place = _text(item.get("place"), str(fallback["place"]), limit=80)
    protagonist = _text(item.get("protagonist"), str(fallback["protagonist"]), limit=120)
    relationship = _text(item.get("relationship"), str(fallback["relationship"]), limit=80)
    key_prop = _text(item.get("key_prop"), str(fallback["key_prop"]), limit=80)
    setup = _text(item.get("story_setup"), str(fallback["story_setup"]), limit=180)
    change = _text(item.get("story_change"), str(fallback["story_change"]), limit=180)
    ending = _text(item.get("story_ending"), str(fallback["story_ending"]), limit=180)
    visual = f"{place}；{protagonist}；{setup}；{change}；{ending}"
    cards = _normalize_cards(
        item,
        variant=variant,
        fallback=fallback,
        place=place,
        protagonist=protagonist,
        key_prop=key_prop,
        setup=setup,
        change=change,
        ending=ending,
    )
    if variant == HOMOPHONE_COMIC_CATEGORY and (
        not _homophone_item_sequence_passes(item)
        or not _homophone_sequence_passes(
            cards,
            source_word=_text(item.get("source_word"), "", limit=18),
            pun_word=_text(item.get("pun_word"), "", limit=18),
            pinyin_pair=_text(item.get("pinyin_pair"), "", limit=64),
        )
    ):
        item = dict(fallback)
        place = _text(item.get("place"), str(fallback["place"]), limit=80)
        protagonist = _text(item.get("protagonist"), str(fallback["protagonist"]), limit=120)
        relationship = _text(item.get("relationship"), str(fallback["relationship"]), limit=80)
        key_prop = _text(item.get("key_prop"), str(fallback["key_prop"]), limit=80)
        setup = _text(item.get("story_setup"), str(fallback["story_setup"]), limit=180)
        change = _text(item.get("story_change"), str(fallback["story_change"]), limit=180)
        ending = _text(item.get("story_ending"), str(fallback["story_ending"]), limit=180)
        visual = f"{place}；{protagonist}；{setup}；{change}；{ending}"
        cards = _fallback_cards(
            variant=variant,
            fallback=fallback,
            place=place,
            protagonist=protagonist,
            key_prop=key_prop,
            setup=setup,
            change=change,
            ending=ending,
        )
    caption = str(cards[-1].get("text") or fallback["caption"])
    episode: dict[str, object] = {
        "title": _text(item.get("title"), f"小故事{index}", limit=24),
        "layout": layout,
        "caption": caption,
        "image_text": [caption],
        "phrases": [caption],
        "protagonist": protagonist,
        "place": place,
        "time_weather": _text(item.get("time_weather"), fallback.get("time_weather", "当天稍晚些时候"), limit=80),
        "relationship": relationship,
        "key_prop": key_prop,
        "shot_design": _text(item.get("shot_design"), fallback.get("shot_design", "由环境中景推进到动作近景"), limit=100),
        "story_setup": setup,
        "story_change": change,
        "story_ending": ending,
        "visual": visual,
        "panel_script": [
            {"panel": int(card["sub_index"]), "visual": str(card.get("visual") or "纯文字留白页")}
            for card in cards
        ],
        "cards": cards,
    }
    if variant == HOMOPHONE_COMIC_CATEGORY:
        source_word = _text(item.get("source_word"), str(fallback["source_word"]), limit=18)
        pun_word = _text(item.get("pun_word"), str(fallback["pun_word"]), limit=18)
        pinyin_pair = _text(item.get("pinyin_pair"), str(fallback["pinyin_pair"]), limit=48)
        episode.update(
            {
                "source_word": source_word,
                "pun_word": pun_word,
                "pinyin_pair": pinyin_pair,
                "target_behavior": _text(item.get("target_behavior"), str(fallback["target_behavior"]), limit=120),
                "roast_mode": _text(item.get("roast_mode"), str(fallback["roast_mode"]), limit=32),
                "ordinary_story": _text(item.get("ordinary_story"), setup, limit=240),
                "audience_expectation": _text(item.get("audience_expectation"), setup, limit=180),
                "odd_repetition": _text(item.get("odd_repetition"), change, limit=180),
                "reveal_trigger": _text(item.get("reveal_trigger"), ending, limit=180),
                "reveal_line": _text(item.get("reveal_line"), pun_word, limit=80),
                "final_callback": _text(item.get("final_callback"), source_word, limit=80),
                "delayed_reveal_plan": _text(item.get("delayed_reveal_plan"), "最后三张卡片才出现谐音答案", limit=220),
                "human_reason": _text(item.get("human_reason"), "故事先成立，谐音最后才回扣", limit=180),
                "delivery_mode": _text(
                    item.get("delivery_mode"), "delayed_reveal", limit=48
                ),
                "scores": item.get("scores") if isinstance(item.get("scores"), dict) else fallback["scores"],
            }
        )
    episode["images"] = cards
    return episode


def build_variant_writer_prompt(
    category: str,
    title: str,
    research_context: str,
    *,
    panel_count: int,
    character_prompt: str | None = None,
    source_content: str | None = None,
    joke_bank: str | None = None,
) -> str:
    if category not in COMIC_VARIANT_CATEGORIES:
        raise ValueError(f"unsupported comic variant: {category}")
    filename = "comic_story_writer.md" if category == STORY_COMIC_CATEGORY else "comic_homophone_writer.md"
    return _render_prompt(
        filename,
        TITLE=parse_healing_comic_title(title) or ("给你讲个故事" if category == STORY_COMIC_CATEGORY else "今天也有好笑的事"),
        EPISODE_COUNT=parse_healing_comic_panel_count(panel_count),
        CHARACTER_REQUIREMENT=_text(character_prompt, "未指定，由模型按每个故事单独设计原创主角。", limit=2000),
        SOURCE_CONTENT=_prompt_block(source_content, "未提供，只根据书名原创。", limit=20000),
        RESEARCH_CONTEXT=_text(research_context, "沿用现有漫画链路的出版感、留白、动作清楚与原创要求。", limit=8000),
        JOKE_BANK=_text(joke_bank, "[]", limit=30000),
    )


def parse_variant_writer_output(
    category: str,
    text: str,
    title: str,
    *,
    panel_count: int,
) -> tuple[str, str, list[dict[str, object]], dict[str, str]]:
    if category not in COMIC_VARIANT_CATEGORIES:
        raise ValueError(f"unsupported comic variant: {category}")
    count = parse_healing_comic_panel_count(panel_count)
    final_title = parse_healing_comic_title(title) or ("给你讲个故事" if category == STORY_COMIC_CATEGORY else "今天也有好笑的事")
    raw_episodes: list[object] = []
    payload: dict[str, object] = {}
    try:
        payload = _json_object(text)
        candidate = payload.get("episodes") or payload.get("panels")
        if isinstance(candidate, list):
            raw_episodes = candidate
    except Exception:
        raw_episodes = []
    episodes: list[dict[str, object]] = []
    for item in raw_episodes[:count]:
        if isinstance(item, dict):
            episodes.append(_normalize_episode(item, variant=category, index=len(episodes) + 1))
    while len(episodes) < count:
        episodes.append(_normalize_episode({}, variant=category, index=len(episodes) + 1))
    variant_label = "原创图文小故事" if category == STORY_COMIC_CATEGORY else "原创谐音梗漫画"
    default_series_style = (
        "统一的清透暖白纸底、大留白、圆润原创手绘线条；"
        "杏橙、天蓝、叶绿、珊瑚粉等三至五种清楚的低中饱和色自然分布，"
        "没有泥灰、泛黄滤镜或上下分色，场景和镜头持续变化。"
    )
    if category == HOMOPHONE_COMIC_CATEGORY:
        default_series_style = (
            "原创儿童马克笔与蜡笔平涂小涂鸦：单页使用一种明亮纯色铺满背景，"
            "深炭灰粗线条略有手绘抖动，形体高度简化，色块鲜明，少量自然擦痕；"
            "不做暖白水彩、精致二次元、写实光影、上下分色或矢量图标感。"
        )
    series_meta = {
        "series_style": _text(
            payload.get("series_style") if category == STORY_COMIC_CATEGORY else None,
            default_series_style,
            limit=500,
        ),
        "cover_visual": _text(payload.get("cover_visual"), f"用一个具体生活场景呈现{variant_label}的气质。", limit=300),
        "variant_label": variant_label,
    }
    return final_title, _text(payload.get("intro"), f"一本由具体生活小事组成的{variant_label}。", limit=180), episodes, series_meta


def build_variant_panel_prompt(
    category: str,
    *,
    panel: dict[str, object],
    index: int,
    title: str,
    image: dict[str, object] | None = None,
    sub_index: int = 1,
) -> str:
    cards = panel.get("images")
    if image is None and isinstance(cards, list) and cards:
        raw_image = cards[min(max(sub_index - 1, 0), len(cards) - 1)]
        image = raw_image if isinstance(raw_image, dict) else {}
    image = image or {}
    common = {
        "BOOK_TITLE": title,
        "EPISODE_INDEX": index,
        "EPISODE_TITLE": panel.get("title"),
        "CARD_INDEX": sub_index,
        "CARD_TOTAL": len(cards) if isinstance(cards, list) else 1,
        "CARD_ROLE": image.get("card_role") or image.get("scene_role") or "story",
        "CARD_TEXT": image.get("text") or panel.get("caption"),
        "CARD_VISUAL": image.get("visual") or "纯文字留白页",
        "RENDER_MODE": image.get("render_mode") or "text_with_doodle",
        "PROTAGONIST": panel.get("protagonist"),
        "PLACE": panel.get("place"),
        "RELATIONSHIP": panel.get("relationship"),
        "KEY_PROP": panel.get("key_prop"),
        "STORY_SETUP": panel.get("story_setup"),
        "STORY_CHANGE": panel.get("story_change"),
        "STORY_ENDING": panel.get("story_ending"),
    }
    if category == STORY_COMIC_CATEGORY:
        common.update(
            {
                "TIME_WEATHER": panel.get("time_weather"),
                "SHOT_DESIGN": panel.get("shot_design"),
            }
        )
        return _render_prompt("comic_story_image.md", **common)
    if category == HOMOPHONE_COMIC_CATEGORY:
        common.update(
            {
                "CARD_BACKGROUND": _variant_background_name(category, panel.get("title") or index),
                "SOURCE_WORD": panel.get("source_word"),
                "PUN_WORD": panel.get("pun_word"),
                "PINYIN_PAIR": panel.get("pinyin_pair"),
                "ROAST_MODE": panel.get("roast_mode"),
                "ORDINARY_STORY": panel.get("ordinary_story"),
                "AUDIENCE_EXPECTATION": panel.get("audience_expectation"),
                "ODD_REPETITION": panel.get("odd_repetition"),
                "REVEAL_TRIGGER": panel.get("reveal_trigger"),
                "REVEAL_LINE": panel.get("reveal_line"),
                "FINAL_CALLBACK": panel.get("final_callback"),
                "DELAYED_REVEAL_PLAN": panel.get("delayed_reveal_plan"),
                "DELIVERY_MODE": panel.get("delivery_mode"),
            }
        )
        return _render_prompt("comic_homophone_image.md", **common)
    raise ValueError(f"unsupported comic variant: {category}")


def build_variant_cover_prompt(
    category: str,
    *,
    title: str,
    intro: str,
    panels: list[dict[str, object]],
    series_meta: dict[str, str],
) -> str:
    prompt_name = (
        "comic_homophone_cover.md"
        if category == HOMOPHONE_COMIC_CATEGORY
        else "comic_variant_cover.md"
    )
    return _render_prompt(
        prompt_name,
        BOOK_TITLE=title,
        INTRO=intro,
        VARIANT_LABEL=series_meta.get("variant_label") or category,
        SERIES_STYLE=series_meta.get("series_style"),
        COVER_VISUAL=series_meta.get("cover_visual"),
        EPISODE_TITLES="、".join(str(panel.get("title") or "") for panel in panels[:12]),
        CARD_BACKGROUND=_variant_background_name(category, title),
    )


def plan_variant_comic_images(
    category: str,
    panels: list[dict[str, object]],
    output_dir: Path,
    *,
    title: str,
    intro: str,
    series_meta: dict[str, str],
) -> list[ComicSectionImage]:
    image_dir = output_dir / "comic_images"
    image_dir.mkdir(parents=True, exist_ok=True)
    cover_prompt = build_variant_cover_prompt(
        category,
        title=title,
        intro=intro,
        panels=panels,
        series_meta=series_meta,
    )
    cover_path = image_dir / COMIC_COVER_IMAGE_FILENAME
    (image_dir / "comic_cover_prompt.txt").write_text(cover_prompt, encoding="utf-8")
    results = [
        ComicSectionImage(
            index=-1,
            phrase=title,
            title="封面",
            animal="原创角色",
            subject=series_meta.get("cover_visual") or "",
            prompt=cover_prompt,
            filename=COMIC_COVER_IMAGE_FILENAME,
            path=cover_path,
        )
    ]
    for index, panel in enumerate(panels, start=1):
        raw_images = panel.get("images")
        images = [item for item in raw_images if isinstance(item, dict)] if isinstance(raw_images, list) else []
        for sub_index, image_item in enumerate(images, start=1):
            prompt = build_variant_panel_prompt(
                category,
                panel=panel,
                image=image_item,
                index=index,
                sub_index=sub_index,
                title=title,
            )
            filename = f"comic_section_{index:02d}_{sub_index:02d}.png"
            path = image_dir / filename
            (image_dir / f"comic_section_{index:02d}_{sub_index:02d}_prompt.txt").write_text(prompt, encoding="utf-8")
            results.append(
                ComicSectionImage(
                    index=index,
                    phrase=str(image_item.get("text") or panel.get("caption") or ""),
                    title=str(panel.get("title") or f"第{index}章"),
                    animal=str(image_item.get("render_mode") or "text_with_doodle"),
                    subject=str(image_item.get("visual") or ""),
                    prompt=prompt,
                    filename=filename,
                    path=path,
                    sub_index=sub_index,
                )
            )
    _write_manifest(image_dir, results)
    return results


def _comic_cjk_font_path() -> Path:
    configured = str(os.getenv("COMIC_CJK_FONT_PATH") or "").strip()
    candidates = [
        configured,
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "C:/Windows/Fonts/SIMYOU.TTF",
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ]
    for raw in candidates:
        if raw and Path(raw).exists():
            return Path(raw)
    raise RuntimeError("缺少中文漫画排版字体，请安装 fonts-noto-cjk 或配置 COMIC_CJK_FONT_PATH")


def _homophone_cjk_font_path() -> Path:
    """Use the bundled OFL handwritten font only for homophone cards."""

    configured = str(os.getenv("HOMOPHONE_COMIC_FONT_PATH") or "").strip()
    candidates = [
        configured,
        str(_FONT_DIR / "Xiaolai-Regular.ttf"),
        "C:/Windows/Fonts/FZYTK.TTF",
        "C:/Windows/Fonts/SIMYOU.TTF",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    ]
    for raw in candidates:
        if raw and Path(raw).exists():
            return Path(raw)
    raise RuntimeError("缺少谐音梗漫画手写中文字体，请恢复内置 Xiaolai 字体或配置 HOMOPHONE_COMIC_FONT_PATH")


def _wrap_card_text(draw: object, text: str, font: object, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if current and bbox[2] - bbox[0] > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


_VARIANT_PAPER_RGB = (255, 252, 244)
_VARIANT_INK_RGB = (62, 52, 44)
_VARIANT_TEXT_SHADOW_RGB = (218, 190, 161, 92)
_HOMOPHONE_BACKGROUND_RGB = (174, 232, 244)
_HOMOPHONE_INK_RGB = (58, 62, 66)


def _sample_variant_paper_color(image: object) -> tuple[int, int, int]:
    """Estimate the story illustration's warm paper tone."""

    width, height = image.size
    sample_width = max(8, int(width * 0.055))
    sample_height = max(8, int(height * 0.16))
    regions = (
        image.crop((0, 0, width, sample_height)),
        image.crop((0, 0, sample_width, height)),
        image.crop((width - sample_width, 0, width, height)),
    )
    candidates: list[tuple[int, int, int]] = []
    for region in regions:
        reduced = region.resize((24, 24)).convert("RGB")
        pixels = (
            reduced.get_flattened_data()
            if hasattr(reduced, "get_flattened_data")
            else reduced.getdata()
        )
        for red, green, blue in pixels:
            if min(red, green, blue) >= 205 and max(red, green, blue) - min(red, green, blue) <= 38:
                candidates.append((red, green, blue))
    if not candidates:
        return _VARIANT_PAPER_RGB
    candidates.sort(key=lambda color: (sum(color), color))
    median = candidates[len(candidates) // 2]
    return tuple(
        max(238, min(255, round(channel * 0.62 + preferred * 0.38)))
        for channel, preferred in zip(median, _VARIANT_PAPER_RGB)
    )


def _sample_homophone_background_color(image: object) -> tuple[int, int, int]:
    """Estimate the homophone card's full-bleed pastel background."""

    width, height = image.size
    sample_width = max(8, int(width * 0.055))
    sample_height = max(8, int(height * 0.16))
    regions = (
        image.crop((0, 0, width, sample_height)),
        image.crop((0, 0, sample_width, height)),
        image.crop((width - sample_width, 0, width, height)),
    )
    candidates: list[tuple[int, int, int]] = []
    for region in regions:
        reduced = region.resize((24, 24)).convert("RGB")
        pixels = reduced.get_flattened_data() if hasattr(reduced, "get_flattened_data") else reduced.getdata()
        for red, green, blue in pixels:
            if max(red, green, blue) >= 210 and min(red, green, blue) >= 145:
                candidates.append((red, green, blue))
    if not candidates:
        return _HOMOPHONE_BACKGROUND_RGB
    return tuple(
        sorted(color[channel] for color in candidates)[len(candidates) // 2]
        for channel in range(3)
    )


def _soft_clear_variant_text_region(
    canvas: object,
    *,
    top_region: int,
    paper_rgb: tuple[int, int, int],
    anchor: str = "top",
) -> object:
    """Clear the caption area without introducing a horizontal colour seam."""

    from PIL import Image, ImageDraw

    width, height = canvas.size
    overlay = Image.new("RGB", (width, height), paper_rgb)
    mask = Image.new("L", (width, height), 0)
    mask_draw = ImageDraw.Draw(mask)
    feather = max(120, int(height * 0.115))
    if anchor == "bottom":
        fade_start = max(0, top_region - feather)
        solid_start = min(height - 1, top_region + feather)
        mask_draw.rectangle((0, solid_start, width, height), fill=255)
        span = max(1, solid_start - fade_start)
        for y in range(fade_start, solid_start + 1):
            progress = (y - fade_start) / span
            smooth = progress * progress * (3.0 - 2.0 * progress)
            mask_draw.line((0, y, width, y), fill=round(255 * smooth))
    else:
        solid_end = max(0, top_region - feather)
        fade_end = min(height - 1, top_region + feather)
        mask_draw.rectangle((0, 0, width, solid_end), fill=255)
        span = max(1, fade_end - solid_end)
        for y in range(solid_end, fade_end + 1):
            progress = (y - solid_end) / span
            smooth = progress * progress * (3.0 - 2.0 * progress)
            mask_draw.line((0, y, width, y), fill=round(255 * (1.0 - smooth)))
    return Image.composite(overlay, canvas, mask)


def _draw_cartoon_card_text(
    canvas: object,
    *,
    lines: list[str],
    font: object,
    center_y: int,
) -> None:
    """Draw accurate Chinese copy with the established story-card finish."""

    from PIL import Image, ImageDraw

    width, height = canvas.size
    measure = ImageDraw.Draw(canvas)
    line_gap = max(14, int(font.size * 0.38))
    boxes = [measure.textbbox((0, 0), line, font=font, stroke_width=0) for line in lines]
    line_heights = [box[3] - box[1] for box in boxes]
    block_height = sum(line_heights) + line_gap * max(0, len(lines) - 1)
    y = max(int(height * 0.055), center_y - block_height // 2)
    for line_index, (line, box, line_height) in enumerate(zip(lines, boxes, line_heights)):
        line_width = box[2] - box[0]
        padding = max(24, int(font.size * 0.18))
        layer_width = line_width + padding * 2
        layer_height = line_height + padding * 2
        layer = Image.new("RGBA", (layer_width, layer_height), (0, 0, 0, 0))
        layer_draw = ImageDraw.Draw(layer)
        stroke = max(2, int(font.size * 0.022))
        shadow_offset = max(3, int(font.size * 0.025))
        origin = (padding - box[0], padding - box[1])
        layer_draw.text(
            (origin[0] + shadow_offset, origin[1] + shadow_offset),
            line,
            font=font,
            fill=_VARIANT_TEXT_SHADOW_RGB,
            stroke_width=0,
        )
        layer_draw.text(
            origin,
            line,
            font=font,
            fill=(*_VARIANT_INK_RGB, 255),
            stroke_width=stroke,
            stroke_fill=(*_VARIANT_INK_RGB, 255),
        )
        angle_seed = sum((index + 1) * ord(char) for index, char in enumerate(line))
        angle = ((angle_seed % 7) - 3) * 0.22
        if line_index % 2:
            angle *= -1
        rotated = layer.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
        x = (width - rotated.width) // 2
        paste_y = y - (rotated.height - layer_height) // 2
        canvas.paste(rotated, (x, paste_y), rotated)
        y += line_height + line_gap


def _draw_handwritten_card_text(
    canvas: object,
    *,
    lines: list[str],
    font: object,
    center_y: int,
) -> None:
    """Typeset exact copy in a loose but readable child-handwritten rhythm."""

    from PIL import Image, ImageDraw

    width, height = canvas.size
    measure = ImageDraw.Draw(canvas)
    line_gap = max(14, int(font.size * 0.28))
    boxes = [measure.textbbox((0, 0), line, font=font, stroke_width=0) for line in lines]
    line_heights = [box[3] - box[1] for box in boxes]
    block_height = sum(line_heights) + line_gap * max(0, len(lines) - 1)
    y = max(int(height * 0.055), center_y - block_height // 2)
    for line_index, (line, box, line_height) in enumerate(zip(lines, boxes, line_heights)):
        line_width = box[2] - box[0]
        padding = max(20, int(font.size * 0.15))
        x = (width - line_width) // 2
        for char_index, char in enumerate(line):
            char_box = measure.textbbox((0, 0), char, font=font, stroke_width=0)
            char_width = max(1, char_box[2] - char_box[0])
            char_height = max(1, char_box[3] - char_box[1])
            layer = Image.new(
                "RGBA",
                (char_width + padding * 2, char_height + padding * 2),
                (0, 0, 0, 0),
            )
            layer_draw = ImageDraw.Draw(layer)
            layer_draw.text(
                (padding - char_box[0], padding - char_box[1]),
                char,
                font=font,
                fill=(*_HOMOPHONE_INK_RGB, 255),
            )
            seed = (line_index + 1) * 1009 + (char_index + 1) * 131 + ord(char)
            angle = ((seed % 13) - 6) * 0.22
            rotated = layer.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
            baseline_jitter = ((seed // 13) % 7 - 3) * max(1, int(font.size * 0.016))
            paste_x = x - (rotated.width - layer.width) // 2
            paste_y = y + baseline_jitter - (rotated.height - layer.height) // 2
            canvas.paste(rotated, (paste_x, paste_y), rotated)
            x += char_width
        y += line_height + line_gap


def _typeset_variant_card(
    path: Path,
    text: str,
    *,
    render_mode: str,
    category: str = STORY_COMIC_CATEGORY,
) -> None:
    """Typeset exact copy after illustration generation.

    The image model only paints the doodle.  Pillow owns every Chinese glyph,
    so the published card cannot silently lose, invent or misspell the copy.
    Re-running is idempotent because the text region is painted clean first.
    """

    from PIL import Image, ImageDraw, ImageEnhance, ImageFont

    is_homophone = category == HOMOPHONE_COMIC_CATEGORY
    base_rgb = _HOMOPHONE_BACKGROUND_RGB if is_homophone else _VARIANT_PAPER_RGB
    if render_mode == "text_only" or not path.exists():
        canvas = Image.new("RGB", (2450, 3500), base_rgb)
        paper_rgb = base_rgb
    else:
        with Image.open(path) as source:
            canvas = source.convert("RGB")
        paper_rgb = (
            _sample_homophone_background_color(canvas)
            if is_homophone
            else _sample_variant_paper_color(canvas)
        )
        canvas = ImageEnhance.Color(canvas).enhance(1.10 if is_homophone else 1.20)
        canvas = ImageEnhance.Contrast(canvas).enhance(1.04 if is_homophone else 1.035)
    width, height = canvas.size
    draw = ImageDraw.Draw(canvas)
    text_boundary = 0.70 if is_homophone else 0.47
    top_region = int(height * (text_boundary if render_mode != "text_only" else 1.0))
    if render_mode != "text_only":
        canvas = _soft_clear_variant_text_region(
            canvas,
            top_region=top_region,
            paper_rgb=paper_rgb,
            anchor="bottom" if is_homophone else "top",
        )
        draw = ImageDraw.Draw(canvas)
    font_path = _homophone_cjk_font_path() if is_homophone else _comic_cjk_font_path()
    max_width = int(width * 0.82)
    selected_font = None
    selected_lines: list[str] = []
    for size in range(max(44, int(width * 0.075)), max(32, int(width * 0.035)) - 1, -4):
        font = ImageFont.truetype(str(font_path), size=size)
        lines = _wrap_card_text(draw, text, font, max_width)
        if len(lines) <= 2:
            selected_font = font
            selected_lines = lines
            break
    if selected_font is None:
        selected_font = ImageFont.truetype(str(font_path), size=max(32, int(width * 0.035)))
        selected_lines = _wrap_card_text(draw, text, selected_font, max_width)[:3]
    center_y = int(
        height
        * (
            0.50
            if render_mode == "text_only"
            else (0.83 if is_homophone else 0.22)
        )
    )
    draw_text = _draw_handwritten_card_text if is_homophone else _draw_cartoon_card_text
    draw_text(
        canvas,
        lines=selected_lines,
        font=selected_font,
        center_y=center_y,
    )
    canvas.save(path, format="PNG", optimize=True)


def generate_variant_comic_images(
    category: str,
    panels: list[dict[str, object]],
    output_dir: Path,
    *,
    title: str,
    intro: str,
    series_meta: dict[str, str],
    force: bool = False,
    allow_uncertain: bool = False,
    retry_uncertain: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
    should_pause: Callable[[], bool] | None = None,
) -> list[ComicSectionImage]:
    image_dir = output_dir / "comic_images"
    results = plan_variant_comic_images(
        category,
        panels,
        output_dir,
        title=title,
        intro=intro,
        series_meta=series_meta,
    )
    total = len(results)

    def raise_if_paused() -> None:
        if should_pause is not None and should_pause():
            _write_manifest(image_dir, results)
            raise ComicGenerationPaused("漫画生成已暂停")

    def generate_one(image: ComicSectionImage) -> ComicSectionImage:
        raise_if_paused()
        stem = image.path.stem
        if image.index >= 1 and image.animal == "text_only":
            _typeset_variant_card(
                image.path,
                image.phrase,
                render_mode="text_only",
                category=category,
            )
            return ComicSectionImage(**{**image.__dict__, "status": "completed", "error": None})
        generated = _generate_image_with_comic_protection(
            image,
            image_dir=image_dir,
            stem=stem,
            response_path=image_dir / f"{stem}_response.json",
            uncertain_path=image_dir / f"{stem}_uncertain.json",
            permanent_path=image_dir / f"{stem}_permanent.json",
                force=force,
                allow_uncertain=allow_uncertain,
                retry_uncertain=retry_uncertain,
            )
        if generated.path.exists() and generated.path.stat().st_size > 0:
            if generated.index == -1:
                _typeset_variant_card(
                    generated.path,
                    generated.phrase,
                    render_mode="cover",
                    category=category,
                )
            elif generated.index >= 1:
                _typeset_variant_card(
                    generated.path,
                    generated.phrase,
                    render_mode=generated.animal,
                    category=category,
                )
        return generated

    if results:
        results[0] = generate_one(results[0])
        if on_progress is not None:
            on_progress(1, total)
    max_workers = max(1, min(int(getattr(settings, "IMAGE_MAX_CONCURRENT_CALLS", 2) or 2), 2))
    pending = list(enumerate(results[1:], start=1))
    next_index = 0
    completed = 1 if results else 0
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="comic-variant-image") as executor:
        futures: dict[object, int] = {}

        def submit_next() -> None:
            nonlocal next_index
            raise_if_paused()
            if next_index >= len(pending):
                return
            offset, image = pending[next_index]
            next_index += 1
            futures[executor.submit(generate_one, image)] = offset

        for _ in range(min(max_workers, len(pending))):
            submit_next()
        while futures:
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            future = next(iter(done))
            offset = futures.pop(future)
            try:
                results[offset] = future.result()
            except Exception:
                for pending_future in futures:
                    pending_future.cancel()
                _write_manifest(image_dir, results)
                raise
            completed += 1
            _write_manifest(image_dir, results)
            if on_progress is not None:
                on_progress(completed, total)
            submit_next()
    _write_manifest(image_dir, results)
    return results


def assemble_variant_comic_markdown(
    title: str,
    intro: str,
    panels: list[dict[str, object]],
) -> str:
    lines = ["@最终书名", title, "@最终简介", intro, "@目录"]
    for index, panel in enumerate(panels, start=1):
        lines.append(f"{index}. {panel.get('title') or f'第{index}章'}")
    lines.append("@正文")
    for index, panel in enumerate(panels, start=1):
        lines.append(f"@{index}. {panel.get('title') or f'第{index}章'}")
        images = panel.get("images")
        if isinstance(images, list):
            for sub_index, _ in enumerate(images, start=1):
                lines.append(f"{{{{COMIC_IMAGE_{index}_{sub_index}}}}}")
    return "\n".join(lines).strip()


__all__ = [
    "COMIC_VARIANT_CATEGORIES",
    "COMIC_VARIANT_PIPELINE_VERSION",
    "HOMOPHONE_COMIC_PIPELINE_VERSION",
    "HEALING_COMIC_FAMILY_CATEGORIES",
    "HOMOPHONE_COMIC_CATEGORY",
    "STORY_COMIC_CATEGORY",
    "assemble_variant_comic_markdown",
    "build_homophone_idea_prompt",
    "build_variant_cover_prompt",
    "build_variant_panel_prompt",
    "build_variant_writer_prompt",
    "generate_variant_comic_images",
    "format_homophone_joke_bank",
    "is_healing_comic_family",
    "normalize_complete_caption",
    "parse_homophone_idea_output",
    "parse_variant_writer_output",
    "plan_variant_comic_images",
    "variant_pipeline_version",
]
