# Reference Intelligence 证据复核

Updated: 2026-06-12

Gate 0 verdict: `PARTIAL_IMPLEMENTATION`

Reference Intelligence production status: PARTIAL

Gate 2A bounded proof status: `PASS` on 2026-06-12.

Gate 2B staged expansion status: `PARTIAL` on 2026-06-12.

Important boundary: Gate 2A proves the section-aware Reference Intelligence pipeline on a bounded proof batch only. It does not mean the full 57 imported books, all 100 listed books, the full Master Bible, 30 serial chapters, ECS deployment, or 300万字 production readiness are complete.

## Gate 2A Bounded Proof Update

Final proof command:

```powershell
$env:PYTHONPATH='backend'; python scripts/epic_gate2a_reference_proof.py --book-limit 1 --section-limit 5 --force
```

Final proof report:

```text
E:\video-plan\.local\epic_novel_runs\gate2a_reference_intelligence_2026-06-12T132502_524973_0000.json
```

Final verdict: `PASS`.

Live proof metrics:

| Metric | Result |
| --- | ---: |
| selected books | 1 |
| proof sections | 5 |
| DeepSeek V4 Pro successful analyses | 5 |
| fallback analyses | 0 |
| error analyses | 0 |
| production-ready cards after dedupe | 18 |
| sourceBookIds preserved | 1 |
| sourceSectionIds preserved | 5 |
| sourceAnalysisIds preserved | 5 |
| profile id | `b1025f7b-d405-4077-8632-d6adf94e3732` |
| profile sourceBookCount | 1 |
| profile sourceCardCount | 18 |
| profile rawTextIncluded | false |
| profile Chinese check | true |

Post-run live DB evidence:

| Metric | Result |
| --- | ---: |
| `ReferenceSection` | 5 |
| `ReferenceSectionAnalysis` | 5 |
| `ReferenceSectionAnalysis.status=success` | 5 |
| `ReferenceSectionAnalysis.status=fallback` | 0 |
| `ReferenceSectionAnalysis.status=error` | 0 |
| `ReferencePatternCard` total | 98,599 |
| old cards with empty `source_section_ids` | 98,581 |
| production-ready cards | 18 |

Interpretation:

- The old 98,581 cards remain historical/non-production evidence because they lack `source_section_ids`.
- Production-ready counts now exclude old cards and require non-empty `source_section_ids`, `source_analysis_ids`, `source_book_ids`, `rawTextIncluded=false`, and `production_quality=true`.
- Fallback analyses are still allowed for resumability but are `production_quality=false` and are not counted as PASS evidence.
- Raw reference text remains limited to ingestion/section abstraction; generation prompts still use only cards/profile/summaries.

## Gate 2B Staged Expansion Update

真实 staged run:

```powershell
$env:PYTHONPATH='backend'; python scripts/epic_gate2b_reference_staged_run.py --book-limit 3 --sections-per-book 5 --section-limit 15 --max-cost 0.05 --stop-on-cost-limit --max-runtime-seconds 1500 --skip-full-estimate
```

报告:

```text
E:\video-plan\.local\epic_novel_runs\gate2b_reference_staged_2026-06-12T142226_539531_0000.json
```

Gate 2B verdict: `PARTIAL`.

| 指标 | Gate 2A 后 | Gate 2B 后 |
| --- | ---: | ---: |
| 生产级覆盖书数 | 1 | 3 |
| `ReferenceSection` | 5 | 15 |
| `ReferenceSectionAnalysis` | 5 | 15 |
| DeepSeek 成功分析 | 5 | 15 |
| fallback / error | 0 / 0 | 0 / 0 |
| 生产级卡片 | 18 | 36 |
| 生产级来源 section | 5 | 15 |
| 生产级来源 analysis | 5 | 15 |
| 旧无来源卡片 | 98,581 | 98,581，继续排除 |

最新 profile:

| 字段 | 结果 |
| --- | --- |
| id | `f376caf3-1a3b-4694-b78b-90c9bbafc96b` |
| generationStatus | `local_summary` |
| buildMode | `production_ready_cards` |
| sourceBookCount | 3 |
| sourceCardCount | 36 |
| productionReadyCardCount | 36 |
| oldSourceLessCardsExcluded | true |
| rawTextIncluded | false |
| profileVersion | `83fa8d6a8e570d48ce3b3d3a786535731f05a3837226b7f8c86381ba0bf23bd8` |

成本和续跑证据:

- dry-run 估算报告: `.local/epic_novel_runs/gate2b_reference_staged_2026-06-12T142024_203143_0000.json`
- retry-failed-only dry-run 报告: `.local/epic_novel_runs/gate2b_reference_staged_2026-06-12T143247_517259_0000.json`
- cost-limit dry-run 报告: `.local/epic_novel_runs/gate2b_reference_staged_2026-06-12T143259_386938_0000.json`

结论:

