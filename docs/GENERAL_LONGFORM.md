# 通用长篇分类集成

> 本文保留首版 v16 的设计和发布证据。2026-09-15 起的新项目默认使用 v02；完整的 840 段、正反样稿与固定版本升级说明见 [GENERAL_LONGFORM_V02.md](GENERAL_LONGFORM_V02.md)。旧项目继续按 v16 运行。

## 范围与实现顺序（2026-09-14）

新增 `general_longform`（通用长篇），不替换社会痛点通用主笔，不修改现有分类的专属 prompt、标题清理、章节均衡或史诗小说状态规则。

1. 完整归档用户的 2026-09-14 Word；按原段落顺序拆分规则及五份附录，记录原文件、文本、分段 SHA-256 和阶段对应表，校验零遗漏、零改写。
2. 复用现有 `Task`、`Chapter`、Celery 排队/运行租约、OpenAI Chapter Draft Writer 和 Gemini 服务；规划、候选正文、验收正文、研究、QA、调用日志及导出分开保存到现有持久化输出卷，不新增数据库迁移。
3. 建立专属串行链路：材料识别 → 实际联网检索/页面读取与证据摘要 → 五组包装及 12—15 章、100000—110000 字大纲 → 用户确认 → 当前章分节起草 → 一润/二润 → 状态抽取、计划实绩对比、验收 → 必要的局部修复和复验 → 保存验收状态 → 等待下一章操作 → 全书终审/合稿。
4. 为新分类单独实现公司 Word 协议：章 `##` + Heading 1，小节 `#` + Heading 2；来源 Heading 1 无井号；单章正文与来源/编辑记录两份文件、同批北京时间秒级时间戳；全书汇总去重来源。
5. 接入现有分类选择、任务书架及详情工作区，提供大纲修订/确认、下一章、指定章修订、材料导入、断点恢复和版本化下载。
6. 运行逐段保真、路由隔离、章节串行/验收状态、研究证据、Word XML、API 权限、端到端模拟和旧分类回归；构建前端，推送同一 Git 提交到 GitHub 并部署北京 ECS，保留生产配置/数据并核对远端运行版本。

## 材料边界

- 附件中的写书指令是本分类的应用 prompt，不是要求本次开发对话直接写书。
- 附录二旧样稿、附录三检测原文完整归档，只进入参考抽象/诊断，不成为当前书稿进度，不直接进入正文生成上下文。
- 生成只使用 `ReferencePatternCards`、`ApprovedEditingSummary`、经核查的来源摘要和当前书稿。本附件的编辑摘要不冒充 Epic 百书 `ReferenceMechanismProfile`；新分类的 `##`/`#` 规则不改变旧分类全面取消 `###` 的行为。
- 本分类完成与原有 Epic Engine 的 300 万字生产就绪是不同验收范围；不修改历史 `PARTIAL_IMPLEMENTATION` 结论。

## 阶段记录

- 仓库/前文/附件阅读：完成。已逐条读取链接会话中的用户与助手正文；完整读取 Word 的 571 个正文/表格段落（含一个空段）、页眉和页脚。
- 原始 Word SHA-256：`3dc1e57353c3ac57535ee1a818effb12501364eb1fe7f40b2f1de69437e96f07`。
- 开始前基线：Git `b5b5fbc3625a11ef535bbac885d1ba0a9c8ed660`；已有未跟踪 `.tmp/` 保持原样，不加入提交。
- 实现、最终本地验收和 GitHub/ECS 代码发布：完成；2026-09-15 的实际结果见文末发布记录。

## 操作入口

1. 在现有创建任务窗口选择“通用长篇”；其他分类和默认选项保持原样。
2. 没有材料时可留空自主策划，也可填写选题要求、视频链接或逐字稿。已有文件时勾选“已有材料：先创建工作区，暂不调用模型”。
3. 从现有书架打开该任务，进入独立工作区的规划、章节、材料、文件、研究、日志六个标签。
4. 查看五组包装、完整字数分配及来源，选择包装后确认大纲。确认动作只保存确认版 Word，不自动写第一章。
5. “研究并写作第 N 章”每次只执行一章；每章验收后提供正文和来源/编辑记录两份 Word，再等待明确操作。
6. 已有确认大纲/正文可导入，识别真实进度后仅续写当前或下一章。检测报告和编辑记录只提供辅助信息，单独导入不会擅自续写。
7. 大纲局部修订会重新抽象新材料并依据修改要求核查来源；成功后重新等待确认。已验收章节不随大纲补丁重写，已修复的导入候选材料标记为已消费。
8. 全部章节验收后执行“全书终审与合稿 Word”，核对有效正文总字数、章节、来源与终审问题。

