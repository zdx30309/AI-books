# 大导出系统验收报告

状态：`PASS`

对应 Gate：Gate 12 Large Export System

## 覆盖范围

本轮实现并验证以下导出路径：

- 全书圣经 Markdown / JSON
- 正文 Markdown
- 分卷正文 Markdown
- 章节包 JSON
- 质检报告 JSON
- 检索轨迹 JSON
- 验收报告 JSON
- 大文件路径探针

默认 API 行为为返回 metadata，不返回巨大正文体。需要正文内容时必须显式走 `mode=download`。

## 证据

| 项目 | 结果 |
| --- | --- |
| 验收脚本 | `.local/epic_novel_runs/gate12_export_2026-06-12T224306_008134Z.json` |
| 人读报告 | `.local/epic_novel_runs/export_system_2026-06-12T224306_006655Z.md` |
| 项目 ID | `44ce72be-a423-4f3b-895b-ad8a0fbb5e63` |
| 导出数量 | 8 |
| 大文件探针 | 3,480,027 bytes |
| 检索轨迹导出 | 4,684,462 bytes |
| `contentReturned` | false |
| `sha256` / `objectId` | 已写入 |
| 下载路由 | 已写入 |
| 原始参考文本 | 未导出 |

## 检查结果

| 检查项 | 结果 |
| --- | --- |
| metadata 结构完整 | PASS |
| 默认不返回正文 | PASS |
| 校验和存在 | PASS |
| 支持重新生成 | PASS |
| 大文件走文件路径 | PASS |
| 下载接口与 metadata 接口分离 | PASS |
| Bible 导出含预期章节 | PASS |
| 正文导出含章节 | PASS |
| 章节包含章节 | PASS |
| 质检报告含验收信息 | PASS |
| 检索轨迹含摘要证据 | PASS |
| 验收报告含条目 | PASS |
| 不包含原始参考文本 | PASS |

## 生产边界

该报告只证明导出基础设施能够承载大文件路径和 metadata 响应，不证明完整 300 万字正文已生成，也不证明全语料 Reference Intelligence 已完成。
