# Epic Novel Engine Gate 0 对抗审计报告

Updated: 2026-06-12

Final verdict: `PARTIAL_IMPLEMENTATION`

## 审计边界

本轮是 Gate 0 只读对抗审计。已读取 `AGENTS.md`、`EPIC_NOVEL_ENGINE_FULL_SPEC.md`、`09_ACCEPTANCE_CRITERIA.md`、现有状态/矩阵/验收报告，并只读检查后端服务、API、前端、测试脚本、历史验收日志和 live DB 项目 `44ce72be-a423-4f3b-895b-ad8a0fbb5e63`。

本轮未修改生产代码、未新增功能、未修复实现。只更新审计文档和 `IMPLEMENTATION_STATUS.md`。

## 总结结论

历史 `scripts/epic_acceptance_smoke.py` 通过，证明当前系统具备一条可运行的 DB-backed smoke/流程链路：项目创建、24 个 Bible section、3 个 volume contract、10 个 chapter contract、前 3 章串行执行、ContextPack、RetrievalTrace、AcceptanceReport、acceptedContent 写入 state ledgers、markdown 导出。

但该证据不能证明生产就绪。当前 live DB 证据显示 Reference Intelligence 未达到 SPEC 要求的“全 100 本深度抽象机制画像”：100 条书单中只有 57 本导入；`ReferenceSection=0`、`ReferenceSectionAnalysis=0`；98,581 张卡全部缺少 `source_section_ids`，`evidence_count` 全为 1；最新 `ReferenceMechanismProfile.raw_json` 缺少 `generationStatus/sourceBookIds/sourceCardIdsSample/profileVersion` 等追溯字段，且样本卡仍是英文泛化模式名。

因此，当前状态不是 `PRODUCTION_READY`，也不能继续写成 “PASS FOR 09_ACCEPTANCE_CRITERIA”。更准确的判定是：生产模块有部分实现，流程 smoke 可跑，但生产验收、引用智能深度、长篇质量门和 300 万字级连续运行证据不足。

## Live DB 证据

| 项 | 只读查询结果 |
| --- | --- |
| 项目 | `44ce72be-a423-4f3b-895b-ad8a0fbb5e63` |
| 标题 | `acceptance smoke 2026-06-12 143900` |
| 目标规模 | `300000` 字 / 3 卷 / 10 章，不是 300 万字 / 12 卷 / 700 章 |
| 当前产出 | 15,018 字 / 当前第 3 章 |
| 书单 | 100 条 |
| 下载任务 | imported 53, downloaded 2, downloading 1, found 1, not_found 43 |
| 已导入书籍 | 57 本 |
| ReferenceSection | 0 |
| ReferenceSectionAnalysis | 0 |
| ReferencePatternCard | 98,581 张 |
| 卡片 raw text | `raw_text_included=true` 为 0 |
| 卡片来源章节 | `source_section_ids` 为空：98,581 张 |
| 卡片证据数 | min=1, max=1 |
| 最新 ReferenceMechanismProfile | sourceBookCount=57, sourceCardCount=98,581，但 raw_json 只有 `categories/abstractOnly` |
| Bible sections | 24 个 accepted，coverage 24 complete |
| Accepted chapters | 3 章，长度约 7,971 / 3,349 / 3,698 chars |
| Chapter contracts | 10 个，范围 1-10 |
| State ledgers | canon/source 均为 acceptedContent；各核心 state 表各 3 条左右 |
| RetrievalTrace | 19 条，raw_text_included=true 为 0，chapter_context_pack 有 overflow 记录 |
| Generation logs | 项目日志 213 条，success 206，error 7；error 均为 `bible_section_generate` 连接错误样本 |

## 模块审计矩阵

