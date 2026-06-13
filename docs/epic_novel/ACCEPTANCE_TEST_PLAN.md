# Epic Novel Engine Acceptance Test Plan

Updated: 2026-06-13

## Rule

Do not mark the Epic Novel Engine complete unless every item in `09_ACCEPTANCE_CRITERIA.md` is PASS for the current worktree. Older smoke/deploy evidence is historical only after code changes.

## Preflight

1. Confirm required docs were read:
   - `AGENTS.md`
   - `docs/epic_novel/00_README.md`
   - `docs/epic_novel/EPIC_NOVEL_ENGINE_FULL_SPEC.md`
   - `docs/epic_novel/09_ACCEPTANCE_CRITERIA.md`
   - `docs/epic_novel/IMPLEMENTATION_STATUS.md`
2. Confirm no production hardcoded story anchors:
   - `rg -n "沈知微|燕清辞|陆寒枝|裴照月|大雍|鸣鹿京|司籍台|夜巡司|灰钟山|盐河|Night Watch|Shen|Yan|Lu|Great Luming|Luming|Case File|Record and Shadow" backend/app/services/epic_novel.py backend/app/services/epic_reference.py`
3. Confirm local checks:
   - `$env:PYTHONPATH='backend'; python -m compileall backend/app scripts`
   - `$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_reference.py backend/tests/test_epic_context.py backend/tests/test_epic_chinese_output.py -q`
   - `npm.cmd run build` from `frontend/`

## Reference Intelligence Checks

1. Run cost dry-run:
   - `$env:PYTHONPATH='backend'; python -u scripts/epic_ingest_reference_books.py --dry-run-cost --disable-llm-extraction`
2. Verify:
   - provider/model is `deepseek/deepseek-v4-pro`
   - raw reference text is not included in PatternCards or ContextPack
   - ReferenceSection and ReferenceSectionAnalysis preserve source identity
   - failed/unavailable novels are skipped per user instruction, not fabricated.

## Dynamic Context Checks

Required tests:

- `max_output_tokens=0` does not pass an artificial app-layer max-token cap.
- ContextPack does not contain raw reference text.
- ContextPack reports included and overflow sections.
- ReferencePatternCards and ReferenceMechanismProfile are allowed in prompts.
- Raw reference book text is never used in generation prompts.
- Long chapter generation can continue if provider output stops early.
- Large Bible/novel export writes files/metadata rather than returning giant JSON payloads.

Command:

`$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_context.py backend/tests/test_epic_reference.py backend/tests/test_epic_chinese_output.py -q`

## QA / Acceptance Gate Checks

Required tests:

- bad chapter fails.
- fallback/patch output fails.
- English boilerplate fails.
- missing `mustExecute` fails.
- raw reference-like text fails.
- broken power rule fails.
- heroine agency missing fails.
- bureaucracy function missing fails.
- foreshadow or reader-question over-closure fails.
- high/severe issues block acceptance.
- AcceptanceReport scores are issue-driven, not fixed constants.
- repaired draft candidates cannot bypass acceptance or write canon.
- only accepted content can write canon/state ledgers.

Commands:

`$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_qa.py -q`

`$env:PYTHONPATH='backend'; python scripts/epic_gate6_qa_acceptance_audit.py --project-id 44ce72be-a423-4f3b-895b-ad8a0fbb5e63`

Current evidence:

- `.local/epic_novel_runs/gate6_qa_acceptance_2026-06-12T202046_749120Z.json`
- bad audit chapter stayed `need_review`
- `EpicIssue` and `EpicAcceptanceReport` rows were recorded
- state/canon ledger counts were unchanged before/after bad and repaired-draft checks

## Canon-State Execution 1-3 Checks

Required tests:

- chapters 1-3 remain accepted after strengthened QA rerun.
- each chapter has `chapter_context_pack`, `qa_planned_vs_actual_diff`, `qa_acceptance_gate`, and `qa_chapter_check` RetrievalTrace.
- each chapter has latest AcceptanceReport status `accepted`.
- chapter 2 context reads chapter 1 summary/endingHook/state.
- chapter 3 context reads chapter 2 summary/endingHook/state.
- every chapter has required state ledgers.
- `nonAcceptedCanonSources=0`.
- Bible and novel exports return file metadata, not huge response content.

Command:

`$env:PYTHONPATH='backend'; python scripts/epic_gate7_canon_state_audit.py --project-id 44ce72be-a423-4f3b-895b-ad8a0fbb5e63`

Current evidence:

- `.local/epic_novel_runs/gate7_canon_state_2026-06-12T205822_873613Z.json`
- novel markdown export: `.local/epic_novel_exports/本长篇项目 正文.md`

## 30-Chapter Continuity Checks

Required tests:

- 30 serial chapters accepted, or deterministic simulation proves equivalent state behavior.
- every chapter has ChapterContract, acceptedContent, RetrievalTrace, AcceptanceReport, and state ledgers.
- every chapter after 1 verifies previous chapter accepted before continuation.
- ReferencePatternCards and ReferenceMechanismProfile are used across the chain.
- context overflow metadata exists.
- raw reference text is not included.
- no high/severe issue remains open.
- `nonAcceptedCanonSources=0`.
- retry does not duplicate canon/state writes.
- continuity report and manuscript export are written as files.

