# Epic Novel Engine Acceptance Run Report

Updated: 2026-06-13

## Overall Result

Status: `HISTORICAL SMOKE PASS / CURRENT GATE PLAN PARTIAL`

Strict live acceptance smoke previously passed after the hardcoded-service cleanup, Dynamic Context safeguards, reference-intelligence controls, Chinese Master Bible validation, and structured-generation fallback fixes.

Current gate-plan status is `PARTIAL_IMPLEMENTATION`: Gate 2B staged Reference Intelligence expansion is PARTIAL, Gate 3 Reference Usage Depth is PASS, Gate 4 Real Chinese Master Bible is PASS, Gate 5 Dynamic Context Compiler is PASS, Gate 6 QA / Acceptance Gate is PASS, Gate 7 Canon-State Execution 1-3 is PASS, Gate 8 Thirty-Chapter Continuity is PASS by deterministic simulation, Gate 9 Full Volume Acceptance is PASS for the configured live test volume, Gate 10 Multi-Volume Rolling Plan is PASS, Gate 11 Scheduler / Cost / Recovery is PASS, Gate 12 Large Export System is PASS, Gate 13 Frontend Workspace is PASS for local browser runtime, and Gate 14 remains pending.

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
| `$env:PYTHONPATH='backend'; python scripts/epic_gate3_reference_usage_depth.py --chapter 4 --no-execute` | PASS, output saved to `.local/epic_novel_runs/gate3_reference_usage_depth_2026-06-12T152527_937305Z.json` |
| `$env:PYTHONPATH='backend'; python scripts/epic_gate4_master_bible_audit.py` | PASS, output saved to `.local/epic_novel_runs/gate4_master_bible_2026-06-12T154950_704943Z.json` |
| `$env:PYTHONPATH='backend'; python scripts/epic_gate5_dynamic_context_audit.py --chapter 1` | PASS, output saved to `.local/epic_novel_runs/gate5_dynamic_context_2026-06-12T200323_839412Z.json` |
| `$env:PYTHONPATH='backend'; python scripts/epic_gate6_qa_acceptance_audit.py --project-id 44ce72be-a423-4f3b-895b-ad8a0fbb5e63` | PASS, output saved to `.local/epic_novel_runs/gate6_qa_acceptance_2026-06-12T202046_749120Z.json` |
| `$env:PYTHONPATH='backend'; python scripts/epic_gate7_canon_state_audit.py --project-id 44ce72be-a423-4f3b-895b-ad8a0fbb5e63` | PASS, output saved to `.local/epic_novel_runs/gate7_canon_state_2026-06-12T205822_873613Z.json` |
| `$env:PYTHONPATH='backend'; python scripts/epic_gate8_thirty_chapter_simulation.py` | PASS, output saved to `.local/epic_novel_runs/gate8_thirty_chapter_2026-06-12T211746_940280Z.json` |
| `$env:PYTHONPATH='backend'; python scripts/epic_gate9_full_volume_acceptance.py --project-id 44ce72be-a423-4f3b-895b-ad8a0fbb5e63 --volume 1` | PASS, output saved to `.local/epic_novel_runs/gate9_full_volume_2026-06-12T214212_737800Z.json` |
| `$env:PYTHONPATH='backend'; python scripts/epic_gate10_multi_volume_rolling_plan.py --project-id 44ce72be-a423-4f3b-895b-ad8a0fbb5e63 --min-volumes 3` | PASS, output saved to `.local/epic_novel_runs/gate10_multi_volume_2026-06-12T220227_446447Z.json` |
| `$env:PYTHONPATH='backend'; python scripts/epic_gate11_scheduler_recovery.py --project-id 44ce72be-a423-4f3b-895b-ad8a0fbb5e63` | PASS, output saved to `.local/epic_novel_runs/gate11_scheduler_2026-06-12T221941_023711Z.json` |
| `$env:PYTHONPATH='backend'; python scripts/epic_gate12_export_system.py --project-id 44ce72be-a423-4f3b-895b-ad8a0fbb5e63` | PASS, output saved to `.local/epic_novel_runs/gate12_export_2026-06-12T224306_008134Z.json` |
| `npm.cmd run build` in `frontend/` after Gate 13 fix | PASS |
| `$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_reference.py backend/tests/test_epic_context.py backend/tests/test_epic_chinese_output.py backend/tests/test_epic_qa.py backend/tests/test_epic_multi_volume.py backend/tests/test_epic_scheduler.py -q` | PASS, `53 passed, 1 warning` |
| Local browser smoke for Gate 13 | PASS, 10/10 Epic tabs passed, export metadata click passed, no new runtime errors |

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

