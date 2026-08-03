from __future__ import annotations

from app.services import zhuque_detector_profile as profile


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


def test_fetch_public_profile_builds_stable_detector_generation() -> None:
    calls: list[tuple[str, dict]] = []

    def fake_get(url: str, **kwargs):
        calls.append((url, kwargs))
        return _FakeResponse(
            {
                "success": True,
                "message": "获取成功",
                "data": {"updateDate": "2026-07-21T12:22:05.000Z"},
            }
        )

    result = profile.fetch_zhuque_detector_profile(http_get=fake_get)

    assert result["status"] == "available"
    assert result["model_update_at"] == "2026-07-21T12:22:05Z"
    assert result["model_generation"].startswith("tencent_zhuque@2026-07-21T12:22:05Z#")
    assert result["minimum_text_chars"] == 350
    assert result["result_contract"]["labels"] == {
        "0": "human",
        "1": "ai",
        "2": "suspected",
    }
    assert calls[0][0].endswith("/model/update_time")


def test_fetch_public_profile_fails_open_for_manual_report_workflow() -> None:
    def failing_get(*_args, **_kwargs):
        raise TimeoutError("public endpoint timeout")

    result = profile.fetch_zhuque_detector_profile(http_get=failing_get)

    assert result["status"] == "unavailable"
    assert result["model_generation"] is None
    assert "TimeoutError" in result["error"]


def test_report_snapshot_is_small_and_explicitly_upload_time_provenance() -> None:
    snapshot = profile.report_detector_snapshot(
        {
            "profile_version": profile.PROFILE_VERSION,
            "provider": "tencent_zhuque",
            "status": "available",
            "model_update_at": "2026-07-21T12:22:05Z",
            "model_generation": "generation-1",
            "fetched_at": "2026-07-30T00:00:00Z",
            "detector_url": "https://matrix.tencent.com/ai-detect/ai_gen_txt/",
            "model_update_url": "https://matrix.tencent.com/model/update_time",
            "scope_note": "public metadata only",
            "secret": "must-not-leak",
        }
    )

    assert snapshot is not None
    assert snapshot["model_generation"] == "generation-1"
    assert snapshot["provenance_basis"].endswith("observed_at_report_upload")
    assert "secret" not in snapshot
