# -*- coding: utf-8 -*-
"""Generate manifest.json for the general_short prompt bundle (hashes + stage map)."""
from hashlib import sha256
from pathlib import Path
import json

BUNDLE = Path("app/prompts/general_short")
SECT = BUNDLE / "sections"
STAGES = BUNDLE / "stages"

SECTIONS = [
    ("adapter", "01_adapter.txt", "prompt", ["draft"]),
    ("identity", "02_identity.txt", "prompt", ["draft"]),
    ("taxonomy", "03_taxonomy.txt", "prompt", ["draft", "structure_extract"]),
    ("facts", "04_facts.txt", "prompt", ["draft"]),
    ("packaging", "05_packaging.txt", "prompt", ["draft"]),
    ("outline", "06_outline.txt", "prompt", ["draft"]),
    ("style", "07_style.txt", "prompt", ["draft"]),
    ("continuity", "08_continuity.txt", "prompt", ["draft"]),
]
STAGE_MAP = {
    "draft": ["adapter", "identity", "taxonomy", "facts", "packaging", "outline", "style", "continuity"],
    "structure_extract": ["taxonomy"],
    "differentiation_audit": [],
}

def main() -> None:
    manifest = {
        "category": "general_short",
        "label": "通用短篇",
        "version": "2026-09-28-v01",
        "derived_from": "general_short_writer.md (v09 mega-prompt, archived)",
        "sections": [],
        "stage_sections": STAGE_MAP,
    }
    for sid, filename, role, stages in SECTIONS:
        raw = (SECT / filename).read_bytes()
        manifest["sections"].append({
            "id": sid, "file": f"sections/{filename}", "role": role,
            "stages": stages, "chars": len(raw.decode("utf-8")),
            "sha256": sha256(raw).hexdigest(),
        })
    (BUNDLE / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    total = sum(s["chars"] for s in manifest["sections"])
    print("sections:", len(manifest["sections"]), "chars:", total)

if __name__ == "__main__":
    main()
