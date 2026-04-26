"""Manual Supabase connection smoke test.

This script uses the shared SessionLocal from app.db.session to verify that we can
write a row to the `tasks` table, commit it, read it back immediately, and then
clean up the test record.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import Optional

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Ensure Pydantic Settings resolves the project-root `.env` file.
os.chdir(PROJECT_ROOT.parent)

from app.db.session import SessionLocal
from app.models.task import Task

TEST_SOURCE_URL = "https://test-link.com"
TEST_STATUS = "testing"


def run_connection_check() -> bool:
    """Insert, read back, compare, and clean up a test task row."""
    db = SessionLocal()
    task_id: Optional[uuid.UUID] = None
    try:
        task = Task(
            source_url=TEST_SOURCE_URL,
            status=TEST_STATUS,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        task_id = task.id

        fetched_task = db.execute(
            select(Task).where(Task.source_url == TEST_SOURCE_URL)
        ).scalar_one()

        if (
            fetched_task.source_url == task.source_url
            and fetched_task.status == task.status
            and fetched_task.id == task.id
        ):
            print("🚀 Supabase Connection Success: Data Read/Write Loop Verified!")
            return True

        return False
    finally:
        if task_id is not None:
            db.query(Task).filter(Task.id == task_id).delete(synchronize_session=False)
            db.commit()
        db.close()


if __name__ == "__main__":
    raise SystemExit(0 if run_connection_check() else 1)
