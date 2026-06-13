# Gate 3: Reference Usage Depth

Status:
DONE / PASS on 2026-06-12.

Evidence:
- `docs/epic_novel/GATE_3_REFERENCE_USAGE_DEPTH_REPORT.md`
- `.local/epic_novel_runs/gate3_reference_usage_depth_2026-06-12T152527_937305Z.json`

Goal:
Prove ReferencePatternCards and ReferenceMechanismProfile are actually consumed by all required generation layers.

Read first:
- AGENTS.md
- docs/epic_novel/GATE_2B_REFERENCE_FULL_RUN_REPORT.md
- docs/epic_novel/REFERENCE_INTELLIGENCE_EVIDENCE.md
- docs/epic_novel/EPIC_NOVEL_ENGINE_FULL_SPEC.md
- docs/epic_novel/09_ACCEPTANCE_CRITERIA.md

Scope:
Reference usage wiring, retrieval trace, prompt inclusion safety, layer-by-layer proof.

Out of scope:
- full novel generation
- 30-chapter continuity
- frontend expansion
- export rewrite

Tasks:
For each layer, verify and fix usage of ReferencePatternCards and ReferenceMechanismProfile:

- Genesis
- WorldBible
- WorldAtlas
- Bureaucracy
- PowerSystem
- EnsembleCast
- GlobalSpine
- Master Bible
- VolumeContracts
- ChapterContracts
- Chapter execution
- QA
- Repair

Each generation task must record RetrievalTrace:
- taskType
- projectId
- retrievedCardIds
- usedCardIds
- usedProfileSections
- profileVersion
- usageSummary
- why used
- excluded/overflow
- rawTextIncluded=false

Requirements:
1. Old source-less cards cannot count as production cards.
2. Every layer must use production-ready cards/profile, not raw chunks.
3. RetrievalTrace must prove what was used.
4. ContextPack must record included/excluded/overflow metadata.
5. If a layer cannot use cards/profile, mark not_used with blocker.

Tests:
- layer fails if no cards/profile requested when required
- RetrievalTrace missing fails
- usedCardIds empty fails for layers that require reference use
- old source-less cards are excluded
- raw reference text entering prompt fails
- profileVersion missing fails
- usageSummary missing fails

Create/update:
- docs/epic_novel/GATE_3_REFERENCE_USAGE_DEPTH_REPORT.md
- docs/epic_novel/SPEC_TRACEABILITY_MATRIX.md
- docs/epic_novel/GATE_ACCEPTANCE_MATRIX.md
- docs/epic_novel/IMPLEMENTATION_STATUS.md
- docs/epic_novel/PRODUCTION_READINESS_RECHECK.md

PASS criteria:
- Every required generation layer is marked used with exact file/function/table/log evidence.
- No required layer is shallow/fake/unknown.
- RetrievalTrace proves cards/profile usage.
- No raw reference text enters prompts.
