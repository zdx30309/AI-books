# Epic Novel Engine Fallback 与 Fixture 清单

Updated: 2026-06-12

Gate 0 verdict: `PARTIAL_IMPLEMENTATION`

## 判定口径

本文件只记录本轮只读审计发现的 fallback、fixture、模板化或 smoke-only 路径。存在 fallback 不一定等于错误，但如果生产验收把 fallback/smoke 结果当作 PASS，就会违反 SPEC。

## 生产服务中的 fallback/模板路径

| 文件 | 位置/符号 | 类型 | 风险 |
| --- | --- | --- | --- |
| `backend/app/services/epic_novel.py` | `_fallback_meta` | 显式 fallback 元数据 | 已标记 `production_quality=false`，设计合理；但必须确保生产验收不能接受 |
| `backend/app/services/epic_novel.py` | `_call_structured_generation` | 模型失败后 fallback | LLM 成功会强制写 `_meta.production_quality=true`，但缺少内容深度判定 |
| `backend/app/services/epic_novel.py` | `_generic_entity_pack` | 通用实体包 | 兜底可用于 smoke，不应进入生产质量声明 |
| `backend/app/services/epic_novel.py` | `generate_world_bible/generate_world_atlas/forge_power_system/generate_ensemble_cast/generate_global_spine` | 内联 prompt + fallback payload | 多处 prompt 仍写在业务逻辑内，和 SPEC “prompt 不硬编码在业务逻辑”冲突风险高 |
| `backend/app/services/epic_novel.py` | `_bible_story_payload` | Bible fallback payload | 文案含 smoke/fallback 概念，必须被验收拦截 |
| `backend/app/services/epic_novel.py` | `_write_draft_from_context` | 确定性章节草稿 fallback | 只适合 smoke；不应作为生产章节质量证据 |
| `backend/app/services/epic_novel.py` | `_extract_state_from_accepted` | 模板化状态抽取 | 不从章节正文真实抽取，容易形成固定 state ledgers |
| `backend/app/services/epic_novel.py` | `_planned_vs_actual` | 关键词式 diff | 仅做语义关键词匹配，无法覆盖复杂计划差异 |
| `backend/app/services/epic_novel.py` | `_acceptance` | 浅层验收 | 只检查空内容、raw source text 字符串、bureaucracy function、mustExecute 缺失 |
| `backend/app/services/epic_novel.py` | `execute_chapter` repair patch | 机械修复文本 | repair 文案是英文 patch 句子，不能当作生产修复 |
| `backend/app/services/epic_reference.py` | `_fallback_section_analysis` | 本地抽象 fallback | 安全但粗糙；需要与 LLM section analysis 区分 |
| `backend/app/services/epic_reference.py` | `build_reference_mechanism_profile` fallback_profile | 本地聚合 profile | 当前 live profile 更像旧/本地粗聚合，缺追溯元数据 |
| `scripts/epic_acceptance_smoke.py` | 全脚本 | smoke 验收 | 脚本文档即写明 deterministic engine smoke，不能当生产验收 |

## Live DB 中的 fixture/smoke 信号

| 项 | 证据 | 判定 |
| --- | --- | --- |
| 项目标题 | `acceptance smoke 2026-06-12 143900` | 明确是 smoke 项目 |
| 项目规模 | 300,000 字 / 3 卷 / 10 章 | 不是 300 万字生产规模 |
| 已验收章节 | 3 章 | 只覆盖 smoke milestone |
| ReferencePatternCard 样本 | `Pleasure Pattern 1`, `reader reward rhythm`, `case-to-arc escalation` | 英文泛化卡片，不是深度中文机制抽象 |
| 卡片来源 | 98,581 张全部 `source_section_ids=[]` | 无章节级证据链 |
| profile raw_json | 只有 `categories/abstractOnly` | 无 generationStatus/source ids/profileVersion |
| AcceptanceReport | 3 条 accepted，分数由服务固定写入 | 不能证明真实 QA 审稿 |

## 已有安全边界

- fallback 元数据设计上会标 `production_quality=false`。
- Bible section acceptance 会拒绝 fallback/placeholder meta。
- ContextPack 会清洗 raw reference text，并记录 overflow。
- RetrievalTrace 当前 `raw_text_included=true` 为 0。
- CanonFact 当前 `source=acceptedContent` 为 3 条，未发现 draft/repaired 直接写 canon 的 live 证据。

## 必须修复的误用

- 不得把 `scripts/epic_acceptance_smoke.py` 通过写成完整 09 验收 PASS。
- 不得把 98,581 张粗卡片数量写成 Reference Intelligence 深度 PASS。
- 不得把 3 章 smoke 项目写成 300 万字-ready。
- 不得把 latest profile 的 `sourceBookCount/sourceCardCount` 当作可追溯 profile 证据，除非 raw_json/source ids/profileVersion/section analysis 都补齐。
