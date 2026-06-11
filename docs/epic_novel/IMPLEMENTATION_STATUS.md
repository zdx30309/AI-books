# Implementation Status

Updated: 2026-06-12

## Completed

- Full Epic Novel Engine spec detected in `EPIC_NOVEL_ENGINE_FULL_SPEC.md`; placeholder gate passed.
- Repo inspection completed and summarized in `PRESETUP_REPORT.md` and `IMPLEMENTATION_PLAN.md`.
- Implementation plan written in `IMPLEMENTATION_PLAN.md`.
- Epic config settings added to root/backend env examples and backend settings.
- Local storage helper layer added for reference raw/processed/downloads, exports, and run artifacts.
- SQLAlchemy Epic models added and registered, covering:
  - reference booklist, download tasks, raw books, ingestion jobs, pattern cards, and mechanism profiles
  - Epic projects, worldview candidates, world bible, world atlas, nations, bureaucracy, regions, institutions, factions
  - master bible, bible sections, coverage items, entities, power system, power ledger
  - characters, character arcs, relationships, global spine, volume/chapter contracts
  - chapters, state extracts, canon facts, character/object/faction/institution state
  - retrieval traces, acceptance reports, issues, and generation logs
- Epic prompt templates added under `backend/app/prompts`.
- Role-based Epic model router added for editor, author, and cheap roles, with token/cost logging.
  - SPEC-first defaults now resolve to editor `openai/gpt-5.5`, author `deepseek/deepseek-v4-pro`, and cheap `deepseek/deepseek-v4-pro` unless `EPIC_*` env vars override them.
  - Chapter Draft Writer can call the existing project writer/provider path through `call_epic_model()` and falls back only after recording the provider error path.
- Reference corpus workflow added:
  - seed parser/normalizer
  - SoNovel detector with WebUI search/download integration plus manual-required fallback
  - download scan/import with hash tracking
  - txt/md/html/json/epub parsing
  - abstract-only PatternCard generation
  - ReferenceMechanismProfile builder
  - AutoRetrievalPlanner that returns cards/profile, not raw source text
- Epic service layer added:
  - project creation/list/detail/dashboard
  - worldview tournament, world bible, world atlas, bureaucracy/nations/regions/institutions/factions
  - power system, ensemble cast, global spine
  - bible manifest/sections, coverage CI, repair, export
  - volume contracts, arc ledgers, outline CI/repair, chapter contracts
  - chapter context pack, strict serial chapter execution, chapter check/repair, volume/batch generation, novel export
  - canon/state writes occur only after `accepted_content`
- `/api/v1/epic-novel` router added and registered.
- Frontend Epic workspace added at `/epic-novel` and `/epic-novel/:id`, with:
  - project list/create
  - foundation generation controls
  - reference scan/import/profile controls
  - chapter compile/execute/batch controls
  - QA dashboard and logs
  - Bible/novel exports
- Startup schema self-heal added for Epic incremental columns used by the current prototype.
- Reference booklist parser hardened for the local 100-book seed format; normalized output is 100 items with tier counts 20/30/25/25.
- SoNovel batch helpers added:
  - `scripts/epic_download_reference_corpus.py` for DB-backed task download/import
  - `scripts/epic_download_reference_manifest.py` for DB-free manifest download/retry/reconciliation
  - `scripts/epic_stage_reference_downloads.py` for staging matched downloads into `.local/novel_ref_corpus/raw`
- SoNovel WebUI was enabled locally and exercised through `http://127.0.0.1:7765`.
- Reference manifest run completed for all 100 seed rows:
  - manifest: `.local/epic_novel_runs/reference_manifest_download_20260611_154730.json`
  - booklist rows matched to downloaded files: 53
  - rows with no exact/high-confidence SoNovel candidate: 46
  - rows with candidates but source download failure: 1 (`运朝之主`)
  - local SoNovel download directory currently has 56 epub files, including 3 extra/unmatched files from earlier manual/partial runs.
  - staged local reference corpus currently has 57 epub files under `.local/novel_ref_corpus/raw` with tier distribution 17/4/7/29.
- After DB connectivity recovered, reference corpus was re-imported and ingested:
  - SoNovel scan count: 56
  - matched imports from current downloads: 53
  - `reference_books`: 57
  - `reference_ingestion_jobs`: 57
  - `reference_pattern_cards`: 893
  - `reference_mechanism_profiles`: 2
  - batch ingest generated 861 new PatternCards across 55 newly indexed books, with 0 ingest failures
