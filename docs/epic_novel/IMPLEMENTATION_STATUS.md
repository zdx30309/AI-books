# Epic Novel Engine Implementation Status

Updated: 2026-08-10

## Current Status

Status: `PARTIAL_IMPLEMENTATION`

Post-gate hardening update on 2026-06-18: consistency/API/frontend hardening is `HARDENED_PARTIAL`.

This pass added generic Knowledge / Secret / Reader Question / Growth / Power / Authority / Relationship ledgers, strengthened ChapterContract and ContextPack consistency constraints, and expanded the Acceptance Gate to block knowledge leaks, early secret exposure, early reader-question answers, professional terms without in-world source, unauthorized growth, locked ability use, unauthorized authority, relationship jumps, and forbidden major-character introductions.

Only `acceptedContent` persists the new ledgers. `draftContent` and `repairedContent` remain non-canon candidates.

API/frontend hardening in this pass added project deletion, project status, project artifact records, project ledgers, reference book list/delete, reference task retry, reference readiness, structured business errors, frontend project deletion, reference book management, task retry, readiness display, and artifact record display.

Validation run on 2026-06-18:

- `python -m compileall backend/app`
- `python -m pytest backend/tests/test_epic_qa.py backend/tests/test_epic_context.py backend/tests/test_epic_reference.py backend/tests/test_epic_scheduler.py -q` -> 46 passed
- `npm.cmd run build` in `frontend` -> passed

Publication/deployment on 2026-06-18:

- Post-gate hardening HEAD was pushed to `origin/codex/bilibili-cost-guard-main` and `origin/main`.
- Beijing ECS `/opt/video-plan` was updated through tarball deployment while preserving `.env` and `.local`.
- `.deploy-version` was verified against deployed HEAD.
- `docker compose ps` showed `backend`, `worker`, `beat`, and `web` Up.
- Public `http://101.200.48.238/`, `http://101.200.48.238/epic-novel`, and `http://101.200.48.238/api/v1/system/health` returned 200 after startup.
- Remote `init_db()` confirmed the new hardening tables, including `epic_character_knowledge_ledgers` and `epic_artifact_records`.

New post-gate hardening docs:

- `docs/epic_novel/POST_GATE_HARDENING_AUDIT.md`
- `docs/epic_novel/KNOWLEDGE_SECRET_GROWTH_CONSISTENCY_REPORT.md`
- `docs/epic_novel/PROTAGONIST_GROWTH_TRAJECTORY_REPORT.md`
- `docs/epic_novel/CHARACTER_ARC_LEDGER_REPORT.md`
- `docs/epic_novel/READER_QUESTION_LEDGER_REPORT.md`
- `docs/epic_novel/API_CONTRACT_REPORT.md`
- `docs/epic_novel/FRONTEND_WORKSPACE_FUNCTIONALITY_REPORT.md`
- `docs/epic_novel/ARTIFACT_DOWNLOAD_REPORT.md`

This does not change the overall production-readiness verdict: the system is still not proven 300万字-ready because full 100-book production extraction, full-corpus `ReferenceMechanismProfile`, and real long-run manuscript generation remain incomplete.

Gate 2A update on 2026-06-12: bounded section-aware Reference Intelligence proof is `PASS`.

The Gate 2A pass is deliberately narrow: 1 imported book / 5 sections were processed with real DeepSeek V4 Pro section analysis. This proves the corrected pipeline shape, not full 57-book extraction, not full 100-book corpus completion, and not 300万字 production readiness.

Gate 2B update on 2026-06-13: staged Reference Intelligence expansion is `PARTIAL`.

The latest Gate 2B staged evidence covers 3 source books / 27 successful DeepSeek V4 Pro section analyses / 48 production-ready cards. Full 57-book extraction is still incomplete, so full Reference Intelligence remains PARTIAL.

Gate 3 update on 2026-06-12: Reference Usage Depth is `PASS`.

Gate 3 proves production-ready ReferencePatternCards and ReferenceMechanismProfile are consumed by the required generation layers, QA and repair entrypoints, with `EpicRetrievalTrace` evidence. It does not change the broader production-readiness verdict.

Gate 4 update on 2026-06-12: Real Chinese Master Bible is `PASS`.

Gate 4 proves 24/24 Master Bible sections pass stricter Chinese rendering and acceptance, with fallback/smoke/English-key markers rejected and markdown/json exported as files.

Gate 5 update on 2026-06-13: Dynamic Context Compiler is `PASS`.

Gate 5 proves provider-window prompt budgeting, explicit included/excluded/overflow metadata, selected card/profile metadata, raw-reference exclusion, no unbounded `full_context_json` prompt dump, live ContextPack `RetrievalTrace` persistence, continuation handling, and file-backed large export metadata.

Gate 6 update on 2026-06-13: QA / Acceptance Gate is `PASS`.

Gate 6 proves targeted bad-output rejection, high/severe blocking, issue-driven scores, `EpicIssue` / `EpicAcceptanceReport` persistence, local issue-targeted repair scope, and no canon/state write from bad draft or repaired draft candidates.

Gate 7 update on 2026-06-13: Canon-State Execution 1-3 is `PASS`.

Gate 7 proves chapters 1-3 still pass after the strengthened QA gate, every chapter has the required QA/context RetrievalTrace types and AcceptanceReport, chapter 2/3 consume previous state, required canon/state ledgers are present, `nonAcceptedCanonSources=0`, and Bible/novel exports are file-backed with Chinese visible filenames.

Gate 8 update on 2026-06-13: 30-Chapter Continuity is `PASS` by deterministic simulation.

Gate 8 proves equivalent 30-chapter state behavior with 30 accepted simulated chapters, required contracts/context traces/QA traces/reports/state ledgers, reference profile/cards, overflow metadata, `nonAcceptedCanonSources=0`, and no duplicate canon writes after retry. This is not a production prose run.

Gate 9 update on 2026-06-13: Full Volume Acceptance is `PASS` for the configured live test volume.

