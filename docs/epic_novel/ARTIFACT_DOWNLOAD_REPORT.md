# Artifact Download Report

Updated: 2026-06-18

Status: `HARDENED_PARTIAL`

已实现：

- 新增 `EpicArtifactRecord`，导出 metadata 生成后记录 artifact type、filename、path、media type、bytes、sha256、status。
- 项目 artifacts API 可列出已生成产物。
- 正文导出在没有 accepted chapters 时返回 not ready，而不是生成空正文文件。
- 通用 artifact export 会写入 artifact record。

限制：

- 现有导出仍采用请求 metadata 时生成文件的兼容模式。
- 大文件断点续传/对象存储尚未实现。
