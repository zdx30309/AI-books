import pytest

from app.services import editor_v05
from app.services.editor_v05 import SimilarityGateHold, VIDEO_REJECTED, similarity_gate


def data(score):
    return {"max_composite": score, "overall_verdict": "高危" if score >= 50 else "安全"}


def test_below_limit_records_score_and_continues():
    patch = similarity_gate({}, "第一稿", data(42.5))
    assert patch["editor_v05_similarity"] == 42.5
    assert patch["editor_v05_similarity_attempt"] == 1
    assert len(patch["editor_v05_similarity_history"]) == 1


def test_first_high_score_stops_for_human_regeneration_decision():
    with pytest.raises(SimilarityGateHold) as caught:
        similarity_gate({}, "第一稿", data(78.7))
    assert not caught.value.rejected
    assert "人工确认是否重新生成" in str(caught.value)
    assert caught.value.config_patch["editor_v05_status"] == "similarity_blocked"


def test_resume_of_same_draft_is_not_counted_as_second_generation():
    with pytest.raises(SimilarityGateHold) as first:
        similarity_gate({}, "第一稿", data(60))
    with pytest.raises(SimilarityGateHold) as again:
        similarity_gate(first.value.config_patch, "第一稿", data(60))
    assert not again.value.rejected
    assert len(again.value.config_patch["editor_v05_similarity_history"]) == 1


def test_second_generation_still_high_rejects_video():
    with pytest.raises(SimilarityGateHold) as first:
        similarity_gate({}, "第一稿", data(60))
    with pytest.raises(SimilarityGateHold) as second:
        similarity_gate({**first.value.config_patch, "editor_v05_generation_attempt": 2}, "第二稿", data(55))
    assert second.value.rejected
    assert str(second.value).startswith(VIDEO_REJECTED)
    assert second.value.config_patch["editor_v05_video_rejected"] is True


def test_second_generation_below_limit_continues():
    with pytest.raises(SimilarityGateHold) as first:
        similarity_gate({}, "第一稿", data(60))
    patch = similarity_gate({**first.value.config_patch, "editor_v05_generation_attempt": 2}, "第二稿", data(41))
    assert patch["editor_v05_similarity_attempt"] == 2


def test_gate_can_be_disabled(monkeypatch):
    monkeypatch.setattr(editor_v05.settings, "EDITOR_V05_SIMILARITY_GATE_ENABLED", False)
    assert similarity_gate({}, "第一稿", data(90))["editor_v05_similarity"] == 90


@pytest.mark.parametrize("score", [50, 50.01, 100])
def test_limit_includes_exactly_fifty(score):
    with pytest.raises(SimilarityGateHold):
        similarity_gate({}, "第一稿", data(score))


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -1, 101, None])
def test_invalid_score_fails_closed(score):
    with pytest.raises(editor_v05.FinalizerHold):
        similarity_gate({}, "第一稿", {"max_composite": score})


def test_identical_regenerated_draft_is_still_second_attempt():
    with pytest.raises(SimilarityGateHold) as first:
        similarity_gate({}, "第一稿", data(78.7))
    with pytest.raises(SimilarityGateHold) as second:
        similarity_gate({**first.value.config_patch, "editor_v05_generation_attempt": 2}, "第一稿", data(78.7))
    assert second.value.rejected
    assert len(second.value.config_patch["editor_v05_similarity_history"]) == 2


def test_rejected_video_cannot_be_released_by_new_score():
    with pytest.raises(SimilarityGateHold) as caught:
        similarity_gate({"editor_v05_video_rejected": True}, "新稿", data(10))
    assert caught.value.rejected


def test_rejected_video_never_calls_audit_again(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(editor_v05, "call_audit", lambda *args: pytest.fail("被拒绝的视频不应重新审查"))
    current = SimpleNamespace(config={"editor_v05_video_rejected": True})
    with pytest.raises(SimilarityGateHold) as caught:
        editor_v05.finalize_editor_task(current, "新稿", tmp_path)
    assert caught.value.rejected


def test_first_gate_blocks_rewrite_and_preserves_report_on_resume(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace

    source = "资本突然撤离，市场流动性减少。"
    current = SimpleNamespace(config={"raw_text": source}, created_at=datetime.now(timezone.utc))
    monkeypatch.setattr(editor_v05, "call_audit", lambda *args: pytest.fail("副本不应调用审查模型"))
    monkeypatch.setattr(editor_v05, "finalize_manuscript", lambda *args, **kwargs: pytest.fail("高相似度不能降AI"))
    for _attempt in range(2):
        with pytest.raises(SimilarityGateHold) as caught:
            editor_v05.finalize_editor_task(current, source, tmp_path)
        assert not caught.value.rejected
        assert current.config["editor_v05_status"] == "similarity_blocked"
    assert len(current.config["editor_v05_similarity_history"]) == 1
    assert list((tmp_path / editor_v05.REPORT_DIR_NAME).glob("*.docx"))


def test_below_gate_reaches_next_stage(tmp_path, monkeypatch):
    from types import SimpleNamespace

    class NextStage(Exception):
        pass

    monkeypatch.setattr(editor_v05, "audit_manuscript", lambda *args, **kwargs: {"data": data(49.9)})
    monkeypatch.setattr(editor_v05, "finalize_manuscript", lambda *args, **kwargs: (_ for _ in ()).throw(NextStage()))
    with pytest.raises(NextStage):
        editor_v05._finalize_editor_task(SimpleNamespace(config={"raw_text": "原稿"}), "#新稿\n正文", tmp_path)
