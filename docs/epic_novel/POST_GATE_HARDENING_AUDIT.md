# Epic Novel Engine Post-Gate Hardening Audit

Updated: 2026-06-18

Status: `PARTIAL_IMPLEMENTATION`

## 本次硬化范围

本次只针对史诗长篇板块做 post-gate hardening，没有替换现有短篇/短视频生成系统。现有短篇生成链路仍作为 Chapter Draft Writer 的基础能力保留。

已完成：

- 新增通用一致性账本：`EpicCharacterKnowledgeLedger`、`EpicSecretExposurePolicy`、`EpicReaderQuestionLedger`、`EpicCharacterGrowthLedger`、`EpicAuthorityLedger`、`EpicRelationshipLedger`、`EpicSecretLedger`。
- 强化 `ChapterContract.raw_json.consistencyContract`，支持知识、秘密、成长、能力、权限、关系、专业术语、新角色等允许/禁止约束。
- 强化 ContextPack，加入当前知识状态、秘密曝光、读者问题、成长、关系、力量、权限、伤病限制、allowed/forbidden 约束和 overflow summary。
- 强化 Acceptance Gate，新增阻断项：知识泄露、秘密提前曝光、读者问题过早回答、专业术语无世界内来源、未授权成长、未解锁能力使用、未授权权限使用、关系跃迁、新核心角色越权。
- acceptedContent 持久化时写入新增账本；draftContent 与 repairedContent 仍不写 canon/state ledger。
- 新增项目删除、项目状态、项目 artifacts、项目 ledgers、引用书列表/删除、下载任务重试、引用语料 readiness API。
- 前端工作区新增项目删除路径、引用书列表刷新/移除、下载任务重试、引用 readiness 与 artifact 记录显示。
- 单元测试覆盖新增 QA 阻断项。

## 非完成项

本次硬化没有改变总体生产就绪结论：

- 100 本 corpus 仍未全部完成生产级抽取。
- 当前证据仍不是 300 万字全文生产跑通。
- 章节生成质量仍需要真实长跑验证，当前测试主要覆盖控制平面、确定性路径与 targeted QA case。
- 北京 ECS 部署需要本次提交后重新验证，验证结果记录在最终执行输出中。

## 风险与后续

- 新增表依赖当前 `Base.metadata.create_all` 幂等建表；如果后续引入 Alembic，需要补正式 migration。
- 专业术语来源检查采用术语附近窗口判断，后续可升级为结构化 evidence span。
- 引用书删除只删除数据库引用与派生记录，不删除本地 raw 文件，避免误删用户本地 corpus。
