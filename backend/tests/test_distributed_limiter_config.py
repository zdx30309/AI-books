from app.core.config import settings
from app.utils.distributed_limiter import _redis_url


def test_paid_api_gate_uses_dedicated_redis_first(monkeypatch):
    monkeypatch.setattr(settings, "PAID_API_GATE_REDIS_URL", "redis://gate:6379/0")
    monkeypatch.setattr(settings, "REDIS_URL", "redis://queue:6379/0")
    monkeypatch.setattr(settings, "CELERY_BROKER_URL", "redis://broker:6379/0")

    assert _redis_url() == "redis://gate:6379/0"


def test_paid_api_gate_falls_back_to_general_redis(monkeypatch):
    monkeypatch.setattr(settings, "PAID_API_GATE_REDIS_URL", "")
    monkeypatch.setattr(settings, "REDIS_URL", "redis://queue:6379/0")
    monkeypatch.setattr(settings, "CELERY_BROKER_URL", "redis://broker:6379/0")

    assert _redis_url() == "redis://queue:6379/0"
