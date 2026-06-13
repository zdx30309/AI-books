# Gate 12 大导出系统报告

状态：`DONE / PASS`

验收时间：2026-06-13

## 结论

Gate 12 已通过。大型导出默认返回文件/对象元数据，不再默认返回巨型正文响应；显式下载路由与元数据路由分离；导出文件包含 `sha256`、`objectId`、文件大小、创建时间和下载地址；检索轨迹导出已改为摘要化证据，不写入原始参考文本。

这只证明导出系统安全边界，不证明 300 万字全文已经生成。

## 运行命令

```powershell
$env:PYTHONPATH='backend'; python -m compileall backend/app/services/epic_novel.py backend/app/api/routers/epic_novel.py scripts/epic_gate12_export_system.py
$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_context.py -q
$env:PYTHONPATH='backend'; python scripts/epic_gate12_export_system.py --project-id 44ce72be-a423-4f3b-895b-ad8a0fbb5e63
```

## 结果

| 检查项 | 结果 | 证据 |
| --- | --- | --- |
| Gate 12 现场验收 | PASS | `.local/epic_novel_runs/gate12_export_2026-06-12T224306_008134Z.json` |
| Markdown 报告 | PASS | `.local/epic_novel_runs/export_system_2026-06-12T224306_006655Z.md` |
| 编译 | PASS | `compileall` 成功 |
| 上下文/导出测试 | PASS | `backend/tests/test_epic_context.py`，9 passed |
| 默认响应不返回正文 | PASS | 所有导出 metadata 均为 `contentReturned=false` |
| 校验和 | PASS | 每个导出 metadata 均有 `sha256` / `objectId` |
| 下载路由分离 | PASS | metadata 中提供 `mode=download` 下载地址 |
| 大文件路径 | PASS | `Gate12 大文件路径探针 20260612224305.md`，3,480,027 bytes |
| 检索轨迹瘦身 | PASS | `本长篇项目 检索轨迹.json`，4,684,462 bytes，摘要化字段 |
| 原始参考文本安全 | PASS | `rawReferenceTextExcluded=true` |

## 导出物

| 类型 | 文件 | 字节数 |
| --- | --- | ---: |
| 全书圣经 Markdown | `.local/epic_novel_exports/本长篇项目 全书圣经.md` | 294,715 |
| 全书圣经 JSON | `.local/epic_novel_exports/本长篇项目 全书圣经.json` | 851,838 |
| 正文 Markdown | `.local/epic_novel_exports/本长篇项目 正文.md` | 53,279 |
| 第 1 卷正文 | `.local/epic_novel_exports/本长篇项目 第1卷 正文.md` | 44,448 |
| 章节包 | `.local/epic_novel_exports/本长篇项目 章节包.json` | 212,475 |
| 质检报告 | `.local/epic_novel_exports/本长篇项目 质检报告.json` | 242,348 |
| 检索轨迹 | `.local/epic_novel_exports/本长篇项目 检索轨迹.json` | 4,684,462 |
| 验收报告 | `.local/epic_novel_exports/本长篇项目 验收报告.json` | 225,081 |

## 已修复的问题

- Bible/正文导出接口默认模式从直接下载改为 `metadata`。
- 新增统一 artifact 导出接口，支持分卷正文、章节包、质检报告、检索轨迹、验收报告。
- 导出元数据统一包含文件路径、对象 ID、字节数、校验和、创建时间、下载路由和 artifact 类型。
- 检索轨迹导出不再写入完整 `raw_json` 或完整卡片 ID 大列表，只输出计数、样本和上下文元数据摘要。

## 边界

- Gate 12 不生成完整 300 万字正文。
- Gate 12 不替代 Gate 13 前端运行态验收。
- Gate 12 不改变 Gate 2B 全语料抽象仍为 `PARTIAL` 的事实。
