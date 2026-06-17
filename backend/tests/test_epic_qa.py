from __future__ import annotations

from uuid import uuid4

from app.models import EpicChapterContract
from app.services.epic_novel import _acceptance, _acceptance_scores, _planned_vs_actual, _write_draft_from_context


def _contract(**overrides) -> EpicChapterContract:
    data = {
        "project_id": uuid4(),
        "volume_number": 1,
        "chapter_number": 1,
        "title": "第一章 封存裂痕",
        "target_words": 1200,
        "chapter_goal": "用证据链逼近制度漏洞",
        "plot_function": "开局抛出核心疑问",
        "bureaucracy_function": "让档案司流程成为主动阻力",
        "world_atlas_function": "展示都城档案区",
        "character_arc_deltas": [{"character": "主角", "delta": "从被动追问转为主动设局"}],
        "relationship_changes": [],
        "power_system_change": {"change": "能力试探", "cost": "必须付出代价"},
        "institution_used": ["档案司"],
        "foreshadows_to_plant": ["封存编号"],
        "foreshadows_to_echo": [],
        "foreshadows_to_payoff": [],
        "reader_question_raised": "谁改了档案",
        "reader_question_answered": None,
        "core_pleasure": "证据奖励",
        "must_execute": ["展示证据链", "让官僚体系成为主动阻碍", "以钩子结尾"],
        "must_not_reveal": ["幕后主谋"],
        "ending_hook": "封存编号不该存在",
    }
    data.update(overrides)
    return EpicChapterContract(**data)


def _good_content() -> str:
    return (
        "主角在档案司调阅卷宗，先核对记录，再询问口供，最后把两份证据链并排摆出。"
        "制度程序没有配合他，公文流程反而成为主动阻碍，逼他付出代价并遵守力量规则。"
        "他决定设局追问：为什么封存编号会出现在不该存在的位置？"
        "章末钩子落在一页裂开的档案上，新的伏笔被埋下。"
    )


def _issue_types(content: str, contract: EpicChapterContract | None = None) -> tuple[str, set[str]]:
    contract = contract or _contract()
    diff = _planned_vs_actual(contract, content)
    status, issues = _acceptance(contract, content, diff)
    return status, {str(issue.get("issueType")) for issue in issues}


def test_good_chapter_passes_gate_without_blocking_issue() -> None:
    status, issues = _acceptance(_contract(), _good_content(), _planned_vs_actual(_contract(), _good_content()))

    assert status == "accepted"
    assert not any(issue["severity"] in {"high", "severe"} for issue in issues)


def test_missing_must_execute_blocks_acceptance() -> None:
    status, issue_types = _issue_types("主角在档案司听见一个传闻，章末留下钩子和伏笔。")

    assert status == "blocked"
    assert "planned_vs_actual" in issue_types


def test_fallback_or_patch_output_blocks_acceptance() -> None:
    status, issue_types = _issue_types(_good_content() + "\n\n修复补丁：补足章节合同。")

    assert status == "blocked"
    assert "fallback_or_patch_output" in issue_types


def test_english_boilerplate_blocks_acceptance() -> None:
    content = "As an AI, I cannot write the full chapter. This chapter will explain the plot outline and summarize events. " * 3
    status, issue_types = _issue_types(content)

    assert status == "blocked"
    assert "english_or_boilerplate" in issue_types


def test_raw_reference_like_text_blocks_acceptance() -> None:
    status, issue_types = _issue_types(_good_content() + "这里复制原书原始参考文本作为桥段。")

    assert status == "blocked"
    assert "raw_reference_leakage" in issue_types


def test_broken_power_rule_blocks_acceptance() -> None:
    status, issue_types = _issue_types(_good_content() + "随后他无需代价直接突破，秒杀所有阻碍。")

    assert status == "blocked"
    assert "broken_power_rule" in issue_types


def test_heroine_agency_missing_blocks_acceptance() -> None:
    contract = _contract(character_arc_deltas=[{"character": "林照", "role": "女主", "delta": "独立调查"}])
    content = (
        "主角在档案司调阅卷宗，核对记录与口供，展示证据链。"
        "制度程序成为主动阻碍，力量规则要求代价。章末封存编号不该存在，伏笔继续。"
    )
    status, issue_types = _issue_types(content, contract)

    assert status == "blocked"
    assert "heroine_agency_missing" in issue_types