## Gate 2B Reference Intelligence Update

| Metric | Value |
| --- | ---: |
| Gate 2B verdict | PARTIAL |
| Staged report | `.local/epic_novel_runs/gate2b_reference_staged_2026-06-12T142226_539531_0000.json` |
| Production-ready source books | 3 |
| ReferenceSection rows | 15 |
| ReferenceSectionAnalysis rows | 15 |
| Successful DeepSeek analyses | 15 |
| Fallback/error analyses | 0 / 0 |
| Production-ready cards | 36 |
| Old source-less cards excluded | 98,581 |

This does not replace the need for Gate 12-14 evidence and does not prove 300万字 readiness.

## Gate 3 Reference Usage Depth Update

| Metric | Value |
| --- | ---: |
| Gate 3 verdict | PASS |
| Smoke report | `.local/epic_novel_runs/gate3_reference_usage_depth_2026-06-12T152527_937305Z.json` |
| Live project | `44ce72be-a423-4f3b-895b-ad8a0fbb5e63` |
| New RetrievalTrace rows | 69 |
| Required task types | 19 / 19 PASS |
| Missing task types | 0 |
| Failing task types | 0 |
| Production-ready cards used | 36 |
| Old source-less cards excluded | 98,581 |
| Raw reference text included | false |

Chapter 4 Gate 3 evidence:

- `qa_planned_vs_actual_diff` trace `23ca7c09-6ffe-436a-a65a-baf623d64387`, created `2026-06-12 15:07:01.532603+00:00`, used 36 cards.
- `qa_acceptance_gate` trace `4eae2d62-2703-4cd6-b0c0-85584ead22a2`, created `2026-06-12 15:07:04.447036+00:00`, used 36 cards.
- Chapter 4 is `accepted`; canon source for chapter 4 is `acceptedContent` only.

This replaces the Gate 3 pending status but does not prove Gate 12-14 or 300万字 readiness.

## Gate 4 Real Chinese Master Bible Update

| Metric | Value |
| --- | ---: |
| Gate 4 verdict | PASS |
| Audit report | `.local/epic_novel_runs/gate4_master_bible_2026-06-12T154950_704943Z.json` |
| Live project | `44ce72be-a423-4f3b-895b-ad8a0fbb5e63` |
| Bible status | `completed` |
| Accepted Bible sections | 24 / 24 |
| Failed sections | 0 |
| Coverage failures | 0 |
| Markdown export | `.local/epic_novel_exports/本长篇项目 全书圣经.md` |
| JSON export | `.local/epic_novel_exports/本长篇项目 全书圣经.json` |
| Forbidden marker scan | PASS |

This replaces the Gate 4 pending status but does not prove Gate 12-14 or 300万字 readiness.

## Gate 5 Dynamic Context Compiler Update

| Metric | Value |
| --- | ---: |
| Gate 5 verdict | PASS |
| Audit report | `.local/epic_novel_runs/gate5_dynamic_context_2026-06-12T200323_839412Z.json` |
| Live project | `44ce72be-a423-4f3b-895b-ad8a0fbb5e63` |
| Chapter checked | 1 |
| Synthetic overflow count | 1 |
| Live metadata included count | 20 |
| Live metadata raw text included | false |
| Live RetrievalTrace | `fa6ed1e5-c1bf-4a15-b2a7-b257d28c3f1d` |
| Chapter prompt `full_context_json` | absent |
| Synthetic/raw card text in prompt | absent |
| Targeted context tests | 8 passed |

This replaces the Gate 5 pending status but does not prove Gate 12-14 or 300万字 readiness.

## Gate 6 QA / Acceptance Gate Update

| Metric | Value |
| --- | ---: |
| Gate 6 verdict | PASS |
| Audit report | `.local/epic_novel_runs/gate6_qa_acceptance_2026-06-12T202046_749120Z.json` |
| Live project | `44ce72be-a423-4f3b-895b-ad8a0fbb5e63` |
| Bad audit chapter | `911628` |
| Repaired-draft audit chapter | `911629` |
| Bad chapter blocked | true |
| EpicIssue rows recorded | 13 for bad chapter |
| Latest bad AcceptanceReport status | `blocked` |
| Repair scope | `local_issue_targeted`, full rewrite forbidden |
| Canon/state counts unchanged | true |
| Targeted QA tests | 11 passed |

