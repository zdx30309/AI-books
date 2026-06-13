# Gate 8: 30-Chapter Continuity

Status:
DONE / PASS by deterministic continuity simulation on 2026-06-13.

Goal:
Generate or deterministically simulate 30 serial chapters and verify continuity.

Read first:
- AGENTS.md
- docs/epic_novel/GATE_7_CANON_STATE_REPORT.md
- docs/epic_novel/ACCEPTANCE_RUN_REPORT.md
- docs/epic_novel/EPIC_NOVEL_ENGINE_FULL_SPEC.md
- docs/epic_novel/09_ACCEPTANCE_CRITERIA.md

Scope:
30 serial chapters or deterministic simulation proving equivalent state behavior.

Out of scope:
- full 300W manuscript
- frontend
- cross-volume final readiness

Tasks:
1. Generate or simulate chapters 1-30 serially.
2. Every chapter must have:
   - ChapterContract
   - ContextPack
   - RetrievalTrace
   - AcceptanceReport
   - acceptedContent
   - state ledger updates
3. Verify:
   - previous state read
   - canon drift detection
   - foreshadow continuity
   - reader question continuity
   - power progression consistency
   - character arc continuity
   - relationship changes
   - world law consistency
   - ReferenceProfile/cards used
   - no raw reference text
4. high/severe blocks continuation.
5. Export 30-chapter report and manuscript.

Tests:
- cannot generate chapter N without N-1 accepted
- drift issue blocks
- missing state blocks
- ledger continuity exists
- profile/cards used across chapters
- context overflow handled
- no duplicate canon writes after retry

Create/update:
- docs/epic_novel/30_CHAPTER_CONTINUITY_REPORT.md
- docs/epic_novel/GATE_8_THIRTY_CHAPTER_REPORT.md
- docs/epic_novel/IMPLEMENTATION_STATUS.md
- docs/epic_novel/PRODUCTION_READINESS_RECHECK.md

PASS criteria:
- 30 serial chapters accepted, or deterministic continuity simulation proves equivalent state behavior.
- No unresolved canon drift.
- Every chapter has context/trace/report/state evidence.

Evidence:
- `docs/epic_novel/GATE_8_THIRTY_CHAPTER_REPORT.md`
- `docs/epic_novel/30_CHAPTER_CONTINUITY_REPORT.md`
- `.local/epic_novel_runs/gate8_thirty_chapter_2026-06-12T211746_940280Z.json`
- `.local/epic_novel_runs/30_chapter_continuity_2026-06-12T211746_034084Z.md`
- `E:\video-plan\.local\epic_novel_exports\第八闸门三十章连续性审计 20260612211418 正文.md`
- `$env:PYTHONPATH='backend'; python scripts/epic_gate8_thirty_chapter_simulation.py` -> PASS

Boundary:
- This is deterministic continuity simulation evidence, not a 30-chapter production prose run.
