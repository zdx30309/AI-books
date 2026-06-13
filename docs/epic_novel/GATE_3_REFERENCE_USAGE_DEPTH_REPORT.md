# Gate 3 参考机制使用深度报告

日期：2026-06-12

Gate 3 verdict：`PASS`

结论边界：Gate 3 只证明 ReferencePatternCards 与 ReferenceMechanismProfile 已经接入必需生成层、QA 和修复入口，并且 trace 字段完整；它不代表 Gate 4-14、全量 57 本参考抽取、30 章连续性或 300 万字生产就绪已经完成。

## 覆盖模块

| 模块 | Trace taskType | 结果 |
| --- | --- | --- |
| Genesis 世界观竞选 | `genesis_worldview_tournament` | PASS |
| WorldBible | `world_bible` | PASS |
| WorldAtlas | `world_atlas` | PASS |
| BureaucracySystem | `bureaucracy_system` | PASS |
| PowerSystem | `power_system` | PASS |
| EnsembleCast | `ensemble_cast` | PASS |
| GlobalSpine | `global_spine` | PASS |
| Master Bible manifest | `master_bible_manifest` | PASS |
| Master Bible section | `master_bible_section` | PASS |
| VolumeContracts | `volume_contracts` | PASS |
| ChapterContracts | `chapter_contract` | PASS |
| Chapter ContextPack | `chapter_context_pack` | PASS |
| Bible Coverage CI | `qa_bible_coverage_ci` | PASS |
| Outline CI | `qa_outline_ci` | PASS |
| Chapter check | `qa_chapter_check` | PASS |
| Planned-vs-Actual Diff | `qa_planned_vs_actual_diff` | PASS |
| Acceptance Gate | `qa_acceptance_gate` | PASS |
| Outline repair | `repair_outline` | PASS |
| Chapter repair entry | `repair_chapter_entry` | PASS |

## 代码修复

- `backend/app/services/epic_reference.py`
  - `auto_retrieval_plan()` 默认只检索 production-ready `ReferencePatternCard`。
  - 旧 source-less 卡只进入 `oldSourceLessCardsExcluded` 统计，不再进入生成上下文。
  - `EpicRetrievalTrace.raw_json` 记录 `productionReadyOnly=true`、`usedCardIds`、`profileVersion`、`excludedOrOverflow`、`rawTextIncluded=false`。

- `backend/app/services/epic_novel.py`
  - 新增统一 `_reference_usage_pack()`，通过 Dynamic Context Compiler 编译参考包。
  - Genesis、WorldBible、WorldAtlas、Bureaucracy、PowerSystem、EnsembleCast、GlobalSpine、Master Bible、VolumeContracts、ChapterContracts、ContextPack、QA、Repair 全部写入或附着 reference usage evidence。
  - 修正 ContextPack trace 的 overflow 字段，使用 `metadata.overflow`，不再读取不存在的 `overflowBlocks`。
  - 章节修复补丁输出改为中文。

- `scripts/epic_gate3_reference_usage_depth.py`
  - 新增 Gate 3 live smoke。
  - 每个步骤独立 DB session，并对 `OperationalError` 做一次重试。
  - 输出步骤耗时、trace 检查、缺失任务、失败任务和生产卡证据。

## 运行命令

```powershell
$env:PYTHONPATH='backend'; python -m compileall backend/app/services/epic_novel.py backend/app/services/epic_reference.py scripts/epic_gate3_reference_usage_depth.py
$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_reference.py backend/tests/test_epic_context.py backend/tests/test_epic_chinese_output.py -q
$env:PYTHONPATH='backend'; python scripts/epic_gate3_reference_usage_depth.py --chapter 4 --no-execute
```

## 运行结果

| 检查 | 结果 |
| --- | --- |
| compileall | PASS |
| pytest | `25 passed in 2.15s` |
| Gate 3 smoke | PASS |
| 输出文件 | `.local/epic_novel_runs/gate3_reference_usage_depth_2026-06-12T152527_937305Z.json` |
| 项目 | `44ce72be-a423-4f3b-895b-ad8a0fbb5e63` |
| 新增 RetrievalTrace | 69 |
| 缺失 taskType | 0 |
| 失败 taskType | 0 |
| production-ready cards | 36 |
| old source-less cards excluded | 98,581 |

## Live DB 证据

章 4 的 QA trace：

| taskType | traceId | created_at | chapter | usedCardCount |
| --- | --- | --- | --- | --- |
| `qa_planned_vs_actual_diff` | `23ca7c09-6ffe-436a-a65a-baf623d64387` | `2026-06-12 15:07:01.532603+00:00` | 4 | 36 |
| `qa_acceptance_gate` | `4eae2d62-2703-4cd6-b0c0-85584ead22a2` | `2026-06-12 15:07:04.447036+00:00` | 4 | 36 |

章 4 状态边界：

| 项 | 结果 |
| --- | --- |
| chapter 4 status | `accepted` |
| summary / endingHook | present |
| acceptedContent | present |
| draftContent | present |
| repairedContent | absent |
| EpicStateExtract | 1 |
| EpicCanonFact | 1 |
| EpicCharacterState | 1 |
| EpicPowerLedger | 1 |
| EpicObjectState | 1 |
| EpicFactionState | 1 |
| EpicInstitutionState | 1 |
| canon source | `acceptedContent` only |

## Gate 3 判定

`PASS`

原因：

- 每个必需生成层、QA 层、修复入口均有 `EpicRetrievalTrace`。
- 每条必需 trace 均包含非空 `usedCardIds`、`usedProfileSections`、`profileVersion`、`usageSummary`。
- `rawTextIncluded=false`。
- `productionReadyOnly=true`。
- 旧 source-less 卡被排除，不计为生产卡。
- ContextPack 记录 included / overflow 元数据。

下一门禁：Gate 4 `REAL_CHINESE_MASTER_BIBLE`。
