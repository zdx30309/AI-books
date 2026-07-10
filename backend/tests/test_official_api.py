from __future__ import annotations

from app.core.config import settings
from app.utils.official_api import official_openai_base_url, official_proxy


def test_direct_only_keeps_official_base_url_but_allows_configured_proxy(monkeypatch) -> None:
    monkeypatch.setattr(settings, "OFFICIAL_API_DIRECT_ONLY", True)

    assert official_openai_base_url("https://example.com/v1") == "https://api.openai.com/v1"
    assert official_proxy("openai", "http://remote-us-proxy.example:7896") == "http://remote-us-proxy.example:7896"