Gate 9 proves live project volume 1, configured chapter range 1-3, passes VolumeContract, chapter execution evidence, strengthened QA, state ledgers, reference traces, `nonAcceptedCanonSources=0`, and volume manuscript export. This does not prove all volumes or a 10-chapter production volume.

Gate 10 update on 2026-06-13: Multi-Volume Rolling Plan is `PASS`.

Gate 10 proves 3 planned volumes have GlobalSpine linkage, local case/mystery, world expansion, bureaucracy/faction use, power progression, protagonist/heroine arcs, foreshadow setup/echo/payoff, reader-question flow, compressed old state, active unresolved ledger, prior volume summaries, future reveal constraints, reference usage trace, and file-backed rolling-plan export. This does not prove all-volume generated prose.

Gate 11 update on 2026-06-13: Scheduler / Cost / Recovery is `PASS`.

Gate 11 proves EpicProductionJob checkpointing, pause/resume/stop, retry failed only, continuation chunk recording, cost cap, cost logs, dashboard exposure, reference extraction control, and no duplicate canon writes after retry. This does not prove full corpus extraction or full manuscript generation.

Gate 12 update on 2026-06-13: Large Export System is `PASS`.

Gate 12 proves Bible/novel/volume/chapter/report/trace exports default to file-backed metadata with checksum/object id/download route, explicit `mode=download` separation, large-file path support, and raw-reference exclusion. This does not prove frontend runtime, remote deployment, full generated manuscript, or 300万字 readiness.

Gate 13 update on 2026-06-13: Frontend Workspace is `PASS` for local browser runtime.

Gate 13 proves the local Epic workspace is Chinese, loads real API panels, exposes dashboard/export controls, handles empty/error/loading states, and no longer crashes on missing export metadata fields. It does not prove Beijing ECS has the latest build, full corpus extraction, full generated manuscript, or 300万字 readiness.

Gate 14 update on 2026-06-13: Final SPEC Readiness is `PARTIAL_IMPLEMENTATION` with current GitHub/ECS deployment complete.

Current pushed HEAD was pushed to `origin/codex/bilibili-cost-guard-main` and `origin/main`, then deployed to Beijing ECS `/opt/video-plan`; `.deploy-version` was verified against deployed HEAD. Public `/`, `/epic-novel`, and `/api/v1/system/health` returned 200, ECS retained 57 epub raw reference files, and live DB evidence shows `ReferenceSection=27`, success analyses=27, production-ready cards=48. This proves current publication/deployment, not 300万字 readiness.

Gate 0 adversarial recheck on 2026-06-12 found that the previous “PASS FOR 09_ACCEPTANCE_CRITERIA” statement overstated production readiness. The historical strict smoke run on project `44ce72be-a423-4f3b-895b-ad8a0fbb5e63` proves a DB-backed smoke/control-plane flow, not full SPEC production acceptance.

The current implementation has meaningful Epic modules in place, but it is not `PRODUCTION_READY` and must not be described as 300万字-ready. Blocking evidence:

- Live DB has 100 booklist items but only 57 indexed imported books.
- Gate 2B staged run now has 27 stable `ReferenceSection` rows and 27 successful `ReferenceSectionAnalysis` rows.
- Gate 0 baseline had 98,581 old `ReferencePatternCard` rows with empty `source_section_ids`; Gate 2B staged run now has 48 production-ready cards with non-empty `source_section_ids`, `source_analysis_ids`, and `source_book_ids`. Old cards remain excluded from production-ready counts.
- Gate 0 baseline profile lacked source metadata; latest Gate 2B profile `846e11e2-385d-4c94-b122-793f84112f5e` now has `generationStatus`, `buildMode`, `profileVersion`, provider/model trace fields, source book/card/section samples, `rawTextIncluded=false`, categories, evidence counts, and `oldSourceLessCardsExcluded=true`.
- The live project is a 30万字 / 3卷 / 10章 smoke project with only 3 accepted chapters and 15,018 current words.
- Acceptance gate now blocks the current high/severe test cases and persists issue-driven reports, but long-run false-positive/false-negative behavior is not proven beyond the current Gate 6-10 evidence.
- GitHub/ECS deployment state was not reverified in this Gate 0 audit and is not counted as current evidence.

New Gate 0 docs:

- `docs/epic_novel/ADVERSARIAL_AUDIT_REPORT.md`
- `docs/epic_novel/FALLBACK_AND_FIXTURE_INVENTORY.md`
- `docs/epic_novel/REFERENCE_INTELLIGENCE_EVIDENCE.md`
- `docs/epic_novel/PRODUCTION_READINESS_RECHECK.md`

New Gate 2A doc:

- `docs/epic_novel/GATE_2A_REFERENCE_INTELLIGENCE_RUN_REPORT.md`

New Gate 2B doc:

- `docs/epic_novel/GATE_2B_REFERENCE_FULL_RUN_REPORT.md`

New Gate 3 doc:

- `docs/epic_novel/GATE_3_REFERENCE_USAGE_DEPTH_REPORT.md`

New Gate 4 doc:

- `docs/epic_novel/GATE_4_MASTER_BIBLE_REPORT.md`

New Gate 5 doc:

- `docs/epic_novel/GATE_5_DYNAMIC_CONTEXT_REPORT.md`

New Gate 6 doc:

- `docs/epic_novel/GATE_6_QA_ACCEPTANCE_REPORT.md`

New Gate 7 doc:

- `docs/epic_novel/GATE_7_CANON_STATE_REPORT.md`

New Gate 8 docs:

- `docs/epic_novel/GATE_8_THIRTY_CHAPTER_REPORT.md`
- `docs/epic_novel/30_CHAPTER_CONTINUITY_REPORT.md`

New Gate 9 docs:

- `docs/epic_novel/GATE_9_FULL_VOLUME_REPORT.md`
- `docs/epic_novel/FULL_VOLUME_ACCEPTANCE_REPORT.md`

