"""Independent story-comic and homophone-comic pipelines.

The established healing-comic pipeline stays the default and is not routed
through this module.  The two variants reuse its image protection, manifests,
pause semantics and markdown image injection while keeping separate prompt
contracts and copy validation.
"""
from __future__ import annotations

import json
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
HEALING_COMIC_FAMILY_CATEGORIES = frozenset(
    {"healing_comic", STORY_COMIC_CATEGORY, HOMOPHONE_COMIC_CATEGORY}
)

_PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"
_TERMINAL_PUNCTUATION = "。！？"
_NON_TERMINAL_PUNCTUATION_RE = re.compile(r"[。！？!?]+")
_DASH_RE = re.compile(r"[—–﹣－]+")
_STALE_PUN_PAIRS = {
    frozenset(("压力", "鸭力")),
    frozenset(("惊喜", "鲸喜")),
    frozenset(("好事", "好柿")),
    frozenset(("期待", "脐带")),
    frozenset(("知足", "蜘蛛")),
}

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
        "source_word": "放心",
        "pun_word": "芳心",
        "pinyin_pair": "fàng xīn / fāng xīn",
        "caption": "邻居让她尽管放心，她却先替窗台接住了那盆摇摇欲坠的芳心。",
        "protagonist": "系绿色围裙的原创花店学徒",
        "place": "阵雨刚到的老楼走廊",
        "relationship": "花店学徒和临时出门的邻居",
        "key_prop": "一盆被风吹歪的芳香植物",
        "story_setup": "邻居出门前托她照看窗台并说尽管放心",
        "story_change": "雨风把花盆推到窗台边，她赶紧伸手接住",
        "story_ending": "邻居回来时，她抱着仍在开花的植物站在门口",
    },
    {
        "source_word": "天意",
        "pun_word": "添衣",
        "pinyin_pair": "tiān yì / tiān yī",
        "caption": "他一直等所谓的天意，外婆却先追到站台，认真替他添衣。",
        "protagonist": "准备远行的原创年轻木匠与白发外婆",
        "place": "清晨有风的乡镇站台",
        "relationship": "外婆和第一次远行的外孙",
        "key_prop": "一件多缝了内袋的旧外套",
        "story_setup": "年轻人望着远处等一个决定是否出发的征兆",
        "story_change": "外婆从坡道追来，把旧外套披到他肩上",
        "story_ending": "车门打开时，他把外套扣好并回头挥手",
    },
    {
        "source_word": "无谓",
        "pun_word": "无味",
        "pinyin_pair": "wú wèi / wú wèi",
        "caption": "她劝两位厨师别再作无谓争论，一尝汤才发现今天真正无味的是这一锅。",
        "protagonist": "戴格纹头巾的原创小餐馆老板",
        "place": "午市前忙乱的小餐馆后厨",
        "relationship": "老板和两位争论配方的厨师",
        "key_prop": "一勺忘记放盐的热汤",
        "story_setup": "两位厨师隔着灶台争论谁记错了配方",
        "story_change": "老板端起汤勺让两个人先停下来尝一口",
        "story_ending": "三个人同时看向原封未动的盐罐并笑出声",
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


def build_homophone_idea_prompt(
    title: str,
    *,
    panel_count: int,
    character_prompt: str | None = None,
    source_content: str | None = None,
) -> str:
    episode_count = parse_healing_comic_panel_count(panel_count)
    candidate_count = min(max(episode_count * 2, 6), 48)
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
        SOURCE_CONTENT=_text(source_content, "未提供，只根据书名原创。", limit=20000),
    )


