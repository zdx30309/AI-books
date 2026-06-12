# Epic Novel Engine Acceptance Run Report

Updated: 2026-06-12

## Overall Result

Status: `PASS FOR CURRENT WORKTREE`

Strict live acceptance passed after the hardcoded-service cleanup, Dynamic Context safeguards, reference-intelligence controls, Chinese Master Bible validation, and structured-generation fallback fixes.

## Final Passing Run

- Command:
  - `$env:PYTHONPATH='backend'; $env:DATABASE_POOL_SIZE='1'; $env:DATABASE_MAX_OVERFLOW='0'; $env:DATABASE_POOL_TIMEOUT='8'; $env:DATABASE_CONNECT_TIMEOUT='8'; $env:DATABASE_POOL_RECYCLE_SECONDS='30'; $env:EPIC_ACCEPTANCE_PROJECT_ID='44ce72be-a423-4f3b-895b-ad8a0fbb5e63'; python -u scripts/epic_acceptance_smoke.py`
- Log:
  - `.local/epic_novel_runs/acceptance_smoke_strict_2026-06-12-resume4.log`
- Exit code:
  - `0`

## Final Summary

| Metric | Value |
| --- | ---: |
| Project | `44ce72be-a423-4f3b-895b-ad8a0fbb5e63` |
| ReferencePatternCards | 98,581 |
| ReferenceMechanismProfiles | 10 |
| BibleSections | 24 |
| BibleFailures | 0 |
| CoverageItems | 24 |
| CoverageFailures | 0 |
| Volumes | 3 |
| ChapterContracts | 10 |
| AcceptedChapters | 3 |
| StateExtracts | 3 |
| CanonFacts | 3 |
| CharacterStates | 3 |
| ObjectStates | 3 |
| FactionStates | 3 |
| InstitutionStates | 3 |
| RetrievalTraces | 19 |
| UnsafeRetrievalTraces | 0 |
| AcceptanceReports | 3 |
| NonAcceptedCanonSources | 0 |
| Bible export chars | 150,603 |
| Novel export chars | 15,099 |

Chapter 2 and 3 context evidence:

- `previousRequiredStateReady=true`
- `bibleSectionCount=24`
- `hasWorldAtlas=true`
- `bureaucracyCount=1`
- `hasReferenceMechanismProfile=true`
- `referenceCardCount=98581`

## Commands Run

| Command | Result |
| --- | --- |
| `git status --short` | completed |
| `rg` fixed smoke-story names in production services | PASS for production services |
| `$env:PYTHONPATH='backend'; python -m compileall backend/app scripts` | PASS |
| `$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_reference.py backend/tests/test_epic_context.py backend/tests/test_epic_chinese_output.py -q` | PASS, `15 passed` |
| `npm.cmd run build` in `frontend/` | PASS |
| `$env:PYTHONPATH='backend'; python -u -c "from app.db.session import init_db; init_db(); print('init_db_ok')"` | PASS after advisory-lock/connect-timeout patch |
| `python -u scripts/epic_ingest_reference_books.py --dry-run-cost --disable-llm-extraction` | PASS, output saved to `.local/epic_novel_runs/reference_cost_dry_run_2026-06-12.json` |
| `python -u scripts/epic_acceptance_smoke.py` and resumed runs | final run PASS |

## Fixes Applied During Acceptance

- Fixed DB-held-during-LLM issue:
  - Model router now releases DB transaction before long external model calls.
  - Generation logs are committed immediately.
- Fixed startup schema race:
  - PostgreSQL advisory lock around startup schema sync.
  - retry on startup `OperationalError`.
- Fixed DB pool stability:
  - `DATABASE_CONNECT_TIMEOUT`
  - `DATABASE_POOL_RECYCLE_SECONDS`
  - LIFO pool reuse.
- Fixed resumability:
  - `scripts/epic_acceptance_smoke.py` supports `EPIC_ACCEPTANCE_PROJECT_ID`.
  - accepted Bible sections are skipped if still valid.
- Fixed Bible production validation:
  - fallback/placeholder Bible metadata blocks acceptance.
  - planning/control sections include project-derived Chinese acceptance anchors.
  - Bible markdown no longer dumps `_meta` or raw English JSON keys.
- Fixed provider resilience:
  - structured generation tries author/DeepSeek first.
  - if enabled, it falls back to editor/GPT route before smoke fallback.
- Fixed state persistence bug:
  - `_persist_accepted_state` now defines protagonist/institution locally before writing ledgers.

## Reference Cost Dry Run

Source: `.local/epic_novel_runs/reference_cost_dry_run_2026-06-12.json`

| Metric | Value |
| --- | ---: |
| Imported local reference books | 57 |
| Estimated sections | 106,914 |
| Estimated chars | 185,567,295 |
| Estimated prompt tokens | 86,582,220 |
| Batch token budget | 60,000 |
| Estimated batches | 1,444 |
| Provider/model | `deepseek/deepseek-v4-pro` |

## Acceptance Checklist Status

| Criterion from `09_ACCEPTANCE_CRITERIA.md` | Current status |
| --- | --- |
| Existing short-form generation still works | PASS by targeted regression/build; full short-form E2E not rerun |
| EpicNovelProject can be created | PASS |
| Reference raw storage can be configured | PASS |
| Reference books can be imported | PASS for available local corpus |
| ReferencePatternCards can be generated | PASS |
| AutoRetrievalPlanner can retrieve pattern cards | PASS |
| WorldBible, WorldAtlas, BureaucracySystem, PowerSystem, Ensemble Cast, Global Spine can be generated | PASS |
| EpicMasterBible can be created | PASS |
| Bible Manifest and Bible sections can be generated | PASS |
| Bible Coverage CI works | PASS |
| Bible export markdown/json works | PASS |
| Volume Contracts and Chapter Contracts can be generated | PASS |
| Chapter Context Pack can be viewed | PASS |
| First 3 chapters can be executed | PASS |
| Chapter 2/3 read previous state, Bible sections, WorldAtlas, bureaucracy, and RAG inspiration pack | PASS |
| draftContent and repairedContent never write to canon | PASS, `nonAcceptedCanonSources=0` |
| acceptedContent writes to state ledgers | PASS |
| planned-vs-actual diff works | PASS |
| acceptance gate works | PASS |
| RetrievalTrace and AcceptanceReport are recorded | PASS |
| chapters generate strictly serially | PASS |
| frontend workspace and dashboard exist | PASS build |

## Delivery Evidence

| Delivery check | Result |
| --- | --- |
| GitHub push | PASS, branch `codex/video-source-classical-regenerate` pushed to `origin` |
| ECS deploy path | PASS, `/opt/video-plan` |
| ECS compose | PASS, `backend`, `worker`, `beat`, and `web` running after rebuild |
| ECS backend health | PASS, backend container `/health` returned ok |
| ECS router import | PASS, router count `119` |
| ECS DB counts | PASS, `books 57`, `cards 98581`, `profiles 10` |
| ECS raw corpus | PASS, 57 files, 296,501,932 bytes |
| Public homepage | PASS, `http://101.200.48.238/` HTTP 200 |
| Public system health | PASS, `http://101.200.48.238/api/v1/system/health` HTTP 200, `celery_alive=true` |

## Remaining Non-Blocking Work

- Continue full DeepSeek section extraction if desired.
- Continue importing additional SoNovel files if unavailable titles become downloadable.
