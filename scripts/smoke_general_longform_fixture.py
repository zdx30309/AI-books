"""Build deterministic browser data and real DOCX files, without any paid calls."""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]

from app.prompts.general_longform_prompts import get_manifest
from app.prompts.goal_driven_templates import list_categories
from app.services import general_longform as engine
from general_longform_helpers import FakeGateway, fake_research, new_state, run


def generate(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    engine.research = fake_research
    state = new_state()
    gateway = FakeGateway(directory / "projects" / state["project_id"])
    def visible():
        return engine.public_state(deepcopy(state)) | {"task_status": "paused", "progress": 15, "current_step": "通用长篇：确定性浏览器测试数据",
            "call_logs": [], "delivery_holds": [], "task_error": None}
    run(gateway.directory, state, gateway, "plan")
    initial = visible()
    run(gateway.directory, state, gateway, "confirm_outline")
    confirmed = visible()
    run(gateway.directory, state, gateway, "next_chapter")
    one_chapter = visible()
    files = {item["id"]: {"filename": item["filename"], "base64": base64.b64encode((gateway.directory / "exports" / item["filename"]).read_bytes()).decode()} for item in state["artifacts"]}
    accepted = state["chapters"]["1"]["accepted"]
    legacy = engine.initial_state(state["project_id"], version="2026-09-14-v16")
    legacy.update(phase="awaiting_materials", task_status="paused", progress=0, current_step="旧版项目保持固定提示词",
                  call_logs=[], delivery_holds=[], task_error=None, can_resume=False)
    manifest = {**get_manifest(), "default_version": get_manifest()["version"]}
    legacy_manifest = {**get_manifest("2026-09-14-v16"), "default_version": get_manifest()["version"]}
    fixture = {"simulation": True, "task_id": state["project_id"], "initial": initial, "confirmed": confirmed,
        "one_chapter": one_chapter, "chapter_content": (gateway.directory / accepted["acceptedContent"]).read_text(encoding="utf-8"),
        "files": files, "manifest": manifest, "legacy_project": legacy, "legacy_manifest": legacy_manifest, "categories": list_categories()}
    path = directory / "browser_fixture.json"
    path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".local/general_longform_integration/browser")
    args = parser.parse_args()
    print(generate(args.output))
