# Gate 2B: Reference Intelligence Staged / Full Corpus Expansion

Status:
NEXT after Gate 2A bounded proof PASS.

Goal:
Scale the proven Gate 2A section-aware Reference Intelligence pipeline across more imported books/sections, with cost control, resume, source traceability, and production-ready card/profile evidence.

Read first:
- AGENTS.md
- docs/epic_novel/GATE_2A_REFERENCE_INTELLIGENCE_RUN_REPORT.md
- docs/epic_novel/REFERENCE_INTELLIGENCE_EVIDENCE.md
- docs/epic_novel/PRODUCTION_READINESS_RECHECK.md
- docs/epic_novel/IMPLEMENTATION_STATUS.md
- docs/epic_novel/EPIC_NOVEL_ENGINE_FULL_SPEC.md
- docs/epic_novel/09_ACCEPTANCE_CRITERIA.md

Current verified baseline:
- Gate 2A PASS bounded proof.
- 1 book / 5 sections processed.
- DeepSeek V4 Pro success analyses: 5.
- production-ready cards: 18.
- old 98,581 source-less cards remain excluded from production-ready counts.
- full 57-book run is not complete.

Scope:
Expand beyond Gate 2A.
Run staged extraction safely.

Out of scope:
- frontend
- Master Bible
- chapter generation
- QA rewrite
- 300W-ready claims

Cost rule:
Do not blindly run the full 57-book DeepSeek extraction unless credentials, cost cap, and safe limits are configured.
Start with staged expansion:
- 3 to 5 imported books, or
- 200 to 1000 sections.

If staged run passes and cost/time are acceptable, optionally continue larger batches.
If full run is unsafe/too expensive, mark PARTIAL and document exact remaining work.

Tasks:
1. Implement/verify resumable runner supports:
- --book-limit
- --section-limit
- --tier
- --category
- --resume
- --retry-failed-only
- --force
- --dry-run
- --max-cost
- --stop-on-cost-limit
- pause/resume/stop control file

2. ReferenceSection coverage:
For every processed imported ReferenceBook:
- create stable ReferenceSection rows
- preserve sourceBookId/sourceSectionId/sectionHash/chapterTitle/sectionIndex/tokenEstimate
- status reflects pending/success/skipped/error
- ambiguous file matching becomes manual_required
- do not mix books
- do not merge raw text across books

3. DeepSeek V4 Pro SectionAnalysis:
For processed sections, create ReferenceSectionAnalysis with:
- sourceBookId
- sourceSectionId
- sectionHash
- Chinese structured analysis JSON
- modelProvider/modelName
- promptTokens/completionTokens/estimatedCost
- analysisHash
- status success/fallback/error
- rawTextIncluded=false

Fallback:
- allowed for recovery only
- status=fallback
- production_quality=false
- never counted as PASS evidence

4. Production-ready ReferencePatternCards:
Generate cards only from successful non-fallback analyses.
Each must have:
- Chinese name/function
- category
- conditions/beats/pitfalls/bestUse/originalityNotes
- sourceBookId
- sourceBookIds non-empty
- sourceSectionIds non-empty
- sourceAnalysisIds non-empty
- evidenceCount
- extractionModelProvider/name
- outputHash
- rawTextIncluded=false
- production_quality=true

5. Dedupe/merge:
Merge by:
- category
- function
- beat structure
- use case
- tags

Merged cards preserve:
- sourceCardIds
- sourceBookIds
- sourceSectionIds
- sourceAnalysisIds
- evidenceCount

6. ReferenceMechanismProfile:
Build/rebuild Chinese profile from cumulative deduped production-ready cards.
raw_json must include:
- generationStatus
- profileVersion
- provider
- model
- buildMode
- sourceBookIds
- sourceBookCount
- sourceCardCount
- sourceCardIdsSample
- sourceSectionIdsSample
- generatedAt
- rawTextIncluded=false
- categoriesCovered
- evidenceCounts
- productionReadyCardCount
- oldSourceLessCardsExcluded=true

Profile must be Chinese and mechanism-rich.
Must not contain:
- Pleasure Pattern
- reader reward rhythm
- case-to-arc escalation
- institutional obstacle

If profile is built locally due API/cost/concurrency limits:
- mark generationStatus=local_summary
- include reason
- do not call it full production profile unless acceptance allows it

Tests:
- staged batch creates ReferenceSection rows for multiple books
- staged batch creates successful analyses when credentials available
- retry-failed-only does not reprocess successes
- resume does not duplicate sections/cards
- cost cap stops safely
- fallback analyses excluded from production-ready counts
- old source-less cards excluded
- production cards preserve sourceBookIds/sourceSectionIds/sourceAnalysisIds
- profile excludes English placeholder labels
- profile has required source metadata
- no raw reference text enters generation prompts

Create/update:
- docs/epic_novel/GATE_2B_REFERENCE_FULL_RUN_REPORT.md
- docs/epic_novel/REFERENCE_INTELLIGENCE_EVIDENCE.md
- docs/epic_novel/PRODUCTION_READINESS_RECHECK.md
- docs/epic_novel/IMPLEMENTATION_STATUS.md
- docs/epic_novel/GATE_ACCEPTANCE_MATRIX.md

PASS criteria:
- more than Gate 2A proof batch processed
- multiple books or meaningfully larger section batch processed
- DeepSeek success analyses > Gate 2A baseline
- production-ready cards > Gate 2A baseline
- cumulative profile rebuilt from production-ready cards
- sourceBookIds/sourceSectionIds/sourceAnalysisIds preserved
- fallback not counted
- old source-less cards excluded
- cost/resume/retry controls tested
- no raw reference text enters prompts

If full 57-book processing is not completed:
- do not mark full Reference Intelligence PASS
- mark Gate 2B PARTIAL
- document exact remaining books/sections/cost/time/command