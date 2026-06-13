# Epic Novel Engine Production Readiness Matrix

Updated: 2026-06-13

Status: `PARTIAL_IMPLEMENTATION`

| Gate | Required | Current evidence | Status |
| --- | --- | --- | --- |
| No production hardcoded story anchors | Production services must derive entities from project/reference/model inputs | production service scan has no old fixed story names; remaining hits are SPEC/test docs and negative tests | PASS |
| Chinese-only user-facing generation | Generated story/Bible/fallback content must be Simplified Chinese | tests cover fallback and Bible acceptance; strict live Bible passed | PASS |
| Fallback labeling | deterministic fallback must be `fallback/placeholder`, `production_quality=false` | implemented and tested | PASS |
| Live LLM production path | production generation must use configured LLM instead of smoke fixtures | strict live run used providers and passed; structured fallback to editor/GPT is available | PASS |
| DeepSeek V4 Pro reference intelligence | reference role defaults to DeepSeek V4 Pro; staged extraction evidence exists | Gate 2B: 3 source books / 27 successful section analyses / 48 production-ready cards | PARTIAL |
| Full-corpus LLM reference extraction | all available sections analyzed with source identity and no mixing | dry-run estimates 106,914 sections; current successful analyses=27 | PARTIAL |
| Reference usage depth | all required layers consume production-ready cards/profile and record RetrievalTrace | Gate 3: 19/19 task types PASS, 69 new traces; latest Gate 2B production-ready card pool is 48, rawTextIncluded=false | PASS |
| Dynamic Context safety | no infinite prompt explosion, overflow metadata, no raw reference text | Gate 5 audit `.local/epic_novel_runs/gate5_dynamic_context_2026-06-12T200323_839412Z.json`; context tests 8 passed | PASS |
| Master Bible | accepted sections, complete coverage, no fallback, Chinese-only visible rendering | Gate 4: 24/24 accepted, Bible `completed`, markdown/json exported as `本长篇项目 全书圣经.*` | PASS |
| QA Acceptance Gate | bad outputs blocked, high/severe issues block, issue-driven reports, local repair scope, canon boundary | Gate 6 audit `.local/epic_novel_runs/gate6_qa_acceptance_2026-06-12T202046_749120Z.json`; QA tests 11 passed | PASS |
| Chapter execution | chapters serial, state ledgers, diff, acceptance, trace/report | strict smoke accepted chapters 1-3; Gate 3 chapter 4 accepted with QA traces and acceptedContent-only canon source | PASS |
| Canon-state chapters 1-3 | strengthened QA rerun, context metadata, prior state consumption, acceptedContent-only canon ledgers | Gate 7 audit `.local/epic_novel_runs/gate7_canon_state_2026-06-12T205822_873613Z.json`; `nonAcceptedCanonSources=0` | PASS |
| 30-chapter continuity simulation | 30 serial chapters or equivalent deterministic state behavior | Gate 8 audit `.local/epic_novel_runs/gate8_thirty_chapter_2026-06-12T211746_940280Z.json`; 30/30 simulated chapters accepted; retry idempotent | PASS_SIMULATION |
| Full configured-volume acceptance | one complete configured live test volume with accepted chapters, traces, reports, state ledgers, and file-backed manuscript export | Gate 9 audit `.local/epic_novel_runs/gate9_full_volume_2026-06-12T214212_737800Z.json`; volume 1 chapter range 1-3 accepted; `nonAcceptedCanonSources=0`; volume markdown exported | PASS_CONFIGURED_VOLUME |
| Multi-volume rolling plan | 2-3+ planned volumes with GlobalSpine linkage, active ledgers, old-state compression, future reveal constraints, and file-backed report/export | Gate 10 audit `.local/epic_novel_runs/gate10_multi_volume_2026-06-12T220227_446447Z.json`; 3 volumes; 0 issues; trace `8fd0f399-9ce8-4566-8f1c-3246a5577df6`; plan export written | PASS |
| Scheduler / cost / recovery | long-run job controls, pause/resume/stop, retry failed only, checkpointing, continuation chunks, cost logs, dashboard exposure, safe retry | Gate 11 audit `.local/epic_novel_runs/gate11_scheduler_2026-06-12T221941_023711Z.json`; main job completed; cost cap/stopped/paused/retry checks pass; canon facts stayed 1->1 | PASS |
| Export | Bible/novel/volume/chapter/report/trace file/object export, not giant API response by default | Gate 12 audit `.local/epic_novel_runs/gate12_export_2026-06-12T224306_008134Z.json`; 8 metadata exports; checksums/download routes; large probe; raw reference excluded | PASS |
| Frontend | Chinese workspace controls, dashboard, extraction control, export metadata UI, loading/error/empty states | Gate 13 local browser smoke: 10/10 tabs PASS, export metadata click PASS, no new runtime errors; build passes | PASS |
| GitHub current worktree | current changes committed/pushed | commit `a1907e8` pushed to `origin/codex/video-source-classical-regenerate` and `origin/main` | PASS |
| Beijing ECS current worktree | current changes deployed and verified | `/opt/video-plan/.deploy-version = a1907e8`; compose services Up; public `/`, `/epic-novel`, `/api/v1/system/health` 200; raw corpus 57 epub | PASS |

## Decision

Current verdict: `PARTIAL_IMPLEMENTATION`.

The current code has a staged, resumable production pipeline, Gate 3 reference-usage evidence, Gate 4 Master Bible evidence, Gate 5 dynamic-context evidence, Gate 6 QA evidence, Gate 7 chapters 1-3 canon-state evidence, Gate 8 deterministic continuity evidence, Gate 9 configured-volume evidence, Gate 10 multi-volume rolling-plan evidence, Gate 11 scheduler/cost/recovery evidence, Gate 12 large-export evidence, Gate 13 local frontend runtime evidence, and Gate 14 current GitHub/ECS publication evidence, but it does not prove full Reference Intelligence, all-volume generated prose, 30 production-quality prose chapters, or 300万字 readiness.
