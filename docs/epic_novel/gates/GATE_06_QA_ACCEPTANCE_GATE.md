# Gate 6: QA and Acceptance Gate

Status:
DONE / PASS on 2026-06-13.

Goal:
Replace shallow acceptance with SPEC-grade QA that can block bad Bible sections, plans, and chapters.

Read first:
- AGENTS.md
- docs/epic_novel/GATE_5_DYNAMIC_CONTEXT_REPORT.md
- docs/epic_novel/EPIC_NOVEL_ENGINE_FULL_SPEC.md
- docs/epic_novel/09_ACCEPTANCE_CRITERIA.md
- docs/epic_novel/ACCEPTANCE_TEST_PLAN.md

Scope:
QA logic, acceptance reports, issue severity, canon write boundaries.

Out of scope:
- 30-chapter generation
- frontend
- export rewrite

Tasks:
1. Inspect current acceptance logic.
2. Replace or strengthen checks for:
   - plan mismatch
   - missing mustExecute
   - broken world law
   - broken power system
   - character arc drift
   - heroine agency failure
   - bureaucracy function missing
   - foreshadow setup/echo/payoff errors
   - reader question ledger drift
   - copied/source-like content
   - raw reference leakage
   - fallback output
   - English user-facing output
   - generic boilerplate
   - canon inconsistency
   - relationship drift
   - contradiction with Master Bible / WorldAtlas / PowerSystem
3. high/severe issues must block acceptance.
4. Draft/repaired cannot write canon.
5. Only acceptedContent writes canon.
6. QA must record AcceptanceReport and EpicIssue rows with severity.
7. Repair must be local and issue-targeted; repair cannot bypass acceptance.

Tests:
- bad chapter fails
- fallback chapter fails
- English boilerplate fails
- missing mustExecute fails
- raw reference-like text fails
- broken power rule fails
- heroine agency missing fails
- bureaucracy function missing fails
- foreshadow contradiction fails
- high/severe blocks acceptance
- acceptedContent only writes canon
- repaired draft must be re-accepted before canon write

Create/update:
- docs/epic_novel/GATE_6_QA_ACCEPTANCE_REPORT.md
- docs/epic_novel/ACCEPTANCE_TEST_PLAN.md
- docs/epic_novel/IMPLEMENTATION_STATUS.md
- docs/epic_novel/PRODUCTION_READINESS_RECHECK.md

PASS criteria:
- QA can reject bad outputs.
- high/severe blocks.
- Acceptance is not fixed-score or keyword-only.
- Canon write boundary is enforced.

Evidence:
- `docs/epic_novel/GATE_6_QA_ACCEPTANCE_REPORT.md`
- `.local/epic_novel_runs/gate6_qa_acceptance_2026-06-12T202046_749120Z.json`
- `$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_qa.py -q` -> 10 passed
- `$env:PYTHONPATH='backend'; python scripts/epic_gate6_qa_acceptance_audit.py --project-id 44ce72be-a423-4f3b-895b-ad8a0fbb5e63` -> PASS
