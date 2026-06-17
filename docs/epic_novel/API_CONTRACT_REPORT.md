# Epic Novel API Contract Report

Updated: 2026-06-18

Status: `HARDENED_PARTIAL`

新增/强化 API：

- `DELETE /api/v1/epic-novel/projects/{project_id}`
- `GET /api/v1/epic-novel/projects/{project_id}/status`
- `GET /api/v1/epic-novel/projects/{project_id}/artifacts`
- `GET /api/v1/epic-novel/projects/{project_id}/ledgers`
- `GET /api/v1/epic-novel/reference-books`
- `DELETE /api/v1/epic-novel/reference-books/{book_id}`
- `POST /api/v1/epic-novel/reference-download-tasks/{task_id}/retry`
- `GET /api/v1/epic-novel/reference-readiness`

错误契约：

- 业务错误返回结构化 `detail.errorCode`、`detail.message`、`detail.details`。
- not found 映射 404。
- not ready / missing precondition 映射 409。
