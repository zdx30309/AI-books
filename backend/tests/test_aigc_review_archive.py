from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.api.routers import aigc_review as router
from app.models import Chapter, Task, TaskStatus
from app.services.aigc_report_review import sha256_text


class _FakeQuery:
    def __init__(self, db, model):
        self.db = db
        self.model = model

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def first(self):
        return next((item for item in self.db.items if isinstance(item, self.model)), None)


class _FakeDb:
    def __init__(self):
        self.items = []
        self.commits = 0

    def add(self, item):
        self.items.append(item)

    def flush(self):
        for item in self.items:
            if isinstance(item, Task) and item.id is None:
                item.id = uuid4()

    def commit(self):
        self.commits += 1

    def refresh(self, _item):
        return None

    def rollback(self):
        return None

    def query(self, model):
        return _FakeQuery(self, model)


def test_review_candidate_is_archived_with_original_candidate_and_report(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(router, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(router, "_copy_stored_report", lambda *_args, **_kwargs: False)
    db = _FakeDb()
    user = SimpleNamespace(id=uuid4())
    original = "书名\n\n这是不可变原稿。"
    candidate = "书名\n\n这是通过质量审查的候选稿。"
    result = {
        "request_id": "request-archive",
        "status": "candidate_ready",
        "original_sha256": sha256_text(original),
        "candidate_sha256": sha256_text(candidate),
        "candidate_text": candidate,
        "quality_audit": {"pass": True},
        "iteration": 1,
        "prompt_version": "zhuque-report-review-v3.4",
        "models": {"rewrite_provider": "deepseek", "rewrite": "deepseek-v4-pro"},
        "cost": {"estimated_cny": 0.01},
    }
    baseline = {"human_percent": 0.0, "report_storage_id": str(uuid4())}

    task = router._save_review_archive(
        db,
        user,
        original=original,
        result=result,
        baseline_report=baseline,
        title=None,
        content_type="article",
    )

    assert task.status == TaskStatus.MANUAL_REVIEW
    assert task.config["bookshelf"] == "AI审稿书架"
    assert task.config["aigc_status"] == "candidate_ready"
    chapter = next(item for item in db.items if isinstance(item, Chapter))
    assert chapter.content == candidate
    output = tmp_path / "backend" / "output" / f"task_{task.id}"
    assert (output / "04_format.md").read_text(encoding="utf-8") == original
    assert (output / "05_aigc_candidate.md").read_text(encoding="utf-8") == candidate
    assert (output / "aigc_baseline_report.json").exists()


def test_strict_recheck_promotes_archived_candidate_to_completed(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(router, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(router, "_copy_stored_report", lambda *_args, **_kwargs: False)
    db = _FakeDb()
    user = SimpleNamespace(id=uuid4())
    original = "原稿完整保留。"
    candidate = "完整保留的候选稿。"
    optimized = {
        "request_id": "request-pass",
        "status": "candidate_ready",
        "original_sha256": sha256_text(original),
        "candidate_sha256": sha256_text(candidate),
        "candidate_text": candidate,
        "quality_audit": {"pass": True},
        "prompt_version": "zhuque-report-review-v3.4",
        "models": {"rewrite_provider": "deepseek"},
        "cost": {"estimated_cny": 0.01},
    }
    task = router._save_review_archive(
        db,
        user,
        original=original,
        result=optimized,
        baseline_report={"human_percent": 0.0},
        title="验收稿",
        content_type="article",
    )

    router._update_review_archive(
        db,
        user,
        archive_task_id=str(task.id),
        optimize_result=optimized,
        recheck_report={"human_percent": 61.2},
        comparison={
            "comparison_id": "comparison-pass",
            "strict_pass": True,
            "recheck_human_percent": 61.2,
            "human_percent_delta": 61.2,
            "selected_humanized_share_percent": 61.2,
            "recommendation_reason": "已通过",
        },
    )

    assert task.status == TaskStatus.COMPLETED
    assert task.config["aigc_status"] == "passed"
    assert task.config["markdown"] == candidate
    output = tmp_path / "backend" / "output" / f"task_{task.id}"
    assert (output / "05_aigc_final.md").read_text(encoding="utf-8") == candidate
