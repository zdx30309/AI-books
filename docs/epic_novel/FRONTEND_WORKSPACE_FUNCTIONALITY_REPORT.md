# Frontend Workspace Functionality Report

Updated: 2026-06-18

Status: `HARDENED_PARTIAL`

已完成：

- 项目列表支持删除项目。
- 项目详情加载项目状态、artifact records、引用书、引用 readiness。
- 参考语料页支持刷新引用书、刷新 readiness、下载任务重试、引用书移除。
- 导出页显示后端 artifact 记录数。
- 前端 API client 支持结构化错误 message 解析，避免显示 `[object Object]`。

验证：

- `npm.cmd run build` 通过。

限制：

- 未做浏览器截图级 UI QA。
- 引用书管理目前是最小可用管理表，后续可拆成独立 tab/panel。