| 模块 | 状态 | 证据 | 主要缺口 |
| --- | --- | --- | --- |
| Model Router | PARTIAL | `epic_model_router.py` 支持 editor/author/cheap/reference 路由、日志、`max_output_tokens=0` 透传为 provider 无固定上限、continuation | `estimated_cost` 恒为 0；生产质量不由模型日志证明；项目日志存在 7 个 provider error |
| Dynamic Context Compiler | PASS | `epic_context.py` 记录 provider window、included/overflow、raw text 清洗；ContextPack trace 记录 overflow | 解决上下文安全，但不证明内容深度 |
| Storage/State | PARTIAL | 模型覆盖 canon/state/retrieval/report/export；acceptedContent 写 canon source 证据为 3 条 | 无 Alembic；状态抽取是模板化；未证明 30 章以上状态压力 |
| 100-book parser | PARTIAL | 书单 100 条存在 | 45 pending，43 not_found；未形成 100 本全量 profile |
| SoNovelAdapter | PARTIAL | 下载任务/导入/扫描工作流存在；本地路径有 57 文件证据 | 仍有 found/downloading/downloaded 未闭环；缺少本轮可复现 SoNovel 调用证据 |
| Download tasks | PARTIAL | 100 个任务，状态/路径/hash/card count 有记录 | 部分 imported 任务 `generated_card_count=0`；不是全量完成 |
| epub/txt import | PARTIAL | 57 本 indexed，`.local/novel_ref_corpus/raw` 和部署 zip 有证据 | live DB 无 ReferenceSection/Analysis 记录 |
| ReferencePatternCards | FAIL | 卡片总数 98,581，raw text false | 全部缺少 source_section_ids；evidence_count 全 1；卡片名/函数大量英文泛化，不能证明深度抽象 |
| ReferenceMechanismProfile | FAIL | 10 个 profile，最新 sourceBookCount=57/sourceCardCount=98,581 | 最新 profile 缺少 generationStatus/source ids/profileVersion；样本显示像本地粗聚合，不是全 100 本深度画像 |
| AutoRetrievalPlanner | PARTIAL | 19 条 trace，chapter_context_pack/chapter_contract 均有记录，raw text false | `usedCards=98581` 说明检索排序/裁剪证据不足；有效使用深度未证明 |
| WorldAtlas / WorldBible / Power / Bureaucracy | PARTIAL | 相关生成函数和 DB 记录存在，Bible 内容有中文模型产物 | 一些生成 prompt/兜底逻辑仍在业务服务内；深度 QA 不足 |
| MegaBible | PARTIAL | 24 sections accepted，coverage complete，导出 150,603 chars | acceptance 主要查长度/中文/占位符/meta；不等于 SPEC 级内容质量；项目规模是 30 万 smoke |
| Genesis | PARTIAL | worldview tournament、world bible 等函数存在 | tournament 深度与多候选质量未用强验收证明 |
| Planning | PARTIAL | 3 volumes、10 chapter contracts | 合同内容模板化，未到 30 章或 700 章级规划证明 |
| Execution | PARTIAL | 前 3 章串行，章 2/3 前章 state ready | 只跑 3 章；章节草稿与状态抽取仍有模板化路径 |
| QA / Acceptance | FAIL | AcceptanceReport 3 条，status accepted | `_acceptance` 只做空内容、raw source text 字符串、bureaucracy、mustExecute 关键词等浅检查；报告分数固定化风险高 |
| API | PARTIAL at Gate 0 baseline; Gate 12 later PASS | Epic routes 覆盖 reference、Bible、execution、dashboard、export；Gate 12 已复核大导出默认 metadata、显式下载路由分离 | Gate 13 仍需运行态前端/API 验证 |
| Frontend | SUPERSEDED_BY_GATE_13 | `EpicNovelWorkspace.tsx` 已在 Gate 13 做本地浏览器运行态复验 | Gate 13 本地通过；历史 ECS/UI 状态仍需 Gate 14 远端重验 |
| Dashboard | PARTIAL | dashboard route 汇总进度、coverage、下载、成本 | 成本恒 0；多个指标是简化/固定推断 |
| Export | PARTIAL at Gate 0 baseline; Gate 12 later PASS | Bible/novel markdown 文件写入 `.local/epic_novel_exports`；Gate 12 已补充章节包、质检报告、检索轨迹、验收报告等 metadata 导出 | true docx 仍是未来增强 |

## 阻断项

1. Reference Intelligence 未达生产要求：没有 live `ReferenceSection` / `ReferenceSectionAnalysis`，卡片缺少章节来源，profile 缺少追溯元数据。
2. 章节验收不是 SPEC 级验收：当前 gate 可通过浅层关键词/固定分数，不能阻断复杂连续性、人物弧线、权力规则、伏笔偿还、反套路等高风险问题。
3. 300 万字级基建未被证明：当前项目是 30 万 / 10 章 smoke，实际 accepted 只有 3 章。
4. 生成服务仍存在较多业务内 prompt/fallback 模板路径，和“prompt 不硬编码在业务逻辑”要求存在冲突风险。
5. 部署/GitHub/ECS 状态本轮未重验，只能视为历史状态文档声称，不能作为当前 Gate 0 live 证据。

## 300 万字就绪判定

300万字-ready: NO

理由：没有 12 卷/700 章项目的生成、状态膨胀、检索压测、成本记录、失败恢复、导出大文件、30 章连续质量门或人工复核闭环证据。当前只能证明 3 章 smoke flow。

## 下一步修复优先级

P0:

- 重新跑 57 本已导入书的 section-aware ingest，必须写入 `ReferenceSection`、`ReferenceSectionAnalysis`、`source_section_ids`、`section_hash`、`analysis_status`。
- 让 `ReferenceMechanismProfile` 使用可追溯 card/source metadata，raw_json 必须包含 generationStatus/sourceBookIds/sourceCardIdsSample/sourceCardCount/profileVersion/provider/model。
- 重写 Acceptance Gate，使 high/severe 真正覆盖人物弧线、世界法则、官僚机制、战力规则、伏笔、读者疑问、计划差异和反抄袭风险。
- 将生产 prompt 模板迁到 prompt template 系统或独立模板文件，业务服务只做装配。

P1:

- 跑至少 30 章串行验收，并证明每章读取前章 state、Bible、WorldAtlas、ReferenceMechanismProfile。
- 加入真实成本统计、provider token/cost、失败重试、continuation chunk 证据。
- 修复 export download 与 metadata 的边界，大型导出默认返回文件元数据。

P2:

- 重验前端运行态中文 UI、ECS 部署状态、GitHub 当前分支与 main 状态。
- 为 100 本未完成下载项保留明确 skipped/not_found/手动导入证据，避免“全 100 本已处理”的表述。
