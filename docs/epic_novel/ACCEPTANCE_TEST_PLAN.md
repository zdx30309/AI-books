# Epic Novel Engine Acceptance Test Plan

Updated: 2026-06-12

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
