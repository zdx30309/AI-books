# Gate 9 Full Volume Acceptance Report

Updated: 2026-06-13

## Verdict

`PASS` for the configured live test volume.

The live project `44ce72be-a423-4f3b-895b-ad8a0fbb5e63` currently configures volume 1 as chapters 1-3. Gate 9 verifies that complete configured volume, not a 10-chapter production volume and not 300万字 readiness.

## Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Gate 9 audit | PASS | `.local/epic_novel_runs/gate9_full_volume_2026-06-12T214212_737800Z.json` |
| Full volume report | PASS | `.local/epic_novel_runs/full_volume_acceptance_2026-06-12T214212_736039Z.md` |
| Live project | PASS | `44ce72be-a423-4f3b-895b-ad8a0fbb5e63` |
| Volume contract | PASS | 第1卷 阶段主线, chapter range 1-3 |
| Accepted chapters | PASS | 3 / 3 |
| QA reports | PASS | chapters 1-3 latest AcceptanceReport status `accepted` |
| Required traces | PASS | every chapter has `chapter_context_pack`, `qa_planned_vs_actual_diff`, `qa_acceptance_gate`, `qa_chapter_check` |
| State ledgers | PASS | every chapter has StateExtract, CanonFact, CharacterState, PowerLedger, ObjectState, FactionState, InstitutionState, PleasureBeat |
| Reference usage | PASS | every chapter context trace uses selected cards and profile version |
| Canon boundary | PASS | `nonAcceptedCanonSources=0` |
| Foreshadow / reader questions | PASS | project ledgers present |
| Volume manuscript export | PASS | `E:\video-plan\.local\epic_novel_exports\本长篇项目 第1卷 正文.md`; metadata only, no giant response |

## Code Changes

- Added `scripts/epic_gate9_full_volume_acceptance.py`.
- The script verifies a configured volume end-to-end, reruns chapter QA, checks trace/report/state evidence, verifies canon source boundaries, and exports a volume manuscript/report.
- The script also normalizes old smoke-derived volume titles before reporting, keeping visible product output Chinese.
- `_write_draft_from_context()` now produces Chinese story text instead of meta placeholder prose and no longer emits old smoke titles, dict-style institution objects, or raw-reference warning text into draft content.
- Accepted chapter retries now mark old open chapter issues as `fixed`.

## Boundary

- This is one configured live test volume with 3 chapters.
- It does not prove a 10-chapter volume, all generated volumes, frontend runtime, current deployment, or 300万字 readiness.