New Gate 10 docs:

- `docs/epic_novel/GATE_10_MULTI_VOLUME_REPORT.md`
- `docs/epic_novel/MULTI_VOLUME_ROLLING_PLAN_REPORT.md`

New Gate 11 docs:

- `docs/epic_novel/GATE_11_SCHEDULER_REPORT.md`
- `docs/epic_novel/PRODUCTION_SCHEDULER_REPORT.md`

New Gate 12 docs:

- `docs/epic_novel/GATE_12_EXPORT_REPORT.md`
- `docs/epic_novel/EXPORT_SYSTEM_REPORT.md`

New Gate 13 docs:

- `docs/epic_novel/GATE_13_FRONTEND_REPORT.md`
- `docs/epic_novel/FRONTEND_WORKSPACE_REPORT.md`
- `docs/epic_novel/gates/GATE_13_FRONTEND_WORKSPACE.md`

New Gate 14 doc:

- `docs/epic_novel/GATE_14_FINAL_SPEC_READINESS_REPORT.md`

The old smoke evidence below is retained as historical evidence only.

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
- 中文化 EPIC 前端工作区:
  - `/epic-novel` 入口、项目列表、创建表单、基建流水线、参考书库、章节执行、质检验收、生成日志均改为中文可见文案
  - 后端返回的状态枚举和生成动作枚举通过前端映射显示为中文，不再直接暴露 `accepted`、`manual_required`、`execute_chapter` 等英文 key
  - 模型配置摘要和日志模型列使用中文展示名，不在工作区原样展示 provider/model id
  - 前端错误提示改为中文兜底，避免英文接口错误直接显示给用户.
  - 详情页扩展为 10 个中文标签：基建总览、参考语料、圣经世界、规划合同、章节生产、上下文检索、质检验收、生产仪表盘、文件导出、调用日志.
  - 文件导出页面接入 Gate 12 metadata/download 分离，空 metadata 不再触发 `sha256.slice` 崩溃.

## Gate 2A Completed In Current Worktree

- Added stable `ReferenceSection` identity by book hash + section hash + section index.
- Added `ReferenceSection.token_estimate`, `ReferenceSectionAnalysis.analysis_hash`, `ReferencePatternCard.source_analysis_ids`, and `ReferencePatternCard.source_book_ids`, with startup schema self-heal.
- Added DeepSeek V4 Pro section-analysis workflow with Chinese JSON normalization for the Gate 2A fields.
- Fallback section analyses are stored as `status=fallback`, `production_quality=false`, and are not counted as production evidence.
- New production-ready `ReferencePatternCard` rows are generated only from successful non-fallback `ReferenceSectionAnalysis` rows.
- Production-ready card checks require non-empty `source_section_ids`, `source_analysis_ids`, source book evidence, `rawTextIncluded=false`, and `production_quality=true`.
- Added dedupe/merge helpers that preserve source book, section, analysis, and card IDs without merging raw text.
- Rebuilt proof `ReferenceMechanismProfile` from production-ready cards only, with `generationStatus`, `profileVersion`, provider/model, source samples, categories, evidence counts, and `rawTextIncluded=false`.
- Added bounded proof runner: `scripts/epic_gate2a_reference_proof.py`.
- Added targeted tests for stable section IDs, fallback exclusion, production-card source IDs, old-card exclusion, merge evidence preservation, profile metadata, and raw-reference prompt safety.

## Gate 2A Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Final Gate 2A proof | PASS | `$env:PYTHONPATH='backend'; python scripts/epic_gate2a_reference_proof.py --book-limit 1 --section-limit 5 --force` |
| Proof report | PASS | `.local/epic_novel_runs/gate2a_reference_intelligence_2026-06-12T132502_524973_0000.json` |
| DeepSeek section analysis | PASS | 5 successful DeepSeek V4 Pro section analyses, 0 fallback, 0 error |
| Production-ready cards | PASS | 18 cards after dedupe; old 98,581 empty-source cards excluded from production-ready counts |
| Profile metadata | PASS | profile `b1025f7b-d405-4077-8632-d6adf94e3732`, `rawTextIncluded=false`, `sourceBookCount=1`, `sourceCardCount=18`, 5 source sections sampled |
| Targeted tests | PASS | `$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_reference.py backend/tests/test_epic_context.py -q` -> 19 passed |

Gate 2A remains bounded proof only. Full Reference Intelligence is still `PARTIAL` until more imported books/sections are processed and the broader acceptance flow is rerun.

## Gate 2B Completed In Current Worktree

- Added staged reference expansion runner:
  - `scripts/epic_gate2b_reference_staged_run.py`
  - `run_gate2b_reference_staged_expansion(...)`
- Runner supports book/section limits, per-book section limits, tier/category filters, resume, retry-failed-only, force, dry-run, cost cap, stop-on-cost-limit, runtime cap, and the existing pause/resume/stop control file.
- Added estimated reference model cost calculation for prompt/completion tokens.
- Added Gate 2B profile metadata: `buildMode`, `productionReadyCardCount`, `oldSourceLessCardsExcluded`.
- Ran staged expansion:
  - selected 3 imported books
  - skipped 5 already-successful Gate 2A sections by resume
  - newly processed 10 sections from 2 more books
  - cumulative successful analyses: 15
  - cumulative production-ready cards: 36
  - fallback/error: 0
- 2026-06-13 continuation:
  - optimized production-ready card counting and pattern-key lookup so Gate 2B no longer scans the old 98,581 source-less card table on each small batch
  - fixed Gate 2B resume budget so skipped already-successful sections do not consume `section_limit`
  - latest staged report: `.local/epic_novel_runs/gate2b_reference_staged_2026-06-13T153012_468370_0000.json`
  - cumulative successful analyses: 27
  - cumulative production-ready cards: 48
  - fallback/error: 0
- Ran dry-run controls:
  - full estimate dry-run for 3 books / 15 candidate sections
  - retry-failed-only dry-run
  - cost-limit dry-run with `stopReason=cost_limit`

