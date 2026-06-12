# Three Million Word Production Runbook

Updated: 2026-06-12

## Purpose

This runbook describes the safe path from reference corpus to longform production. The current worktree passed strict acceptance on project `44ce72be-a423-4f3b-895b-ad8a0fbb5e63`; rerun the same gates after any future code change.

## 1. Preflight

```powershell
$env:PYTHONPATH='backend'
python -m compileall backend/app scripts
python -m pytest backend/tests/test_epic_reference.py backend/tests/test_epic_context.py backend/tests/test_epic_chinese_output.py -q
Push-Location frontend
npm.cmd run build
Pop-Location
```

## 2. Reference Cost Estimate

```powershell
$env:PYTHONPATH='backend'
python -u scripts/epic_ingest_reference_books.py --dry-run-cost --disable-llm-extraction
```

Current dry-run aggregate:

- 57 local imported books
- 106,914 estimated sections
- 185,567,295 chars
- 86,582,220 estimated prompt tokens
- 1,444 estimated batches at 60,000 token budget
- `deepseek/deepseek-v4-pro`

## 3. Reference Extraction Controls

Pause:

```powershell
$env:PYTHONPATH='backend'
python -u scripts/epic_ingest_reference_books.py --pause
```

Resume:

```powershell
$env:PYTHONPATH='backend'
python -u scripts/epic_ingest_reference_books.py --resume
```

Stop:

```powershell
$env:PYTHONPATH='backend'
python -u scripts/epic_ingest_reference_books.py --stop
```

Run extraction:

```powershell
$env:PYTHONPATH='backend'
python -u scripts/epic_ingest_reference_books.py
```

Use `--force-reingest` only when intentionally rebuilding cached analyses. Use `--disable-llm-extraction` only for smoke/dev.

## 4. Strict Acceptance

Fresh run:

```powershell
$env:PYTHONPATH='backend'
python -u scripts/epic_acceptance_smoke.py
```

Resume current passing project:

```powershell
$env:PYTHONPATH='backend'
$env:EPIC_ACCEPTANCE_PROJECT_ID='44ce72be-a423-4f3b-895b-ad8a0fbb5e63'
python -u scripts/epic_acceptance_smoke.py
```

Pass requires:

- no fallback/placeholder Bible section
- Bible coverage complete
- chapters 1-3 accepted
- previous-state readiness for chapters 2/3
- retrieval traces and acceptance reports present
- no raw reference text in traces/context
- `nonAcceptedCanonSources=0`

Current passing log:

`.local/epic_novel_runs/acceptance_smoke_strict_2026-06-12-resume4.log`

## 5. Production Generation

Only after strict acceptance passes:

1. Create the production project.
2. Build or select a complete accepted Master Bible.
3. Generate volume contracts in order.
4. Compile chapter contracts in order.
5. Execute chapters strictly serially.
6. Stop on any high/severe issue.
7. Never persist draft/repaired content into canon.
8. Persist state only from accepted content.
9. Export Bible/novel as files or object-storage artifacts.

## 6. Deployment

Only after strict acceptance passes:

1. Commit current worktree.
2. Push to GitHub.
3. Deploy Beijing ECS from that commit.
4. Verify:
   - backend `/health`
   - system health
   - router import
   - DB model/schema availability
   - raw corpus files under ECS `.local/novel_ref_corpus/raw`
   - frontend route loads
   - strict smoke or a documented production smoke can run on ECS.
