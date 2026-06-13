# Gate 2A Reference Intelligence Run Report

Updated: 2026-06-12

Gate 2A verdict: `PASS`

Scope: bounded proof batch only. This is not full 57-book extraction, not full 100-book corpus completion, not Master Bible quality acceptance, and not 300万字 production readiness.

## Final Proof Run

Command:

```powershell
$env:PYTHONPATH='backend'; python scripts/epic_gate2a_reference_proof.py --book-limit 1 --section-limit 5 --force
```

Output report:

```text
E:\video-plan\.local\epic_novel_runs\gate2a_reference_intelligence_2026-06-12T132502_524973_0000.json
```

Selected proof book:

| Field | Value |
| --- | --- |
| bookId | `d5657d38-ddb3-4c32-ad6b-947ed4a6c90d` |
| title | `仙子且慢，我只想斩妖除魔` |
| proof sections | 5 |
| provider/model | `deepseek` / `deepseek-v4-pro` |
| DeepSeek credentials available | true |

## Metrics

| Metric | Result |
| --- | ---: |
| `ReferenceSection` rows touched in proof | 5 |
| `ReferenceSectionAnalysis` rows touched in proof | 5 |
| successful DeepSeek V4 Pro section analyses | 5 |
| fallback analyses | 0 |
| error analyses | 0 |
| production-ready cards before dedupe | 18 |
| production-ready cards after dedupe | 18 |
| merged cards created/updated | 0 |
| sourceBookIds preserved | 1 |
| sourceSectionIds preserved | 5 |
| sourceAnalysisIds preserved | 5 |
| prompt tokens estimated | 5,330 |
| completion tokens estimated | 3,456 |
| failures | 0 |
| blockers | 0 |

Latest profile:

| Field | Value |
| --- | --- |
| profileId | `b1025f7b-d405-4077-8632-d6adf94e3732` |
| build mode | `local_from_production_ready_cards` |
| generationStatus | `local_summary` |
| profileVersion | `0936628da7413da256307f5fc5509e1c62d5c5e8431ea0aea70b53728cb7448c` |
| sourceBookCount | 1 |
| sourceCardCount | 18 |
| sourceSection sample count | 5 |
| categoriesCovered | `pleasure`, `plot_structure`, `villain_arc` |
| evidenceCounts | min=1, max=1, total=18, cardCount=18 |
| rawTextIncluded | false |
| profile Chinese check | true |

## Live DB Evidence After Final Run

Command summary:

```powershell
$env:PYTHONPATH='backend'; python - <<'PY'
# queried ReferenceSection, ReferenceSectionAnalysis, ReferencePatternCard,
# ReferenceMechanismProfile, EpicGenerationLog
PY
```

Observed DB counts:

| Metric | Result |
| --- | ---: |
| `ReferenceSection` | 5 |
| `ReferenceSectionAnalysis` | 5 |
| `ReferenceSectionAnalysis.status=success` | 5 |
| `ReferenceSectionAnalysis.status=fallback` | 0 |
| `ReferenceSectionAnalysis.status=error` | 0 |
| `ReferencePatternCard` total | 98,599 |
| old cards with empty `source_section_ids` | 98,581 |
| production-ready cards | 18 |
| `EpicGenerationLog.reference_section_analysis.success` | 10 |

Note: generation log success count is 10 because two proof runs made real section-analysis calls after the DB insert bug was fixed. The final PASS report itself contains 5 successful DeepSeek V4 Pro section analyses.

## Implemented Fixes

- Added `ReferenceSection.token_estimate`, `ReferenceSectionAnalysis.analysis_hash`, `ReferencePatternCard.source_analysis_ids`, and `ReferencePatternCard.source_book_ids`, with startup schema self-heal.
- Made `ReferenceSection.source_section_id` stable by book hash + section hash + section index.
- Added Gate 2A Chinese section analysis normalization with required fields:
  `sectionSummary`, `plotFunction`, `pleasureFunction`, `characterArcMove`, `heroineAgencyMove`, `mysteryClueForeshadowActions`, `bureaucracyCourtFactionFunction`, `powerSystemMove`, `pacingRole`, `antiClicheNotes`, `reusableMechanisms`, `notToCopyNotes`.
- Ensured fallback analyses are `status=fallback`, `production_quality=false`, and never counted as production-ready.
- Generated production-ready `ReferencePatternCard` rows only from successful non-fallback `ReferenceSectionAnalysis` rows.
- Added dedupe/merge helpers that preserve `sourceBookIds`, `sourceSectionIds`, `sourceAnalysisIds`, `sourceCardIds`, and `evidenceCount`.
- Rebuilt `ReferenceMechanismProfile` from production-ready cards only for proof mode, with required raw_json metadata and `rawTextIncluded=false`.
- Added bounded proof script: `scripts/epic_gate2a_reference_proof.py`.
- Added targeted unit tests for stable IDs, fallback exclusion, production card source IDs, old-card exclusion, merge evidence preservation, profile metadata, and raw-reference prompt safety.

## Failures Encountered And Fixed

1. First proof attempt returned `UNKNOWN_NEEDS_EVIDENCE`.
   Cause: a pending `ReferenceSectionAnalysis` row was attached to the DB session before `section_hash` was filled; `call_epic_model()` commits before long model calls, triggering a PostgreSQL not-null violation.
   Fix: generate a local `analysis_id` first, call the model, then create/update the DB row with all required fields.

2. Second proof attempt returned `PARTIAL`.
   Cause: profile Chinese check counted UUID/hash/model metadata as natural-language text; the profile LLM call also hit a paid API concurrency limiter timeout.
   Fix: build the Gate 2A proof profile locally from production-ready cards and run Chinese checks only on natural-language profile fields.

## Verification Commands

```powershell
python -m compileall backend/app/services/epic_reference.py backend/app/schemas/epic_novel.py scripts/epic_gate2a_reference_proof.py backend/tests/test_epic_reference.py
$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_reference.py -q
$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_context.py -q
$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_reference.py backend/tests/test_epic_context.py -q
$env:PYTHONPATH='backend'; python scripts/epic_gate2a_reference_proof.py --book-limit 1 --section-limit 5 --force
$env:PYTHONPATH='backend'; python scripts/epic_gate2a_reference_proof.py --book-limit 1 --section-limit 5
```

Results:

- `backend/tests/test_epic_reference.py`: 13 passed.
- `backend/tests/test_epic_context.py`: 6 passed.
- Combined targeted run: 19 passed.
- Final Gate 2A proof: `PASS`.
- Post-template-migration cached proof: `PASS`, report `.local/epic_novel_runs/gate2a_reference_intelligence_2026-06-12T134136_226027_0000.json`.

## Remaining TODOs

- Gate 2A is a bounded proof only. Full Reference Intelligence remains `PARTIAL` until the imported corpus is processed at production scale.
- Process more imported books/sections when cost and time are approved; current full-corpus dry run previously estimated 106,914 sections.
- Keep unavailable/failed reference titles skipped per user instruction, but record skipped/manual evidence when running full corpus workflows.
- Do not mark `09_ACCEPTANCE_CRITERIA.md` all PASS and do not claim 300万字-ready from this bounded proof.
