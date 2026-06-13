from __future__ import annotations

from copy import deepcopy

from app.services.epic_novel import validate_multi_volume_rolling_plan


def _volume(number: int, phase: str) -> dict:
    return {
        "volumeNumber": number,
        "title": f"第{number}卷",
        "globalSpineRole": f"{phase}全局主线职责",
        "localCaseMystery": f"{phase}案件线索与核心疑问推进",
        "worldExpansion": f"{phase}地图与机构扩张",
        "bureaucracyFactionUse": {"bureaucracy": f"{phase}官僚流程阻碍", "factions": [f"{phase}势力"]},
        "powerProgression": f"{phase}力量进阶必须付出代价",
        "protagonistArc": f"{phase}主角承担新的秩序代价",
        "heroineArcs": [f"{phase}女主独立选择并付出代价"],
        "foreshadowFlow": {
            "setups": [f"{phase}伏笔种子"],
            "echoes": [f"{phase}伏笔回声"],
            "payoffs": [f"{phase}阶段兑现"],
        },
        "readerQuestionFlow": {
            "raised": [f"{phase}谁改写了证据"],
            "partialReveals": [f"{phase}只给局部答案"],
            "answers": [],
        },
        "endingExplosion": f"{phase}卷尾爆点改变局势",
        "priorVolumeSummaries": [] if number == 1 else [{"volumeNumber": number - 1, "summary": "前卷留下未解线索"}],
        "activeUnresolvedLedger": {
            "foreshadows": [{"key": "global_truth_gap", "status": "open"}],
            "readerQuestions": [{"key": "who_changed_record", "status": "open"}],
        },
        "futureRevealConstraints": [f"第{number}卷不得提前泄露终局揭示"],
        "longRangeCharacterArcs": [{"character": "主角", "arcEnd": "终局承担"}],
    }


def _valid_plan() -> dict:
    return {
        "globalSpine": {"finalReveal": "终局真相", "hiddenTruth": "隐藏制度漏洞"},
        "volumes": [_volume(1, "立规"), _volume(2, "扩域"), _volume(3, "终局")],
        "rollingContext": {
            "oldStateCompression": {
                "sourceCounts": {"canonFacts": 1, "characterStates": 1},
                "activeFacts": ["主角已经取得第一份可公开证据"],
                "activeCharacterStates": ["主角仍受制度约束"],
                "activePowerStates": [],
                "activeFactionStates": [],
                "activeInstitutionStates": [],
            }
        },
    }


def _issue_types(plan: dict) -> set[str]:
    return {issue["issueType"] for issue in validate_multi_volume_rolling_plan(plan)["issues"]}


def test_multi_volume_plan_passes_when_cross_volume_context_is_complete() -> None:
    result = validate_multi_volume_rolling_plan(_valid_plan())

    assert result["status"] == "pass"
    assert result["blockingIssueCount"] == 0


def test_volume_missing_global_spine_role_fails() -> None:
    plan = _valid_plan()
    plan["volumes"][0]["globalSpineRole"] = ""

    result = validate_multi_volume_rolling_plan(plan)

    assert result["status"] == "need_repair"
    assert "missing_globalSpineRole" in _issue_types(plan)


def test_repeated_filler_volume_fails() -> None:
    plan = _valid_plan()
    repeated = _volume(1, "重复")
    repeated["volumeNumber"] = 2
    repeated["priorVolumeSummaries"] = [{"volumeNumber": 1, "summary": "前卷摘要"}]
    repeated_final = deepcopy(repeated)
    repeated_final["volumeNumber"] = 3
    repeated_final["priorVolumeSummaries"] = [{"volumeNumber": 2, "summary": "前卷摘要"}]
    plan["volumes"] = [repeated, repeated_final]

    result = validate_multi_volume_rolling_plan(plan)

    assert result["status"] == "need_repair"
    assert "repeated_filler_volume" in _issue_types(plan)


def test_unresolved_ledger_not_carried_forward_fails() -> None:
    plan = _valid_plan()
    plan["volumes"][1]["activeUnresolvedLedger"] = {"foreshadows": [], "readerQuestions": []}

    result = validate_multi_volume_rolling_plan(plan)

    assert result["status"] == "need_repair"
    assert "unresolved_ledger_not_carried_forward" in _issue_types(plan)


def test_future_reveal_violated_fails_before_final_volume() -> None:
    plan = _valid_plan()
    plan["volumes"][0]["endingExplosion"] = "本卷直接公开终局真相"

    result = validate_multi_volume_rolling_plan(plan)

    assert result["status"] == "need_repair"
    assert "future_reveal_violated" in _issue_types(plan)


def test_old_state_compression_must_preserve_active_facts() -> None:
    plan = _valid_plan()
    plan["rollingContext"]["oldStateCompression"] = {
        "sourceCounts": {"canonFacts": 2, "characterStates": 1},
        "activeFacts": [],
        "activeCharacterStates": [],
        "activePowerStates": [],
        "activeFactionStates": [],
        "activeInstitutionStates": [],
    }

    result = validate_multi_volume_rolling_plan(plan)

    assert result["status"] == "need_repair"
    assert "old_state_compression_lost_active_facts" in _issue_types(plan)
