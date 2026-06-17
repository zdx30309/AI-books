# Reader Question Ledger Report

Updated: 2026-06-18

Status: `HARDENED_PARTIAL`

已实现：

- 新增 `EpicReaderQuestionLedger`，记录 question、introduced_chapter、current_status、planned_reveal_window、partial_answers、forbidden_early_answer、payoff_target。
- Acceptance Gate 会检查 `forbiddenEarlyAnswers` 和 reader question ledger，阻断过早回答。
- acceptedContent 持久化时同步写入旧 `EpicReaderQuestion` 和新 ledger。

限制：

- 目前是合同与 ledger 驱动检查，尚未实现跨 700 章的自动悬念曲线优化。
