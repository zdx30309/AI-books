from contextlib import nullcontext
from pathlib import Path
import sys
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services import gemini


def _capture(monkeypatch):
    calls = []

    def generate(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text="润色结果", candidates=[], usage_metadata=None)

    monkeypatch.setattr(gemini.settings, "GOOGLE_GENAI_USE_VERTEXAI", True)
    monkeypatch.setattr(gemini, "_get_vertex_client", lambda *args: SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    monkeypatch.setattr(gemini, "_gemini_call_gate", nullcontext)
    monkeypatch.setattr(gemini, "_google_proxy_env", nullcontext)
    return calls


def test_thinking_level_replaces_budget_and_temperature(monkeypatch):
    calls = _capture(monkeypatch)
    monkeypatch.setattr(gemini.settings, "GEMINI_THINKING_LEVEL", "low")
    assert gemini.generate_text("来源", temperature=0.3) == "润色结果"
    config = calls[0]["config"]
    assert str(config.thinking_config.thinking_level.value).lower() == "low"
    assert config.thinking_config.thinking_budget is None
    assert config.temperature is None


def test_empty_thinking_level_keeps_legacy_budget(monkeypatch):
    calls = _capture(monkeypatch)
    monkeypatch.setattr(gemini.settings, "GEMINI_THINKING_LEVEL", "")
    monkeypatch.setattr(gemini.settings, "GEMINI_THINKING_BUDGET", 0)
    assert gemini.generate_text("来源", temperature=0.3) == "润色结果"
    config = calls[0]["config"]
    assert config.thinking_config.thinking_budget == 0
    assert config.thinking_config.thinking_level is None
    assert config.temperature == 0.3