## Gate 2B Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Gate 2B staged run | PARTIAL | latest `.local/epic_novel_runs/gate2b_reference_staged_2026-06-13T153012_468370_0000.json`; prior expansion `.local/epic_novel_runs/gate2b_reference_staged_2026-06-12T142226_539531_0000.json` |
| Dry-run estimate | PASS | `.local/epic_novel_runs/gate2b_reference_staged_2026-06-12T142024_203143_0000.json` |
| Retry-failed-only control | PASS | `.local/epic_novel_runs/gate2b_reference_staged_2026-06-12T143247_517259_0000.json` |
| Cost cap control | PASS | `.local/epic_novel_runs/gate2b_reference_staged_2026-06-12T143259_386938_0000.json`, `stopReason=cost_limit` |
| Runtime DB evidence | PASS | `ReferenceSection=27`, `ReferenceSectionAnalysis=27`, success=27, fallback=0, error=0, production-ready cards=48 |
| Targeted tests | PASS | `$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_reference.py backend/tests/test_epic_context.py backend/tests/test_epic_chinese_output.py backend/tests/test_epic_qa.py backend/tests/test_epic_multi_volume.py backend/tests/test_epic_scheduler.py -q` -> 53 passed |

Gate 2B is not full PASS because 57 imported books / estimated 106,914 sections have not all been processed.

## Gate 3 Completed In Current Worktree

- `auto_retrieval_plan()` now retrieves production-ready `ReferencePatternCard` rows only.
- Old source-less cards are excluded from generation prompts and recorded as `oldSourceLessCardsExcluded`.
- Added unified `_reference_usage_pack()` for Epic generation layers.
- Connected reference usage traces to:
  - Genesis worldview tournament
  - WorldBible
  - WorldAtlas
  - BureaucracySystem
  - PowerSystem
  - EnsembleCast
  - GlobalSpine
  - Master Bible manifest and sections
  - VolumeContracts
  - ChapterContracts
  - Chapter ContextPack
  - Bible Coverage CI
  - Outline CI
  - Chapter check
  - Planned-vs-Actual Diff
  - Acceptance Gate
  - Outline repair
  - Chapter repair entry
- Fixed ContextPack trace overflow recording from nonexistent `overflowBlocks` to `metadata.overflow`.
- Added `scripts/epic_gate3_reference_usage_depth.py`.

## Gate 3 Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Gate 3 smoke | PASS | `.local/epic_novel_runs/gate3_reference_usage_depth_2026-06-12T152527_937305Z.json` |
| Required task types | PASS | 19/19 required task types present |
| Trace quality | PASS | every required trace has non-empty `usedCardIds`, `usedProfileSections`, `profileVersion`, `usageSummary` |
| Production-card filter | PASS | Gate 3 smoke used 36 production-ready cards at run time; latest Gate 2B card pool is 48; 98,581 old source-less cards excluded |
| Raw text safety | PASS | `rawTextIncluded=false` for required traces |
| QA trace live evidence | PASS | chapter 4 `qa_planned_vs_actual_diff` and `qa_acceptance_gate` each used 36 cards |
| Canon boundary | PASS | chapter 4 accepted; state ledgers persisted; canon source is `acceptedContent` only |
| Targeted tests | PASS | `$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_reference.py backend/tests/test_epic_context.py backend/tests/test_epic_chinese_output.py -q` -> 28 passed |

## Gate 4 Completed In Current Worktree

- Expanded Bible payload label mapping so common camelCase/snake_case keys render as Chinese.
- Added recursive Bible text cleanup for smoke project labels and English premise fragments.
- Hardened `validate_bible_section_content()` against visible English key labels, smoke fixtures, excessive generic `结构字段`, missing section payload, and missing reference usage.
- Added `scripts/epic_gate4_master_bible_audit.py`.
- Re-rendered all 24 accepted Bible sections into stricter Chinese Markdown.
- Exported Bible markdown/json files with Chinese filenames.

## Gate 4 Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Gate 4 audit | PASS | `.local/epic_novel_runs/gate4_master_bible_2026-06-12T154950_704943Z.json` |
| Bible status | PASS | `completed` |
| Accepted sections | PASS | 24 / 24 |
| Coverage failures | PASS | 0 |
| Markdown export | PASS | `.local/epic_novel_exports/本长篇项目 全书圣经.md` |
| JSON export | PASS | `.local/epic_novel_exports/本长篇项目 全书圣经.json` |
| Forbidden marker scan | PASS | no `acceptance smoke`, raw English key labels, `结构字段`, `Long-form`, or `Epic Novel Engine` hits |
| Targeted tests | PASS | `$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_chinese_output.py backend/tests/test_epic_reference.py backend/tests/test_epic_context.py -q` -> 28 passed |

## Gate 5 Completed In Current Worktree

- Added `raw_reference_field_paths()` and `excludedRawReferenceFields` metadata so raw-reference exclusions are auditable.
- Dynamic Context metadata now records `includedPaths`, `overflowPaths`, `excluded`, `selectedCardIds`, `profileVersion`, `selectedBibleSectionKeys`, and `noSilentTruncation`.
- Chapter writer prompt now carries only selected compiled context plus compact metadata; it does not dump `full_context_json`.
- Added `scripts/epic_gate5_dynamic_context_audit.py` for repeatable synthetic and live DB checks.
- Expanded `backend/tests/test_epic_context.py` to cover `0` token behavior, overflow metadata, card/profile selection, prompt raw-text safety, continuation, and file-backed exports.

