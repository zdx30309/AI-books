"""Pytest for the Supabase connection smoke test script."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from test_supabase_connection import run_connection_check


@pytest.mark.integration
def test_supabase_connection_round_trip() -> None:
    assert run_connection_check() is True
