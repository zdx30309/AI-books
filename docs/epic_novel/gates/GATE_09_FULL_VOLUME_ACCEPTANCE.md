# Gate 9: Full Volume Acceptance

Status:
DONE / PASS for configured live test volume on 2026-06-13.

Goal:
Complete one full volume with planning, chapters, QA, repairs, canon state, and export.

Read first:
- AGENTS.md
- docs/epic_novel/GATE_8_THIRTY_CHAPTER_REPORT.md
- docs/epic_novel/30_CHAPTER_CONTINUITY_REPORT.md
- docs/epic_novel/EPIC_NOVEL_ENGINE_FULL_SPEC.md
- docs/epic_novel/09_ACCEPTANCE_CRITERIA.md

Scope:
One full volume end-to-end.

Out of scope:
- full 300W manuscript
- final frontend readiness
- full cross-volume completion

Tasks:
1. Select volume 1 or a configured test volume.
2. Verify VolumeContract exists and is production-quality.
3. Generate all ChapterContracts for the volume.
4. Execute every chapter serially.
5. Run QA and repairs.
6. Verify:
   - volume main plot resolved
   - local mystery resolved or advanced correctly
   - global spine advanced
   - heroine arcs advanced
   - power progression consistent
   - foreshadows setup/echo/payoff tracked
   - reader questions managed
   - state ledgers updated
   - ReferenceProfile/cards used
7. Export volume manuscript and volume acceptance report.

Tests:
- volume contract required
- missing chapter contract fails
- failed QA blocks volume completion
- unresolved required payoff fails
- state continuity holds
- export writes file/metadata

Create/update:
- docs/epic_novel/FULL_VOLUME_ACCEPTANCE_REPORT.md
- docs/epic_novel/GATE_9_FULL_VOLUME_REPORT.md
- docs/epic_novel/IMPLEMENTATION_STATUS.md
- docs/epic_novel/PRODUCTION_READINESS_RECHECK.md

PASS criteria:
- One full volume passes contracts, execution, QA, state, and export.
- Volume-level plot and ledgers are coherent.

Evidence:
- `docs/epic_novel/GATE_9_FULL_VOLUME_REPORT.md`
- `docs/epic_novel/FULL_VOLUME_ACCEPTANCE_REPORT.md`
- `.local/epic_novel_runs/gate9_full_volume_2026-06-12T214212_737800Z.json`
- `.local/epic_novel_runs/full_volume_acceptance_2026-06-12T214212_736039Z.md`
- `E:\video-plan\.local\epic_novel_exports\本长篇项目 第1卷 正文.md`
- `$env:PYTHONPATH='backend'; python scripts/epic_gate9_full_volume_acceptance.py --project-id 44ce72be-a423-4f3b-895b-ad8a0fbb5e63 --volume 1` -> PASS

Boundary:
- The verified configured volume is chapter range 1-3 in the live smoke project.
- This does not prove every volume or 300万字 readiness.
