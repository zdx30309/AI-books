# Epic Novel Engine Implementation Status

Updated: 2026-06-12

## Current Status

Status: `PASS FOR 09_ACCEPTANCE_CRITERIA IN CURRENT WORKTREE`

Strict live acceptance passed on project `44ce72be-a423-4f3b-895b-ad8a0fbb5e63` after the hardcoded-service-layer cleanup, Dynamic Context safeguards, reference-intelligence controls, and Bible repair/coverage fixes. The current worktree is not yet pushed/deployed after these changes; GitHub/ECS delivery must still be performed from this worktree.

## Completed In Current Worktree

- Removed fixed Epic smoke-story anchors from production Epic service generation paths. The old fixed names now remain only in SPEC/test-project docs or negative tests.
- Added production/fallback separation:
  - production LLM generations record `_meta.status=generated`, `production_quality=true`, `rawTextIncluded=false`
  - deterministic fallback is labeled `status=fallback`, `production_quality=false`, `smoke_validated=true`
  - Bible section acceptance rejects fallback/placeholder metadata.
- Added structured-generation resilience:
  - author/DeepSeek calls are tried first for author-role structured generation
  - if configured, failed author calls fall back to editor/GPT routing before smoke fallback
  - generation logs record the model/action/status/error path
  - long model calls release DB transactions before waiting on providers.
- Added Reference Intelligence controls:
  - `EPIC_REFERENCE_MODEL_PROVIDER=deepseek`
  - `EPIC_REFERENCE_MODEL_NAME=deepseek-v4-pro`
  - LLM extraction enable/force/cache/retry controls
  - pause/resume/stop extraction control file
  - dry-run cost estimation.
- Added source-identity/reference-section models:
  - `ReferenceSection`
  - `ReferenceSectionAnalysis`
  - extended `ReferencePatternCard` with source section ids, source card ids, evidence count, extraction model, output hash, and `raw_text_included=false`.
- Hardened Dynamic Context / generation safety:
  - raw reference text is excluded from ContextPack and generation prompts
  - ReferencePatternCards and ReferenceMechanismProfile remain allowed prompt sources
  - overflow/excluded context metadata is recorded on retrieval traces
  - large exports return file/object metadata instead of one giant API payload.
- Added Reference extraction API/frontend controls:
  - cost estimate endpoint
  - pause/resume/stop endpoint
  - frontend reference extraction status/cards.
- Added DB startup resilience:
  - advisory lock around startup schema sync
  - short retry on startup OperationalError
  - PostgreSQL `connect_timeout`
  - `pool_recycle` and LIFO pooling.
- Tightened strict acceptance smoke:
  - supports `EPIC_ACCEPTANCE_PROJECT_ID` resume
  - fails if Bible sections are fallback/need_repair
  - fails if coverage is not complete
  - fails if retrieval traces contain raw reference text.
- Added Master Bible acceptance anchors and Chinese-safe payload rendering:
  - planning/control sections now include project-derived anchors for 主角, 关键机构, 世界规则, 关系代价, 伏笔终局, 势力冲突
  - `_meta` and raw English JSON keys are not dumped into Bible markdown.

## Current Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Python compile | PASS | `$env:PYTHONPATH='backend'; python -m compileall backend/app scripts` |
| Targeted tests | PASS | `15 passed` for `backend/tests/test_epic_reference.py backend/tests/test_epic_context.py backend/tests/test_epic_chinese_output.py` |
| Frontend build | PASS | `npm.cmd run build` in `frontend/` |
| DB init | PASS | `init_db_ok` with small pool / short timeout |
| Reference cost dry-run | PASS | `.local/epic_novel_runs/reference_cost_dry_run_2026-06-12.json`: 57 books, 106,914 sections, 185,567,295 chars, 86,582,220 estimated prompt tokens, 1,444 batches, DeepSeek V4 Pro |
| Production hardcoded-name scan | PASS for production services | no fixed smoke-story names in `backend/app/services/epic_novel.py` or `backend/app/services/epic_reference.py`; remaining hits are SPEC/test-project docs and negative tests |
| Strict live acceptance smoke | PASS | `.local/epic_novel_runs/acceptance_smoke_strict_2026-06-12-resume4.log`, project `44ce72be-a423-4f3b-895b-ad8a0fbb5e63`, exit code 0 |

## Strict Acceptance Summary

- Project: `44ce72be-a423-4f3b-895b-ad8a0fbb5e63`
- ReferencePatternCards: `98,581`
- ReferenceMechanismProfiles: `10`
- Bible sections: `24`
- Bible failures: `[]`
- Bible coverage items: `24`
- Coverage failures: `[]`
- Volume contracts: `3`
- Chapter contracts: `10`
- Accepted chapters: `3`
- State extracts: `3`
- Retrieval traces: `19`
- Unsafe retrieval traces: `[]`
- Acceptance reports: `3`
- `nonAcceptedCanonSources`: `0`
- Chapter 2 and 3 previous-state readiness: `true`
- Chapter 2 and 3 context includes Bible, WorldAtlas, bureaucracy, and ReferenceMechanismProfile.
- Bible export chars: `150,603`
- Novel export chars: `15,099`

## Current Limits

- Current worktree has not yet been committed, pushed, or deployed to Beijing ECS after these changes.
- Full 100-book corpus is still limited by source availability: 57 local imported books are available; unavailable/failed titles remain skipped per user instruction.
- Full DeepSeek LLM section analysis over all estimated 106,914 sections has not been run to completion in this worktree; cost dry-run and controls are implemented.
- No first-class Alembic migration framework is present; this repo continues to use startup `create_all()` plus self-heal columns.
- True docx Bible export remains a future enhancement; current acceptance covers markdown/json/file metadata export.
- Do not claim a completed 300万字 production novel run; current proof is the SPEC acceptance smoke, not a full 300万字 manuscript.

## Remaining Before Delivery

- Run final local checks after documentation updates.
- Commit current worktree.
- Push to GitHub.
- Deploy the current commit to Beijing ECS.
- Verify ECS health/API/router/frontend/corpus after deployment.
