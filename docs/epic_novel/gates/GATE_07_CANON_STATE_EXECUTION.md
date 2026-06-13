# Gate 7: Canon-State Execution 1-3 Strict

Status:
DONE / PASS on 2026-06-13.

Goal:
Re-run chapters 1-3 after Gates 2-6 and prove strict canon-state execution.

Read first:
- AGENTS.md
- docs/epic_novel/GATE_6_QA_ACCEPTANCE_REPORT.md
- docs/epic_novel/MASTER_BIBLE_CONTENT_MATRIX.md
- docs/epic_novel/REFERENCE_INTELLIGENCE_EVIDENCE.md
- docs/epic_novel/EPIC_NOVEL_ENGINE_FULL_SPEC.md
- docs/epic_novel/09_ACCEPTANCE_CRITERIA.md

Scope:
Strict live DB 1-3 chapter generation, state continuity, canon write boundaries.

Out of scope:
- 30 chapters
- full volume
- frontend
- 300W readiness

Tasks:
1. Create or reuse a test project.
2. Generate:
   - Master Bible
   - volume contracts
   - chapter contracts
   - chapters 1-3
3. Verify:
   - chapter 1 accepted before chapter 2
   - chapter 2 reads chapter 1 summary/endingHook/state
   - chapter 3 reads chapter 2 summary/endingHook/state
   - draft/repaired never write canon
   - acceptedContent writes canon ledgers only after QA PASS
4. Required canon/state ledgers:
   - CanonFact
   - CharacterState
   - PowerLedger
   - ObjectState
   - FactionState
   - InstitutionState
   - Foreshadow
   - ReaderQuestion
5. RetrievalTrace and AcceptanceReport required for every chapter.
6. ContextPack metadata required for every chapter.

Tests:
- serial generation enforced
- skipping previous accepted state fails
- nonAcceptedCanonSources=0
- chapter 2/3 previous state ready
- context includes Bible/WorldAtlas/PowerSystem/Profile/cards
- bad QA blocks canon write
- exports write files/metadata

Create/update:
- docs/epic_novel/GATE_7_CANON_STATE_REPORT.md
- docs/epic_novel/ACCEPTANCE_RUN_REPORT.md
- docs/epic_novel/IMPLEMENTATION_STATUS.md
- docs/epic_novel/PRODUCTION_READINESS_RECHECK.md

PASS criteria:
- Chapters 1-3 strict live DB canon-state PASS.
- No non-accepted canon writes.
- chapter 2/3 prove previous state consumption.

Evidence:
- `docs/epic_novel/GATE_7_CANON_STATE_REPORT.md`
- `.local/epic_novel_runs/gate7_canon_state_2026-06-12T205822_873613Z.json`
- `$env:PYTHONPATH='backend'; python scripts/epic_gate7_canon_state_audit.py --project-id 44ce72be-a423-4f3b-895b-ad8a0fbb5e63` -> PASS