def parse_homophone_idea_output(text: str, *, panel_count: int) -> list[dict[str, object]]:
    """Select a diverse high-quality joke bank from a single ideation call."""
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
        scores = {
            key: _score_int(scores_raw.get(key))
            for key in (
                "naturalness",
                "surprise",
                "visual_payoff",
                "warmth",
                "freshness",
                "instant_clarity",
                "forcedness",
            )
        }
        total = (
            scores["naturalness"] * 24
            + scores["surprise"] * 20
            + scores["visual_payoff"] * 22
            + scores["warmth"] * 14
            + scores["freshness"] * 12
            + scores["instant_clarity"] * 8
            - scores["forcedness"] * 25
        )
        strict_pass = (
            scores["naturalness"] >= 8
            and scores["surprise"] >= 7
            and scores["visual_payoff"] >= 8
            and scores["warmth"] >= 7
            and scores["freshness"] >= 7
            and scores["instant_clarity"] >= 8
            and scores["forcedness"] <= 2
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
                "visual_turn": _text(raw.get("visual_turn"), "人物用动作改变眼前情形", limit=180),
                "payoff": _text(raw.get("payoff"), "最后一格完成双义回扣", limit=180),
                "caption": normalize_complete_caption(
                    raw.get("caption"),
                    fallback=f"事情拐了一个弯，最后刚好落在{pun_word}上。",
                ),
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
            if candidate in selected or str(candidate["pun_word"]) in used_puns:
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
                    "caption",
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


def _layout(value: object, *, variant: str, index: int) -> str:
    raw = str(value or "").strip().lower()
    if variant == STORY_COMIC_CATEGORY:
        allowed = {"single_scene", "three_panel", "four_panel"}
        return raw if raw in allowed else ("single_scene", "three_panel", "four_panel")[(index - 1) % 3]
    return raw if raw in {"three_panel", "four_panel"} else ("three_panel" if index % 2 else "four_panel")


def _fallback_episode(variant: str, index: int) -> dict[str, str]:
    source = (_STORY_FALLBACKS if variant == STORY_COMIC_CATEGORY else _HOMOPHONE_FALLBACKS)[
        (index - 1) % 3
    ]
    return {str(key): str(value) for key, value in source.items()}


def _normalize_panel_script(value: object, *, layout: str, visual: str) -> list[dict[str, object]]:
    expected = {"single_scene": 1, "three_panel": 3, "four_panel": 4}[layout]
    result: list[dict[str, object]] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                item_visual = _text(item.get("visual"), visual, limit=360)
            else:
                item_visual = _text(item, visual, limit=360)
            if item_visual:
                result.append({"panel": len(result) + 1, "visual": item_visual})
            if len(result) >= expected:
                break
    while len(result) < expected:
        result.append(
            {
                "panel": len(result) + 1,
                "visual": f"{visual}；连续事件的第{len(result) + 1}步，必须画出新的动作和可见变化。",
            }
        )
    return result


def _normalize_episode(
    item: dict[str, object],
    *,
    variant: str,
    index: int,
) -> dict[str, object]:
    fallback = _fallback_episode(variant, index)
    layout = _layout(item.get("layout"), variant=variant, index=index)
    place = _text(item.get("place"), fallback["place"], limit=80)
    protagonist = _text(item.get("protagonist"), fallback["protagonist"], limit=120)
    relationship = _text(item.get("relationship"), fallback["relationship"], limit=80)
    key_prop = _text(item.get("key_prop"), fallback["key_prop"], limit=80)
    setup = _text(item.get("story_setup"), fallback["story_setup"], limit=180)
    change = _text(item.get("story_change"), fallback["story_change"], limit=180)
    ending = _text(item.get("story_ending"), fallback["story_ending"], limit=180)
    visual = f"{place}；{protagonist}；{setup}；{change}；{ending}"
    caption = normalize_complete_caption(item.get("caption") or item.get("image_text"), fallback=fallback["caption"])
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
        "panel_script": _normalize_panel_script(item.get("panel_script"), layout=layout, visual=visual),
    }
    if variant == HOMOPHONE_COMIC_CATEGORY:
        source_word = _text(item.get("source_word"), fallback["source_word"], limit=18)
        pun_word = _text(item.get("pun_word"), fallback["pun_word"], limit=18)
        pinyin_pair = _text(item.get("pinyin_pair"), fallback["pinyin_pair"], limit=48)
        if (
            source_word == pun_word
            or pun_word not in caption
            or frozenset((source_word, pun_word)) in _STALE_PUN_PAIRS
            or not _credible_pinyin_pair(pinyin_pair)
        ):
            source_word = fallback["source_word"]
            pun_word = fallback["pun_word"]
            pinyin_pair = fallback["pinyin_pair"]
            caption = normalize_complete_caption(fallback["caption"], fallback=fallback["caption"])
            episode["caption"] = caption
            episode["image_text"] = [caption]
            episode["phrases"] = [caption]
        episode.update(
            {
                "source_word": source_word,
                "pun_word": pun_word,
                "pinyin_pair": pinyin_pair,
            }
        )
    episode["images"] = [
        {
            "sub_index": 1,
            "scene_role": "完整图文小故事" if variant == STORY_COMIC_CATEGORY else "完整谐音梗小故事",
            "image_text": [episode["caption"]],
            "visual": visual,
            "narrative": f"{setup}；{change}；{ending}",
            "layout": layout,
            "panel_script": episode["panel_script"],
        }
    ]
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
        SOURCE_CONTENT=_text(source_content, "未提供，只根据书名原创。", limit=20000),
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
    series_meta = {
        "series_style": _text(
            payload.get("series_style"),
            "奶油白纸色、大留白、简洁原创手绘线条、克制配色、轻微纸张颗粒、场景和镜头持续变化。",
            limit=500,
        ),
        "cover_visual": _text(payload.get("cover_visual"), f"用一个具体生活场景呈现{variant_label}的气质。", limit=300),
        "variant_label": variant_label,
    }
    return final_title, _text(payload.get("intro"), f"一本由具体生活小事组成的{variant_label}。", limit=180), episodes, series_meta


