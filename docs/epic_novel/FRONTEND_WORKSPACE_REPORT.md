# Epic 前端工作台报告

更新时间：2026-06-13

## 范围

本报告记录 Epic Novel Engine 前端工作台的本地运行态结果。它覆盖前端页面、API 客户端、类型定义、中文化、真实数据面板、导出入口和浏览器烟测。

## 涉及文件

| 文件 | 作用 |
| --- | --- |
| `frontend/src/pages/EpicNovelWorkspace.tsx` | Epic 项目列表、创建表单、详情工作台、10 个中文标签页、操作按钮、空/错/加载状态 |
| `frontend/src/api/client.ts` | Epic API 客户端、导出 metadata/download 分离、参考语料和生产控制接口 |
| `frontend/src/api/types.ts` | Epic 详情、参考书单、下载任务、模式卡、导出 metadata、dashboard 字段类型 |
| `backend/app/services/epic_novel.py` | `project_detail` 暴露真实 DB 数据，导出 metadata 生成 |
| `backend/app/schemas/epic_novel.py` | 项目详情响应字段 |
| `backend/app/api/routers/epic_novel.py` | Gate 12/13 所需 metadata/download 导出路由 |

## 用户可见中文模块

- 项目列表与创建表单
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

## 真实数据接入

| 模块 | 数据来源 | 状态 |
| --- | --- | --- |
| 参考书单 | `/reference-booklist/import`、`/reference-booklist/normalize` | 已接入 |
| SoNovel | `/reference-downloader/detect`、scan/import routes | 已接入 |
| 下载任务 | `/reference-download-tasks` | 已接入 |
| 模式卡 | `/reference-patterns/search` | 已接入 |
| 机制画像 | `/reference-mechanism-profile` | 已接入 |
| 圣经世界 | Bible/world/atlas/power/cast/global spine routes | 已接入 |
| 规划合同 | volume/chapter/arc/outline routes | 已接入 |
| 章节生产 | execute/check/repair/batch routes | 已接入 |
| 上下文检索 | context pack + retrieval traces | 已接入 |
| 质检验收 | acceptance reports + issues | 已接入 |
| 仪表盘 | `/dashboard` | 已接入 |
| 文件导出 | Gate 12 metadata/download routes | 已接入 |
| 调用日志 | generation logs | 已接入 |

## 验证

```powershell
npm.cmd run build
```

结果：PASS。

```powershell
$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_reference.py backend/tests/test_epic_context.py backend/tests/test_epic_chinese_output.py backend/tests/test_epic_qa.py backend/tests/test_epic_multi_volume.py backend/tests/test_epic_scheduler.py -q
```

结果：`53 passed, 1 warning`。

浏览器烟测：

- 项目列表中文入口：PASS。
- 详情页 10 个中文标签：PASS。
- 旧英文 Epic 文案扫描：PASS。
- 文件导出 metadata 点击：PASS。
- 新增运行时错误：0。

## 边界

本报告不等同于远端部署报告。北京 ECS 仍需在 Gate 14 中重新部署并验证。
