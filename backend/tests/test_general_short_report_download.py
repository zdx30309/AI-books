"""通用短篇审查报告下载接口测试。"""
from __future__ import annotations

from pathlib import Path

from app.api.routers import tasks as tasks_router


def _fake_task(task_id: str = "11111111-1111-1111-1111-111111111111"):
    class T:
        id = task_id
        owner_id = "owner"
    return T()


def test_report_response_serves_the_newest_docx(tmp_path: Path, monkeypatch) -> None:
    report_dir = tmp_path / "04_文本相似度审查报告"
    report_dir.mkdir(parents=True)
    older = report_dir / "书_文本相似度审查报告_2026年9月29日_01时00分00秒.docx"
    newer = report_dir / "书_文本相似度审查报告_2026年9月29日_02时00分00秒.docx"
    older.write_bytes(b"PK\x03\x04old")
    newer.write_bytes(b"PK\x03\x04new")
    import os, time
    os.utime(older, (time.time() - 100, time.time() - 100))

    monkeypatch.setattr(tasks_router, "_task_output_dir", lambda _id: tmp_path)
    resp = tasks_router._build_similarity_report_response(_fake_task())
    assert Path(resp.path).name == newer.name
    assert "文本相似度审查报告" in resp.filename


def test_missing_report_dir_returns_404(tmp_path: Path, monkeypatch) -> None:
    from fastapi import HTTPException
    monkeypatch.setattr(tasks_router, "_task_output_dir", lambda _id: tmp_path)
    try:
        tasks_router._build_similarity_report_response(_fake_task())
    except HTTPException as exc:
        assert exc.status_code == 404
        assert "审查报告" in str(exc.detail)
    else:
        raise AssertionError("缺少报告时应返回 404")


def test_empty_report_dir_returns_404(tmp_path: Path, monkeypatch) -> None:
    from fastapi import HTTPException
    (tmp_path / "04_文本相似度审查报告").mkdir(parents=True)
    monkeypatch.setattr(tasks_router, "_task_output_dir", lambda _id: tmp_path)
    try:
        tasks_router._build_similarity_report_response(_fake_task())
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("目录为空时应返回 404")


def test_routes_are_registered() -> None:
    paths = {getattr(r, "path", "") for r in tasks_router.router.routes}
    assert any(p.endswith("/{task_id}/similarity-report") for p in paths)
    assert any(p.endswith("/{task_id}/similarity-report/direct") for p in paths)
    assert any(p.endswith("/{task_id}/similarity-report/data") for p in paths)
