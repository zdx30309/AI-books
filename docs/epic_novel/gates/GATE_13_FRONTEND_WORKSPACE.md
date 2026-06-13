# Gate 13: 前端工作台

状态：DONE / PASS，本地运行态已验证。

更新时间：2026-06-13

## 目标

验证 Epic Novel Engine 的前端工作台不是静态壳子，而是可以通过真实 API 展示项目、参考语料、圣经世界、规划合同、章节生产、上下文检索、质检验收、生产仪表盘、导出和调用日志。

## 本轮改动

- `/epic-novel` 项目列表、创建表单和项目详情工作台改为中文可见文案。
- 项目详情扩展为 10 个中文标签：
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
- 新增真实 API 数据面板：
  - 参考书单导入/规范化
  - SoNovel 检测、下载目录扫描、导入下载文件
  - 下载任务
  - 参考模式卡搜索
  - 参考机制画像
  - Master Bible、Bible sections、Coverage CI
  - WorldAtlas、官僚体系、战力体系、国家/区域/机构/势力/角色/全局主线
  - 分卷合同、章节合同、伏笔、读者疑问、爽点
  - ContextPack、RetrievalTrace、AcceptanceReport、Issues
  - 生产任务、进度、成本、错误、重试状态
  - Bible/正文/卷/章节包/QA/检索/验收报告导出元数据与显式下载
- 后端 `project_detail` 增补真实 DB 字段，前端类型和 API 客户端同步补齐。
- 导出接口前端默认请求 metadata，下载按钮显式走 download，避免把大型导出塞进页面状态。
- 修复导出元数据空字段崩溃：空项目或异常 metadata 不再因为缺失 `sha256` 触发运行时错误，而是显示“未返回校验和”。

## 验证命令

```powershell
npm.cmd run build
```

结果：PASS。

```powershell
$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_reference.py backend/tests/test_epic_context.py backend/tests/test_epic_chinese_output.py backend/tests/test_epic_qa.py backend/tests/test_epic_multi_volume.py backend/tests/test_epic_scheduler.py -q
```

结果：`53 passed, 1 warning`。警告来自 pytest cache 目录已存在，不是测试失败。

## 浏览器烟测

本地地址：

- `http://127.0.0.1:5173/epic-novel`
- `http://127.0.0.1:5173/epic-novel/ac9f3c28-c725-44cb-bd4b-327bb7b80dc2`

项目：

- `ac9f3c28-c725-44cb-bd4b-327bb7b80dc2`
- 用途：Epic 中文工作台本地测试项目

检查结果：

| 检查项 | 结果 |
| --- | --- |
| 项目列表中文入口 | PASS |
| 项目详情可加载 | PASS |
| 10 个中文标签页均可点击 | PASS |
| 旧英文标题 `Long-form control room` 不再出现 | PASS |
| 旧英文按钮 `Create project` 不再出现 | PASS |
| 旧英文加载文案 `Loading projects` 不再出现 | PASS |
| `文件导出 -> 正文元数据` 不再崩溃 | PASS |
| 空 metadata 显示“未返回校验和” | PASS |
| 新增前端运行时错误 | 0 |

10 个标签页复验结果：

| 标签 | 结果 |
| --- | --- |
| 基建总览 | PASS |
| 参考语料 | PASS |
| 圣经世界 | PASS |
| 规划合同 | PASS |
| 章节生产 | PASS |
| 上下文检索 | PASS |
| 质检验收 | PASS |
| 生产仪表盘 | PASS |
| 文件导出 | PASS |
| 调用日志 | PASS |

## 边界

- Gate 13 只证明当前本地前端工作台运行态通过。
- Gate 13 不证明 ECS 远端已更新。
- Gate 13 不证明 100 本全量参考抽象完成。
- Gate 13 不证明 30 个生产级正文章节或 300 万字成书质量。

## 结论

Gate 13：PASS。

下一步进入 Gate 14：最终 SPEC readiness 复核、GitHub 发布、北京 ECS 部署与远端验收。
