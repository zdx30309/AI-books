# 通用长篇 v02 增量迭代

## 范围与实施计划（2026-09-15）

- 基线：`2faeb735f4500df551a5995b7705cb6e02c7d3dd`，原有未跟踪 `.tmp/` 保持不动。
- 新附件 SHA-256：`1f70baec84e1cd1f47ddb269574c1e7daa0d6ac54e6d71b8f1c9bbe996f8cbef`；完整解析得到 840 段、58587 文本字符，页眉页脚已读取。与旧版逐段比对，未变化段落复用此前的阅读结论，新增和修改段落逐一核对。
- 新增重点：正面样稿及其历史检测报告；原样稿明确改为反面诊断；跨题材迁移信息组织、参与者关系、连续因果与自然节奏；常用词按上下文功能判断而非机械禁词。两份报告属于附件历史材料，不是当前新稿的检测结果。
- 附件仍是应用 prompt 数据，不是要求本开发对话生成书稿。两篇样稿的角色保存在版本化数据中，业务代码不硬编码书名、正文、事实或单书模仿锚点。全文只进入抽象，生成只接收批准摘要和角色明确的抽象卡。

实施顺序：

1. 无损保留原始 Word、完整段落与全部附录；新版按正/反样稿、对应报告细分为 23 个连续分段；旧 v16 bundle 按原字节归档。
2. 新项目默认 v02；已有项目继续使用其固定版本，恢复付费调用仍读取原 prompt/response；未知版本显式阻断，不把旧项目静默切换到新规则。
3. 在既有参考抽象中增加正/反角色与来源分段校验；把批准方法/诊断传递到原来的规划、写作、润色、修复节点。加入有正文证据的章节与全书风格验收，缺项或颠倒角色阻断验收。
4. 工作区按项目显示实际版本、段落数、正反材料标注与验收记录；Word 配套编辑记录带上版本和风格复核。保留原串行链路、来源规则、字数与双 Word 协议。
5. 运行新版保真、旧版可恢复、角色校验、常用词不过度拦截、原文隔离、端到端模拟、API/Word/浏览器及 33 个旧分类回归；更新实施状态。
6. 用同一已提交 Git archive 增量同步 GitHub 与北京 ECS，保留配置、凭据、卷与旧镜像，核验线上版本与免模型费用的真实工作区路径。

## 阶段 1—2：完整打包与固定版本（PASS）

- v02 原始 DOCX、840 段、58587 文本字符与页眉页脚完整保留；23 个连续分段逐段重建通过。新增正/反样稿和报告仅路由至参考抽象节点，角色和历史检测信息从附件解析进入 manifest。
- v16 的 39 个原文件按 `2faeb73` 已提交字节归档；新增逐文件哈希回归，15 个节点的旧模板装配保持一致。
- 新任务默认 v02，旧项目按 state/config 固定版本继续；模型日志记录版本/源哈希，恢复调用复用原 prompt 与 response ID。未知版本或项目哈希不匹配在模型调用前停止。
- 相关验收：`33 passed, 1 existing Google SDK warning`（32.60s），含新旧原文完整路由、旧版串行运行、原响应恢复与 33 分类/132 prompt 哈希不变。
- 下一阶段只在本分类内增加对照抽象和风格检查；完整回归、浏览器验证及发布仍待执行。

## 阶段 3：对照抽象与有证据的验收（PASS）

- 新版卡片必须具有正确的正/反角色、来源分段和唯一 id，批准摘要同时覆盖正面方法、反面诊断、语境判断与历史报告边界。拒绝样稿书名、长原文片段、角色颠倒、重复 id、缺项以及机械禁词；旧版摘要格式保持兼容。
- 同一份批准卡和摘要进入原规划、起草、续写、一润、二润、局部修复和验收节点。只在提示词文件添加写作要求，不加入词语删除、事实替换或整书重写后处理。
- 每章及全书新增 `style_review`：正面方法、反面诊断、普通词语境检查均引用对应章实际正文短片段；全书审核覆盖全部章号。缺项、假证据、负面问题未消除、角色颠倒、机械禁词或冒用历史分数都会产生 high issue，沿用一次局部修复、重新抽取、复验、仍失败停住的链路。
- v02 专项、原通用长篇、模型断点和 API 回归：`145 passed, 1 existing Google SDK warning`（22.90s）。包括完整 12 章/105000 字模拟合稿、全书风格门禁、旧项目串行恢复、原 Word 元数据保真。前端 typecheck PASS。
- 实施中两条上传测试最初错误地假定“仅归档指令后不自动策划”；核对原链路后保留既有自动策划行为，并验证其不把指令当正文/素材，也不自动写章；修正测试后全部通过。

