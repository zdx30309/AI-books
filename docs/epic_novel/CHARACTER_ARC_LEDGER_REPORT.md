# Character Arc Ledger Report

Updated: 2026-06-18

Status: `HARDENED_PARTIAL`

已实现：

- `EpicCharacterGrowthLedger` 记录角色成长轴、角色 tier、章节号、before/after、trigger、cost、futureImpact。
- `EpicRelationshipLedger` 记录 from/to character、before/after state、trust/conflict delta、unresolved tension。
- QA 将 `unauthorized_growth` 与 `relationship_jump` 计入 character arc / continuity 分数。

限制：

- 角色 tier 屏幕时间预算和 A-tier 消失检测仍依赖后续更长生产样本验证。
