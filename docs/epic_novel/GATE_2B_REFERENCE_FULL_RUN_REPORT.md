# Gate 2B 参考智能分阶段扩展报告

Updated: 2026-06-12

Gate 2B verdict: `PARTIAL`

结论边界：本轮完成了 Gate 2B 要求的“分阶段扩展”，但没有完成 57 本已导入书的全量 DeepSeek 分节抽取，因此不能标记为 full Reference Intelligence PASS，也不能声称 100 本书或 300万字生产就绪。

## 本轮命令

dry-run 估算：

```powershell
$env:PYTHONPATH='backend'; python scripts/epic_gate2b_reference_staged_run.py --book-limit 3 --sections-per-book 5 --section-limit 15 --dry-run --max-cost 0.05 --stop-on-cost-limit
```

真实 staged run：

```powershell
$env:PYTHONPATH='backend'; python scripts/epic_gate2b_reference_staged_run.py --book-limit 3 --sections-per-book 5 --section-limit 15 --max-cost 0.05 --stop-on-cost-limit --max-runtime-seconds 1500 --skip-full-estimate
```

retry-only dry-run：

```powershell
$env:PYTHONPATH='backend'; python scripts/epic_gate2b_reference_staged_run.py --book-limit 3 --sections-per-book 5 --section-limit 15 --retry-failed-only --dry-run --skip-full-estimate
```

成本上限安全停止 dry-run：

```powershell
$env:PYTHONPATH='backend'; python scripts/epic_gate2b_reference_staged_run.py --book-limit 4 --sections-per-book 5 --section-limit 20 --dry-run --max-cost 0.000001 --stop-on-cost-limit --skip-full-estimate
```

## 运行报告文件

| 类型 | 路径 | 结果 |
| --- | --- | --- |
| dry-run 估算 | `.local/epic_novel_runs/gate2b_reference_staged_2026-06-12T142024_203143_0000.json` | `UNKNOWN_NEEDS_EVIDENCE`，仅估算不写库 |
| 真实 staged run | `.local/epic_novel_runs/gate2b_reference_staged_2026-06-12T142226_539531_0000.json` | `PARTIAL` |
| retry-only dry-run | `.local/epic_novel_runs/gate2b_reference_staged_2026-06-12T143247_517259_0000.json` | `UNKNOWN_NEEDS_EVIDENCE`，成功项不重跑 |
| 成本上限 dry-run | `.local/epic_novel_runs/gate2b_reference_staged_2026-06-12T143259_386938_0000.json` | `UNKNOWN_NEEDS_EVIDENCE`，`stopReason=cost_limit` |

## 分阶段扩展结果

| 指标 | Gate 2A 基线 | Gate 2B 后 |
| --- | ---: | ---: |
| 已处理参考书来源 | 1 | 3 |
| `ReferenceSection` | 5 | 15 |
| `ReferenceSectionAnalysis` | 5 | 15 |
| DeepSeek V4 Pro 成功分析 | 5 | 15 |
| fallback 分析 | 0 | 0 |
| error 分析 | 0 | 0 |
| 生产级 ReferencePatternCard | 18 | 36 |
| 生产级卡片来源书 | 1 | 3 |
| 生产级卡片来源 section | 5 | 15 |
| 生产级卡片来源 analysis | 5 | 15 |
| 旧无来源卡片 | 98,581 | 98,581，继续排除 |

真实 staged run 选择的书：

| 书名 | 状态 |
| --- | --- |
| `仙子且慢，我只想斩妖除魔` | Gate 2A 已成功，resume 跳过 5 个 section |
| `仙官有令` | 新处理 5 个 section，成功 5 |
| `公子别秀` | 新处理 5 个 section，成功 5 |

真实 staged run 关键指标：

| 指标 | 结果 |
| --- | ---: |
| 候选书 | 3 |
| 候选 section | 15 |
| 本轮新处理 section | 10 |
| 本轮成功分析 | 10 |
| 本轮 fallback | 0 |
| 本轮 error | 0 |
| 本轮新增生产级卡片 | 18 |
| prompt tokens | 6,852 |
| completion tokens | 5,765 |
| 估算成本 | 0.00257348 |
| 运行时上限 | 1500 秒 |
| 成本上限 | 0.05 |
| stopReason | null |

## 最新机制画像

| 字段 | 结果 |
| --- | --- |
| profileId | `f376caf3-1a3b-4694-b78b-90c9bbafc96b` |
| buildMode | `production_ready_cards` |
| generationStatus | `local_summary` |
| profileVersion | `83fa8d6a8e570d48ce3b3d3a786535731f05a3837226b7f8c86381ba0bf23bd8` |
| sourceBookCount | 3 |
| sourceCardCount | 36 |
| sourceSection sample count | 15 |
| categoriesCovered | `pleasure`, `plot_structure`, `villain_arc` |
| evidenceCounts | min=1, max=1, total=36, cardCount=36 |
| rawTextIncluded | false |
| productionReadyCardCount | 36 |
| oldSourceLessCardsExcluded | true |
| profileChinese | true |

说明：本轮 profile 使用生产级卡片本地汇总，`generationStatus=local_summary`。它可以作为分阶段扩展证据，但不能被称为“全语料生产级机制画像”。

## 成本、续跑、重试证据

- `resume=true`：已成功的 Gate 2A 5 个 section 被跳过，没有重复重跑。
- `retry-failed-only` dry-run：成功项没有重跑；当前无 fallback/error 可重试。
- `--max-cost 0.000001 --stop-on-cost-limit` dry-run：触发 `stopReason=cost_limit`，未写 DB，证明成本上限可以安全停止。
- pause/resume/stop 控制仍沿用 `.local/epic_novel_runs/reference_extraction_control.json`。

## 测试

```powershell
python -m compileall backend/app/services/epic_reference.py scripts/epic_gate2b_reference_staged_run.py backend/tests/test_epic_reference.py
$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_reference.py backend/tests/test_epic_context.py -q
git diff --check
```

结果：

- `backend/tests/test_epic_reference.py backend/tests/test_epic_context.py`: 22 passed.
- `git diff --check`: PASS。

## 剩余工作

全量仍未完成：

| 项目 | 剩余 |
| --- | --- |
| 已导入参考书 | 57 本总量，本轮生产级覆盖 3 本 |
| 全量 section 估算 | 106,914 |
| 当前成功分析 | 15 |
| 估算剩余 section | 约 106,899 |
| 100 本书单 | 仍有不可用/待下载项，按用户要求无法下载的跳过 |

下一步如继续 Gate 2B：

```powershell
$env:PYTHONPATH='backend'; python scripts/epic_gate2b_reference_staged_run.py --book-limit 5 --sections-per-book 40 --section-limit 200 --max-cost 0.05 --stop-on-cost-limit --max-runtime-seconds 3600
```

当前不继续盲跑 57 本全量，原因是 full dry-run 已显示约 106,914 个 section；按本轮 10 次 DeepSeek 调用约 9 分 30 秒估算，全量运行会非常长，必须分批和持续成本控制。

## Gate 2B 判定

`PARTIAL`

依据：

- 已满足“超过 Gate 2A proof batch”的 staged expansion。
- 已证明多书来源、更多 section、更多 DeepSeek 成功分析、更多生产级卡片、来源 ID 保留、fallback 不计生产、旧无来源卡片排除、成本/续跑/重试控制。
- 未完成 57 本全量抽取，因此不能 PASS full Reference Intelligence。
