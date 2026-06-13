# Gate 4: Real Chinese Master Bible

Status:
DONE / PASS on 2026-06-12.

Evidence:
- `docs/epic_novel/GATE_4_MASTER_BIBLE_REPORT.md`
- `.local/epic_novel_runs/gate4_master_bible_2026-06-12T154950_704943Z.json`
- `.local/epic_novel_exports/本长篇项目 全书圣经.md`
- `.local/epic_novel_exports/本长篇项目 全书圣经.json`

Goal:
Produce and verify a production-quality Chinese Master Bible with all 24 required sections.

Read first:
- AGENTS.md
- docs/epic_novel/GATE_3_REFERENCE_USAGE_DEPTH_REPORT.md
- docs/epic_novel/REFERENCE_INTELLIGENCE_EVIDENCE.md
- docs/epic_novel/MASTER_BIBLE_CONTENT_MATRIX.md
- docs/epic_novel/EPIC_NOVEL_ENGINE_FULL_SPEC.md
- docs/epic_novel/09_ACCEPTANCE_CRITERIA.md

Scope:
Master Bible generation, 24 section quality, Chinese output, section acceptance.

Out of scope:
- 30-chapter run
- full volume
- frontend
- full 300W generation

Tasks:
1. Generate Master Bible dynamically from:
   - project premise
   - genre
   - WorldBible
   - WorldAtlas
   - PowerSystem
   - EnsembleCast
   - GlobalSpine
   - ReferenceMechanismProfile
   - selected production-ready PatternCards
   - accepted structured context

2. No hardcoded fixture payload.
3. No fallback/placeholder.
4. No English user-facing content.
5. No raw reference text.
6. Each section must be concrete, section-specific, and useful for production.

Required 24 sections:
- 项目总览
- 类型/爽点承诺
- 世界底层法则
- 战力体系
- 国家与朝堂
- 官僚体系
- 地图与地理
- 势力系统
- 人物系统
- 多女主独立线
- 关系图谱
- 历史年表
- 全书大主线
- 分卷合同
- 伏笔账本
- 读者疑问账本
- 爽点曲线
- 反派计划
- 风格指南
- 禁忌与反俗套
- 结局规划
- 术语表
- ReferenceMechanismProfile接入说明
- 生产控制规则

BibleSectionAcceptance must fail if:
- boilerplate
- fallback
- placeholder
- too short
- mostly English
- lacks concrete story entities/rules/relationships
- duplicates >20%
- only says cards/profile available
- rawTextIncluded=true
- hardcoded fixture content detected
- section is generic and not section-specific

Create/update:
- docs/epic_novel/MASTER_BIBLE_CONTENT_MATRIX.md
- docs/epic_novel/GATE_4_MASTER_BIBLE_REPORT.md
- docs/epic_novel/IMPLEMENTATION_STATUS.md
- docs/epic_novel/PRODUCTION_READINESS_RECHECK.md

Export:
- Master Bible markdown
- Master Bible JSON
- section acceptance report

Tests:
- 24 sections generated
- 24 sections accepted
- fallback sections fail
- English boilerplate fails
- generic text fails
- raw reference text fails
- section-specific required fields exist
- Master Bible export writes files/metadata

PASS criteria:
- 24/24 sections production_quality PASS.
- No fallback.
- No hardcoded fixture.
- Chinese section-specific story payload.
- ReferenceMechanismProfile and production-ready cards are used and traced.