## Gate 5 Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Gate 5 audit | PASS | `.local/epic_novel_runs/gate5_dynamic_context_2026-06-12T200323_839412Z.json` |
| Synthetic overflow | PASS | small budget produced overflow and recorded included/excluded/overflow metadata |
| Live ContextPack trace | PASS | project `44ce72be-a423-4f3b-895b-ad8a0fbb5e63`, chapter 1, trace `fa6ed1e5-c1bf-4a15-b2a7-b257d28c3f1d` |
| Raw reference safety | PASS | compiled context and chapter writer prompt exclude raw book/card text values |
| No unbounded prompt dump | PASS | chapter writer prompt has no `full_context_json` |
| Targeted tests | PASS | `$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_context.py -q` -> 8 passed |
| Compile check | PASS | `$env:PYTHONPATH='backend'; python -m compileall backend/app/services/epic_context.py backend/app/services/epic_novel.py scripts/epic_gate5_dynamic_context_audit.py` |

## Gate 6 Completed In Current Worktree

- Added issue-driven QA checks for plan mismatch, missing mustExecute, fallback/patch output, English boilerplate, raw reference leakage, bureaucracy function, broken power rule, heroine agency, character/relationship drift, foreshadow and reader-question drift, premature reveal, world/Bible contradiction, and undersized content.
- High/severe issues block acceptance.
- AcceptanceReport scores now derive from issue severity/type instead of fixed constants.
- `check_chapter()` re-runs QA against the current candidate text and persists `EpicIssue` + `EpicAcceptanceReport`.
- Chapter issues now carry `repair_scope.mode=local_issue_targeted` and `rewrite=forbidden`.
- Added `backend/tests/test_epic_qa.py`.
- Added `scripts/epic_gate6_qa_acceptance_audit.py`.

## Gate 6 Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Gate 6 audit | PASS | `.local/epic_novel_runs/gate6_qa_acceptance_2026-06-12T202046_749120Z.json` |
| Bad chapter blocking | PASS | audit chapter `911628` stayed `need_review`, `acceptedContentPresent=false` |
| Blocking issue types | PASS | planned-vs-actual, fallback/patch, raw reference leakage, English boilerplate, bureaucracy missing, broken power rule, heroine agency missing, foreshadow failures |
| Repair scope | PASS | all open bad-chapter issues use local issue-targeted repair scope; full rewrite forbidden |
| Canon boundary | PASS | before/after state counts unchanged for state extracts, canon facts, character/power/object/faction/institution ledgers, and pleasure beats |
| Repaired draft boundary | PASS | audit chapter `911629` did not write accepted content or canon state |
| Targeted tests | PASS | `$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_qa.py -q` -> 10 passed |
| Combined tests | PASS | `$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_qa.py backend/tests/test_epic_context.py -q` -> 18 passed |
| Compile check | PASS | `$env:PYTHONPATH='backend'; python -m compileall backend/app/services/epic_novel.py scripts/epic_gate6_qa_acceptance_audit.py` |

## Gate 7 Completed In Current Worktree

- Upgraded `check_chapter()` so strengthened QA reruns also write `qa_planned_vs_actual_diff` and `qa_acceptance_gate` RetrievalTrace rows.
- `check_chapter()` now writes an AcceptanceReport on every strengthened QA rerun, including no-blocker reruns.
- Novel markdown/txt/json export filenames now use the Chinese display title and `正文` suffix instead of old English smoke project titles.
- Added `scripts/epic_gate7_canon_state_audit.py`.
- Re-ran chapters 1-3 through strengthened QA/context audit on the live project.

## Gate 7 Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Gate 7 audit | PASS | `.local/epic_novel_runs/gate7_canon_state_2026-06-12T205822_873613Z.json` |
| Chapters 1-3 | PASS | all three are `accepted`; latest report status `accepted`; no open high/severe issue |
| RetrievalTrace coverage | PASS | every chapter has `chapter_context_pack`, `qa_planned_vs_actual_diff`, `qa_acceptance_gate`, `qa_chapter_check` |
| ContextPack metadata | PASS | every chapter has metadata and `contextRawTextIncluded=false` |
| Previous state consumption | PASS | chapter 2 recent chapters include 1; chapter 3 recent chapters include 2 and 1; `previousRequiredStateReady=true` |
| Required ledgers | PASS | every chapter has StateExtract, CanonFact, CharacterState, PowerLedger, ObjectState, FactionState, InstitutionState, PleasureBeat |
| Canon boundary | PASS | `nonAcceptedCanonSources=0` |
| Exports | PASS | `.local/epic_novel_exports/本长篇项目 全书圣经.md`; `.local/epic_novel_exports/本长篇项目 正文.md`; metadata returns `contentReturned=false` |

## Gate 8 Completed In Current Worktree

- Added `scripts/epic_gate8_thirty_chapter_simulation.py`.
- Created a dedicated Chinese-titled continuity simulation project.
- Seeded minimal world/state foundation and 30 serial chapter contracts.
- Simulated 30 accepted chapters with required ContextPack/QA RetrievalTrace rows, AcceptanceReport rows, accepted content, and state ledgers.
- Verified previous-chapter readiness for every chapter after 1.
- Verified reference profile/card usage across all chapter traces.
- Verified overflow metadata in 4 chapters.
- Verified no raw reference text and `nonAcceptedCanonSources=0`.
- Retried chapter 30 and confirmed no duplicate canon/state writes.
- Exported continuity report and 30-chapter manuscript metadata.

## Gate 8 Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Gate 8 simulation | PASS | `.local/epic_novel_runs/gate8_thirty_chapter_2026-06-12T211746_940280Z.json` |
| 30 chapter report | PASS | `.local/epic_novel_runs/30_chapter_continuity_2026-06-12T211746_034084Z.md` |
| Simulation project | PASS | `c32bece9-5913-42d5-8a35-7bedfca497f4` |
| Accepted chapters | PASS | 30 / 30 |
| Required traces/reports/ledgers | PASS | every chapter has contract, context/QA traces, AcceptanceReport, acceptedContent, and state ledgers |
| Reference usage | PASS | every chapter trace uses profile `f376caf3-1a3b-4694-b78b-90c9bbafc96b` and production-ready cards |
| Retry idempotency | PASS | chapter 30 retry created no duplicate canon/state writes |
| Manuscript export | PASS | `.local/epic_novel_exports/第八闸门三十章连续性审计 20260612211418 正文.md` |

