"""Local storage helpers for the Epic Novel Engine."""
from __future__ import annotations

import hashlib
from pathlib import Path

from app.core.config import PROJECT_ROOT, settings


def resolve_repo_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def reference_root() -> Path:
    raw_path = resolve_repo_path(settings.REFERENCE_RAW_PATH)
    if raw_path.name == "raw":
        return raw_path.parent
    return raw_path


def reference_raw_path() -> Path:
    return resolve_repo_path(settings.REFERENCE_RAW_PATH)


def reference_processed_path() -> Path:
    return resolve_repo_path(settings.REFERENCE_PROCESSED_PATH)


def reference_download_path() -> Path:
    return resolve_repo_path(settings.REFERENCE_DOWNLOAD_PATH)


def epic_export_path() -> Path:
    return resolve_repo_path(settings.EPIC_EXPORT_PATH)


def epic_run_artifact_path() -> Path:
    return resolve_repo_path(settings.EPIC_RUN_ARTIFACT_PATH)


def ensure_epic_storage() -> dict[str, str]:
    paths = {
        "reference_root": reference_root(),
        "reference_raw": reference_raw_path(),
        "reference_processed": reference_processed_path(),
        "reference_downloads": reference_download_path(),
        "exports": epic_export_path(),
        "runs": epic_run_artifact_path(),
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    for name in [
        "tier_1_core_similarity",
        "tier_2_case_mystery",
        "tier_3_court_power",
        "tier_4_longform_xianxia",
    ]:
        (reference_raw_path() / name).mkdir(parents=True, exist_ok=True)
    for name in [
        "pattern_cards",
        "arc_cards",
        "foreshadow_cards",
        "anti_cliche_cards",
        "worldbuilding_cards",
        "bureaucracy_cards",
        "map_cards",
        "faction_cards",
        "mechanism_profiles",
    ]:
        (reference_processed_path() / name).mkdir(parents=True, exist_ok=True)
    return {key: str(path) for key, path in paths.items()}


def compute_file_hash(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tier_folder(tier: int) -> str:
    return {
        1: "tier_1_core_similarity",
        2: "tier_2_case_mystery",
        3: "tier_3_court_power",
        4: "tier_4_longform_xianxia",
    }.get(int(tier or 4), "tier_4_longform_xianxia")
