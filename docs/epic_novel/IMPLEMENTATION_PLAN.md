# Epic Novel Engine Implementation Plan

Updated: 2026-06-10

## Repo Inspection Findings

- Frontend: React 18 + TypeScript + Vite + React Router + Tailwind CSS under `frontend/src`.
- Backend: FastAPI under `backend/main.py` with router aggregation in `backend/app/api/api.py`.
- DB/ORM: SQLAlchemy models under `backend/app/models`, shared `Base` and sessions in `backend/app/db/session.py`.
- Task system: Celery configured in `backend/celery_app.py`; existing generation uses task and chapter tables plus `backend/output/task_<id>/` artifacts.
- LLM clients: OpenAI writer in `backend/app/services/content_engine.py`; DeepSeek-compatible client in `backend/app/services/deepseek.py`; Gemini in `backend/app/services/gemini.py`.
- Prompt system: existing prompt modules under `backend/app/prompts`; new Epic prompts should live there, not inline in business logic.
- Existing short-form flow: `backend/app/api/routers/tasks.py` dispatches work to `backend/app/tasks/tasks.py`, which calls `content_engine.generate_full_book`; this must be preserved and treated as Chapter Draft Writer.
- Existing long-form prior art: `backend/app/models/long_novel.py`, `backend/app/services/family_long_novel.py`, `backend/app/api/routers/long_novel.py`, and `frontend/src/pages/LongNovelWorkspace.tsx`.
- Export/logging: existing task exports in `tasks.py`; long-novel export in `family_long_novel.py`; standard Python logging plus generation log tables.

## Implementation Strategy

Use the existing SQLAlchemy/FastAPI/React patterns. Add Epic Novel Engine as a separate module family rather than altering short-form or existing family long-novel behavior.

Because the full SPEC is large, implement in runnable priority slices:

1. Foundation
   - Add config envs and local storage path helpers.
   - Add SQLAlchemy models for Epic projects, reference corpus, planning/state, QA, retrieval, exports, and LLM logs.
   - Register models with `app.models`.
   - Add Pydantic schemas.

2. Model Router and Prompts
   - Add a small role-based model router that maps editor/author/cheap roles to configured providers/models.
   - Add named prompt templates in `backend/app/prompts`, keeping prompts out of business logic.
   - Log every Epic model action, including deterministic local fallback calls.

3. Reference Corpus Pipeline
   - Parse seed lists from `.local/novel_ref_corpus` and `docs/epic_novel`.
   - Normalize book records into four tiers and corpus roles.
   - Create `ReferenceDownloadTask` records.
   - Implement `ExternalDownloaderAdapter` and `SoNovelAdapter` with safe detection, manual-required fallback, download scanning, file import, and hash recording.
   - Parse txt/md/html and best-effort epub text without adding dependencies.

4. RAG/Profile/Planner
   - Generate abstract `ReferencePatternCard` records from imported raw text.
   - Build `ReferenceMechanismProfile` from cards without anchoring to one title.
   - Implement `AutoRetrievalPlanner` returning cards/profile summaries only.

5. Epic Generation Backbone
   - Create Epic projects.
   - Generate deterministic WorldBible, WorldAtlas, BureaucracySystem, PowerSystem, Ensemble Cast, Global Spine, Bible Manifest/sections, Volume Contracts, Chapter Contracts.
   - Build chapter context packs.
   - Execute chapters strictly serially with draft/repaired/accepted separation.
   - Persist canon/state only from accepted content.
   - Record RetrievalTrace, Planned-vs-Actual Diff, AcceptanceReport, and LLM/cost logs.

6. API, Frontend, Dashboard, Export
   - Add `/api/v1/epic-novel` routes matching the SPEC route surface.
   - Add a frontend Epic workspace shell and dashboard.
   - Export Bible markdown/json and novel markdown.

7. Verification
   - Run backend import/compile checks and targeted pytest tests.
   - Run frontend typecheck/build if the changed frontend scope requires it.
   - Update `IMPLEMENTATION_STATUS.md` with completed items, failed items, commands, test results, TODOs, and next steps.

## Current Constraints

- Real API keys may be absent, so the first runnable implementation must support deterministic local fallback behavior while preserving model-router/log semantics.
- Raw reference books may be absent; downloader/import flows must handle empty/manual-required states cleanly.
- SoNovel invocation cannot assume a fixed CLI; adapter must detect and fall back to manual workflow when unclear.
