import json

import pytest

from app.core.quality_metrics import _detect_standard_longform_chapter_structure_errors
from app.services.aigc_quality_gate import AigcManualReviewRequired
from app.services import final_structure_repair as repair
from app.services.content_engine import _finance_chapter_parts


def manuscript():
    titles = ["香江保卫战", "资本的通行证", "热钱涌向亚洲", "泰国失守", "烧到俄罗斯", "资本没有统一意志"]
    sizes = [816, 760, 665, 1032, 988, 1074]
    headings = [f"@{index}. {title}" for index, title in enumerate(titles, 1)]
    return "@最终书名\n测试\n@最终简介\n简介\n@目录\n" + "\n".join(headings) + "\n@导言\n导言\n@正文\n" + "\n\n".join(
        heading + "\n" + "文" * size for heading, size in zip(headings, sizes)
    )


def groups():
    parts = _finance_chapter_parts(manuscript())
    return [{"chapters": [1], "title": parts[0][1]}, {"chapters": [2, 3], "title": "资本通行与亚洲热钱"}] + [
        {"chapters": [number], "title": title} for number, title, body in parts[3:]
    ]


def test_local_merge_preserves_all_prose_and_passes_existing_gate(monkeypatch):
    monkeypatch.setattr(repair, "detect_final_hard_errors", _detect_standard_longform_chapter_structure_errors)
    source = manuscript()
    assert _detect_standard_longform_chapter_structure_errors(source, category="social_science_core_conflict")
    result = repair.apply_groups(source, groups(), "social_science_core_conflict")
    assert len(_finance_chapter_parts(result)) == 5
    assert "".join(body for _, _, body in _finance_chapter_parts(source)) == "".join(
        body.replace("\n\n", "") for _, _, body in _finance_chapter_parts(result)
    )
    assert result.count("@2. 资本通行与亚洲热钱") == 2


@pytest.mark.parametrize("bad", [[], [{"chapters": [1, 2, 3, 4], "title": "越界修改"}],
                                   [{"chapters": [99], "title": "错误"}],
                                   [{"chapters": [1], "title": "@错误"}]])
def test_invalid_plan_cannot_change_manuscript(bad):
    with pytest.raises(AigcManualReviewRequired):
        repair.apply_groups(manuscript(), bad, "social_science_core_conflict")


def test_approved_resume_requires_exact_source_content_and_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(repair, "detect_final_hard_errors", _detect_standard_longform_chapter_structure_errors)
    source = manuscript()
    result = repair.apply_groups(source, groups(), "social_science_core_conflict")
    (tmp_path / "02_polish.md").write_text(source)
    (tmp_path / "approved_final_structure.md").write_text(result)
    (tmp_path / "approved_final_structure.json").write_text(json.dumps({
        "source_sha256": repair.digest(source), "content_sha256": repair.digest(result),
        "category": "social_science_core_conflict", "status": "quality_checked", "groups": groups(),
    }))
    assert repair.load_approved_structure(tmp_path, "social_science_core_conflict", repair.digest(result)) == result
    (tmp_path / "approved_final_structure.md").write_text(result + "篡改")
    with pytest.raises(AigcManualReviewRequired):
        repair.load_approved_structure(tmp_path, "social_science_core_conflict", repair.digest(result))


def test_paid_merge_response_is_reused_not_replayed(tmp_path, monkeypatch):
    from app.services import deepseek

    monkeypatch.setattr(repair, "detect_final_hard_errors", _detect_standard_longform_chapter_structure_errors)
    (tmp_path / "02_polish.md").write_text(manuscript())
    calls = []
    monkeypatch.setattr(deepseek, "generate_text", lambda *args, **kwargs: calls.append(kwargs) or json.dumps({"groups": groups()}))
    first = repair.repair_structure(tmp_path, "social_science_core_conflict")
    assert repair.repair_structure(tmp_path, "social_science_core_conflict") == first
    assert len(calls) == 1
    assert (tmp_path / "02_polish_before_structure_repair.md").read_text() == manuscript()


def test_uncertain_paid_merge_cannot_be_replayed(tmp_path):
    (tmp_path / "02_polish.md").write_text(manuscript())
    (tmp_path / "final_structure_merge_request.json").write_text("{}")
    with pytest.raises(AigcManualReviewRequired, match="不自动重放"):
        repair.repair_structure(tmp_path, "social_science_core_conflict")