## Gate 9 Completed In Current Worktree

- Added `scripts/epic_gate9_full_volume_acceptance.py`.
- Verified configured live test volume 1, chapter range 1-3.
- Re-ran strengthened chapter QA during volume verification.
- Verified required trace types, AcceptanceReport rows, state ledgers, reference usage, foreshadow/reader-question ledgers, and canon source boundaries.
- Exported the volume manuscript as a file-backed artifact.
- Cleaned old smoke-derived volume title before reporting.
- Improved deterministic chapter draft text to avoid meta placeholders, dict-rendered institutions, English smoke titles, and raw-reference warning phrases in story content.
- Accepted chapter retries now mark old open chapter issues as `fixed`.

## Gate 9 Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Gate 9 audit | PASS | `.local/epic_novel_runs/gate9_full_volume_2026-06-12T214212_737800Z.json` |
| Full volume report | PASS | `.local/epic_novel_runs/full_volume_acceptance_2026-06-12T214212_736039Z.md` |
| Configured volume | PASS | volume 1, chapter range 1-3, title `第1卷 阶段主线` |
| Accepted chapters | PASS | 3 / 3 |
| Required traces/reports/ledgers | PASS | every chapter has required RetrievalTrace types, latest accepted report, and state ledgers |
| Canon boundary | PASS | `nonAcceptedCanonSources=0` |
| Volume export | PASS | `.local/epic_novel_exports/本长篇项目 第1卷 正文.md` |

## Gate 10 Completed In Current Worktree

- Added `validate_multi_volume_rolling_plan()` with explicit checks for missing GlobalSpine role, repeated filler volumes, missing unresolved-ledger carry-forward, premature future reveal, and old-state compression loss.
- Added `build_multi_volume_rolling_plan()` to assemble GlobalSpine-linked volume plans, active unresolved ledgers, prior volume summaries, future reveal constraints, long-range character arcs, compressed state, and reference usage metadata.
- Added `scripts/epic_gate10_multi_volume_rolling_plan.py`.
- Added `backend/tests/test_epic_multi_volume.py`.
- Wrote file-backed rolling-plan exports.

## Gate 10 Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Gate 10 audit | PASS | `.local/epic_novel_runs/gate10_multi_volume_2026-06-12T220227_446447Z.json` |
| Rolling plan report | PASS | `.local/epic_novel_runs/multi_volume_rolling_plan_2026-06-12T220227_445809Z.md` |
| Planned volumes | PASS | 3 |
| Validation | PASS | status `pass`, issueCount `0`, blockingIssueCount `0` |
| RetrievalTrace | PASS | `8fd0f399-9ce8-4566-8f1c-3246a5577df6`, raw reference excluded |
| Rolling context | PASS | old state compression, active unresolved ledger, prior summaries, future reveal constraints, long-range arcs |
| Plan export | PASS | `.local/epic_novel_exports/本长篇项目 多卷滚动规划.json` / `.md` |
| Targeted tests | PASS | `backend/tests/test_epic_multi_volume.py` -> 6 passed |

## Gate 11 Completed In Current Worktree

- Added `EpicProductionJob` for Epic scheduler state.
- Added `EpicGenerationLog.actual_cost`.
- Added scheduler helpers for create/progress/pause/resume/stop/retry-failed-only/cost cap/continuation chunks.
- Extended dashboard response with current job, progress, estimated remaining cost, errors, and retry status.
- Added `scripts/epic_gate11_scheduler_recovery.py`.
- Added `backend/tests/test_epic_scheduler.py`.

## Gate 11 Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Gate 11 audit | PASS | `.local/epic_novel_runs/gate11_scheduler_2026-06-12T221941_023711Z.json` |
| Scheduler report | PASS | `.local/epic_novel_runs/production_scheduler_2026-06-12T221941_022151Z.md` |
| Main job | PASS | `1e0d97ce-1866-4530-a343-effea22b406e`, completed 6/6 |
| Pause/resume/stop | PASS | paused and stopped jobs did not advance; resumed job continued |
| Retry failed only | PASS | failed `chapter_generation` retried only that step |
| Cost evidence | PASS | provider/model/tokens/estimated cost/actual cost/status/error logs recorded |
| Cost cap | PASS | cost-cap job stopped before advancing |
| Continuation chunks | PASS | 1 chunk recorded with `contentReturned=false` |
| Canon retry safety | PASS | chapter 1 canon facts stayed `1 -> 1` |
| Dashboard exposure | PASS | current job/progress/remaining cost/errors/retry fields present |
| Targeted tests | PASS | `backend/tests/test_epic_scheduler.py` -> 5 passed |

## Gate 12 Completed In Current Worktree

- Changed Bible/novel export API defaults to metadata mode.
- Added artifact export metadata for volume markdown, chapter bundle, QA reports, retrieval traces, and acceptance reports.
- Added file checksum/object id/download route metadata.
- Summarized retrieval trace export so raw `raw_json` and full large ID lists are not emitted.
- Added `scripts/epic_gate12_export_system.py`.
- Extended export metadata tests in `backend/tests/test_epic_context.py`.

## Gate 12 Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Gate 12 audit | PASS | `.local/epic_novel_runs/gate12_export_2026-06-12T224306_008134Z.json` |
| Export report | PASS | `.local/epic_novel_runs/export_system_2026-06-12T224306_006655Z.md` |
| Export count | PASS | 8 file-backed artifacts |
| Metadata default | PASS | `contentReturned=false` |
| Checksum/object id | PASS | `sha256` and `objectId` present |
| Download route separation | PASS | explicit `mode=download` routes present |
| Large-file path | PASS | `Gate12 大文件路径探针 20260612224305.md`, 3,480,027 bytes |
| Retrieval trace size | PASS | `本长篇项目 检索轨迹.json`, 4,684,462 bytes after summary export |
| Raw reference exclusion | PASS | `rawReferenceTextExcluded=true` |
| Targeted tests | PASS | `backend/tests/test_epic_context.py` -> 9 passed |

