# Epic Novel Engine Acceptance Test Plan

Updated: 2026-06-12

## Scope

This plan verifies `docs/epic_novel/09_ACCEPTANCE_CRITERIA.md` plus the Dynamic Context Compiler rules added on 2026-06-12.

## Preconditions

- Backend can import `app.api.api`.
- Database is reachable and `init_db()` can create/self-heal Epic tables.
- SoNovel WebUI is available or downloader tasks can enter `manual_required`.
- Local raw reference files under `.local/novel_ref_corpus/raw` are ignored by git.
- Existing writer keys are available in local environment; tests must not print secrets.

## Automated Unit / Smoke Checks

1. `python -m compileall backend/app scripts`
   - Proves Python syntax/import surfaces compile.
2. `$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_context.py backend/tests/test_epic_reference.py -q`
   - Verifies:
     - `max_output_tokens=0` does not pass an artificial app-layer max token limit.
     - ContextPack compiler excludes raw reference text.
     - ContextPack reports included vs overflow sections.
     - ReferencePatternCards and ReferenceMechanismProfile are allowed prompt sources.
     - Raw reference book text is never used in generation context.
     - Continuation chunks can resume when output lacks done marker.
     - Large export metadata points to files and does not return content.
     - 100-book parser handles continuous numbered Chinese seed text.
     - SoNovel candidate selector rejects near-miss titles.
3. `npm.cmd run build` from `frontend/`
   - Proves Epic workspace/type changes compile.
4. `$env:PYTHONPATH='backend'; python -u scripts/epic_acceptance_smoke.py`
   - Live DB-backed acceptance flow for the first 3 chapters.
   - Required assertions:
     - at least 10 chapter contracts
     - 3 accepted chapters
     - RetrievalTrace and AcceptanceReport recorded
     - `nonAcceptedCanonSources = 0`
     - chapter 2/3 `previousRequiredStateReady = true`
     - context packs include Bible, WorldAtlas, bureaucracy, ReferenceMechanismProfile evidence
     - Bible and novel exports have non-empty content

## Live Acceptance Flow

The live flow must perform or verify these steps:

1. Create or use test authenticated user.
2. Create EpicNovelProject.
3. Parse/normalize booklist.
4. Detect SoNovel or record `manual_required`.
5. Import local/downloaded epub/txt if present.
6. Generate ReferencePatternCards.
7. Build ReferenceMechanismProfile.
8. Generate WorldBible, WorldAtlas, BureaucracySystem, PowerSystem, EnsembleCast, GlobalSpine.
9. Generate MegaBible manifest and at least 10 sections.
10. Run Bible Coverage CI.
11. Generate 3 VolumeContracts.
12. Compile 10 ChapterContracts.
13. Execute chapters 1-3 serially.
14. Verify chapter 2/3 read previous state, Bible, WorldAtlas, bureaucracy, and ReferenceMechanismProfile.
15. Verify planned-vs-actual diff, acceptance gate, RetrievalTrace, AcceptanceReport.
16. Verify draft/repaired never write canon and acceptedContent writes state ledgers.
17. Export Bible markdown/json and novel markdown.

## Pass / Fail Rules

- Do not mark complete unless every item in `09_ACCEPTANCE_CRITERIA.md` is PASS.
- If SoNovel cannot find/download a title, record status and continue only because the user explicitly approved skipping unavailable novels on 2026-06-12.
- If a provider/context limit is hit, the system must use continuation or record overflow metadata; silent truncation is FAIL.
- Raw reference text in a generation prompt is FAIL.
- High/severe chapter issues accepting content is FAIL.