- Gate 2B 已证明多书 staged expansion，但未完成 57 本全量。
- Reference Intelligence production status 从 `FAIL` 提升为 `PARTIAL`。
- 不允许把 36 张生产级卡片称为全语料机制画像。

## Live DB 摘要

| 指标 | 结果 |
| --- | --- |
| `ReferenceBooklistItem` | 100 |
| 书单状态 | imported 53, downloaded 2, pending 45 |
| `ReferenceDownloadTask` | 100 |
| 下载任务状态 | imported 53, downloaded 2, downloading 1, found 1, not_found 43 |
| `ReferenceBook` | 57 indexed |
| 按 tier | tier1=17, tier2=4, tier3=7, tier4=29 |
| 按 category | core_similarity=17, case_mystery=4, court_power=7, longform_xianxia=25, metadata_only=4 |
| `ReferenceSection` | 15 |
| `ReferenceSectionAnalysis` | 15 |
| `ReferencePatternCard` | 98,617 |
| distinct source books in cards | 57 |
| raw_text_included=true | 0 |
| source_section_ids 为空 | 98,581 old cards excluded from production-ready counts |
| production-ready cards | 36 |
| evidence_count | production-ready min=1, max=1 |
| `ReferenceMechanismProfile` | 15 |

## 卡片分类分布

| category | count |
| --- | ---: |
| pleasure | 7,223 |
| plot_structure | 7,217 |
| worldbuilding | 7,011 |
| bureaucracy | 7,018 |
| faction | 7,016 |
| map | 7,014 |
| anti_cliche | 7,007 |
| longform_pacing | 7,003 |
| power_system | 7,021 |
| foreshadow | 7,030 |
| villain_arc | 7,034 |
| volume_structure | 7,025 |
| character_arc | 6,981 |
| heroine_arc | 6,981 |

数量分布看起来完整，但证据质量不足：所有卡片都缺少 `source_section_ids`，所有卡片 `evidence_count=1`，无法证明多书、多章节、可追溯的机制归纳。

## 样本问题

只读抽样的最早卡片显示：

- `name=Pleasure Pattern 1`
- `function=reader reward rhythm`
- `description=Abstract mechanism inferred from a low-density section titled 'opening'. Use only the structural function...`
- `sourceSectionIds=[]`
- `rawJsonKeys=["abstractOnly","sectionTitle","sourceBookId"]`

该样本安全地避免了 raw text，但不是 SPEC 要求的高质量中文抽象卡。它缺少章节 hash、section id、LLM/本地分析状态、证据来源、可复核机制细节。

## Profile 证据

最新 profile：

| 字段 | 结果 |
| --- | --- |
| id | `7c0b7bac-61dc-4ffc-9502-5cefb39e5ab5` |
| project_id | null |
| sourceBookCount | 57 |
| sourceCardCount | 98,581 |
| raw_json keys | `categories`, `abstractOnly` |
| generationStatus | null |
| rawTextIncluded | null |
| sourceBookIdsCount | 0 |
| sourceCardIdsSampleCount | 0 |
| profileVersion | null |
| firstCorePleasure | `Pleasure Pattern 1` |

这不能证明 `ReferenceMechanismProfile` 是由全量卡片经过 DeepSeek/GPT 深度综合产生，也不能支持“全 100 本语料机制画像已完成”的状态声明。

## 模型日志证据

历史 reference 相关日志只有：

- `reference_pattern_extract:success`
- `reference_mechanism_profile:success`

样本中 `reference_mechanism_profile` 的 prompt/completion token 只有约 10/6，说明该日志更像聚合记录，不是一次真实携带大量抽象卡片的 LLM profile 生成证据。live DB 未见 `reference_section_analysis`/`reference_section_analysis_fallback` 大规模记录。

## 安全结论

PASS:

- 当前卡片、trace、Bible JSON 中未发现 raw reference text 直接进入 generation prompt 的 live 标记。
- `raw_text_included=true` 计数为 0。

FAIL:

- 未证明逐章节抽象。
- 未证明 LLM section analysis。
- 未证明 profile 有完整来源追溯。
- 未证明 100 本全量使用。
- 未证明卡片质量足以支撑生产级长篇规划/章节生成。

## 修复验收线

Reference Intelligence 重新判 PASS 至少需要：

1. 57 本已导入书全部生成 `ReferenceSection` 和 `ReferenceSectionAnalysis`。
2. 每张新卡必须有 `source_section_ids`、`section_hash`、`analysisHash`、`evidence_count`、`extraction_model_provider/name/status`。
3. Profile raw_json 必须记录 `generationStatus`、`sourceBookIds`、`sourceCardIdsSample`、`sourceCardCount`、`profileVersion`、provider/model。
4. Profile 内容必须为中文机制层总结，不得保留 `Pleasure Pattern` / `reader reward rhythm` 这类英文泛化占位。
5. 对 43 个 not_found、45 个 pending、downloaded/found/downloading 项给出明确 skipped/manual/import evidence。
