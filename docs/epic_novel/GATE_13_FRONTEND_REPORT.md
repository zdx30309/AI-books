# Gate 13 前端工作台验收报告

更新时间：2026-06-13

结论：PASS，本地前端运行态已通过。

## 已完成

- Epic 项目列表、创建表单、项目详情、操作按钮、错误/空状态和核心模块标题全部改为中文可见文案。
- 详情页从原来的窄入口扩展为 10 个中文工作区标签：
  `基建总览`、`参考语料`、`圣经世界`、`规划合同`、`章节生产`、`上下文检索`、`质检验收`、`生产仪表盘`、`文件导出`、`调用日志`。
- 前端接入真实 API 数据，不再用假成功遮盖：
  参考书单、SoNovel、下载任务、参考模式卡、参考机制画像、Bible、WorldAtlas、规划合同、章节、ContextPack、RetrievalTrace、AcceptanceReport、Issues、生产任务和导出元数据。
- 导出元数据按钮已接入 Gate 12 的文件/对象 metadata 逻辑；下载仍通过显式 download 路由触发。
- 修复 `exportLabel` 对空 metadata 的崩溃，缺少 `sha256` 时显示中文占位，不再抛运行时异常。

## 命令证据

| 命令 | 结果 |
| --- | --- |
| `npm.cmd run build` | PASS |
| `$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_reference.py backend/tests/test_epic_context.py backend/tests/test_epic_chinese_output.py backend/tests/test_epic_qa.py backend/tests/test_epic_multi_volume.py backend/tests/test_epic_scheduler.py -q` | PASS，`53 passed, 1 warning` |

pytest 警告为 `.pytest_cache` 目录已存在，未造成测试失败。

## 浏览器运行态证据

本地页面：

- `http://127.0.0.1:5173/epic-novel`
- `http://127.0.0.1:5173/epic-novel/ac9f3c28-c725-44cb-bd4b-327bb7b80dc2`

项目详情页可见中文模块：

- 基建总览
- 参考语料
- 圣经世界
- 规划合同
- 章节生产
- 上下文检索
- 质检验收
- 生产仪表盘
- 文件导出
- 调用日志

复验结果：

- 10 个标签页全部可点击。
- 页面未出现 `Long-form control room`、`Create project`、`Loading projects` 等旧英文 Epic 文案。
- 点击 `文件导出 -> 正文元数据` 后页面未崩溃。
- 空项目导出 metadata 缺字段时显示中文占位：`未返回校验和`。
- 浏览器控制台没有新增 `EpicNovelWorkspace` 或 `Cannot read properties` 运行时错误。

## 仍需 Gate 14 处理

- Gate 14 已将当前改动以 commit `a1907e8` 推送到 GitHub。
- Gate 14 已将 commit `a1907e8` 部署到北京 ECS，并验证公网 `/epic-novel` 返回 200、web 容器包含 Gate 13 中文工作台文案。
- Gate 2B 仍是 staged PARTIAL，100 本全量参考抽象没有完成。
- 300 万字生产级正文质量没有被证明，不能宣称 `PRODUCTION_READY`。
