# Gate 5: Dynamic Context Compiler and Prompt Safety

Status:
DONE / PASS on 2026-06-13.

Goal:
Ensure all production generation prompts use compiled, ranked, deduped, compressed, budget-aware context.

Read first:
- AGENTS.md
- docs/epic_novel/GATE_4_MASTER_BIBLE_REPORT.md
- docs/epic_novel/EPIC_NOVEL_ENGINE_FULL_SPEC.md
- docs/epic_novel/09_ACCEPTANCE_CRITERIA.md

Scope:
Context compiler, prompt safety, raw reference protection, overflow metadata.

Out of scope:
- reference extraction
- Master Bible rewrite
- 30-chapter run
- frontend

Tasks:
1. Do not dump unbounded full_context_json into prompts.
2. Use model/provider context window when available.
3. ContextPack must record:
   - included items
   - excluded items
   - overflow items
   - token estimates
   - exclusion reasons
   - profileVersion
   - selected cards
   - selected Bible/state/world items
4. Prompts may include only:
   - ChapterContract
   - accepted previous state summaries
   - selected Bible summaries
   - selected WorldAtlas/PowerSystem/state
   - selected production-ready PatternCards
   - selected ReferenceMechanismProfile sections
   - ContextPack metadata
5. Raw reference text must never enter prompt.
6. 0 limit means no arbitrary Epic business cap, not unlimited prompt stuffing.
7. No silent truncation is allowed.

Tests:
- full_context_json not present in chapter writer prompt
- prompt size respects budget
- overflow metadata exists
- raw text stripped
- included/excluded reasons recorded
- old source-less cards excluded from production prompt context
- production-ready cards/profile selected by relevance
- ContextPack metadata persisted
- RetrievalTrace links to ContextPack metadata

Create/update:
- docs/epic_novel/GATE_5_DYNAMIC_CONTEXT_REPORT.md
- docs/epic_novel/IMPLEMENTATION_STATUS.md
- docs/epic_novel/PRODUCTION_READINESS_RECHECK.md
- docs/epic_novel/SPEC_TRACEABILITY_MATRIX.md

PASS criteria:
- All production prompts use compiled context only.
- No raw reference text.
- No full unbounded JSON dump.
- No silent truncation.
- Overflow/included/excluded metadata present.

Evidence:
- `docs/epic_novel/GATE_5_DYNAMIC_CONTEXT_REPORT.md`
- `.local/epic_novel_runs/gate5_dynamic_context_2026-06-12T200323_839412Z.json`
- `$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_context.py -q` -> 8 passed
- `$env:PYTHONPATH='backend'; python scripts/epic_gate5_dynamic_context_audit.py --chapter 1` -> PASS
