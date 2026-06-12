# Epic Novel Engine Implementation Status

Updated: 2026-06-12

## Current Status

Status: `PASS FOR 09_ACCEPTANCE_CRITERIA IN CURRENT WORKTREE`

Strict live acceptance passed on project `44ce72be-a423-4f3b-895b-ad8a0fbb5e63` after the hardcoded-service-layer cleanup, Dynamic Context safeguards, reference-intelligence controls, and Bible repair/coverage fixes. The verified worktree has been pushed to GitHub and deployed to the Beijing ECS path `/opt/video-plan`.

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

## Current Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Python compile | PASS | `$env:PYTHONPATH='backend'; python -m compileall backend/app scripts` |
| Targeted tests | PASS | `15 passed` for `backend/tests/test_epic_reference.py backend/tests/test_epic_context.py backend/tests/test_epic_chinese_output.py` |
| Frontend build | PASS | `npm.cmd run build` in `frontend/` |
| EPIC frontend Chinese UI scan | PASS | old visible labels such as `Epic Novel`, `Long-form`, `World Bible`, `Pattern Cards`, `Execute chapter`, `Generation logs` have no hits in `frontend/src/pages/EpicNovelWorkspace.tsx` or `frontend/src/components/Sidebar.tsx`; `npm run build` passed after the UI change |
| EPIC browser UI smoke | PASS | local browser login, created project `ac9f3c28-c725-44cb-bd4b-327bb7b80dc2`, verified project list/detail tabs: 基建流水线, 参考书库, 章节生产, 质检验收, 生成日志; status/action/model values display in Chinese |
| EPIC Chinese UI GitHub/ECS deploy | PASS | current branch pushed to GitHub; Beijing ECS `/opt/video-plan` received the UI bundle, web/backend restarted, public `/` and `/api/v1/system/health` returned HTTP 200, live asset contains `长篇小说生产控制台` and no old `Epic Novel` / `Long-form control room` labels |
| DB init | PASS | `init_db_ok` with small pool / short timeout |
| Reference cost dry-run | PASS | `.local/epic_novel_runs/reference_cost_dry_run_2026-06-12.json`: 57 books, 106,914 sections, 185,567,295 chars, 86,582,220 estimated prompt tokens, 1,444 batches, DeepSeek V4 Pro |
| Production hardcoded-name scan | PASS for production services | no fixed smoke-story names in `backend/app/services/epic_novel.py` or `backend/app/services/epic_reference.py`; remaining hits are SPEC/test-project docs and negative tests |
| Strict live acceptance smoke | PASS | `.local/epic_novel_runs/acceptance_smoke_strict_2026-06-12-resume4.log`, project `44ce72be-a423-4f3b-895b-ad8a0fbb5e63`, exit code 0 |
| GitHub push | PASS | branch `codex/video-source-classical-regenerate` pushed to `origin` |
| Beijing ECS deploy | PASS | `/opt/video-plan`, compose services rebuilt/restarted |
| ECS backend/container health | PASS | backend container `/health` returned ok; router count `119`; DB counts `books 57`, `cards 98581`, `profiles 10` |
| Public ECS health | PASS | `http://101.200.48.238/` HTTP 200; `http://101.200.48.238/api/v1/system/health` HTTP 200 with `celery_alive=true` |
| ECS raw corpus | PASS | `/opt/video-plan/.local/novel_ref_corpus/raw`: 57 files, 296,501,932 bytes |

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

- Full 100-book corpus is still limited by source availability: 57 local imported books are available; unavailable/failed titles remain skipped per user instruction.
- Full DeepSeek LLM section analysis over all estimated 106,914 sections has not been run to completion in this worktree; cost dry-run and controls are implemented.
- No first-class Alembic migration framework is present; this repo continues to use startup `create_all()` plus self-heal columns.
- True docx Bible export remains a future enhancement; current acceptance covers markdown/json/file metadata export.
- Do not claim a completed 300万字 production novel run; current proof is the SPEC acceptance smoke, not a full 300万字 manuscript.

## Remaining

- Continue full DeepSeek LLM section analysis if the user wants every estimated section in the 57-file corpus processed by the expensive extraction pipeline.
- Continue scanning/importing SoNovel downloads if new files become available; unavailable/failed titles remain skipped per user instruction.