## 阶段 4：工作区与交付（PASS）

- 工作区根据项目固定版本请求 manifest；显示实际 840/23 或 571/19，清单错误时显示错误而非虚构默认数量。新增正反角色和历史报告标注，以及正文证据与普通词语境复核。
- 新建说明更新到 v02；旧项目显示保持原版本，不擅自更新正文。大纲、章节双 Word、全书的嵌入元数据包含版本与原文哈希；配套编辑记录显示风格复核，正文不混入记录。旧导出批次 journal 仍按原文件恢复。
- 浏览器验收 PASS：11 项场景、0 page errors，包括真实 Word 下载、v02 正反材料/证据显示、v16 固定版本、清单读取错误以及畸形模型风格记录的可查看性。未把模拟的 API/书稿当作真实模型质量结果。

## 阶段 5：最终本地验收（PASS）

| 检查 | 结果 |
| --- | --- |
| 原文、页眉页脚、全部分段、完整阶段路由 | v02 840/23、v16 571/19；原始 DOCX/全文/逐段哈希全部 PASS |
| 旧版模板与分类隔离 | 39 个归档文件原字节不变；15 个旧阶段装配不变；33 个其他分类/132 prompt 哈希及顺序不变 |
| 完整后端回归 | `477 passed, 1 existing Google SDK warning`，72.34 秒；无失败项 |
| Python / TypeScript / 前端构建 | compileall、typecheck、production build PASS；仅保留既有大 bundle 提示 |
| 浏览器 | 11 项场景 PASS、0 page errors；已查看 v02 与旧版工作区截图 |
| 模型与工作流模拟 | 12 章、105000 有效正文字数、逐章串行、双 Word、全书风格复核和旧付费断点恢复 PASS |
| 业务边界 | 没有改动其他分类 prompt、Epic canon、来源下载器、模型凭据或生产数据；业务代码没有硬编码样稿书名 |

新增细节回归：原文长片段即便跨换行也按真实字符串值校验，不因 JSON 转义而漏检；不合规的候选风格记录保持可查看，不让工作区崩溃。

本地证据：`.local/general_longform_v02/test_release_final.log`、`browser/browser_report.json` 与 `browser/workspace-*.png`。前端最终文件 `/assets/index-C-eKBrOS.js`，SHA-256 `75f709836445049a1af91c93f9e55e316d5688e78934c64f3624fd529a0c50ca`。

可复现：在仓库根目录设置 `PYTHONPATH=E:/video-plan/backend`，运行 `docs/GENERAL_LONGFORM.md` 中的完整回归命令，并增加 `backend/tests/test_general_longform_v02.py`；新版浏览器数据使用 `python scripts/smoke_general_longform_fixture.py --output .local/general_longform_v02/browser`，浏览器命令使用 `--fixture .local/general_longform_v02/browser/browser_fixture.json`。

## 阶段 6：发布（PASS，2026-09-15）

- 发布前已实际核对：GitHub 两分支与北京 ECS 均为 `2faeb735f4500df551a5995b7705cb6e02c7d3dd`；6 个服务运行；活动任务、Celery 默认队列及 unacked 均为 0；公开健康为 200 且 `celery_alive=true`。
- ECS 剩余 3759 MiB，继续使用提交增量与 Docker 缓存，不做无关清理。只应用本轮相关路径，保留 `.env`、ADC、代理、外部参考资产、数据卷、旧源码及镜像回滚。
- 发布工具已改为同时校验当前 v02 和归档 v16；线上验证仍使用免模型费用的真实 API→Celery import-first 流程，不自动生成付费整书。

实际发布与验收：