Command:

`$env:PYTHONPATH='backend'; python scripts/epic_gate8_thirty_chapter_simulation.py`

Current evidence:

- `.local/epic_novel_runs/gate8_thirty_chapter_2026-06-12T211746_940280Z.json`
- `.local/epic_novel_runs/30_chapter_continuity_2026-06-12T211746_034084Z.md`
- `.local/epic_novel_exports/第八闸门三十章连续性审计 20260612211418 正文.md`

Boundary:

- Gate 8 evidence is deterministic continuity simulation, not 30 production-quality prose chapters.

## Full Configured-Volume Acceptance Checks

Required tests:

- configured volume contract exists and uses Chinese visible title.
- configured volume chapter range is executed strictly serially.
- every chapter in the configured volume has `acceptedContent`.
- every chapter has `chapter_context_pack`, `qa_planned_vs_actual_diff`, `qa_acceptance_gate`, and `qa_chapter_check` RetrievalTrace.
- every chapter has latest AcceptanceReport status `accepted`.
- every chapter has state ledger rows written from `acceptedContent` only.
- `nonAcceptedCanonSources=0`.
- unresolved foreshadow and reader-question ledgers remain carried forward.
- volume manuscript export is written as a file, not returned as a giant response.

Command:

`$env:PYTHONPATH='backend'; python scripts/epic_gate9_full_volume_acceptance.py --project-id 44ce72be-a423-4f3b-895b-ad8a0fbb5e63 --volume 1`

Current evidence:

- `.local/epic_novel_runs/gate9_full_volume_2026-06-12T214212_737800Z.json`
- `.local/epic_novel_runs/full_volume_acceptance_2026-06-12T214212_736039Z.md`
- `.local/epic_novel_exports/本长篇项目 第1卷 正文.md`

Boundary:

- Gate 9 evidence covers configured live test volume 1, chapter range 1-3. It does not prove every volume or 300万字 readiness.

## Multi-Volume Rolling Plan Checks

Required tests:

- at least 2-3 planned volumes exist.
- each volume has global spine role, local case/mystery, world expansion, bureaucracy/faction use, power progression, protagonist arc, heroine arcs, foreshadow setup/echo/payoff, reader-question flow, and ending explosion.
- rolling context includes old state compression, active unresolved ledger, prior volume summaries, future reveal constraints, and long-range character arcs.
- missing global spine role fails.
- repeated filler volume fails.
- unresolved ledger not carried forward fails.
- future reveal violation fails.
- old state compression preserves active facts.
- multi-volume export/report exists as files.
- raw reference book text is not included.

Commands:

`$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_multi_volume.py -q`

`$env:PYTHONPATH='backend'; python scripts/epic_gate10_multi_volume_rolling_plan.py --project-id 44ce72be-a423-4f3b-895b-ad8a0fbb5e63 --min-volumes 3`

Current evidence:

- `.local/epic_novel_runs/gate10_multi_volume_2026-06-12T220227_446447Z.json`
- `.local/epic_novel_runs/multi_volume_rolling_plan_2026-06-12T220227_445809Z.md`
- `.local/epic_novel_exports/本长篇项目 多卷滚动规划.json`
- `.local/epic_novel_exports/本长篇项目 多卷滚动规划.md`

Boundary:

- Gate 10 evidence is planning and rolling-context evidence. It does not prove full generated prose for every volume or 300万字 readiness.

## Scheduler / Cost / Recovery Checks

Required tests:

- job resumes after failure.
- retry failed only retries failed checkpoint.
- cost logs record provider, model, prompt tokens, completion tokens, estimated cost, actual cost where available, action, project id, status, and errors.
- continuation chunks are recorded.
- retrying accepted chapters does not duplicate canon writes.
- paused job does not continue.
- resumed job continues from checkpoint.
- stopped job does not continue.
- cost cap stops safely before advancing.
- dashboard exposes current job, progress, remaining cost, errors, and retry status.

Commands:

`$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_scheduler.py -q`

`$env:PYTHONPATH='backend'; python scripts/epic_gate11_scheduler_recovery.py --project-id 44ce72be-a423-4f3b-895b-ad8a0fbb5e63`

Current evidence:

- `.local/epic_novel_runs/gate11_scheduler_2026-06-12T221941_023711Z.json`
- `.local/epic_novel_runs/production_scheduler_2026-06-12T221941_022151Z.md`

Boundary:

- Gate 11 evidence is scheduler/cost/recovery control evidence. It does not prove full corpus extraction, full generated prose, or remote deployment.

## Large Export System Checks

Required tests:

- Bible markdown/json exports write files.
- Novel markdown export writes a file.
- Volume markdown, chapter bundle, QA reports, retrieval traces, and acceptance reports export through artifact metadata.
- Default API response returns metadata rather than a giant content body.
- Metadata includes path/object id, bytes, checksum, createdAt, download route, and artifact type.
- Existing download behavior is available only through explicit `mode=download`.
- Export regeneration produces traceable metadata.
- Large content path writes files without timing out.
- Public exports do not include raw reference book text.

