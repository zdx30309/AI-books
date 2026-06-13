# Gate 11: Production Scheduler, Cost, Recovery

Status:
DONE / PASS on 2026-06-13.

Goal:
Verify long-run production controls for generation, extraction, QA, repair, export, cost, and recovery.

Read first:
- AGENTS.md
- docs/epic_novel/GATE_10_MULTI_VOLUME_REPORT.md
- docs/epic_novel/IMPLEMENTATION_STATUS.md
- docs/epic_novel/EPIC_NOVEL_ENGINE_FULL_SPEC.md
- docs/epic_novel/09_ACCEPTANCE_CRITERIA.md

Scope:
Scheduler, cost logs, retry, checkpoint, continuation, recovery.

Out of scope:
- frontend polish
- actual 300W manuscript generation
- full corpus extraction unless safely configured

Tasks:
1. Implement/verify job scheduler for:
   - reference extraction
   - Bible generation
   - chapter generation
   - QA
   - repairs
   - exports
2. Support:
   - pause
   - resume
   - stop
   - retry failed only
   - checkpointing
   - continuation chunks
   - failure recovery
3. Cost logging:
   - provider
   - model
   - prompt tokens
   - completion tokens
   - estimated cost
   - actual cost if available
   - action
   - project id
4. Dashboard/API exposes:
   - current job
   - progress
   - estimated remaining cost
   - errors
   - retry status
5. Ensure retries do not duplicate canon writes.

Tests:
- job resume after failure
- retry failed only
- cost logs recorded
- continuation chunks recorded
- no duplicate canon writes after retry
- paused job does not continue
- resumed job continues from checkpoint
- cost cap stops safely

Create/update:
- docs/epic_novel/PRODUCTION_SCHEDULER_REPORT.md
- docs/epic_novel/GATE_11_SCHEDULER_REPORT.md
- docs/epic_novel/IMPLEMENTATION_STATUS.md
- docs/epic_novel/PRODUCTION_READINESS_RECHECK.md

PASS criteria:
- Long-run jobs can pause/resume/retry safely with cost evidence.
- Cost tracking exists.
- Recovery does not corrupt canon or duplicate outputs.

Current evidence:
- `.local/epic_novel_runs/gate11_scheduler_2026-06-12T221941_023711Z.json`
- `.local/epic_novel_runs/production_scheduler_2026-06-12T221941_022151Z.md`
- `EpicProductionJob` main job `1e0d97ce-1866-4530-a343-effea22b406e`

Boundary:
- Gate 11 proves scheduler/cost/recovery control behavior only.
- It does not prove full corpus extraction, 300万字 full generation, frontend runtime verification, current ECS/GitHub deployment, or final SPEC readiness.
