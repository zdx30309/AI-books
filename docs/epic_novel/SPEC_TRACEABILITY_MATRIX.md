# Epic Novel Engine SPEC Traceability Matrix

Updated: 2026-06-12

Legend:

- `PASS`: implemented and verified in the current worktree.
- `PARTIAL`: implementation exists, but a non-acceptance enhancement or larger production run remains.
- `FAIL`: not implemented or current live check failed.

## Matrix

| Module | Required features | Implementation files | DB models / migrations | Services | API routes | Frontend | Tests / smoke checks | Live DB evidence | Status | Gaps / fixes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Model Router | role routing, GPT/DeepSeek defaults, reference role, logs, no artificial app cap when `0`, continuation, structured fallback | `backend/app/services/epic_model_router.py`, `backend/app/core/config.py` | `EpicGenerationLog`; startup `create_all`/self-heal, no Alembic | `resolve_model`, `call_epic_model`, `call_epic_model_with_continuation` | indirect | model labels in workspace | `test_epic_context.py`; strict smoke | strict smoke logged live generation and passed | PASS | pricing still estimated as `0.0`; add provider pricing later |
| Storage / State | separate planning/draft/accepted canon/retrieval/QA/export; only accepted content writes state | `backend/app/models/epic_novel.py`, `backend/app/services/epic_novel.py`, `backend/app/services/epic_storage.py` | Epic tables plus new reference section tables; no Alembic | `_persist_accepted_state`, exports, context traces | project/chapter/export routes | workspace/dashboard | strict smoke | `nonAcceptedCanonSources=0`, state ledgers populated for 3 chapters | PASS | first-class migrations remain optional/repo-level gap |
| 100-book parser | parse seed, normalize 100 rows, tiers/roles | `backend/app/services/epic_reference.py` | `ReferenceBooklistItem` | `parse_booklist_seed`, `normalize_booklist` | `/reference-booklist/*` | reference controls | `test_epic_reference.py` | prior 100-row normalization; current 57-file corpus available | PASS | unavailable titles skipped per user instruction |
| SoNovelAdapter | detect install, config, search/download, manual fallback, scanner/importer | `backend/app/services/epic_reference.py`, SoNovel scripts | `ReferenceDownloadTask` | `SoNovelAdapter`, scan/import helpers | downloader routes | reference controls | manifest/staging scripts | previous local/ECS corpus evidence; unavailable titles skipped per user | PASS for available corpus | continue only if new SoNovel files appear |
| Download Tasks | provider/query/path/status/error/hash/card count | `backend/app/services/epic_reference.py` | `ReferenceDownloadTask.generated_card_count` | download task helpers | `/reference-download-tasks` | download panels | script smoke | current available corpus feeds strict smoke | PASS | historical task counts may lag reingest counts |
| epub/txt import | scan downloads, copy raw, hash, parse epub/txt/html/json/md | `backend/app/services/epic_reference.py` | `ReferenceBook`, `ReferenceIngestionJob` | `scan_sonovel_downloads`, `import_reference_file`, `parse_reference_text` | scan/import routes | reference library | dry-run/corpus scripts | 57 imported books used in cost dry-run | PASS | unavailable books skipped |
| ReferencePatternCards | abstract-only cards, no raw text, source section identity | `backend/app/services/epic_reference.py`, `backend/app/services/epic_context.py` | `ReferencePatternCard`, `ReferenceSection`, `ReferenceSectionAnalysis` | `ingest_reference_book`, `analyze_reference_section`, `search_pattern_cards` | `/reference-patterns/search` | pattern/reference controls | tests; strict smoke | strict smoke used 98,581 cards | PASS | full DeepSeek section analysis over all 106,914 estimated sections remains large batch work |
| ReferenceMechanismProfile | aggregate corpus, full-corpus mechanisms, prompt-safe profile | `backend/app/services/epic_reference.py`, `backend/app/services/epic_novel.py` | `ReferenceMechanismProfile` | `build_reference_mechanism_profile`, `latest_reference_profile` | profile routes | profile controls | tests and context checks | strict smoke used 10 profiles | PASS | rebuild after full LLM section analysis if desired |
| AutoRetrievalPlanner | retrieve cards/profile, trace usage, no raw text | `backend/app/services/epic_reference.py` | `EpicRetrievalTrace` extended with used/overflow/raw flags | `auto_retrieval_plan` | indirect via chapters/context | context panels | context tests; strict smoke | 19 retrieval traces; unsafe traces `[]` | PASS | richer ranking can be improved later |
| WorldAtlas | atlas, nations, bureaucracy, regions, institutions, factions | `backend/app/services/epic_novel.py` | `EpicWorldAtlas`, `EpicNation`, `EpicBureaucracySystem`, `EpicMapRegion`, `EpicInstitution`, `EpicFaction` | `generate_world_atlas` | `/world-atlas` | foundation controls | strict smoke | generated and used in chapters 2/3 context | PASS | none for acceptance |
| MegaBible | manifest, sections, acceptance, coverage CI, repair, exports | `backend/app/services/epic_novel.py` | `EpicMasterBible`, `EpicBibleSection`, `EpicBibleCoverageItem` | `generate_bible_manifest`, `generate_bible_section`, `validate_bible_section_content`, `run_bible_coverage_ci` | Bible routes | Bible/export controls | strict smoke; Chinese/fallback tests | 24 sections, 0 failures, 24 coverage complete | PASS | true docx export remains future enhancement |
| Genesis | worldview tournament, world bible, power, cast, global spine | `backend/app/services/epic_novel.py` | worldview/power/character/global spine models | generation services model-driven with labeled fallback | genesis routes | foundation controls | strict smoke | generated before chapters | PASS | none for acceptance |
| Planning | volume contracts, chapter contracts, arc ledgers, CI/repair | `backend/app/services/epic_novel.py` | `EpicVolumeContract`, `EpicChapterContract`, arc models | planning helpers | planning routes | planning controls | strict smoke | 3 volumes, 10 chapter contracts | PASS | outline repair remains local/minimal |
| Execution | serial chapters, context pack, draft, state extract, diff, gate, repair | `backend/app/services/epic_novel.py`, `backend/app/services/epic_context.py` | `EpicChapter`, `EpicStateExtract`, ledgers, reports | `execute_chapter`, `build_chapter_context_pack`, `_planned_vs_actual`, `_acceptance` | chapter routes | chapter controls | strict smoke | chapters 1-3 accepted serially | PASS | long multi-volume run not executed |
| QA | high/severe block acceptance; reports/issues; raw reference safety | `backend/app/services/epic_novel.py`, `backend/app/services/epic_context.py` | `EpicAcceptanceReport`, `EpicIssue` | QA helpers | check/repair/dashboard | dashboard | strict smoke | 3 reports; unsafe traces empty | PASS | overfit checker remains heuristic |
| API | project/reference/genesis/planning/execution/export routes | `backend/app/api/routers/epic_novel.py` | uses Epic models | all services | `/api/v1/epic-novel/*` | `frontend/src/api/client.ts` | compile/build | strict smoke exercised service layer; API routes compile | PASS | run public API smoke after ECS deploy |
| Frontend | workspace, dashboard, controls, extraction control, export | `frontend/src/pages/EpicNovelWorkspace.tsx`, API client/types | N/A | API client | N/A | `/epic-novel` | `npm.cmd run build` | build passed | PASS | browser smoke after deploy still needed |
| Dashboard | counts, issues, reference/cost stats | backend dashboard service, frontend workspace | dashboard response schemas | `dashboard` | `/dashboard` | dashboard cards | build | strict project data available | PASS | verify deployed UI after ECS |
| Export | Bible md/json, novel md/txt/json, file metadata for large output | `backend/app/services/epic_novel.py`, router | export path fields | `export_bible`, `export_novel`, metadata helper | export routes | export buttons | context export metadata test; strict smoke | Bible export chars 150,603; novel export chars 15,099 | PASS | true docx remains future enhancement |

## Final Acceptance Evidence

- Passing log: `.local/epic_novel_runs/acceptance_smoke_strict_2026-06-12-resume4.log`
- Project: `44ce72be-a423-4f3b-895b-ad8a0fbb5e63`
- Bible failures: `[]`
- Coverage failures: `[]`
- Unsafe retrieval traces: `[]`
- `nonAcceptedCanonSources=0`
- Chapter 2/3 previous-state readiness: `true`
- GitHub/ECS delivery: branch `codex/video-source-classical-regenerate` pushed; Beijing ECS `/opt/video-plan` rebuilt; public health checks passed.

## Remaining Non-Acceptance Work

- Continue full DeepSeek section analysis if the user wants all 106,914 estimated sections processed with LLM extraction.
- Continue SoNovel folder scanning when new files are downloaded; unavailable/failed titles are skipped by instruction.