## 串行、验收和恢复

- 继续复用现有 `process_video` 队列和任务租约，新分类分支位于租约建立之后；没有新任务框架、数据库表或迁移。
- 每章分节起草；整章经过现有 Gemini 一润/二润，再做 State Extract、Planned-vs-Actual Diff、Acceptance Gate。高/严重问题最多自动局部修复一次，重新抽取并复验；仍不通过则保留候选并停住。
- `general_longform/state.json` 及其 `planning/`、`chapters/`、`research/`、`materials/`、`calls/`、`exports/` 保存在既有 `backend_output` 持久化卷。仅验收正文、摘要、endingHook 和匹配的 SHA-256 进入本分类接受状态及现有 `Chapter` 行；不写 Epic canon 表。
- 第 N 章必须读取通过完整性核对的有效前章；缺少前章正文、摘要、endingHook 或已持久化状态时阻断。修改旧章时保留其他章版本，并重新核查衔接。
- 一次任务用 request ID、stage、logical slot 标识已付费工作。恢复复用原始 prompt、结果和已收到的背景 response ID，不因时间戳/来源上下文变化重新收费。
- 无响应 ID 的交付待定、以及终止/不完整的检索响应，先返回 409 并要求用户核对后明确勾选重新请求；未勾选时不派发新任务。可取回的原响应优先恢复，不重新 POST。
- 暂停在持久化边界生效，已写部分稿可交付双 Word；恢复仍定位同一章。已验收但双文件未全部落盘时优先补齐原批次导出。
- 原通用“重新生成”、标题/分类更改、直接改正文等入口对新分类返回明确工作区提示，防止绕过版本与验收；旧分类行为不变。

## 研究、日志和费用

- 使用现有 OpenAI 客户端的实际 web search，再真实读取被检索到的页面，最后抽象可支撑观点。单次最多 8 页、每页最多 6 MB、最多 3 次重定向，不递归爬取。
- URL 仅允许无凭据的公开 HTTP(S) 地址；逐跳校验地址并固定实际连接 IP，保留 Host/TLS SNI。原始页面只进入读取与证据抽象，不进入正文生成。
- 大页面按完整读取范围分组抽象；上下文超限显式停止，不静默删除附件、素材或前文。
- 来源记录 provider、query、实际读取范围、时间、local_path、文件/文本哈希、status、error、generated_card_count。上传材料记录同类审计字段和逐项错误。
- 所有模型调用保留 action、provider、model、原始 prompt 哈希、请求/响应记录、状态、错误、token 与配置费率估算。Gemini token 估算明确标注；费用不包含搜索工具额外收费，缺费率显示“待核算”而不是伪造零费用。

## 完整 prompt 路由

`backend/app/prompts/general_longform/manifest.json` 是逐段路由和哈希清单；原 Word、571 段 JSON、完整文本、19 个连续原文分段与页眉页脚同时保留。`.gitattributes` 固定证据文件字节，避免跨平台换行破坏校验。

| 原文部分 | 工作节点 |
| --- | --- |
| 总说明、身份、阶段判断、附录边界、结语 | 全部相关节点 |
| 九领域及附录一选题池 | 研究、策划、大纲修订 |
| 来源查证规则 | 研究、证据抽象、规划、导入、写作、验收、合稿 |
| 五组包装及附录五包装参考 | 策划、大纲修订 |
| 大纲、逐章规则、衔接、字数 | 规划、导入、逐章起草、润色、验收、合稿 |
| 附录二旧样稿、附录三原检测报告 | 仅参考抽象节点；生成只接收批准摘要和抽象卡 |
| 文风规则、附录四编辑示范 | 对应写作、润色、验收、修复和终审节点 |
| Word 与交付协议 | 原文对应节点及独立真实 DOCX 生成器 |

所有 19 段均有节点，没有未使用原文段落；节点适配模板保存在 `stages/`，业务逻辑中不硬编码写作 prompt。

## Word 协议

