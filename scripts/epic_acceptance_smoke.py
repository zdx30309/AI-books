"""DB-backed Epic Novel acceptance smoke for the implemented deterministic engine."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.session import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    EpicAcceptanceReport,
    EpicBibleCoverageItem,
    EpicBibleSection,
    EpicChapter,
    EpicChapterContract,
    EpicCanonFact,
    EpicCharacterState,
    EpicFactionState,
    EpicInstitutionState,
    EpicNovelProject,
    EpicObjectState,
    EpicRetrievalTrace,
    EpicStateExtract,
    EpicVolumeContract,
    ReferenceMechanismProfile,
    ReferencePatternCard,
)
from app.schemas.epic_novel import EpicNovelCreateRequest  # noqa: E402
from app.services.epic_novel import (  # noqa: E402
    build_chapter_context_pack,
    compile_chapter_contracts,
    create_epic_project,
    execute_chapter,
    export_bible,
    export_novel,
    forge_power_system,
    generate_bible_manifest,
    generate_bible_section,
    generate_ensemble_cast,
    generate_global_spine,
    generate_volume_contracts,
    generate_world_atlas,
    generate_world_bible,
    run_bible_coverage_ci,
    run_worldview_tournament,
)


def log(message: str) -> None:
    print(f"[epic-smoke] {message}", flush=True)


def main() -> int:
    log("initializing database")
    init_db()
    db = SessionLocal()
    try:
        existing_project_id = os.getenv("EPIC_ACCEPTANCE_PROJECT_ID", "").strip()
        if existing_project_id:
            log(f"using existing project {existing_project_id}")
            project = db.query(EpicNovelProject).filter(EpicNovelProject.id == UUID(existing_project_id)).first()
            if project is None:
                raise RuntimeError(f"EPIC_ACCEPTANCE_PROJECT_ID not found: {existing_project_id}")
        else:
            stamp = datetime.now().strftime("%Y-%m-%d %H%M%S")
            req = EpicNovelCreateRequest(
                title=f"acceptance smoke {stamp}",
                premise="A modern forensic clerk transmigrates into an ancient immortal bureaucracy and solves cases that expose a dynasty-spanning conspiracy.",
                core_pleasures=["case mystery", "bureaucratic pressure", "power progression", "ensemble payoff"],
                target_volumes=3,
                target_chapters=10,
                target_words=300_000,
                bible_target_words=20_000,
            )
            log("creating project")
            project = create_epic_project(db, req)
        log(f"project={project.id}")
        log("running worldview tournament")
        run_worldview_tournament(db, project.id)
        log("generating world bible")
        generate_world_bible(db, project.id)
        log("generating world atlas")
        generate_world_atlas(db, project.id)
        log("forging power system")
        forge_power_system(db, project.id)
        log("generating ensemble cast")
        generate_ensemble_cast(db, project.id)
        log("generating global spine")
        generate_global_spine(db, project.id)
        log("generating bible manifest")
        manifest = generate_bible_manifest(db, project.id)
        for section in manifest["sections"]:
            log(f"generating bible section {section.section_key}")
            generate_bible_section(db, project.id, section.section_key)
        log("running bible coverage CI")
        run_bible_coverage_ci(db, project.id)
        log("generating volume contracts")
        generate_volume_contracts(db, project.id)
        log("compiling chapter contracts")
        for volume_number in range(1, int(project.target_volumes or 3) + 1):
            log(f"compiling chapter contracts for volume {volume_number}")
            compile_chapter_contracts(db, project.id, volume_number)
        for chapter_number in (1, 2, 3):
            log(f"executing chapter {chapter_number}")
            execute_chapter(db, project.id, chapter_number)

        log("building verification context packs")
        context2 = build_chapter_context_pack(db, project.id, 2)
        context3 = build_chapter_context_pack(db, project.id, 3)
        log("exporting bible and novel")
        bible_content, _, _ = export_bible(db, project.id, "md")
        novel_content, _, _ = export_novel(db, project.id, "md")

        log("collecting summary")
        non_accepted_canon = (
            db.query(EpicCanonFact)
            .filter(EpicCanonFact.project_id == project.id, EpicCanonFact.source != "acceptedContent")
            .count()
        )
        bible_failures = [
            row.section_key
            for row in db.query(EpicBibleSection).filter(EpicBibleSection.project_id == project.id).all()
            if row.status != "accepted"
            or ((row.content_json or {}).get("_meta") or {}).get("production_quality") is False
            or (((row.content_json or {}).get("acceptance") or {}).get("status") != "pass")
        ]
        coverage_failures = [
            row.item_key
            for row in db.query(EpicBibleCoverageItem).filter(EpicBibleCoverageItem.project_id == project.id).all()
            if row.status != "complete"
        ]
        unsafe_traces = [
            str(row.id)
            for row in db.query(EpicRetrievalTrace).filter(EpicRetrievalTrace.project_id == project.id).all()
            if row.raw_text_included is True
        ]
        summary = {
            "projectId": str(project.id),
            "referenceCards": db.query(ReferencePatternCard).count(),
            "referenceProfiles": db.query(ReferenceMechanismProfile).count(),
            "bibleSections": db.query(EpicBibleSection).filter(EpicBibleSection.project_id == project.id).count(),
            "bibleFailures": bible_failures,
            "coverageItems": db.query(EpicBibleCoverageItem).filter(EpicBibleCoverageItem.project_id == project.id).count(),
            "coverageFailures": coverage_failures,
            "volumes": db.query(EpicVolumeContract).filter(EpicVolumeContract.project_id == project.id).count(),
            "chapterContracts": db.query(EpicChapterContract).filter(EpicChapterContract.project_id == project.id).count(),
            "acceptedChapters": db.query(EpicChapter).filter(EpicChapter.project_id == project.id, EpicChapter.status == "accepted").count(),
            "stateExtracts": db.query(EpicStateExtract).filter(EpicStateExtract.project_id == project.id).count(),
            "canonFacts": db.query(EpicCanonFact).filter(EpicCanonFact.project_id == project.id).count(),
            "characterStates": db.query(EpicCharacterState).filter(EpicCharacterState.project_id == project.id).count(),
            "objectStates": db.query(EpicObjectState).filter(EpicObjectState.project_id == project.id).count(),
            "factionStates": db.query(EpicFactionState).filter(EpicFactionState.project_id == project.id).count(),
            "institutionStates": db.query(EpicInstitutionState).filter(EpicInstitutionState.project_id == project.id).count(),
            "retrievalTraces": db.query(EpicRetrievalTrace).filter(EpicRetrievalTrace.project_id == project.id).count(),
            "unsafeRetrievalTraces": unsafe_traces,
            "acceptanceReports": db.query(EpicAcceptanceReport).filter(EpicAcceptanceReport.project_id == project.id).count(),
            "nonAcceptedCanonSources": non_accepted_canon,
            "context2Evidence": context2["contextEvidence"],
            "context3Evidence": context3["contextEvidence"],
            "bibleExportChars": len(bible_content),
            "novelExportChars": len(novel_content),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        required = [
            summary["referenceCards"] >= 800,
            summary["bibleSections"] >= 20,
            not summary["bibleFailures"],
            summary["coverageItems"] >= 20,
            not summary["coverageFailures"],
            summary["volumes"] >= 3,
            summary["chapterContracts"] >= 10,
            summary["acceptedChapters"] == 3,
            summary["stateExtracts"] >= 3,
            summary["retrievalTraces"] >= 3,
            not summary["unsafeRetrievalTraces"],
            summary["acceptanceReports"] >= 3,
            summary["nonAcceptedCanonSources"] == 0,
            bool(summary["context2Evidence"].get("previousRequiredStateReady")),
            bool(summary["context3Evidence"].get("previousRequiredStateReady")),
            bool(summary["context2Evidence"].get("hasReferenceMechanismProfile")),
            bool(summary["context3Evidence"].get("hasReferenceMechanismProfile")),
            summary["novelExportChars"] > 0,
            summary["bibleExportChars"] > 0,
        ]
        return 0 if all(required) else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
