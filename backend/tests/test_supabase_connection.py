"""Pytest for the Supabase connection smoke test script."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _load_connection_check():
    script_path = PROJECT_ROOT / "test_supabase_connection.py"
    spec = importlib.util.spec_from_file_location("supabase_connection_smoke", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_connection_check


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_SUPABASE_INTEGRATION") != "1",
    reason="Set RUN_SUPABASE_INTEGRATION=1 to run the Supabase read/write smoke test.",
)
def test_supabase_connection_round_trip() -> None:
    run_connection_check = _load_connection_check()
    assert run_connection_check() is True