- 章标题实际文字为 `##第N章 标题`，样式为 Heading 1；小节实际文字为 `#标题`，样式为 Heading 2；没有第三层标题。
- 来源标题固定 `文章参考来源`，Heading 1、无井号；来源条目去重并保留可打开的真实链接。
- 单章正文、来源与编辑记录分成两个文件，使用同一批次北京时间秒级时间戳，保留字数/连续性/来源/验收元数据；总字数排除标题及配套文件。
- 全书来源统一附后，旧版本文件不覆盖。bundle journal 支持只成功写出一份文件后的补齐重放。
- 原有书架全书 DOCX 下载复用本分类已验收的原生文件，不套用其他分类标题清理器。

## API

除原文哈希清单外，均使用现有登录鉴权并验证任务 owner：

| Method | Path | 用途 |
| --- | --- | --- |
| GET | `/api/v1/general-longform/prompt-manifest` | 原文版本、哈希与段落路由 |
| GET | `/api/v1/general-longform/{task_id}` | 工作区、QA、来源、调用日志、导出元数据 |
| POST | `/api/v1/general-longform/{task_id}/actions` | 策划、局部修订、确认、下一章、导入、合稿、恢复 |
| POST | `/api/v1/general-longform/{task_id}/materials` | DOCX / UTF-8 TXT / Markdown 上传，10 MB / 250000 字符限制、哈希去重 |
| POST | `/api/v1/general-longform/{task_id}/pause` | 在持久化边界暂停 |
| GET | `/api/v1/general-longform/{task_id}/chapters/{number}/content` | 明确选择验收正文或候选正文 |
| GET | `/api/v1/general-longform/{task_id}/artifacts/{artifact_id}` | 路径与 SHA-256 核对后的 Word 下载 |

## 最终本地验收（2026-09-14）

| 检查 | 实际结果 |
| --- | --- |
| 原 Word、所有分段、重建全文、页眉页脚、节点覆盖 | PASS：571 段 / 19 分段；原文件与全文哈希匹配 |
| 旧分类隔离 | PASS：33 个旧分类、132 份 prompt 哈希及分类顺序不变 |
| 新分类和旧系统回归 | PASS：416 passed，1 条既有 Google SDK deprecation warning，137.58 秒 |
| Python 编译 | PASS |
| 前端 typecheck / production build | PASS；保留既有大 bundle 提示 |
| 真实浏览器的确定性 API 场景 | PASS：分类创建、原默认值、免调用建工作区、五组包装、确认停住、单章操作、六标签、预览、上传、真实 DOCX 下载；0 page errors |
| 章 1—3 串行及重复请求 | PASS（确定性 double） |
| 12 章 / 105000 有效正文字数合稿 | PASS（确定性 double，验证管线和文档结构，不代表付费作品文学质量） |
| 高严重度阻断、局部修复复验、正文/状态哈希 | PASS |
| 暂停/恢复、付费交付待定、来源读取、防重复收费 | PASS |
| 导入候选局部修复、补充材料再次研究、逐材料错误 | PASS |
| owner 权限、版本冲突、路径穿越、旧入口绕过、Word XML | PASS |

可复现命令（仓库根目录设置 `PYTHONPATH=backend`）：

```powershell
python scripts/import_general_longform_prompt.py --check
python -m compileall -q backend/app scripts/import_general_longform_prompt.py scripts/smoke_general_longform_fixture.py
python -m pytest -q backend/tests/test_general_longform.py backend/tests/test_general_longform_runtime.py backend/tests/test_general_longform_api.py backend/tests/test_category_prompt_routing.py backend/tests/test_final_markdown_line_repair.py backend/tests/test_quality_metrics.py backend/tests/test_aigc_task_policy.py backend/tests/test_paid_call_delivery.py backend/tests/test_epic_reference.py backend/tests/test_epic_context.py backend/tests/test_epic_chinese_output.py backend/tests/test_epic_qa.py backend/tests/test_epic_multi_volume.py backend/tests/test_epic_scheduler.py backend/tests/test_classical_docx_download.py backend/tests/test_classical_docx_upload.py backend/tests/test_comic_series_workflow.py backend/tests/test_recovery_watchdog.py backend/tests/test_polish_provider.py backend/tests/test_refine_checkpoint.py backend/tests/test_catalog_repair_and_prompt_requirements.py
npm.cmd --prefix frontend run typecheck
npm.cmd --prefix frontend run build
python scripts/smoke_general_longform_fixture.py
# 启动前端 preview，默认 http://127.0.0.1:4176；准备 Playwright 与 Edge。
node scripts/smoke_general_longform_browser.cjs
git diff --check
```