- 代码提交 `c38a90bac2792ae27003fa579b4319577885c333` 已通过 atomic push 同步到 GitHub 的 `main` 与 `codex/bilibili-cost-guard-main`，远端引用逐一核对一致。
- 从同一提交生成 82 文件、738473 字节的增量 Git archive；SHA-256 `252c1be1bb8dd892adc9d92e108324dfabc7cc312dbe073ba219a370d488a001`。逐文件核对旧基线和新内容后部署，没有覆盖无关路径。
- 北京时间 17:33 部署验收通过。backend/web 构建完成，worker/maintenance/beat 复用相同后端镜像；6 个服务运行。公开首页、健康和提示词清单均为 200，`celery_alive=true`。
- backend 与 worker 各核对 71 个本轮后端文件哈希；当前 v02 840/23 与旧 v16 571/19 的完整原文、逐段路由、39 个旧归档文件、33 个其他分类/132 prompt 均 PASS。
- 线上前端 `/assets/index-C-eKBrOS.js` 与本地已测试构建逐字节一致，SHA-256 `75f709836445049a1af91c93f9e55e316d5688e78934c64f3624fd529a0c50ca`。对已发布前端再次运行 11 项确定性浏览器场景，0 page errors。
- 真实线上验收（不是 API mock）：临时普通用户经真实 API 和 Celery 建立 v02 `awaiting_materials` 工作区；确认 v02 默认版本、旧版查询、34 分类及原默认分类；DOCX 上传/重复检测和 owner 鉴权 PASS。模型调用 0、canon 写入 0，测试用户、任务和文件已清理。
- `.env` 哈希和所有容器挂载的完整属性保持不变；没有改动既有业务数据、ADC、代理、参考资产和 Redis。剩余磁盘 3655 MiB，未执行全局清理。
- 源码、原版本和 5 个回滚镜像保留在 `/opt/video-plan-release-backups/20260915T093101Z-c38a90b/`。收尾文档单独同步，不重建或重启服务；最终 GitHub HEAD 与 ECS `.deploy-version` 同步核对。
- 证据：`.local/general_longform_v02/releases/20260915T093101Z-c38a90b/result.json`、`ecs_workspace_smoke.json`、`browser_production/browser_report.json`。

本轮通用长篇 v02 的实现、回归和代码发布无剩余阻断项；所有新增附录与规则均完整归档并有工作流位置。整书文本验证使用确定性模拟，未执行真实付费十万字创作或承诺检测通过率；Epic 的历史 `PARTIAL_IMPLEMENTATION` 不变。
## 阶段 7：两个校验断点的低成本恢复修正（PASS，2026-09-20）

- 动机：首版大纲只差本地结构校验时会把项目停在“有待处理项”且无按钮；来源抽象条目全部未通过核验时同样停住，用户只能选择重新检索（真实多花一次 web 搜索）。
- 改动（纯加法，未动任何专属 prompt）：`plan` 校验失败后用既有 `outline_revision` 节点做有界局部修复（复用本次证据，修复后仍过同一 `validate_outline`）；新增 `repair_outline_candidate` 动作让已卡住的项目复用 `last_research + sources` 零联网修复候选；`research()` 在同批已读页面上做有界重试并把被拒条目与原因作为 `rejected_entries` 数据回传，单 slot 上限 3 次，尝试次数从 gateway 日志读出；“恢复上次操作”因此每点一次只多 1 次来源抽象调用。
- 发布提交 `669dc419cf79e1c8bb73919a8d28ba9b2125cdec`（GitHub `main` 与 `codex/bilibili-cost-guard-main` 同一 SHA），北京 ECS release `20260920T080857Z-669dc41`，base `518d61e`，备份 `/opt/video-plan-release-backups/20260920T080857Z-669dc41/`。
- 验收：首页/健康/prompt-manifest 200 且 `celery_alive=true`；线上前端 `/assets/index-CRhfK2Xc.js` 与本地构建逐字节一致；backend/worker 内 v02 840/23 与 v16 571/19 完整性与 132 个历史 prompt 哈希 PASS；部署文件哈希 6/6 PASS；生产前端浏览器回归 13 场景 PASS、0 page error（`.local/general_longform_v02/browser_production/browser_report.json`）。
- 环境侧记录：ECS 对 Docker Hub 直连超时，构建前用 `docker.1panel.live` 预取 node/caddy/python 基础镜像（digest 与构建解析一致）；compose worker 原运行镜像已被回收，发布脚本在该情况下回退到 `video-plan-backend:latest` 生成回滚标签并记录 note。发布后剩余磁盘约 2.6 GiB。
- 未做：未运行真实十万字付费生成；未改动 prompt、验收门禁、串行链路、其他 33 个分类与生产数据。
