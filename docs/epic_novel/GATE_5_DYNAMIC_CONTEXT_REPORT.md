# Gate 5 Dynamic Context Compiler Report

Updated: 2026-06-13

## Verdict

`PASS` for Gate 5 scope.

This proves the Dynamic Context Compiler and chapter writer prompt safety path for provider-window budgeting, explicit overflow metadata, raw-reference exclusion, and RetrievalTrace persistence. It does not prove Gate 6-14, 30-chapter continuity, full-volume acceptance, or 300万字 readiness.

## Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Gate 5 live audit | PASS | `.local/epic_novel_runs/gate5_dynamic_context_2026-06-12T200323_839412Z.json` |
| Synthetic overflow check | PASS | small prompt budget produced `smallOverflowCount=1` and recorded `excluded`, `includedPaths`, `overflowPaths`, `noSilentTruncation=true` |
| `0` limit behavior | PASS | `forced_prompt_budget_tokens=0` used provider-window budget instead of an artificial Epic cap or infinite prompt stuffing |
| Raw reference text exclusion | PASS | raw book/card text values were absent from compiled context and chapter writer prompt |
| Allowed reference sources | PASS | `ReferencePatternCards` and `ReferenceMechanismProfile` were selected; synthetic `selectedCardIds=["card-gate5-1"]`, `profileVersion=profile-gate5` |
| Chapter writer prompt | PASS | no `full_context_json`, no raw book text, no raw card text, includes selected context metadata |
| Live ContextPack | PASS | project `44ce72be-a423-4f3b-895b-ad8a0fbb5e63`, chapter 1, `metadataIncludedCount=20`, `metadataRawTextIncluded=false` |
| RetrievalTrace persistence | PASS | new live `chapter_context_pack` trace `fa6ed1e5-c1bf-4a15-b2a7-b257d28c3f1d` stores `contextMetadata`, used card ids, and `raw_text_included=false` |
| Targeted tests | PASS | `$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_context.py -q` -> 8 passed |
| Compile check | PASS | `$env:PYTHONPATH='backend'; python -m compileall backend/app/services/epic_context.py backend/app/services/epic_novel.py scripts/epic_gate5_dynamic_context_audit.py` |

## Code Changes

- `backend/app/services/epic_context.py`
  - records `includedPaths`, `overflowPaths`, `excluded`, `selectedCardIds`, `profileVersion`, `selectedBibleSectionKeys`, `noSilentTruncation`, and `excludedRawReferenceFields`.
  - treats `0` as no fixed Epic business-layer cap while still using the provider/model effective context window.
- `backend/app/services/epic_novel.py`
  - chapter writer prompt consumes compiled context metadata instead of dumping unbounded raw context.
  - selected context metadata includes included/overflow paths, selected card ids, profile version, and raw-reference status.
- `backend/tests/test_epic_context.py`
  - covers `0` token behavior, overflow metadata, selected card/profile metadata, raw text exclusion, continuation, file-backed export, and chapter prompt safety.
- `scripts/epic_gate5_dynamic_context_audit.py`
  - produces repeatable synthetic and live DB evidence for Gate 5.

## Remaining Boundaries

- Live Gate 5 project has provider-window metadata and safe prompt compilation, but this is not a 30-chapter or full-volume run.
- Gate 2B remains partial: only 3 books / 15 sections / 36 production-ready cards are proven.
- Gate 12 export scale has since passed. Gate 13 frontend runtime and Gate 14 final readiness still remain.
- 300万字-ready: `NO`.
