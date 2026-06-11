# Epic Novel Engine Acceptance Run Report

Updated: 2026-06-12

## Current Result

Status: `PASS FOR 09_ACCEPTANCE_CRITERIA`

Dynamic Context Compiler safeguards were added, full-section reference slicing was completed for all imported books, and a new DB-backed full acceptance smoke passed after those changes.

## Commands Run In This Audit Pass

- `Get-Content -Encoding UTF8 -Path AGENTS.md`
- `Get-Content -Encoding UTF8 -Path docs/epic_novel/EPIC_NOVEL_ENGINE_FULL_SPEC.md`
- `Get-Content -Encoding UTF8 -Path docs/epic_novel/09_ACCEPTANCE_CRITERIA.md`
- `Get-Content -Encoding UTF8 -Path docs/epic_novel/IMPLEMENTATION_STATUS.md`
- `python -m compileall backend/app/services/epic_context.py backend/app/services/epic_model_router.py backend/app/services/epic_novel.py backend/app/api/routers/epic_novel.py backend/tests/test_epic_context.py`
- `$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_context.py backend/tests/test_epic_reference.py -q`
- `npm.cmd run build`
- `$env:PYTHONPATH='backend'; python -u scripts/epic_ingest_reference_books.py --reingest-indexed`

## Results

| Check | Result | Evidence |
|---|---:|---|
| SPEC loaded | PASS | `EPIC_NOVEL_ENGINE_FULL_SPEC.md` has 2132 lines and was read in ranges |
| Dynamic Context Compiler tests | PASS | `9 passed, 1 warning` for `test_epic_context.py` + `test_epic_reference.py` |
| Frontend build after context/export changes | PASS | Vite production build succeeded |
| Live provider probes | PASS | DeepSeek V4 Pro returned `EPIC_ROUTE_OK`; GPT writer returned `EPIC_OPENAI_OK` |
| Prior DB acceptance smoke | PASS before latest context changes | Project `520dc081-b5ec-488b-a2ce-4e314fb5cc81`, 3 accepted chapters, `nonAcceptedCanonSources=0` |
| Full-section reference slicing | PASS | 57 imported books processed; 98,581 PatternCards; 10 ReferenceMechanismProfiles |
| Latest DB acceptance smoke | PASS | Project `9cb849c3-e6d4-4995-a6b3-bccb594aa0c6`, 3 accepted chapters, 10 chapter contracts, `nonAcceptedCanonSources=0` |
| Dynamic Context live metadata | PASS | Chapter 2 ContextPack: 608 cards included, 97,973 card overflow recorded, raw reference text included=false |

## Fixes Applied In This Audit Pass

- Added `backend/app/services/epic_context.py`.
- Added Dynamic Context Compiler semantics:
  - `0` means no fixed Epic business-layer cap.
  - `0` does not blindly place infinite content into one prompt.
  - Provider/model effective context window determines prompt budget.
  - Included and overflow blocks are recorded in context metadata.
  - Raw reference text keys are sanitized/excluded.
- Added model continuation support in `backend/app/services/epic_model_router.py`.
- Chapter Draft Writer now uses compiled prompt context and a done marker for continuation.
- Added export metadata mode so large Bible/novel exports can return file metadata instead of content payloads.
- Removed Epic schema/UI display caps that conflicted with longform operation.
- Updated `scripts/epic_ingest_reference_books.py` to use per-book sessions so DB disconnects do not abort the whole slicing run.
- Added unit/smoke coverage in `backend/tests/test_epic_context.py`.
- Added `SPEC_TRACEABILITY_MATRIX.md`, `ACCEPTANCE_TEST_PLAN.md`, and this report.

## Failures / Blockers Observed

- Supabase pooler closed the connection during the first full-section reingest attempt. The batch script was fixed to use per-book sessions and the job was rerun to completion.
- Pytest cache warnings persist because `.pytest_cache` permissions are restricted. Test assertions still pass.

## Remaining TODOs Before Completion

- Optional: true `.docx` Bible export.
- Optional: first-class Alembic migrations if this repo adopts a migration framework.
- Optional: full live-provider chapter smoke with `EPIC_USE_EXISTING_WRITER_FOR_DRAFTS=true`; current acceptance smoke used deterministic prose fallback while separate live provider probes verified GPT-5.5 and DeepSeek V4 Pro connectivity.

## Final Acceptance Evidence

- `reference_books`: 57
- `reference_pattern_cards`: 98,581
- `reference_mechanism_profiles`: 10
- latest smoke project: `9cb849c3-e6d4-4995-a6b3-bccb594aa0c6`
- chapter 2/3 previous state readiness: true
- Bible sections: 21
- chapter contracts: 10
- accepted chapters: 3
- retrieval traces: 15
- acceptance reports: 3
- non-accepted canon sources: 0
- Bible export chars: 9,833
- novel export chars: 58,580
