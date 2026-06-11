# Repository Instructions

Before editing code, read:

1. `docs/epic_novel/00_README.md`
2. `docs/epic_novel/EPIC_NOVEL_ENGINE_FULL_SPEC.md`
3. `docs/epic_novel/09_ACCEPTANCE_CRITERIA.md`
4. `docs/epic_novel/IMPLEMENTATION_STATUS.md`

`docs/epic_novel/*.md` are the source of truth for the Epic Novel Engine.

If instructions conflict, follow this priority:

1. `AGENTS.md`
2. `docs/epic_novel/EPIC_NOVEL_ENGINE_FULL_SPEC.md`
3. `docs/epic_novel/09_ACCEPTANCE_CRITERIA.md`
4. Other `docs/epic_novel/*.md`
5. The short Codex Goal prompt

The existing short-form generation system must be preserved and reused as the Chapter Draft Writer.

The new system is a long-form industrial control plane for 300万字级别网文 production. It must be added on top of the existing project, not by replacing the current app.

---

## Large Spec Reading Rule

Do not treat `EPIC_NOVEL_ENGINE_FULL_SPEC.md` as one giant mental step.

Read the spec progressively by implementation phase.

Before each major phase:

1. Read only the relevant sections of the SPEC.
2. Summarize the requirements for that phase.
3. Implement that phase.
4. Run the most relevant checks/tests available.
5. Update `docs/epic_novel/IMPLEMENTATION_STATUS.md`.
6. Continue to the next phase.

Do not paste the full SPEC into a generated plan or into prompt templates.

Use `docs/epic_novel/09_ACCEPTANCE_CRITERIA.md` as the final checklist.

---

## Implementation Phasing Rule

Implement in this order:

1. Repo inspection and implementation plan
2. Data models and migrations
3. Model Router
4. Storage & State Layer
5. Reference seed list parser and normalizer
6. ExternalDownloaderAdapter and SoNovelAdapter
7. ReferenceDownloadTask workflow
8. SoNovel download scanner and epub/txt importer
9. ReferencePatternCard extraction/search
10. ReferenceMechanismProfile builder
11. AutoRetrievalPlanner
12. WorldAtlas
13. MegaBible
14. Genesis Layer
15. Planning Layer
16. Execution Layer
17. QA / Acceptance Layer
18. API routes
19. Frontend workspace
20. Dashboard
21. Export
22. Test milestone
23. Final status update

Do not try to solve the entire SPEC in one step.

---

## Non-Negotiable Rules

- Do not break existing short-form generation.
- Do not rewrite the whole project.
- Reuse the existing short-form generator as Chapter Draft Writer.
- Do not hardcode prompts in business logic.
- Store prompts in prompt template files or the project’s existing prompt system.
- Do not hardcode any single reference title as an anchor.
- Use the full 100-book corpus to build `ReferenceMechanismProfile`.
- Do not make the system imitate one specific book.
- Raw reference text is only allowed for ingestion and abstraction.
- Generation prompts must use only `ReferencePatternCards`, `ReferenceMechanismProfile`, and approved summaries.
- Raw reference books must never be used directly in generation prompts.
- Do not build a new unrestricted crawler.
- Use the user’s local SoNovel tool through an adapter when `REFERENCE_DOWNLOADER_PROVIDER=sonovel`.
- If SoNovel cannot be invoked programmatically, implement `manual_required` workflow and scan `SONOVEL_DOWNLOAD_PATH` for downloaded files.
- Do not generate chapters in parallel.
- Chapters must be generated strictly serially.
- `draftContent` must never write into canon/state ledgers.
- `repairedContent` must not directly write into canon/state ledgers.
- Only `acceptedContent` can persist accepted canon state.
- High/severe issues block acceptance.
- Outline repair and Bible repair must be local patches, not full rewrites.
- Every chapter must run Planned-vs-Actual Diff and Acceptance Gate.
- Update `docs/epic_novel/IMPLEMENTATION_STATUS.md` after each major implementation phase.
- All LLM calls must be logged with model, action, cost, status, and errors.
- All reference downloads/imports must record provider, query, local path, status, error, file hash, and generated card count.

---

## Reference Corpus Rules

The 100-book list is the reference corpus.

The engine must:

1. Parse the raw markdown seed list.
2. Normalize it into structured booklist records.
3. Classify books into four tiers and corpus roles.
4. Create `ReferenceDownloadTask` records.
5. Use SoNovel through `SoNovelAdapter` where possible.
6. Scan downloaded epub/txt files.
7. Import raw files into `.local/novel_ref_corpus/raw/`.
8. Extract abstract `ReferencePatternCards`.
9. Build `ReferenceMechanismProfile` from the whole corpus.
10. Use the profile and cards to guide Bible, outline, chapter contracts, and QA.

The engine must not:

- Use raw reference text in generation prompts.
- Copy source prose, names, scenes, jokes, twists, institutions, power systems, or chapter structures.
- Overfit to any one book.

Allowed extraction:

- plot function
- pleasure mechanism
- mystery structure
- foreshadow pattern
- character arc pattern
- heroine agency pattern
- bureaucracy conflict pattern
- worldbuilding function
- power progression logic
- anti-cliche rules
- longform pacing patterns

---

## SoNovel Adapter Rules

The project must use a pluggable downloader interface.

Required interface:

- `detectInstallation()`
- `getConfig()`
- `searchBook(title, author?)`
- `downloadBook(searchResult)`
- `listDownloadedFiles()`
- `importDownloadedFile(filePath)`
- `getLogs()`
- `validateOutput()`

Default local config:

```env
REFERENCE_DOWNLOADER_PROVIDER=sonovel
SONOVEL_HOME=C:\Users\15050\Desktop\SoNovel
SONOVEL_DOWNLOAD_PATH=C:\Users\15050\Desktop\SoNovel\downloads
SONOVEL_OUTPUT_FORMAT=epub

Do not assume a fixed SoNovel CLI command.

First inspect:

readme / readme.txt
config files
rules directory
downloads directory
logs directory
executable or jar behavior if safe

If invocation is unclear, implement:

manual command suggestion
manual_required status
download folder scanner
epub/txt importer
Storage and Canon Rules

The system must distinguish:

Planning data
Draft text
Accepted canon state
Retrieval traces
QA reports
Export artifacts

Only accepted chapters may update canon.

acceptedContent may update:

EpicCanonFact
EpicCharacterState
EpicPowerLedger
EpicObjectState
EpicFactionState
EpicInstitutionState
EpicForeshadow
EpicReaderQuestion
EpicPleasureBeat

draftContent and repairedContent must not update those state tables.

Chapter Execution Rule

The chapter pipeline must be serial:

Chapter Contract
→ Auto Retrieval Planner
→ Reference Inspiration Pack
→ Context Pack
→ Draft
→ State Extract
→ Planned-vs-Actual Diff
→ Acceptance Gate
→ Repair if needed
→ Re-extract
→ Re-check
→ Accepted
→ Persist State
→ Next Chapter

For chapterNumber > 1, generation must be blocked unless the previous accepted chapter has:

summary
endingHook
persisted state
Completion Rule

Do not declare the task complete unless docs/epic_novel/09_ACCEPTANCE_CRITERIA.md is satisfied.

If the full implementation is too large for one run, implement the maximum runnable subset in SPEC priority order and update IMPLEMENTATION_STATUS.md with:

completed items
failed items
blocked items
commands run
test results
exact TODOs
next steps