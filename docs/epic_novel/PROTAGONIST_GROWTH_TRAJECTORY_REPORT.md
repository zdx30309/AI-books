# Protagonist Growth Trajectory Report

Updated: 2026-06-18

Status: `PARTIAL_IMPLEMENTATION`

本次把主角成长从“章节正文结果”提升为可检查的 `CharacterGrowthLedger` 与 `PowerLedger` 组合。

已完成：

- 章节合同可声明 `allowedGrowthEvents`、`forbiddenGrowthEvents`、`allowedPowerUse`、`forbiddenPowerUse`。
- Acceptance Gate 会阻断合同未授权的突破、晋升、解锁、权限获得和 locked ability 使用。
- acceptedContent 写入成长账本和力量账本，记录 trigger/cost/limitation/evidence。

未完成：

- 尚未完成全书级主角成长曲线的自动可视化。
- 尚未用 300 万字真实生成跑验证长期成长节奏。