Commands:

`$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_context.py -q`

`$env:PYTHONPATH='backend'; python scripts/epic_gate12_export_system.py --project-id 44ce72be-a423-4f3b-895b-ad8a0fbb5e63`

Current evidence:

- `.local/epic_novel_runs/gate12_export_2026-06-12T224306_008134Z.json`
- `.local/epic_novel_runs/export_system_2026-06-12T224306_006655Z.md`
- `.local/epic_novel_exports/本长篇项目 全书圣经.md`
- `.local/epic_novel_exports/本长篇项目 全书圣经.json`
- `.local/epic_novel_exports/本长篇项目 正文.md`
- `.local/epic_novel_exports/本长篇项目 检索轨迹.json`

Boundary:

- Gate 12 evidence proves export safety and metadata separation. It does not prove frontend runtime, remote deployment, all-volume generated prose, or 300万字 readiness.

## Frontend Workspace Checks

Required tests:

- `/epic-novel` project list loads for an authenticated user.
- Project create form, list rows, loading state, empty state, and error state are Chinese.
- Project detail loads from real API data.
- Required 10 tabs are visible and clickable:
  基建总览、参考语料、圣经世界、规划合同、章节生产、上下文检索、质检验收、生产仪表盘、文件导出、调用日志。
- Old Epic English labels such as `Long-form control room`, `Create project`, and `Loading projects` are not visible in the checked runtime page.
- Reference/SoNovel/download/profile/Bible/planning/execution/context/QA/dashboard/export/log panels use real API clients and do not show fake success.
- `文件导出 -> 正文元数据` handles empty metadata without crashing.
- Export metadata UI displays Chinese fallback text when checksum is absent.
- Browser console has no new `EpicNovelWorkspace` or `Cannot read properties` errors.

Commands:

`npm.cmd run build`

`$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_reference.py backend/tests/test_epic_context.py backend/tests/test_epic_chinese_output.py backend/tests/test_epic_qa.py backend/tests/test_epic_multi_volume.py backend/tests/test_epic_scheduler.py -q`

Current evidence:

- `docs/epic_novel/GATE_13_FRONTEND_REPORT.md`
- `docs/epic_novel/FRONTEND_WORKSPACE_REPORT.md`
- Local browser project `ac9f3c28-c725-44cb-bd4b-327bb7b80dc2`
- Build PASS
- `53 passed, 1 warning`

Boundary:

- Gate 13 evidence proves local frontend runtime only. Remote ECS frontend must be redeployed and rechecked in Gate 14.

## Strict Live Acceptance Flow

Run:

`$env:PYTHONPATH='backend'; python -u scripts/epic_acceptance_smoke.py`

Resume an interrupted run:

`$env:EPIC_ACCEPTANCE_PROJECT_ID='<project-id>'; $env:PYTHONPATH='backend'; python -u scripts/epic_acceptance_smoke.py`

Required live steps:

1. Create or reuse test authenticated user where API auth is used.
2. Create or reuse `EpicNovelProject`.
3. Parse and normalize the 100-book list.
4. Detect SoNovel or mark `manual_required`.
5. Import local/downloaded epub/txt if present.
6. Generate abstract `ReferencePatternCards`.
7. Build `ReferenceMechanismProfile`.
8. Generate WorldBible, WorldAtlas, BureaucracySystem, PowerSystem, EnsembleCast, GlobalSpine.
9. Generate MegaBible manifest and required Bible sections.
10. Run Bible Coverage CI.
11. Generate 3 VolumeContracts.
12. Compile 10 ChapterContracts.
13. Execute chapters 1-3 strictly serially.
14. Verify chapter 2/3 read previous state, Bible, WorldAtlas, bureaucracy, and ReferenceMechanismProfile.
15. Verify planned-vs-actual diff, acceptance gate, RetrievalTrace, AcceptanceReport.
16. Verify draft/repaired content never writes canon and accepted content writes state ledgers.
17. Export Bible markdown/json and novel markdown.

## Pass Gates

- Bible sections must be `accepted`; `fallback`, `placeholder`, or `need_repair` is FAIL.
- Bible coverage items must be complete.
- RetrievalTrace `raw_text_included` must be false.
- ContextPack must include overflow/excluded metadata when relevant.
- Chapter 2 and 3 must have previous accepted state readiness.
- `nonAcceptedCanonSources` must be 0.
- No high/severe issue may be accepted.
- Chapters must run serially.
- Export routes must write files/metadata, not return massive payloads.

## Current Passing Run

Project `44ce72be-a423-4f3b-895b-ad8a0fbb5e63` is the current strict-smoke passing project. The passing log is `.local/epic_novel_runs/acceptance_smoke_strict_2026-06-12-resume4.log`.

Keep this plan unchanged for future regressions: if code changes again, rerun strict smoke and do not reuse this result as current evidence.
