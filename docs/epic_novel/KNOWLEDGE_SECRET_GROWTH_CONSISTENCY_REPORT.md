# Knowledge / Secret / Growth Consistency Report

Updated: 2026-06-18

Status: `HARDENED_PARTIAL`

本次新增了通用一致性层，目标是防止长篇生产中最常见的隐性崩盘：角色知道不该知道的事、秘密提前曝光、成长或能力跳级、权限和关系无因跃迁。

## 已实现

- `CharacterKnowledgeLedger`：记录角色已知、怀疑、未知、允许台词、禁止台词、证据来源。
- `SecretExposurePolicy` 与 `SecretLedger`：记录秘密曝光层级、禁止曝光章节、允许条件、谁知道/谁怀疑、曝光历史。
- `ReaderQuestionLedger`：记录读者问题、计划揭示窗口、禁止提前回答、局部答案和 payoff target。
- `CharacterGrowthLedger`：记录角色成长轴、前后状态、触发事件、代价、文本证据、是否由章节合同授权。
- `PowerLedger` raw_json 增强：记录 unlocked/locked abilities、trigger、cost、limitation、canonSource。
- `AuthorityLedger` 与 `RelationshipLedger`：记录权限边界和关系变化。

## QA 阻断项

高/严重问题会阻断 acceptance：

- `knowledge_leak`
- `early_secret_exposure`
- `reader_question_early_answer`
- `professional_term_without_source`
- `unauthorized_growth`
- `locked_ability_used`
- `unauthorized_authority_use`
- `relationship_jump`
- `forbidden_major_character`

## 状态边界

新增账本只在 acceptedContent 持久化路径写入。draftContent 和 repairedContent 不写 canon/state ledger。