This replaces the Gate 6 pending status but does not prove Gate 12-14 or 300万字 readiness.

## Gate 7 Canon-State Execution 1-3 Update

| Metric | Value |
| --- | ---: |
| Gate 7 verdict | PASS |
| Audit report | `.local/epic_novel_runs/gate7_canon_state_2026-06-12T205822_873613Z.json` |
| Live project | `44ce72be-a423-4f3b-895b-ad8a0fbb5e63` |
| Chapters checked | 1 / 2 / 3 |
| Latest chapter reports | all `accepted` |
| Missing required trace types | 0 |
| Context raw text included | false for all three |
| Chapter 2 previous state ready | true |
| Chapter 3 previous state ready | true |
| nonAcceptedCanonSources | 0 |
| Novel markdown export | `.local/epic_novel_exports/本长篇项目 正文.md` |

This replaces the Gate 7 pending status but does not prove Gate 12-14 or 300万字 readiness.

## Gate 8 Thirty-Chapter Continuity Update

| Metric | Value |
| --- | ---: |
| Gate 8 verdict | PASS by deterministic simulation |
| Audit report | `.local/epic_novel_runs/gate8_thirty_chapter_2026-06-12T211746_940280Z.json` |
| 30 chapter report | `.local/epic_novel_runs/30_chapter_continuity_2026-06-12T211746_034084Z.md` |
| Simulation project | `c32bece9-5913-42d5-8a35-7bedfca497f4` |
| Accepted chapters | 30 / 30 |
| Open high/severe issues | 0 |
| nonAcceptedCanonSources | 0 |
| Retry duplicate canon writes | false; retry no-op confirmed |
| Overflow metadata chapters | 4 |
| Manuscript export | `.local/epic_novel_exports/第八闸门三十章连续性审计 20260612211418 正文.md` |

This replaces the Gate 8 pending status by simulation evidence, but it does not prove Gate 12-14, 30 production-quality prose chapters, all-volume generated prose, or 300万字 readiness.

## Gate 9 Full Volume Acceptance Update

| Metric | Value |
| --- | ---: |
| Gate 9 verdict | PASS for configured live test volume |
| Audit report | `.local/epic_novel_runs/gate9_full_volume_2026-06-12T214212_737800Z.json` |
| Volume report | `.local/epic_novel_runs/full_volume_acceptance_2026-06-12T214212_736039Z.md` |
| Live project | `44ce72be-a423-4f3b-895b-ad8a0fbb5e63` |
| Volume | 1 |
| Chapter range | 1-3 |
| Accepted chapters | 3 / 3 |
| nonAcceptedCanonSources | 0 |
| Volume manuscript export | `.local/epic_novel_exports/本长篇项目 第1卷 正文.md` |

This replaces the Gate 9 pending status for the configured live test volume, but it does not prove Gate 12-14, all-volume generated prose, or 300万字 readiness.

## Gate 10 Multi-Volume Rolling Plan Update

| Metric | Value |
| --- | ---: |
| Gate 10 verdict | PASS |
| Audit report | `.local/epic_novel_runs/gate10_multi_volume_2026-06-12T220227_446447Z.json` |
| Rolling plan report | `.local/epic_novel_runs/multi_volume_rolling_plan_2026-06-12T220227_445809Z.md` |
| Live project | `44ce72be-a423-4f3b-895b-ad8a0fbb5e63` |
| Planned volumes | 3 |
| Validation status | `pass` |
| Issue count | 0 |
| Blocking issues | 0 |
| RetrievalTrace | `8fd0f399-9ce8-4566-8f1c-3246a5577df6` |
| Raw reference excluded | true |
| Old state compression preserved | true |
| Active unresolved ledger carried | true |
| Future reveal constraints present | true |
| Plan export | `.local/epic_novel_exports/本长篇项目 多卷滚动规划.json` / `.md` |

This replaces the Gate 10 pending status for planning/rolling context, but it does not prove Gate 14, all-volume generated prose, current remote deployment, or 300万字 readiness.

## Gate 11 Scheduler / Cost / Recovery Update

