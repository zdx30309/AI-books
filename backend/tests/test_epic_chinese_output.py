from __future__ import annotations

from app.models import EpicBibleSection, EpicNovelProject
from app.services.epic_novel import _bible_acceptance_anchors, _bible_payload_label, _write_draft_from_context, validate_bible_section_content


def _mostly_chinese(text: str) -> bool:
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    latin = sum(1 for ch in text if ("a" <= ch.lower() <= "z"))
    return cjk > latin * 2


def test_fallback_chapter_draft_is_chinese_and_not_fixed_fixture() -> None:
    context = {
        "project": {"title": "测试长篇", "chapter_number": 1},
        "chapterContract": {
            "title": "第1章：阶段线索",
            "chapter_number": 1,
            "plot_function": "局部线索制造制度压力",
            "bureaucracy_function": "流程成为阻碍",
            "institution_used": ["核心机构"],
            "must_execute": ["展示证据链", "让官僚体系成为主动阻碍", "以钩子结尾"],
            "character_arc_deltas": [{"character": "主角"}],
            "ending_hook": "一个证据指向更高层矛盾。",
        },
        "recentChapters": [],
        "compiledPromptContext": {"referenceInspirationPack": {"cards": []}},
        "referenceInspirationPack": {"cards": []},
    }

    draft = _write_draft_from_context(context)

    assert _mostly_chinese(draft)
    assert "Shen" not in draft
    assert "Night Watch" not in draft
    assert "沈知微" not in draft
    assert "夜巡司" not in draft


def test_bible_acceptance_rejects_smoke_fallback_metadata() -> None:
    content = "# 项目总览\n\n## 故事载荷\n- 主角：主角\n- 机构：核心机构\n- 规则：世界规则\n- 关系：关键关系\n"
    result = validate_bible_section_content(
        content,
        {
            "sectionKey": "project_overview",
            "storyPayload": {"_meta": {"status": "fallback", "production_quality": False}},
            "referenceProfile": {},
            "rawTextIncluded": False,
        },
    )

    assert result["status"] == "fail"
    assert "fallback_or_placeholder" in result["issues"]


def test_bible_acceptance_anchors_make_planning_sections_concrete() -> None:
    project = EpicNovelProject(title="验收项目", premise="主角在复杂世界中调查关键机构留下的伏笔")
    section = EpicBibleSection(section_key="global_spine", section_title="全局主线", section_type="planning")
    profile_notes = {"version": "test", "sourceCardCount": 10, "mechanisms": ["制度阻力"], "antiCliche": ["不照搬原文"]}
    payload = {
        "_meta": {"status": "generated", "production_quality": True},
        "主线阶段": "每卷都要推进关键机构、世界规则和伏笔终局。",
        "执行规则": "章节合同必须绑定行动代价、势力冲突和关系变化。",
        "acceptanceAnchors": _bible_acceptance_anchors(project, section, profile_notes),
    }
    content = "\n".join(f"{key}: {value}" for key, value in payload["acceptanceAnchors"].items())
    content += "\n章节执行说明：这一节必须把主角、关键机构、世界规则、人物关系、伏笔终局、行动代价和势力冲突落到后续章节合同。"
    content += "\n生产约束说明：所有规划都要服务章节写作、状态抽取、验收报告和后续导出，不允许停留在抽象口号。"
    content += "\n长线维护说明：每一卷都要检查读者疑问、机构阻力、关系变化和终局压力是否持续升级。"
    result = validate_bible_section_content(
        "# 全局主线\n\n" + content,
        {
            "sectionKey": "global_spine",
            "storyPayload": payload,
            "referenceProfile": profile_notes,
            "_meta": payload["_meta"],
            "rawTextIncluded": False,
        },
    )

    assert result["status"] == "pass"


def test_bible_payload_label_never_exposes_common_english_keys() -> None:
    assert _bible_payload_label("projectOverview") == "项目总览"
    assert _bible_payload_label("narrative_present_tension") == "当前叙事张力"
    assert _bible_payload_label("involved_characters") == "涉及人物"


def test_bible_acceptance_rejects_visible_english_keys_and_smoke_markers() -> None:
    content = "\n".join(
        [
            "# 项目总览",
            "- projectOverview：这是一个具体故事，但标签仍然暴露英文 key。",
            "- 主角：主角必须承担行动代价。",
            "- 关键机构：机构规则会推动案件。",
            "- 世界规则：规则会限制角色。",
            "- 关系：关系变化有代价。",
            "- 伏笔：伏笔指向终局。",
            "- 势力：势力冲突推动章节。",
            "acceptance smoke 2026-06-12",
        ]
    )
    result = validate_bible_section_content(
        content,
        {
            "sectionKey": "project_overview",
            "storyPayload": {"项目总览": "具体内容", "主角": "行动代价", "_meta": {"status": "generated", "production_quality": True}},
            "referenceProfile": {"version": "test"},
            "referenceUsage": {"usedCardIds": ["card"], "profileVersion": "profile"},
            "rawTextIncluded": False,
        },
    )

    assert result["status"] == "fail"
    assert "visible_english_key_labels" in result["issues"]
    assert "hardcoded_fixture_or_smoke_content" in result["issues"]


def test_bible_acceptance_rejects_generic_structure_field_overuse() -> None:
    content = "\n".join(
        [
            "# 全局主线",
            "- 结构字段：主角与关键机构、世界规则、人物关系、伏笔终局、行动代价和势力冲突都在这里。",
            "- 结构字段：主角与关键机构、世界规则、人物关系、伏笔终局、行动代价和势力冲突继续重复。",
            "- 结构字段：主角与关键机构、世界规则、人物关系、伏笔终局、行动代价和势力冲突再次重复。",
            "- 结构字段：主角与关键机构、世界规则、人物关系、伏笔终局、行动代价和势力冲突第四次重复。",
        ]
    )
    result = validate_bible_section_content(
        content,
        {
            "sectionKey": "global_spine",
            "storyPayload": {"主线": "具体内容", "终局": "具体内容", "_meta": {"status": "generated", "production_quality": True}},
            "referenceProfile": {"version": "test"},
            "referenceUsage": {"usedCardIds": ["card"], "profileVersion": "profile"},
            "rawTextIncluded": False,
        },
    )

    assert result["status"] == "fail"
    assert "generic_structure_field_overuse" in result["issues"]