## Gate 13 Completed In Current Worktree

- Expanded the Epic workspace into 10 Chinese module tabs.
- Added real API panels for reference corpus, SoNovel/download tasks, pattern cards, mechanism profile, Bible/world/planning/execution/context/QA/dashboard/export/log data.
- Added export metadata controls for Bible, novel and artifact exports while keeping downloads on explicit download routes.
- Hardened export metadata rendering so missing `sha256`/filename/bytes fields do not crash the page.
- Normalized project detail state so older or partial backend responses do not block the workspace from rendering.
- Kept auxiliary detail calls non-blocking so a slow dashboard/cost/profile endpoint does not prevent the project detail page from opening.

## Gate 13 Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Frontend build | PASS | `npm.cmd run build` |
| Targeted Epic tests | PASS | `backend/tests/test_epic_reference.py backend/tests/test_epic_context.py backend/tests/test_epic_chinese_output.py backend/tests/test_epic_qa.py backend/tests/test_epic_multi_volume.py backend/tests/test_epic_scheduler.py` -> 53 passed |
| Local project list | PASS | `/epic-novel` shows Chinese title, create form, project rows, loading/error/empty states |
| Local project detail | PASS | project `ac9f3c28-c725-44cb-bd4b-327bb7b80dc2` opened in browser |
| 10 tab browser smoke | PASS | 基建总览、参考语料、圣经世界、规划合同、章节生产、上下文检索、质检验收、生产仪表盘、文件导出、调用日志 |
| Old English Epic labels | PASS | `Long-form control room`, `Create project`, and `Loading projects` were absent in the checked runtime page |
| Export metadata UI | PASS | `文件导出 -> 正文元数据` no longer crashes; missing hash shows `未返回校验和` |
| Browser runtime errors | PASS | no new `EpicNovelWorkspace` or `Cannot read properties` errors after the smoke run |

## Current Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Python compile | PASS | `$env:PYTHONPATH='backend'; python -m compileall backend/app scripts` |
| Targeted tests | PASS | `53 passed` for `backend/tests/test_epic_reference.py backend/tests/test_epic_context.py backend/tests/test_epic_chinese_output.py backend/tests/test_epic_qa.py backend/tests/test_epic_multi_volume.py backend/tests/test_epic_scheduler.py` |
| Gate 3 reference usage depth | PASS | `.local/epic_novel_runs/gate3_reference_usage_depth_2026-06-12T152527_937305Z.json`; 69 new traces; 19/19 task types passed |
| Gate 4 real Chinese Master Bible | PASS | `.local/epic_novel_runs/gate4_master_bible_2026-06-12T154950_704943Z.json`; 24/24 accepted; Bible status `completed` |
| Gate 5 dynamic context compiler | PASS | `.local/epic_novel_runs/gate5_dynamic_context_2026-06-12T200323_839412Z.json`; synthetic and live checks passed |
| Gate 6 QA acceptance gate | PASS | `.local/epic_novel_runs/gate6_qa_acceptance_2026-06-12T202046_749120Z.json`; high/severe blocking and canon boundary passed |
| Gate 7 canon-state execution 1-3 | PASS | `.local/epic_novel_runs/gate7_canon_state_2026-06-12T205822_873613Z.json`; chapters 1-3 PASS after strengthened QA |
| Gate 8 30-chapter continuity | PASS simulation | `.local/epic_novel_runs/gate8_thirty_chapter_2026-06-12T211746_940280Z.json`; 30/30 simulated chapters accepted |
| Gate 9 full volume acceptance | PASS configured volume | `.local/epic_novel_runs/gate9_full_volume_2026-06-12T214212_737800Z.json`; configured volume 1, chapters 1-3 PASS |
| Gate 10 multi-volume rolling plan | PASS | `.local/epic_novel_runs/gate10_multi_volume_2026-06-12T220227_446447Z.json`; 3 volumes, 0 issues, rolling context/export PASS |
| Gate 11 scheduler/cost/recovery | PASS | `.local/epic_novel_runs/gate11_scheduler_2026-06-12T221941_023711Z.json`; pause/resume/stop/retry/cost cap/cost logs/canon retry safety PASS |
| Gate 12 large export system | PASS | `.local/epic_novel_runs/gate12_export_2026-06-12T224306_008134Z.json`; 8 metadata exports, checksum/download route, large path, raw reference excluded PASS |
| Gate 13 frontend workspace | PASS | local browser smoke: 10/10 Chinese tabs PASS, export metadata click PASS, no new runtime errors |
| Frontend build | PASS | `npm.cmd run build` in `frontend/` |
| EPIC frontend Chinese UI scan | PASS | old visible labels such as `Epic Novel`, `Long-form`, `World Bible`, `Pattern Cards`, `Execute chapter`, `Generation logs` have no hits in `frontend/src/pages/EpicNovelWorkspace.tsx` or `frontend/src/components/Sidebar.tsx`; `npm run build` passed after the UI change |
| EPIC browser UI smoke | PASS | local browser project `ac9f3c28-c725-44cb-bd4b-327bb7b80dc2`; verified 10 tabs: 基建总览, 参考语料, 圣经世界, 规划合同, 章节生产, 上下文检索, 质检验收, 生产仪表盘, 文件导出, 调用日志 |
| EPIC Chinese UI GitHub/ECS deploy | PASS current | current pushed HEAD deployed to ECS; web bundle contains Gate 13 Chinese labels including `文件导出`, `参考语料`, `生产仪表盘`, `长篇小说工业引擎` |
| DB init | PASS | `init_db_ok` with small pool / short timeout |
| Reference cost dry-run | PASS | `.local/epic_novel_runs/reference_cost_dry_run_2026-06-12.json`: 57 books, 106,914 sections, 185,567,295 chars, 86,582,220 estimated prompt tokens, 1,444 batches, DeepSeek V4 Pro |
| Gate 2B staged expansion | PARTIAL | 3 source books / 27 successful section analyses / 48 production-ready cards; full 57-book extraction incomplete |
| Production hardcoded-name scan | PASS for production services | no fixed smoke-story names in `backend/app/services/epic_novel.py` or `backend/app/services/epic_reference.py`; remaining hits are SPEC/test-project docs and negative tests |
| Strict live acceptance smoke | PASS | `.local/epic_novel_runs/acceptance_smoke_strict_2026-06-12-resume4.log`, project `44ce72be-a423-4f3b-895b-ad8a0fbb5e63`, exit code 0 |
| GitHub push | PASS current | current HEAD pushed to `origin/codex/bilibili-cost-guard-main` and `origin/main` |
| Beijing ECS deploy | PASS current | `/opt/video-plan/.deploy-version` verified against deployed HEAD; `docker compose up -d --build` completed |
| ECS backend/container health | PASS current | `backend`, `worker`, `beat`, `web` Up; backend `/health` returned 200; router count 124 |
| Public ECS health | PASS current | public `/` 200, `/epic-novel` 200, `/api/v1/system/health` 200 with `celery_alive=true` |
| ECS raw corpus | PASS current | `/opt/video-plan/.local/novel_ref_corpus/raw`: 57 epub files, 296,501,932 bytes |
| ECS DB counts | PASS current | booklist 100, books 57, sections 27, analyses 27, success=27, cards 98,629, production-ready cards 48, profiles 21, projects 13, bible sections 192, chapters 90 |

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

