# Gate 11 Scheduler / Cost / Recovery Report

Updated: 2026-06-13

## Verdict

`PASS`.

Gate 11 verifies Epic long-run job controls for scheduling state, pause/resume/stop, retry failed only, checkpointing, continuation chunks, cost logging, dashboard exposure, and retry safety.

## Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Gate 11 audit | PASS | `.local/epic_novel_runs/gate11_scheduler_2026-06-12T221941_023711Z.json` |
| Scheduler report | PASS | `.local/epic_novel_runs/production_scheduler_2026-06-12T221941_022151Z.md` |
| Main job | PASS | `1e0d97ce-1866-4530-a343-effea22b406e`, status `completed`, progress `6/6` |
| Job steps | PASS | reference extraction, Bible generation, chapter generation, QA, repair, export |
| Pause / resume | PASS | paused attempt did not advance; resumed job continued |
| Retry failed only | PASS | failed `chapter_generation`, retry pending only `chapter_generation`, retry count `1` |
| Continuation chunks | PASS | `continuationChunkCount=1`, content not returned in response |
| Cost cap | PASS | cost-cap job stopped before advancing, projected cost `0.01` over cap `0.001` |
| Stop | PASS | stopped job did not advance |
| Cost logs | PASS | `EpicGenerationLog` rows include provider, model, prompt tokens, completion tokens, estimated cost, actual cost where available, action, status, error |
| Canon retry safety | PASS | chapter 1 canon facts stayed `1 -> 1` after retry |
| Dashboard/API exposure | PASS | dashboard returned `current_job`, progress, estimated remaining cost, job errors, retry status fields |
| Reference extraction control | PASS | control file changed `paused -> running` safely |

## Code Changes

- Added `EpicProductionJob` to record Epic production jobs.
- Added `EpicGenerationLog.actual_cost`.
- Added scheduler helpers:
  - `create_epic_production_job`
  - `apply_epic_job_step`
  - `record_epic_job_step`
  - `pause_epic_production_job`
  - `resume_epic_production_job`
  - `stop_epic_production_job`
  - `retry_epic_job_failed_only`
  - `latest_epic_job_snapshot`
- Extended dashboard response with current job, progress, estimated remaining cost, errors, and retry status.
- Added `scripts/epic_gate11_scheduler_recovery.py`.
- Added `backend/tests/test_epic_scheduler.py`.

## Boundary

- Gate 11 proves control-plane safety and recovery behavior.
- It does not run full corpus extraction, 300万字 generation, frontend runtime verification, current ECS/GitHub deployment, or final SPEC readiness.
