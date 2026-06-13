# Gate 6 QA / Acceptance Gate Report

Updated: 2026-06-13

## Verdict

`PASS` for Gate 6 scope.

This proves the current QA / Acceptance Gate can reject bad chapter output, persist issue/report evidence, block high/severe issues, enforce local issue-targeted repair scope, and preserve the canon write boundary. It does not prove frontend runtime, current deployment, or 300万字 readiness.

## Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Gate 6 live audit | PASS | `.local/epic_novel_runs/gate6_qa_acceptance_2026-06-12T202046_749120Z.json` |
| Bad chapter blocks | PASS | audit chapter `911628` stayed `need_review`, `acceptedContentPresent=false` |
| Blocking issue types | PASS | `planned_vs_actual`, `fallback_or_patch_output`, `raw_reference_leakage`, `english_or_boilerplate`, `bureaucracy_function_missing`, `broken_power_rule`, `heroine_agency_missing`, `foreshadow_missing`, `foreshadow_or_question_overclosed` |
| AcceptanceReport persisted | PASS | bad chapter latest report status `blocked`; scores are issue-driven, not fixed constants |
| EpicIssue persisted | PASS | 13 issue rows recorded for bad chapter; open high/severe rows block acceptance |
| Repair scope | PASS | open issues carry `repair_scope.mode=local_issue_targeted` and `rewrite=forbidden` |
| Repaired draft cannot bypass canon | PASS | audit chapter `911629` remained `need_review`, no accepted content was written, canon/state counts unchanged |
| Canon boundary | PASS | before/after state counts identical: `stateExtracts=4`, `canonFacts=4`, `characterStates=4`, `powerLedger=4`, `objectStates=4`, `factionStates=4`, `institutionStates=4`, `pleasureBeats=4` |
| Targeted QA tests | PASS | `$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_qa.py -q` -> 10 passed |
| Combined tests | PASS | `$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_qa.py backend/tests/test_epic_context.py -q` -> 18 passed |
| Compile check | PASS | `$env:PYTHONPATH='backend'; python -m compileall backend/app/services/epic_novel.py scripts/epic_gate6_qa_acceptance_audit.py` |

## Code Changes

- `backend/app/services/epic_novel.py`
  - Added issue-driven chapter QA checks for plan mismatch, missing mustExecute, fallback/patch output, English boilerplate, raw reference leakage, bureaucracy function, broken power rule, heroine agency, character/relationship drift, foreshadow and reader-question drift, premature reveal, world/Bible contradiction, and undersized content.
  - `high` and `severe` issues block acceptance.
  - Acceptance scores now derive from issue severity/type instead of fixed constants.
  - `check_chapter()` re-runs QA against the current candidate text and persists `EpicIssue` + `EpicAcceptanceReport`.
  - Persisted issue rows record local issue-targeted `repair_scope`; full rewrite is marked forbidden.
- `backend/tests/test_epic_qa.py`
  - Covers good chapter acceptance, bad chapter rejection, fallback rejection, English boilerplate rejection, missing mustExecute, raw reference-like text, broken power rule, heroine agency, bureaucracy function, foreshadow over-closure, and dynamic scoring.
- `scripts/epic_gate6_qa_acceptance_audit.py`
  - Produces live DB evidence for bad output blocking and canon boundary preservation.

## Remaining Boundaries

- Gate 6 proves QA depth on targeted bad-output cases and live DB persistence, not a long serial chapter run.
- Gate 7 must still re-prove canon-state execution for chapters 1-3 under the strengthened QA.
- Gate 8 must still prove 30-chapter continuity.
- Gate 9-14 remain pending.
- 300万字-ready: `NO`.