- Gate 2B staged expansion covers 3 source books / 27 successful section analyses only; do not treat it as full Reference Intelligence PASS for all imported books or the 100-book corpus.
- Gate 3 is PASS for reference usage wiring only; it does not prove full Master Bible quality, 30-chapter continuity, full-volume acceptance, or 300万字 readiness.
- Gate 4 is PASS for Master Bible content only; it does not prove 30-chapter continuity, full-volume acceptance, or 300万字 readiness.
- Gate 5 is PASS for dynamic context and prompt safety only; it does not prove 30-chapter continuity, full-volume acceptance, or 300万字 readiness.
- Gate 6 is PASS for targeted QA blocking only; it does not prove 30-chapter continuity, full-volume acceptance, or 300万字 readiness.
- Gate 7 is PASS for chapters 1-3 only; it does not prove 30-chapter continuity, full-volume acceptance, or 300万字 readiness.
- Gate 8 is PASS by deterministic continuity simulation only; it does not prove 30 production-quality prose chapters, full-volume acceptance, or 300万字 readiness.
- Gate 9 is PASS for one configured live test volume only; it does not prove all generated volumes, frontend readiness, current deployment, or 300万字 readiness.
- Gate 10 is PASS for multi-volume planning and rolling context only; it does not prove all-volume generated prose, frontend readiness, current deployment, or 300万字 readiness.
- Gate 11 is PASS for scheduler/cost/recovery controls only; it does not prove frontend readiness, current deployment, or 300万字 readiness.
- Gate 12 is PASS for file-backed export safety only; Gate 13 later proves local frontend runtime, but Gate 12 alone does not prove current deployment, full generated manuscript, or 300万字 readiness.
- Gate 13 is PASS for local frontend runtime only; it does not prove current ECS deployment, full generated manuscript, or 300万字 readiness.
- Gate 14 is PASS for current GitHub/ECS publication, but the final SPEC verdict remains `PARTIAL_IMPLEMENTATION` because full corpus extraction and real 300万字 quality evidence are missing.
- Full 100-book corpus is still limited by source availability: 57 local imported books are available; unavailable/failed titles remain skipped per user instruction.
- Full DeepSeek LLM section analysis over all estimated 106,914 sections has not been run to completion in this worktree; Gate 2B currently proves 3 source books / 27 successful section analyses, and cost dry-run plus controls are implemented.
- No first-class Alembic migration framework is present; this repo continues to use startup `create_all()` plus self-heal columns.
- True docx Bible export remains a future enhancement; current acceptance covers markdown/json/file metadata export.
- Do not claim a completed 300万字 production novel run; current proof is the SPEC acceptance smoke, not a full 300万字 manuscript.

## Remaining

- Continue Gate 2B staged extraction in small resumable batches; fallback analyses must remain excluded from production evidence.
- Continue full DeepSeek LLM section analysis if the user wants every estimated section in the 57-file corpus processed by the expensive extraction pipeline.
- Continue scanning/importing SoNovel downloads if new files become available; unavailable/failed titles remain skipped per user instruction.
- Continue full Reference Intelligence extraction and real long-run generation only when cost/time is approved; do not claim `PRODUCTION_READY` or 300万字-ready from the current evidence.

## SoNovel Adapter Hardening (2026-08-10)

- Added the `ExternalDownloaderAdapter` contract for trusted local reference-download providers; `SoNovelAdapter` now implements it.
- Installation detection no longer requires both `sonovel.exe` and `app.jar`: a local runtime marker, `config.ini`, or configured downloads directory is enough to expose a safe `manual_required` workflow. WebUI invocation remains opt-in and is enabled only after the local WebUI responds.
- Download scanning now includes nested folders under `SONOVEL_DOWNLOAD_PATH`, so date- or source-organized exports are discoverable by the existing importer.
- Output validation now exposes inferred title/author and optional duplicate-book detection when a database session is supplied; unsupported, empty, unnamed, or duplicate files are not marked valid.

Validation on 2026-08-10:

- `PYTHONPATH=backend python -m pytest backend/tests/test_epic_reference.py -q` -> `19 passed`
- `PYTHONPATH=backend python -m compileall -q backend/app/services/epic_reference.py` -> passed

This hardening does not run SoNovel, import new reference books, or invoke DeepSeek. The overall production-readiness verdict remains `PARTIAL_IMPLEMENTATION`; full-corpus extraction still requires explicit cost/time approval.
