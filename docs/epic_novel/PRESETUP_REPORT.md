# Epic Novel Engine Pre-Setup Report

Inspection date: 2026-06-10

Scope: brief repository orientation only. No engine implementation, business logic change, dependency install, migration, or refactor was performed.

## Frontend Framework

- React 18 with TypeScript, Vite, React Router, Tailwind CSS, and lucide-react.
- Main frontend files are under `frontend/src`.
- Route entrypoint is `frontend/src/App.tsx`.
- Existing long-novel UI entry is `frontend/src/pages/LongNovelWorkspace.tsx` with API bindings in `frontend/src/api/client.ts`.

## Backend Framework

- FastAPI backend.
- App entrypoint is `backend/main.py`.
- Router aggregation is in `backend/app/api/api.py`.
- API routers live under `backend/app/api/routers`.

## Database/ORM

- SQLAlchemy ORM with PostgreSQL/Supabase-style `DATABASE_URL`.
- Session/Base setup is in `backend/app/db/session.py`.
- Settings are in `backend/app/core/config.py`.
- ORM models live under `backend/app/models`.
- Existing long-novel models include project, bible, chapter plan, chapter, event, canon fact, character state, object state, continuity issue, and generation log models in `backend/app/models/long_novel.py`.

## Existing LLM Client Location

- OpenAI writer client is used inside `backend/app/services/content_engine.py`.
- DeepSeek-compatible client is in `backend/app/services/deepseek.py`.
- Gemini client is in `backend/app/services/gemini.py`.
- Image generation and editing logic is concentrated in `backend/app/services/cover.py`.
- Provider/model settings are read from `backend/app/core/config.py`.

## Existing Prompt/Template Directory

- Prompt modules live in `backend/app/prompts`.
- Notable files include `goal_driven_templates.py`, `longform_variant_prompts.py`, and `family_long_novel_prompts.py`.
- Additional service-local prompt builders exist in `backend/app/services/content_engine.py`, `backend/app/services/family_long_novel.py`, `backend/app/services/comic_prompts.py`, and `backend/app/services/prompt_templates.py`.

## Existing Short-Form Generation Flow

- Main task creation route is in `backend/app/api/routers/tasks.py`.
- The route creates `Task` records and dispatches Celery work.
- Celery task logic is in `backend/app/tasks/tasks.py`.
- Short-form generation calls `backend/app/services/content_engine.py::generate_full_book`.
- Current stage artifacts are written under `backend/output/task_<task_id>/`, including files such as `01_writer.md`, `02_polish.md`, `03_refine.md`, `04_format.md`, and `quality_report.txt`.
- Chapters are persisted through `Chapter` records after markdown generation.
- For Epic Novel Engine planning, this existing flow should be treated as the Chapter Draft Writer and preserved.

## Existing Long-Novel Flow

- Long-novel routes are in `backend/app/api/routers/long_novel.py`.
- Existing family long-novel service is in `backend/app/services/family_long_novel.py`.
- Current endpoints cover project creation, bible generation, outline generation, chapter planning, chapter generation, context pack, batch generation, pause/resume, continuity check, repair, conflicts, issues, and export.
- This is useful prior art, but the Epic Novel Engine implementation must follow `docs/epic_novel/*.md` and avoid broad rewrites.

## Existing Task/Export/Logging System

- Celery configuration is in `backend/celery_app.py`; broker/backend use Redis-compatible URLs.
- Task state/progress uses SQLAlchemy models such as `Task`, `Chapter`, cover batch models, and long-novel generation logs.
- Exports/downloads for task markdown/docx and batches are in `backend/app/api/routers/tasks.py`.
- Long-novel exports are routed through `backend/app/api/routers/long_novel.py` and implemented by `export_family_novel` in `backend/app/services/family_long_novel.py`.
- Python logging is configured in `backend/main.py`; service modules use standard `logging.getLogger`.

## Implementation Boundary For Next Goal

- Preserve existing short-form generation and reuse it as Chapter Draft Writer.
- Do not use raw reference books directly in generation prompts.
- Do not start implementation until `docs/epic_novel/EPIC_NOVEL_ENGINE_FULL_SPEC.md` contains the complete spec.