- Latest live DB-backed Epic acceptance smoke passed after full-section reference slicing and Dynamic Context Compiler changes:
  - created smoke project `9cb849c3-e6d4-4995-a6b3-bccb594aa0c6`
  - used 98,581 ReferencePatternCards and 10 ReferenceMechanismProfiles
  - generated WorldBible, WorldAtlas, bureaucracy, power system, ensemble cast, global spine, 21 Bible sections/coverage items, 3 volume contracts, and 10 chapter contracts
  - executed chapters 1-3 serially to `accepted`
  - verified previous-chapter state readiness for chapters 2 and 3
  - verified `nonAcceptedCanonSources = 0`
  - exported Bible markdown and novel markdown content successfully
- Dynamic Context Compiler added:
  - `0` means no fixed Epic business-layer cap, but provider/model context windows still apply
  - included context and overflow context are recorded in `contextMetadata`
  - raw reference text is excluded from prompt context
  - latest full-corpus ContextPack evidence for chapter 2: 608 cards included, 97,973 cards recorded as overflow, raw reference text included = false
- Full-section reference slicing was resumed after removing the old 12-section cap:
  - final `reference_books`: 57
  - final `reference_pattern_cards`: 98,581
  - final `reference_mechanism_profiles`: 10
  - remaining below-100-card books are source-limited: `妖刀记` has 13 sections and 17 cards; `百炼成仙` has 32 sections and 36 cards
- GitHub/ECS delivery completed:
  - verified implementation commit pushed to `origin/codex/video-source-classical-regenerate`: `ce45cae1858b53c12acad83f1a071bd54762cd31`
  - Beijing ECS deployed under `/opt/video-plan`; previous directory was preserved under `/opt/video-plan-backups/video-plan-20260612030545`
  - ECS `docker compose up -d --build` succeeded for `backend`, `worker`, `beat`, and `web`
  - ECS deploy verification recorded artifact `ce45cae1858b53c12acad83f1a071bd54762cd31` before this report-only documentation update
  - ECS raw corpus is present under `/opt/video-plan/.local/novel_ref_corpus/raw`
  - ECS raw corpus was manifest-verified against local files: 57 files, 296,481,452 bytes, 0 size mismatches, 0 sha256 mismatches

## Current Limits

- Strict 2026-06-12 audit result: all items in `09_ACCEPTANCE_CRITERIA.md` have passing local/live evidence after the latest smoke. Full SPEC traceability still records non-acceptance enhancements such as true docx export and first-class Alembic migrations as PARTIAL.
- Tiny live provider probes passed:
  - DeepSeek V4 Pro returned text for a minimal `author` probe.
  - GPT-5.5 writer returned `EPIC_OPENAI_OK` through the existing writer path after automatic reasoning-effort retry.
- DB-backed `call_epic_model()` probe passed after the Supabase pooler recovered, returning `EPIC_ROUTE_OK` and writing `EpicGenerationLog`.
- SoNovel download quality depends on source-site availability. The workflow rejects low-confidence fuzzy title matches instead of downloading wrong books.
- The full 100-book corpus is not completely downloaded: 53 seed rows are matched to local downloads, 46 have no exact/high-confidence SoNovel candidate, and 1 source download currently times out. Per user instruction on 2026-06-12, unavailable/failed novels are skipped for deployment.
- Raw reference artifacts are stored under `.local/` paths and ignored by git.
- No DB migration framework was found; models are registered for the repo's existing `Base.metadata.create_all()` startup pattern.
- Public ECS web/API health checks passed after deployment.

## Remaining

- Optional later corpus completion: manually source the 46 unmatched rows and retry `运朝之主` if the source site becomes available.
- Optional: run a full live-provider chapter smoke with `EPIC_USE_EXISTING_WRITER_FOR_DRAFTS=true`; current acceptance smoke used deterministic fallback for chapter prose stability while live provider probes verified model connectivity.
- Add broader API/service automated tests once a dedicated test database setup is confirmed.
- Add first-class migrations if the repo adopts Alembic or another migration framework; current implementation follows the repo's `create_all()` pattern.
- Expand docx export for Bible if a Word export dependency is selected; current API maps docx requests to markdown content.

## Commands Run

