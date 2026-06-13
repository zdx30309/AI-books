# Gate 12: Large Export System

Status:
DONE / PASS on 2026-06-13.

Goal:
Ensure large outputs are exported as files/object metadata, not giant API responses.

Read first:
- AGENTS.md
- docs/epic_novel/GATE_11_SCHEDULER_REPORT.md
- docs/epic_novel/IMPLEMENTATION_STATUS.md
- docs/epic_novel/EPIC_NOVEL_ENGINE_FULL_SPEC.md
- docs/epic_novel/09_ACCEPTANCE_CRITERIA.md

Scope:
Large export safety, metadata response, checksum, long manuscript readiness.

Out of scope:
- docx unless explicitly supported
- frontend polish
- actual 300W final manuscript generation

Tasks:
1. Export:
   - Master Bible markdown/json
   - novel markdown
   - volume markdown
   - chapter bundle
   - QA reports
   - context/retrieval traces
   - acceptance reports
2. Default API response must return metadata:
   - file path/object id
   - size
   - checksum/hash
   - createdAt
   - download route
3. Large content should not be returned in giant JSON/body by default.
4. Export must support long manuscripts safely.
5. Existing download endpoints must be clearly separated from metadata endpoints.

Tests:
- large export writes file
- metadata response default
- checksum exists
- export can be resumed/regenerated
- no timeout for large content path
- old download endpoint separated from metadata endpoint
- export files contain expected sections/chapters
- raw reference text not included in public exports

Create/update:
- docs/epic_novel/EXPORT_SYSTEM_REPORT.md
- docs/epic_novel/GATE_12_EXPORT_REPORT.md
- docs/epic_novel/IMPLEMENTATION_STATUS.md
- docs/epic_novel/PRODUCTION_READINESS_RECHECK.md

PASS criteria:
- Large outputs use file/object export safely.
- API does not default to giant content body.
- Exported artifacts are traceable and checksummed.

Result:
- PASS report: `docs/epic_novel/GATE_12_EXPORT_REPORT.md`
- Export report: `docs/epic_novel/EXPORT_SYSTEM_REPORT.md`
- Runtime evidence: `.local/epic_novel_runs/gate12_export_2026-06-12T224306_008134Z.json`
- Markdown evidence: `.local/epic_novel_runs/export_system_2026-06-12T224306_006655Z.md`
- Targeted tests: `backend/tests/test_epic_context.py` -> 9 passed
