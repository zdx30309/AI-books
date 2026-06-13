# Gate 1: Code Quality, De-hardcoding, Chinese Output, Fallback Isolation

Status:
PENDING unless CODE_QUALITY_AUDIT.md and FALLBACK_AND_FIXTURE_INVENTORY.md prove this gate is already satisfied.

Goal:
Fix hardcoded production story content, English user-facing output, fallback misuse, and monolithic service risk.

Read first:
- AGENTS.md
- docs/epic_novel/ADVERSARIAL_AUDIT_REPORT.md
- docs/epic_novel/FALLBACK_AND_FIXTURE_INVENTORY.md
- docs/epic_novel/IMPLEMENTATION_STATUS.md
- docs/epic_novel/EPIC_NOVEL_ENGINE_FULL_SPEC.md

Scope:
This gate focuses only on code quality, de-hardcoding, Chinese output, fallback isolation, and prompt/service layering.

Out of scope:
- full reference extraction
- Master Bible quality rewrite
- 30-chapter run
- frontend feature expansion
- 300W readiness claims

Tasks:
1. Identify all hardcoded production story payloads:
   - fixed kingdoms
   - characters
   - institutions
   - maps
   - power systems
   - plot payloads
   - English titles
   - English fallback drafts
   - fixed Bible payloads

2. Move legitimate test fixtures to:
   - docs/epic_novel/10_TEST_PROJECT.md
   - dedicated test fixture files
   - negative tests

3. Production generation must derive from:
   - project premise
   - genre
   - ReferenceMechanismProfile
   - ReferencePatternCards
   - prompt templates
   - configured model output
   - acceptance gates

4. Enforce Simplified Chinese user-facing output:
   - WorldBible
   - WorldAtlas
   - PowerSystem
   - EnsembleCast
   - GlobalSpine
   - VolumeContracts
   - ChapterContracts
   - Bible sections
   - chapter drafts
   - QA reports
   - frontend Epic labels

5. Fallback separation:
   - deterministic fallback may remain only for smoke/dev
   - fallback must have status=fallback or placeholder
   - production_quality=false
   - smoke_validated=true only means route/export works
   - fallback can never count as production-quality Bible/chapter/profile/card

6. Prompt/service layering:
   - move production prompts out of business logic where safe
   - use prompt template files or isolated prompt builders
   - service layer should assemble structured context, not contain huge prompt bodies
   - do not break routers/imports/API contracts

Tests:
- no English boilerplate in user-facing Bible/outline/chapter output
- fallback output is Chinese
- fallback is labeled and production_quality=false
- production services do not contain fixed smoke-story names
- hardcoded fixture names exist only in fixture/docs/tests
- existing short-form generation still works
- existing 长篇小说 workspace still works
- python compile passes
- router import smoke passes

Create/update:
- docs/epic_novel/CODE_QUALITY_AUDIT.md
- docs/epic_novel/FALLBACK_AND_FIXTURE_INVENTORY.md
- docs/epic_novel/IMPLEMENTATION_STATUS.md
- docs/epic_novel/PRODUCTION_READINESS_RECHECK.md

PASS criteria:
- No hardcoded fixture may be counted as production output.
- No fallback may be counted as production_quality.
- All user-facing production outputs must be Simplified Chinese.
- Prompt bodies are isolated from generic business service logic where safe.