- `Get-Content -LiteralPath 'AGENTS.md'`
- `Get-Content` on `00_README.md`, `EPIC_NOVEL_ENGINE_FULL_SPEC.md`, `09_ACCEPTANCE_CRITERIA.md`, and `IMPLEMENTATION_STATUS.md`
- `rg` over memory, spec headings, repo files, and implementation-relevant code
- `Get-ChildItem` over repo/backend/frontend roots and local SoNovel directory
- `python -m compileall backend/app`
- `$env:PYTHONPATH='backend'; python -c "from app.api.api import api_router; print(len(api_router.routes))"`
- `npm run build`
- `npm.cmd run build` from `frontend/`
- Vite dev server started on `http://127.0.0.1:5173`
- Browser smoke opened `/epic-novel`; protected route redirected to `/login`
- `python -m compileall backend/app scripts`
- `python -m compileall backend/app/services/epic_reference.py scripts/epic_download_reference_manifest.py`
- `$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_reference.py -q`
- `python -u scripts/epic_download_reference_corpus.py --tier 1 --search-only --sleep 0`
- `python -u scripts/epic_download_reference_manifest.py ...` with resume/retry runs recorded under `.local/epic_novel_runs`
- `python -u scripts/epic_stage_reference_downloads.py .local\epic_novel_runs\reference_manifest_download_20260611_154730.json`
- `python -u scripts/epic_ingest_reference_books.py`
- `python -u scripts/epic_acceptance_smoke.py`
- live DeepSeek V4 Pro probe through `app.services.deepseek.generate_text`
- live GPT-5.5 writer probe through `app.services.content_engine._route_writer_call`
- `python -m pytest backend/tests/test_epic_context.py backend/tests/test_epic_reference.py -q`
- repeated `python -u scripts/epic_ingest_reference_books.py --reingest-indexed ...` runs to finish full-section slicing for all imported books
- SoNovel WebUI probes against `http://127.0.0.1:7765`
- `git add`, `git commit`, and `git push -u origin codex/video-source-classical-regenerate`
- `git archive --format=zip --output=.local/deploy_artifacts/video-plan-ce45cae.zip HEAD`
- `Compress-Archive -Path .local\novel_ref_corpus\raw -DestinationPath .local\deploy_artifacts\novel_ref_corpus_raw.zip -Force`
- SFTP upload of code/corpus archives to Beijing ECS using the root deployment config in `deployment.local.json`
- ECS backup/deploy: move existing `/opt/video-plan` to `/opt/video-plan-backups/video-plan-20260612030545`, restore `.env`, extract code/corpus, and run `docker compose up -d --build`
- ECS verification: deploy version, raw corpus file count/bytes/hash manifest, `docker compose ps`, backend `/health`, router import, DB counts, and `/api/v1/system/health`
- Public verification: `http://101.200.48.238/` and `http://101.200.48.238/api/v1/system/health`

## Test Results

- Backend compile passed.
- FastAPI router import smoke passed; router count printed `116`.
- Frontend TypeScript/Vite production build passed with `npm.cmd run build` from `frontend/`.
- Epic reference unit tests passed: 3 tests for continuous 100-book seed parsing and conservative SoNovel candidate selection. Pytest emitted only a cache write warning for `.pytest_cache` permissions.
- Browser smoke passed for route loading/auth redirect; only existing React Router v7 future-flag warnings appeared.
- Live DB-backed acceptance smoke passed after full-section slicing: project `9cb849c3-e6d4-4995-a6b3-bccb594aa0c6`, 98,581 cards, 10 chapter contracts, 3 accepted chapters, 15 retrieval traces, 3 acceptance reports, `nonAcceptedCanonSources=0`.
- Dynamic Context Compiler tests passed: 9 tests covering no artificial output cap for `0`, raw-text exclusion, included/overflow metadata, card/profile prompt allowance, continuation, and file metadata export.
- Live provider probes passed for DeepSeek V4 Pro and GPT-5.5. DB-backed provider log probe passed after retry.
- Current DB reference import/ingest passed after full-section slicing: 57 books, 98,581 PatternCards, 10 mechanism profiles.
- SoNovel manifest completed all 100 rows; 53 seed rows have matched downloaded files, 46 rows have no exact/high-confidence candidate, and 1 row has source timeout failure.
- GitHub push passed for branch `codex/video-source-classical-regenerate`.
- Beijing ECS deploy passed:
  - `docker compose ps` reported `backend`, `worker`, `beat`, and `web` up after rebuild.
  - backend `/health` returned `{"status":"ok","app":"GravitySingularity","version":"1.0.0"}`.
  - route import on ECS printed router count `116`.
  - DB counts from ECS printed `books 57`, `cards 98581`, `profiles 10`.
  - system health returned `celery_alive=true`.
  - public homepage returned HTTP 200.
  - public system health returned HTTP 200 with `celery_alive=true`.
- ECS corpus manifest verification passed: 57 files, 296,481,452 bytes, 0 missing files, 0 size mismatches, 0 sha256 mismatches.
