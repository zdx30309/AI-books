# Gate 2A: Reference Intelligence Bounded Proof

Status:
DONE / PASS if docs/epic_novel/GATE_2A_REFERENCE_INTELLIGENCE_RUN_REPORT.md exists and proves:
- 1 imported book / 5 sections or larger proof batch
- DeepSeek V4 Pro success analyses > 0
- fallback analyses = 0 or excluded
- production-ready cards with sourceSectionIds > 0
- Chinese traceable profile exists

Do not rerun expensive proof extraction if this evidence already exists and is valid.

Goal:
Implement and prove a section-aware Reference Intelligence pipeline on a bounded proof batch.

Read first:
- AGENTS.md
- docs/epic_novel/ADVERSARIAL_AUDIT_REPORT.md
- docs/epic_novel/REFERENCE_INTELLIGENCE_EVIDENCE.md
- docs/epic_novel/IMPLEMENTATION_STATUS.md
- docs/epic_novel/EPIC_NOVEL_ENGINE_FULL_SPEC.md
- docs/epic_novel/09_ACCEPTANCE_CRITERIA.md

Scope:
Small bounded proof only.

Out of scope:
- full 57-book extraction
- frontend
- Master Bible quality
- chapter generation
- 30-chapter continuity
- 300W readiness

Tasks:
1. Create ReferenceSection rows for imported ReferenceBook rows.
Each ReferenceSection must include:
- sourceBookId
- sourceSectionId
- chapterTitle
- sectionIndex
- sectionHash
- charCount
- tokenEstimate
- status
- stable identity by book hash + section hash

2. Use DeepSeek V4 Pro for proof-batch ReferenceSectionAnalysis.

Config:
EPIC_REFERENCE_MODEL_PROVIDER=deepseek
EPIC_REFERENCE_MODEL_NAME=deepseek-v4-pro
EPIC_REFERENCE_ENABLE_LLM_EXTRACTION=true
EPIC_REFERENCE_EXTRACTION_LANGUAGE=zh-CN

Each analysis must contain Chinese structured JSON:
- sectionSummary
- plotFunction
- pleasureFunction
- characterArcMove
- heroineAgencyMove
- mysteryClueForeshadowActions
- bureaucracyCourtFactionFunction
- powerSystemMove
- pacingRole
- antiClicheNotes
- reusableMechanisms
- notToCopyNotes

Also store:
- sourceBookId
- sourceSectionId
- sectionHash
- modelProvider
- modelName
- promptTokens
- completionTokens
- estimatedCost
- analysisHash
- status success/fallback/error
- rawTextIncluded=false

3. Generate new ReferencePatternCards from successful SectionAnalysis, not raw chunks.

Cards must include:
- Chinese name/function
- category
- conditions
- beats
- pitfalls
- bestUse
- originalityNotes
- sourceBookId
- non-empty sourceSectionIds
- sourceAnalysisIds
- evidenceCount
- extractionModelProvider/name
- outputHash
- rawTextIncluded=false
- production_quality=true only if non-fallback

4. Dedupe/merge cards.
Merged cards must preserve:
- sourceCardIds
- sourceBookIds
- sourceSectionIds
- sourceAnalysisIds
- evidenceCount

5. Build Chinese ReferenceMechanismProfile from deduped production-ready cards.

Profile raw_json must include:
- generationStatus
- profileVersion
- provider
- model
- sourceBookIds
- sourceBookCount
- sourceCardCount
- sourceCardIdsSample
- sourceSectionIdsSample
- generatedAt
- rawTextIncluded=false
- categoriesCovered
- evidenceCounts

6. Run bounded proof batch:
- at least 1 imported book
- preferably 3 books if cost/time acceptable
- or max 50-200 sections

If DeepSeek credentials are unavailable:
- do not fake PASS
- mark UNKNOWN_NEEDS_EVIDENCE
- document missing env/config

Tests:
- ReferenceSection rows created
- SectionAnalysis rows created
- DeepSeek success analyses > 0 if credentials available
- cards have sourceSectionIds
- cards are Chinese
- old source-less cards excluded from production-ready counts
- profile has required raw_json trace fields
- no raw text enters generation prompts

Create/update:
- docs/epic_novel/GATE_2A_REFERENCE_INTELLIGENCE_RUN_REPORT.md
- docs/epic_novel/REFERENCE_INTELLIGENCE_EVIDENCE.md
- docs/epic_novel/IMPLEMENTATION_STATUS.md
- docs/epic_novel/PRODUCTION_READINESS_RECHECK.md

PASS criteria:
- ReferenceSection > 0
- ReferenceSectionAnalysis > 0
- DeepSeek success analyses > 0
- production-ready cards with sourceSectionIds > 0
- Chinese traceable profile exists
- fallback not counted as production-ready