| Metric | Value |
| --- | ---: |
| Gate 11 verdict | PASS |
| Audit report | `.local/epic_novel_runs/gate11_scheduler_2026-06-12T221941_023711Z.json` |
| Scheduler report | `.local/epic_novel_runs/production_scheduler_2026-06-12T221941_022151Z.md` |
| Live project | `44ce72be-a423-4f3b-895b-ad8a0fbb5e63` |
| Main job | `1e0d97ce-1866-4530-a343-effea22b406e` |
| Main job status | `completed` |
| Progress | 6 / 6 |
| Retry failed only | true |
| Continuation chunks | 1 |
| Estimated / actual cost | 0.077 / 0.041 |
| Cost cap stops safely | true |
| Stopped job does not continue | true |
| Canon facts after retry | 1 -> 1 |
| Dashboard exposes current job | true |

This replaces the Gate 11 pending status for scheduler/cost/recovery controls, but it does not prove Gate 14, full generated prose, current remote deployment, or 300万字 readiness.

## Gate 12 Large Export System Update

| Metric | Value |
| --- | ---: |
| Gate 12 verdict | PASS |
| Audit report | `.local/epic_novel_runs/gate12_export_2026-06-12T224306_008134Z.json` |
| Export report | `.local/epic_novel_runs/export_system_2026-06-12T224306_006655Z.md` |
| Live project | `44ce72be-a423-4f3b-895b-ad8a0fbb5e63` |
| Export count | 8 |
| Metadata default no content | true |
| Checksum / object id | true |
| Download endpoint separated | true |
| Large probe bytes | 3,480,027 |
| Retrieval trace export bytes | 4,684,462 |
| Raw reference text excluded | true |
| Targeted tests | `backend/tests/test_epic_context.py` -> 9 passed |

This replaces the Gate 12 pending status for file-backed export safety. Gate 13 later proved local frontend runtime, but current remote deployment, full generated prose, and 300万字 readiness remain unproven.

## Gate 13 Frontend Workspace Update

| Metric | Value |
| --- | ---: |
| Gate 13 verdict | PASS for local browser runtime |
| Reports | `docs/epic_novel/GATE_13_FRONTEND_REPORT.md`, `docs/epic_novel/FRONTEND_WORKSPACE_REPORT.md` |
| Browser smoke project | `ac9f3c28-c725-44cb-bd4b-327bb7b80dc2` |
| Chinese tabs checked | 10 / 10 |
| Old English Epic labels checked | absent |
| Export metadata click | PASS |
| Missing checksum fallback | `未返回校验和` |
| New runtime errors | 0 |
| Frontend build | PASS |
| Targeted Epic tests | `53 passed, 1 warning` |

This replaces the Gate 13 pending status for local frontend runtime, but it does not prove current ECS deployment, full generated prose, full corpus extraction, or 300万字 readiness.

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
| frontend workspace and dashboard exist | PASS build and local browser smoke |
| multi-volume rolling plan exists and preserves long-range context | PASS |
| scheduler/cost/recovery controls exist | PASS |
| large export metadata and download-route separation work | PASS |
| Epic frontend Chinese workspace, dashboard, export metadata UI | PASS local browser smoke |

## Historical Delivery Evidence

| Delivery check | Result |
| --- | --- |
| GitHub push | HISTORICAL PASS, branch `codex/video-source-classical-regenerate` pushed to `origin` before the current Gate 5-13 local changes |
| ECS deploy path | HISTORICAL PASS, `/opt/video-plan`; must be redeployed before claiming current remote PASS |
| ECS compose | HISTORICAL PASS, `backend`, `worker`, `beat`, and `web` were running after rebuild |
| ECS backend health | HISTORICAL PASS, backend container `/health` returned ok |
| ECS router import | HISTORICAL PASS, router count `119` |
| ECS DB counts | HISTORICAL PASS, `books 57`, `cards 98581`, `profiles 10` |
| ECS raw corpus | HISTORICAL PASS, 57 files, 296,501,932 bytes |
| Public homepage | HISTORICAL PASS, `http://101.200.48.238/` HTTP 200 |
| Public system health | HISTORICAL PASS, `http://101.200.48.238/api/v1/system/health` HTTP 200, `celery_alive=true` |

## Remaining Non-Blocking Work

- Continue full DeepSeek section extraction if desired.
- Continue importing additional SoNovel files if unavailable titles become downloadable.