def _panel_script_text(panel: dict[str, object]) -> str:
    script = panel.get("panel_script")
    if not isinstance(script, list):
        return "- 1：按本页故事画出完整事件。"
    return "\n".join(
        f"- {index}：{_text(item.get('visual') if isinstance(item, dict) else item, '连续动作', limit=360)}"
        for index, item in enumerate(script, start=1)
    )


def build_variant_panel_prompt(
    category: str,
    *,
    panel: dict[str, object],
    index: int,
    title: str,
) -> str:
    common = {
        "BOOK_TITLE": title,
        "EPISODE_INDEX": index,
        "EPISODE_TITLE": panel.get("title"),
        "LAYOUT": panel.get("layout"),
        "PROTAGONIST": panel.get("protagonist"),
        "PLACE": panel.get("place"),
        "RELATIONSHIP": panel.get("relationship"),
        "KEY_PROP": panel.get("key_prop"),
        "STORY_SETUP": panel.get("story_setup"),
        "STORY_CHANGE": panel.get("story_change"),
        "STORY_ENDING": panel.get("story_ending"),
        "PANEL_SCRIPT": _panel_script_text(panel),
        "CAPTION": panel.get("caption"),
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
                "SOURCE_WORD": panel.get("source_word"),
                "PUN_WORD": panel.get("pun_word"),
                "PINYIN_PAIR": panel.get("pinyin_pair"),
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
    return _render_prompt(
        "comic_variant_cover.md",
        BOOK_TITLE=title,
        INTRO=intro,
        VARIANT_LABEL=series_meta.get("variant_label") or category,
        SERIES_STYLE=series_meta.get("series_style"),
        COVER_VISUAL=series_meta.get("cover_visual"),
        EPISODE_TITLES="、".join(str(panel.get("title") or "") for panel in panels[:12]),
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
        prompt = build_variant_panel_prompt(category, panel=panel, index=index, title=title)
        filename = f"comic_section_{index:02d}_01.png"
        path = image_dir / filename
        (image_dir / f"comic_section_{index:02d}_01_prompt.txt").write_text(prompt, encoding="utf-8")
        results.append(
            ComicSectionImage(
                index=index,
                phrase=str(panel.get("caption") or ""),
                title=str(panel.get("title") or f"第{index}章"),
                animal="原创角色",
                subject=str(panel.get("visual") or ""),
                prompt=prompt,
                filename=filename,
                path=path,
                sub_index=1,
            )
        )
    _write_manifest(image_dir, results)
    return results


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
        return _generate_image_with_comic_protection(
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
        lines.extend(
            [
                f"@{index}. {panel.get('title') or f'第{index}章'}",
                f"{{{{COMIC_IMAGE_{index}_1}}}}",
                str(panel.get("caption") or ""),
            ]
        )
    return "\n".join(lines).strip()


__all__ = [
    "COMIC_VARIANT_CATEGORIES",
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
]