本地证据：`.local/general_longform_integration/test_release_final.log`、`browser/browser_report.json`、`browser/workspace-{outline,chapter,files}.png`；不提交测试书稿、缓存或 `.tmp/`。

实施中发现并修复：旧通用 prompt 测试需按新分类隔离；Windows 原子临时文件名过长；浏览器模拟 tasks 接口需要 `items`；导入候选修订后材料消费、来源重查及无响应 ID 的明确恢复确认；逐段证据 JSON 的原始 CRLF 通过专属 Git whitespace 属性保留，不为格式检查修改证据字节。最终测试无失败项。

## 发布记录（2026-09-15）

- 发布前只读检查：北京 ECS 基线 `.deploy-version=b5b5fbc`，6 服务运行，业务活动任务 0、默认队列与 unacked 均为 0。
- 剩余磁盘约 3.8 GB：沿用 Docker 缓存、保留旧镜像回滚，不执行全局 prune，不删除无关数据。
- 代码发布提交：`640952d3bb3bc39d748d444a5d56e1941be0a52f`。已用一次 atomic push 同步 GitHub `main` 和 `codex/bilibili-cost-guard-main`，并逐一读取远端 SHA 核对。
- 从同一提交创建仅含本次 69 个变更文件的增量 Git archive，不打包整个工作目录；归档 SHA-256：`07506a774b59cdc42bac057dca974f7dc874d5b68629b868874bb9393b81d1df`。逐文件核对基线后覆盖，新分类之外只应用明确的条件接入点。
- 北京 ECS 已构建 backend/web；worker、maintenance、beat 复用同一 backend 镜像，保留既有命令/环境。六个服务均运行，`.env` SHA-256 和所有挂载的完整属性与发布前一致，未改代理、ADC、输出卷、参考资产或 Redis 服务。
- 代码发布验收时间：北京时间 2026-09-15 01:09。公开首页、`/api/v1/system/health`、新分类 prompt manifest 均为 HTTP 200，`celery_alive=true`。
- backend 和 worker 各核对 57 个已发布后端文件哈希；完整原文、19 分段、33 个旧分类/132 份 prompt 与原顺序均 PASS。
- 线上前端 `/assets/index-DvqQDtrv.js` 与本地已测构建逐字节相同，SHA-256：`e3d5fb97ed9de195704b0e9439e06b3194b24cb50d41df80ba02690802d4b31a`。线上前端再次运行六项浏览器场景，0 page errors（UI 场景使用确定性 API mock，不混同真实写作）。
- 真实线上免付费验收：临时普通用户经 API 创建通用长篇任务，真实 Celery 将其推进到 `awaiting_materials`；DOCX 材料上传、哈希去重、未登录 401、原默认分类均 PASS。模型调用 0、canon 写入 0；临时用户、任务和测试文件已移除，没有修改已有业务任务。
- 源码/版本/镜像回滚备份：`/opt/video-plan-release-backups/20260914T165102Z-640952d/`。`manifest.json` 保存原版本和 5 个旧镜像标签，`source-before.tar.gz` 保存原文件，`rollback.py` 在逐文件校验后恢复对应版本。仅在确认没有活动生成、没有后续代码/文档变更时执行该脚本；有后续文档发布时先按其备份恢复文档版本。
- 部署验证器修正记录：首次归档受 Windows autocrlf 影响，字节预检在应用任何源码前阻断；改用 `git -c core.autocrlf=false -c core.eol=lf archive` 并双端逐文件预检。前端检查改为真实工作区标识及字节比对，挂载检查按完整属性排序比较，避免将显示顺序变化误判成挂载变更。最终全部验证通过，无待修复发布项。
- 发布后磁盘剩余 3771 MiB，未执行清理或全局 prune。证据位于 `.local/general_longform_integration/20260914T165102Z-640952d/result.json`、`ecs_workspace_smoke.json`、`browser_ecs/browser_report.json`。文档收尾提交只同步证据，最终发布 HEAD 以 GitHub 两分支和 ECS `.deploy-version` 为准。
- 本次不运行付费十万字作品生成；其质量需以实际选题/生成产物单独验收。Epic 总体仍为历史 `PARTIAL_IMPLEMENTATION`。