def test_bureaucracy_function_missing_blocks_acceptance() -> None:
    content = (
        "主角核对证据链和口供，付出代价遵守规则。"
        "为什么封存编号不该存在？章末钩子和伏笔继续。"
    )
    status, issue_types = _issue_types(content)

    assert status == "blocked"
    assert "bureaucracy_function_missing" in issue_types


def test_foreshadow_or_reader_question_overclose_blocks_acceptance() -> None:
    contract = _contract(foreshadows_to_payoff=["封存编号"], reader_question_answered="谁改了档案")
    status, issue_types = _issue_types(_good_content() + "全部真相已经揭开，一切都解释清楚，再无疑问。", contract)

    assert status == "blocked"
    assert "foreshadow_or_question_overclosed" in issue_types


def test_acceptance_scores_are_issue_driven_not_fixed_constants() -> None:
    clean_scores = _acceptance_scores([])
    blocked_scores = _acceptance_scores([{"severity": "high", "issueType": "broken_power_rule"}])

    assert clean_scores["power_legality_score"] > blocked_scores["power_legality_score"]
    assert clean_scores["overall"] > blocked_scores["overall"]


def test_forbidden_knowledge_reveal_blocks_acceptance() -> None:
    contract = _contract(
        raw_json={
            "consistencyContract": {
                "forbiddenKnowledgeReveals": ["禁库钥印"],
            }
        }
    )
    status, issue_types = _issue_types(_good_content() + "他忽然说出禁库钥印就在内廷。", contract)

    assert status == "blocked"
    assert "knowledge_leak" in issue_types


def test_early_secret_exposure_blocks_acceptance() -> None:
    contract = _contract(
        raw_json={
            "consistencyContract": {
                "forbiddenSecretExposureChanges": ["幕后真名"],
            }
        }
    )
    status, issue_types = _issue_types(_good_content() + "旁人提前听见幕后真名。", contract)

    assert status == "blocked"
    assert "early_secret_exposure" in issue_types


def test_professional_term_without_in_world_source_blocks_acceptance() -> None:
    contract = _contract(
        raw_json={
            "consistencyContract": {
                "professionalTermsForbiddenUnlessSourced": ["命簿核验术"],
            }
        }
    )
    status, issue_types = _issue_types(_good_content() + "他走到雨棚下，众人沉默，灯火晃动，风声压住脚步。末尾有人直接喊出命簿核验术。", contract)

    assert status == "blocked"
    assert "professional_term_without_source" in issue_types


def test_unauthorized_growth_blocks_acceptance() -> None:
    contract = _contract(
        raw_json={
            "consistencyContract": {
                "forbiddenGrowthEvents": ["突然突破"],
            }
        }
    )
    status, issue_types = _issue_types(_good_content() + "他在没有合同许可的情况下突然突破。", contract)

    assert status == "blocked"
    assert "unauthorized_growth" in issue_types


def test_locked_ability_blocks_acceptance() -> None:
    contract = _contract(
        raw_json={
            "consistencyContract": {
                "forbiddenPowerUse": ["天眼通"],
            }
        }
    )
    status, issue_types = _issue_types(_good_content() + "他当场动用天眼通看穿所有卷册。", contract)

    assert status == "blocked"
    assert "locked_ability_used" in issue_types


def test_deterministic_chapter_draft_is_chinese_story_text_not_meta_placeholder() -> None:
    contract = _contract().__dict__.copy()
    contract["institution_used"] = [{"name": "档案司", "purpose": "审查公文"}]
    context = {
        "project": {"title": "acceptance smoke 2026", "premise": "旧英文烟测标题不应进入正文", "genre": "仙侠悬疑"},
        "chapterContract": contract,
        "recentChapters": [{"chapterNumber": 1, "summary": "上一章已验收", "endingHook": "旧印浮现"}],
        "referenceInspirationPack": {"cards": [{"function": "证据链逐步增压"}], "profile": {}},
        "compiledPromptContext": {"referenceInspirationPack": {"cards": [{"function": "证据链逐步增压"}]}},
    }
    draft = _write_draft_from_context(context)
    status, issue_types = _issue_types(draft, _contract(institution_used=["档案司"]))

    assert "acceptance smoke" not in draft
    assert "{'name'" not in draft
    assert "参考书原文" not in draft
    assert "修复补丁" not in draft
    assert status == "accepted"
    assert "fallback_or_patch_output" not in issue_types
    assert "raw_reference_leakage" not in issue_types
