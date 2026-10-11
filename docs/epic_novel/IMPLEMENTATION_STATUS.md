# Epic Novel Engine Implementation Status

## AI橡皮擦最终交付阶段（2026-10-10）

- 用户确认范围：保留两个审稿导航入口；所有新书在正文生成末尾串行调用朱雀 v1 段落增强，再使用用户提供的完整双稿纠错指令；暂不接入检测、PDF报告或条件性 v2 改写。两个 Word 工作流的相似度/检测分支不属于本轮启用范围。
- 完成：主书籍任务、公版长篇、通用长篇终稿交付接入；Epic 全书文本导出仅在全部目标章节 accepted 后生成独立交付副本，绝不修改 acceptedContent 或 canon。标题和图像链接保护；分段断点、付费请求 in-flight 日志、防并行锁、无法定位/严重问题人工复核。
- 文件：原稿 Markdown/Word、降AI原稿、三列微软雅黑10号纠错Excel、作者为“降AI后纠错”的修订Word和ZIP。书籍详情新增交付包按钮，不替换原有导航；漫画原有图文Word下载保持原实现。
- 配置：默认关闭；生产启用时使用 AI_OR_FINALIZER_START_AT 隔离历史书籍。密钥只存生产环境，不进入代码或Git。纠错模型沿用独立 DeepSeek 配置，记录模型、usage、status、errors；模型响应未提供实际账单时 cost=null，不伪造费用。
- 测试：新增13项离线测试通过；全量后端回归1252 passed / 1 skipped；前端typecheck/build通过。修订Word样例渲染验证，正确显示中文及增删痕迹（本地渲染需显式指定系统中文字体目录）。没有执行真实付费降AI或纠错调用；生产验收不等于证明模型能达到某个AI率。
- 发布准备：北京ECS基线66d605a，现有任务无pending/processing；经用户许可仅清理7天以上未使用构建缓存，释放2.688GB，磁盘可用从593MB到2.5GB。需保留回滚镜像和源文件，发布后核对服务、配置、API与文件哈希。磁盘仍紧张，建议扩容。
- 限制：通行证此前设置50000字符总额度，段落增强双倍消耗；不足时人工暂停，不自动修改额度。请求结果不确定时禁止自动重复扣费，必须查平台记录；不会自动执行Word没有定义的中间阈值分支。完整Epic验收清单状态不因本次改变。
- 发布验收（2026-10-10美国东部 / 2026-10-11北京）：代码提交d4482bb及输出预算补丁7f8437b已推送origin/main；北京ECS采用原镜像上的增量层发布，未重建Redis或删除任何数据卷。回滚源码、环境及旧镜像保留在/opt/video-plan-release-backups/ai-or-d4482bb。九个核心部署文件SHA256与Git一致，前端bundle为index-CprEAvCT.js，网站HTTP200、后端/health HTTP200、公开/api/v1/system/health HTTP200；交付包匿名请求401，认证保护有效。
- 用户批准1元以内真实小样：仅测试自造短文本，朱雀v1段落增强收费0.014元；双稿纠错DeepSeek配置名deepseek-v4-flash，7692输入token、5输出token，流程completed，产出Word/Excel/ZIP。按官方Flash峰值价格和保守汇率计算的调用前预算上限0.1815元，未调用检测或v2。真实纠错输出设置4096token上限、关闭无用thinking，结果截断时人工暂停。
- 生产仅对created_at>=2026-10-11T00:09:31Z的新书启用；任务数量发布前后相同：completed1164、failed5、manual_review9、paused2，无pending/processing。磁盘清理后仍97%，约2.4GB可用，仍需扩容。之前10月6日的四项本地修复保持未提交，未夹带发布。未进行完整新书的付费生成验收，因此不保证所有书籍的语义纠错效果或任何检测分数。

## 通用长篇：服务端自动连跑的守护与三重止损（2026-09-24，第八轮）

- 现状：第 1—7 章 @@@accepted@@@（全书 @@@58480@@@ 字），第 8 章已走完 4 段初稿 + 润色复核 + 首次验收，正在做一次局部修订后复验。
- 自动连跑从「单进程一次性脚本」升级为 @@@/opt/auto_guard.sh@@@（守护）→ @@@/opt/auto_driver.sh@@@ v5（提交器）：守护脚本只在书未完结时重启提交器；提交器只在任务真正空闲（@`pending`/`processing` 一律当忙）时用与工作区按钮完全相同的动作接口提交下一步。
- 三重止损：①同一动作 + 同一进度指纹连续 3 次 → 立刻停机；②连续 @@@24@@@ 次付费步进仍没有新章 @@@accepted@@@（正常一章约 8—12 步）→ 立刻停机；③守护脚本读到同样的停机信号连续两次 → 整条自动连跑下线等人。所有停机都只「停」，不自动重放会付费的请求。
- 磁盘治理：ECS 根分区一度到 93%（剩 5.9 GiB）。清掉 docker build cache（2.63 GB）与 2026-09-22/23 的旧 rollback 镜像后回到 82%（剩 15 GiB），保留最近两套可回滚镜像。
- 文档：@@@docs/通用长篇新书使用指南.docx@@@ 出 v3，新增「第七步 服务端自动连跑（无人值守，推荐）」：每步做什么、三重安全阀、停机对照表、成本分布、只读进度命令（@@@python .local\\tmp\\ssh_file.py .local\\tmp\\q_now3.sh@@@）、暂停/停掉/重接（@@@bash /opt/gl_restart.sh@@@）与新书首跑说明；首页版本说明与一次性跑通流水线同步更新。
- 验证：@@@bash -n@@@ 两个脚本通过，脚本内嵌 Python 块 @@@compile()@@@ 通过；指南 docx 重新生成后 14 个一级章节齐备，正文含 @@@auto_guard@@@/@@@auto_driver@@@/@@@q_now3.sh@@@/@@@TOTAL_SPEND_USD@@@/@@@gl_restart.sh@@@ 关键标记。
- 未变：写作用 @@@backend/app/prompts/general_longform/**@@@ 未改；默认 @@@verified@@@ 来源策略、验收门、@@@100000—110000@@@ 字数与双 Word 契约不变。

## 通用长篇：写入上下文超出限额 + 失败重试重复付费检索（2026-09-24，第七轮）

- 结果：第 6 章《第十三个念头》@@@accepted@@@（8258 字），全书 1—6 章 @@@49553 字@@@；第 7 章已启动（四小节 2623/2607/2257/2368 全部写完）。
- 复现（线上 @@bc69c362@@）：第 7 章四小节写完、进入一润时抛 @@@本节点上下文为 355143 字符，超过 350000 限额；未截断原文或前文@@@，@@phase=needs_review@@@，本章正文与断点全部保留。重试后仍在同一道闸门失败，且每次重试都从 @@research@@ 重新开始付费。
- 根因一（上下文被记录占满）：@@context()@@ 把 @@accepted()@@ 的每章完整记录（@@audit@@ 6—8k、@@issues@@ 1—2k、@@research@@ 4—10k、哈希与本地路径）和 @@sources@@ 的工具记录（@@file_sha256@@/@@local_path@@/@@read_scope@@/@@retrieved_at@@/@@provider@@/@@query@@…）原样塞进 prompt 的 JSON。第 7 章的 @@previous_chapters@@ 一项就是 @@@186902@@@ 字符，其中真正的正文只有约 5 万字；到第 12—13 章必然再次超限。
- 修复一（@@generation_chapters()@@ / @@generation_sources()@@）：生成上下文改为投影——上一章只保留 @@number/title/word_count/content@@ 与从 @@audit@@ 取出的 @@summary/endingHook@@；来源只保留引用字段（@@id/url/title/authors/year/publication/doi/bibliography/approved_summary/supported_claims/limitations@@）。这是同一本书、同一份正文，只是不再把写作用不到的记录送进窗口。@@state.json@@ 本身逐字段不变，程序侧校验（@@chapter_issues@@/@@validate_audit@@/验收状态表）仍拿完整对象。
- 根因二（失败重试重复付费）：@@research@@ 的断点目录按 @@request_id@@ 隔离，一旦上游某阶段失败、作者再点一次，就会为同一章重新付一次联网检索（线上实测 @@$0.62—0.69/次@@）。
- 修复二（@@research_signature()@@ / @@reusable_research()@@）：同一 slot 且"问法指纹"一致（章节契约／大纲／作者反馈 + prompt 版本）时，直接复用这本书已经付过费的 @@approved.json@@，不再联网、不再抽象，只把它写进新请求目录当本请求的断点。指纹不一致（换了章、改了大纲、换了 prompt 版本）一律重新检索。
- 验证（2026-09-24 本机）：@@python -m pytest backend/tests -q -k general_longform@@@ → @@@281 passed@@@。新增 8 项：@@test_general_longform_research_reuse.py@@（5 项：重试复用已付费检索、换章节契约重新检索、换反馈重新检索、邻章不互相借用、换 prompt 版本重新检索）与 @@test_general_longform_context_budget.py@@（3 项：前文正文与摘要齐全而去掉验收记录、来源保留引用而去掉工具记录、第 13 章上下文留在 350000 限额之内而同内容不投影时必然超限）。@@rejected.json@@ 为空、只读到 1 个桩页面的失败重试仍然照旧重新检索（不会把不可读的来源锁死）。
- 不变项：@@backend/app/prompts/general_longform/**@@ 逐字节未改；默认 @@@verified@@@ 来源策略、@@validate_outline@@、来源 URL/哈希核验、验收门、严格串行、每章验收、@@100000—110000@@ 字与双 Word 规则全部不变。

## 通用长篇：修复稿把正文改坏、旧结论顶替新结论（2026-09-24，第六轮）

- 结果：第 5 章《光怎样改时间》@@@accepted@@@（8740 字，两份 Word 已导出），全书 1—5 章 @@@41295 字@@@，@@@phase=ready_for_chapter@@@；随后已开第 6 章（请求 @@@98213455@@@）。
- 复现：第 5 章走完「初稿 10579 字 → 验收（程序判定超出计划 +32%）→ 自动局部修复 → 复验」时，修复返回的是整章重写：篇幅收到 7774 字（合规），却把「褪黑素边界」一节从 2376 字压到 @@@1454 字@@@，低于本章对该小节的程序下限 1600 字。于是复验被同一条程序校验拦下（@@@local_issues: 小节褪黑素边界严重不足@@@），作者又要为第二次重写付费。
- 根因一：@@@_check_and_accept()@@@ 里的修复调用（@@@slot=chapter-N-qa-repair@@@）把返回文本直接当成正文送进复验，没有任何标题/篇幅/小节下限校验——而一润、二润两条链早有 @@@0.95—1.05 / 0.90—1.10@@@ 的篇幅保护；被保护的是那两条，真正改写正文的这条反而没有。
- 根因二：验收槽位按 @@attempt@@ 命名（@@@chapter-N-attempt-M@@@），而 ModelGateway 对同一槽位一律复用已持久化的 prompt 与响应。修复后再平衡过的正文换了、槽位没换，于是一次新付费的复验实际拿到的仍是**再平衡前**的 prompt，结论自然是旧正文的缺口。
- 修复一（@@@_restore_shortened_sections()@@@）：修复稿在送验前先做再平衡。只对被修复稿压到下限以下、而修复前达标的小节各做一次有界续写（@@@continue@@@，@@@max_tokens@@@ 按本章剩余篇幅上限收敛），续写超出上限时按整段裁尾、绝不裁到小节下限以下；修复前就已经偏短的小节不花任何调用；两次续写仍不足时写入 @@@rebalance_notes@@@ 留痕而不是静默放过。
- 修复二（@@@_fit_extended_section()@@@）：段落级裁尾，保证再平衡后的整章仍落在 @@@target*1.12@@@ 的上限内，不会为了补一节而吞并下一章。
- 修复三（验收槽位）：再平衡发生时时，本次复验改用 @@@chapter-N-attempt-M-rebalanced@@@ 独立出结论，旧槽位的已付费结论作为历史保留。同一本书 resume 时：正文未变的槽位照旧复用（0 费用），正文变了的槽位必然重新出结论。
- 验证（2026-09-24 本机）：@@@python -m pytest backend/tests -q -k general_longform@@@ → @@@273 passed@@@（新增 3 项：修复压坏小节时按续写补回并验收通过、修复前就偏短的小节不产生任何调用、续写会顶破章上限时只留 @@@skipped@@@ 记录）。新增用例断言复验槽位为 @@@chapter-1-attempt-1-rebalanced@@@。
- 发布（2026-09-24）：@@@63d3703@@@（再平衡）→ @@@9b6a3cf@@@（按剩余上限收敛续写 + 段落裁尾）→ @@@417040d@@@（独立复验槽位），三次增量发布到北京 ECS，@@@.deploy-version=417040d@@@，备份 @@@/opt/video-plan-release-backups/20260923T161923Z-63d3703@@@、@@@...T163221Z-9b6a3cf@@@、@@@...T164905Z-417040d@@@；每次均通过 prompt 校验、@@@DEPLOYED_CODE_HASHES_PASS@@@、公开首页/健康/manifest 200。
- 成本账（线上实测）：第 5 章补跑新增 4 次调用约 @@@$2.64@@@ —— 续写小节 @@@$0.78@@@、被旧 prompt 顶替的复验 @@@$0.93@@@、再平衡后的新复验 @@@$0.93@@@；第二次续写命中缓存 @@@$0@@@。全任务累计 86 次调用，第 5 章此前已花约 $6.5。
- 不变项：@@@backend/app/prompts/general_longform/**@@@ 逐字节未改；严格串行、每章验收、只有 @@@acceptedContent@@@ 写验收状态表、100000—110000 字与双 Word 规则不变。

## 通用长篇：余额耗尽被当成交付待定（2026-09-23，第五轮）

- 复现：任务 @@@bc69c362@@@ 的第 5 章。连续两次章节验收调用都以 @@@status=failed@@@ 结束，落库信息是 @@@paid_call_delivery_uncertain@@@ / "OpenAI background response delivery failed after the request may have been accepted; do not submit an automatic replacement"，工作区显示「恢复上次操作（先核对交付待定调用）」——把一个根本不存在、也从未计费的交付摆到作者面前要求确认。
- 真实原因（线上拿密钥 @@@responses.retrieve@@@ 那两个 response）：@@@error.code=credit_balance_exhausted@@@、@@@message="You have no credits remaining..."@@@、@@@output=[]@@@、@@@usage=null@@@、@@@model=gpt-5.6-sol@@@。即 provider 在生成任何内容前就拒绝，既没有产出也没有扣费；第 5 章四小节正文、一润、二润、修复全部完好保存在断点里。
- 根因一：@@@_openai_response_debug_summary()@@@ 丢掉了 @@@response.error@@@，@@@_wait_for_openai_background_response()@@@ 只要看到 @@@status != completed@@@ 就统一抛「交付待定」，把平台侧的额度决定误判为「请求可能已被接受」。
- 修复一：新增 @@@_openai_response_error_text()@@@，调试摘要带 @@@error=...@@@；provider error 命中硬额度/计费信号时 @@@paid_api_guard.trip()@@@ 并抛 @@@PaidApiQuotaExhausted@@@，作者看到的是真实原因。@@@general_longform_runtime.provider_failure_status()@@@ 同时把 provider 的 @@@status=failed@@@ 写进调用记录，作者确认重试后可以清掉死 @@@response_id@@@ 并只重发一次。
- 根因二（线上手工修复并入 git）：章节流程里既有 @@@02_polish.md@@@ 被判「章节序列被拆分、合并、改号或遗漏」后会清空 @@@polished_markdown@@@，但紧跟的阶段闸门是 @@@elif@@@，被清空的值进不了闸门——一润整段不跑，下一阶段拿到空稿（线上日志 @@@二润返回空稿@@@）。改为 @@@if@@@，并补一条回归用例（改回 @@@elif@@@ 时用例失败、改回 @@@if@@@ 时通过）。
- 验证（2026-09-23 本机）：@@@python -m pytest backend/tests -q -k general_longform@@@ → @@@270 passed@@@；@@@content_engine@@@ 相关 11 个测试文件 @@@447 passed@@@；@@@test_category_prompt_routing.py@@@ 单独 @@@113 passed@@@。
- 发布（2026-09-23 06:00 本地）：@@@4c3ac5a@@@ 以 @@@--base af0a333@@@ 发布到北京 ECS，3 个文件增量（@@@.deploy-version=4c3ac5a@@@，备份 @@@/opt/video-plan-release-backups/20260922T220053Z-4c3ac5a@@@）。线上那份未提交的手工修复以内容哈希白名单放行：既不回退它，也不接受未知基线；发布后线上 @@@content_engine.py@@@ 与 git blob 逐字节一致（@@@633c7d0a@@@），漂移清零，后续发布不再需要白名单。prompt 校验、@@@DEPLOYED_CODE_HASHES_PASS 3@@@、公开首页/健康/manifest 200、前端 bundle @@@/assets/index-mYELcrQ1.js@@@ 未变。
- 待作者执行：第 5 章正文已写完（@@@chapters/05/v01/draft.txt@@@ 34675 B，四小节 9142/7739/9213/8464 B），只差最后一次验收调用（约 @@@$1@@@）。线上 OpenAI 账户余额耗尽，充值后在通用长篇工作区点一次「恢复上次操作」即可验收第 5 章；充值前反复重试只会重复撞额度，不产生新费用但也不前进。
- 不变项：@@@backend/app/prompts/general_longform/**@@@ 逐字节未改；严格串行、每章验收、只有 @@@acceptedContent@@@ 写验收状态表、100000—110000 字与双 Word 规则不变。

## 通用长篇：来源缺口的措辞族识别（2026-09-23，第四轮）

- 复现：任务 @@@bc69c362@@@ 的第 4 章《晚睡的分界》。第一次验收真的发现问题（@@@11365@@@ 字超计划 42%、诊断门槛结论反复重复），系统按设计做了一次局部修复并复验；复验后剩下的 4 条阻断项全部是「来源覆盖不足 + 它指名的小节未落实」，措辞是 @@@专项来源未成功读取@@@ / @@@本轮没有成功读取@@@ —— 旧的豁免清单只认得较早的「未完整列出 / 未覆盖 / 未取得原文」，于是同一件事换个说法又拦了一轮。
- 修复：@@@coverage_limited_fact()@@@ 在字面清单之外增加 @@@COVERAGE_NEGATION@@@ 正则，统一识别「否定 + 读取/取得/获取/检索/读到/找到/核实/覆盖/包含/提供/列出/说明/展开/支持/佐证」这一族；真正的冲突、编造、事实错误仍先由 @@@CONTRADICTION_MARKERS@@@ 拦下并继续阻断，@@@verified@@@ 策略下整段逻辑不生效。
- 验证（2026-09-23 本机）：@@@python -m pytest backend/tests -q -k general_longform@@@ → @@@265 passed@@@（233s），本轮新增 5 项：第 4 章真实出现过的 4 种措辞全部识别为缺口、同措辞但含 @@@不符@@@ 的冲突仍阻断、纯错误陈述（无覆盖措辞）仍阻断、@@@search_trusted@@@ 下按真实措辞直接验收通过且不触发 repair、指名小节与真实措辞的关联豁免同时生效。
- 真实解封（2026-09-23 04:26 本地）：发布 @@@fc535f9@@@ 后重放原请求 @@@15c49780@@@（@@@.deploy-version = fc535f9@@@，备份 @@@/opt/video-plan-release-backups/20260922T202340Z-fc535f9@@@，prompt 校验与前端 bundle 与上一版逐字节一致），复用了已付费的两次验收与已付费的修复正文：第 4 章 @@@accepted@@@（8343 字《晚睡的分界》，两份 Word 已导出），全书 @@@1—4 章全部验收、共 32555 字@@@，本轮新增调用 @@@0@@@ 次、新增费用 @@@$0@@@。留痕：第 4 章 @@@policy_downgrades@@@ 记录 2 条 acceptance（小节「早晚有倾向」「社会时间差」）与 2 条 fact。
- 成本账（线上实测，72 次调用）：累计 @@@$28.89@@@（记账口径 5/30；真实 gpt-5.6-sol 为 4/20，实际约 21—22 美元）。用户在两次失败上重复付费的两条路径——「研究并写作第 N 章重跑检索」与「按验收问题修订第 N 章新建检索」——现在都不再必要：措辞族识别后，来源覆盖类缺口在章节验收门直接降级留痕。
- 不变项：@@@backend/app/prompts/general_longform/**@@@ 逐字节未改；验收门、严格串行、每章验收、100000—110000 字与双 Word 规则不变。


## 通用长篇：验收门的三处死锁（2026-09-23，第三轮）

- 复现的真实停滞：任务 @@@bc69c362-658f-4d67-a00e-e6e4d7c6e997@@@ 的第 2 章《一夜怎样过去》。第 1 章已验收（8215 字），第 2 章连续两次整章停在 @@@needs_review@@@，用户为此重复付费约 @@@$3.47@@@。
- 卡点一（JSON 尾随字符）：@@@acceptance_*.json@@@ 的 @@@text@@@ 是合法 JSON 但尾部多一个 @@@}}}@@@（@@@len=8150@@@），旧 @@@parse_json@@@ 只做 @@@json.loads@@@ 就抛 @@@GeneralLongformReview@@@，整章死锁，唯一出路是重跑整个 @@@next_chapter@@@。
- 卡点二（豁免策略只作用于大纲）：断点里 @@@source_policy=search_trusted@@@（@@@gap_waiver.count=10@@@），但只有 @@@validate_outline@@@ 认这个策略，@@@_check_and_accept@@@ 完全不认，于是每轮验收都把同一条已豁免的来源覆盖不足重新当 high fact 打回。
- 卡点三（验收回复与正文对不上 + 逐小节抖动）：@@@planned_vs_actual[].evidence@@@ 不在本章正文里，且每轮选中的「未完成小节」都不同（@@@睡眠有层次@@@ → @@@醒来未必坏@@@），重问一次就换一个理由。
- 修复一（解析器）：@@@general_longform_runtime.py@@@ 的 @@@parse_json@@@ 重写为容错框架解析（去围栏 / @@@raw_decode@@@ 取首个完整对象 / 去尾逗号 / @@@_close_open_brackets@@@ 只补完整值的尾部，截断在字符串中间时按最后一个逗号回退，绝不补空对象），新增 @@@parse_json_detail@@@、@@@JSON_INCOMPLETE@@@、@@@JSON_OBJECT_ONLY@@@ 与 @@@ModelGateway.record_path()@@@；解析成功且发生过框架修复时把 @@@parse_repaired@@@ + @@@parse_repair_note@@@ 写回已付费的调用记录，不重发。
- 修复二（策略贯通）：@@@apply_source_policy()@@@（@@@COVERAGE_ONLY_MARKERS@@@ / @@@CONTRADICTION_MARKERS@@@ / @@@coverage_limited_fact()@@@）把章节验收门与作者已选的来源策略对齐：@@@search_trusted@@@ 下，只说明已读来源覆盖不足、且不含冲突/编造标记的 high fact 降为 medium 并留痕（@@@downgraded_by/downgraded_reason@@@）；@@@verified@@@ 下完全不生效。
- 修复三（重问而不改文）：@@@audit_defect_only()@@@ + @@@_audited_chapter()@@@（@@@AUDIT_REASK_LIMIT=1@@@）在阻断项全部只是验收回复自身缺陷（证据不在正文、未知卡片、Planned-vs-Actual 对不上）时重问验收，而不是拿正文去修；@@@_acceptance_audit()@@@ 只在 payload 完全无法解析时补一次显式 @@@-json-retry@@@ 调用。@@@validate_audit@@@ 的逐小节检查改为带 @@@section@@@ 的 high issue（指名小节），@@@apply_source_policy@@@ 把「该小节缺口正是已豁免的覆盖不足」关联降级，止住「每轮换一个未完成小节」。
- 验证（2026-09-23 本机）：@@@python -m pytest backend/tests -q -k general_longform@@@ → @@@260 passed@@@（161.95s），本轮新增 16 项（运行时 7 + 引擎 9）；另外用线上真实 audit payload 做过离线回放确认三个根因。
- 真实解封（2026-09-23 01:24 本地）：恢复第 2 章后 @@@phase=ready_for_chapter@@@、第 2 章 @@@accepted@@@（7947 字，标题《一夜怎样过去》，正文与来源编辑记录两份 Word 已导出），全书 @@@total_words=16162@@@；本轮新增调用 @@@0@@@ 次、新增费用 @@@$0@@@（复用 19:03 那次已付费的验收回复）。豁免留痕：@@@policy_downgrades@@@ 记录 1 条 acceptance（小节「醒来未必坏」）与 2 条 fact。
- 成本账（线上实测）：全任务 50 次调用累计 @@@$17.02@@@（第 1 章 @@@$3.71@@@；第 2 章 @@@$8.22@@@ = v01 @@@$4.75@@@ + 重跑 @@@$3.47@@@；策划/检索/大纲 @@@$5.09@@@）。当前记账口径 @@@5/30@@@ 是 gpt-5.5 的价格，实际 @@@gpt-5.6-sol@@@ 为 @@@4/20@@@，上述数字属于高估（只高不低）。
- 发布（2026-09-23）：@@@588f39c@@@（解析器 + 策略贯通 + 重问）先手工部署并写 @@@.deploy-version=588f39c@@@；@@@70409dd@@@（指名小节 + 关联豁免）以 2 文件增量发布到北京 ECS（@@@--base 588f39c@@@，@@@.deploy-version=70409dd@@@，备份 @@@/opt/video-plan-release-backups/20260922T192138Z-70409dd@@@）。两次发布都通过 backend/worker 固定 prompt 校验（v02 840 段/23 节、v16 571 段/19 节、39 个归档原始文件、其它 33 分类 132 条哈希不变）、@@@DEPLOYED_CODE_HASHES_PASS@@@、公开首页/健康/prompt manifest 均 200、前端 bundle @@@/assets/index-mYELcrQ1.js@@@ 未变。
- 发布前磁盘回收：删掉 3 组最旧 rollback tag 与一个误标的 web rollback（约 4.4 GB），根分区 96% → 90%。
- 不变项（硬约束复核）：@@@backend/app/prompts/general_longform/**@@@ 逐字节未改；@@@validate_outline@@@、来源 URL/哈希核验、@@@verified@@@ 默认策略、严格串行、每章验收、100000—110000 字与双 Word 规则全部不变；@@@draftContent@@@/@@@repairedContent@@@ 仍不写入验收状态表。
- 使用指南同步：@@@docs/通用长篇新书使用指南.docx@@@ 新增「本次修好的三处卡点（2026-09-23）」章节、2 条新 FAQ 与费用表条目，版本口径改为 @@@70409dd@@@（7 页，PDF 渲染校对：无右/下溢出）。


## 通用长篇：来源策略开关 + 自动连跑到成书（2026-09-22，第二轮）

- 复现的真实停滞：任务 @@@bc69c362-658f-4d67-a00e-e6e4d7c6e997@@@（B 站《睡眠革命》视频）停在 @@@needs_review@@@，@@@error_message@@@ 是「策划仍有待核实材料，请补充来源或局部调整大纲后再确认」，@@@confirm_outline@@@ 被拒 → @@@generate_chapter@@@ 因 @@@confirmed=False@@@ 必然再拒。断点里已有 7 条已核验来源、13 章大纲、10 条 research_gaps，全部来自「上游页面正文读不到」（PubMed cookie 页 / PMC reCAPTCHA / 指南正文不可读）。
- 这不是模型问题也不是架构问题，而是**缺一条不花钱的出口**：旧版只有「重新联网检索」一种恢复路径，而那 10 条 gap 再检索一次仍然读不到，于是形成「失败→恢复→仍缺→再恢复」的死循环。本轮补上出口，同时**不放宽任何验收门**。
- 新增来源策略 @@@source_policy@@@（@@@verified@@@ 默认 / @@@search_trusted@@@）与动作 @@@set_source_policy@@@（引擎 + API @@@Literal@@@ + 前端 @@@LongformAction@@@）。@@@search_trusted@@@ 只影响 @@@research_gaps@@@ 这一道确认门：@@@confirm()@@@ 与导入大纲路径在勾选后不再因未核实要点拒绝，并写入 @@@gap_waiver@@@（策略、条数、时间、request_id、要点摘要）留痕；**要点本身不删除**，仍在大纲与导出里可见。改动前后 @@@validate_outline@@@、来源 URL/哈希核验、验收门、严格串行、每章验收、100000—110000 字与双 Word 规则全部不变。
- 新增自动连跑：@@@backend/app/services/general_longform_autopilot.py@@@（纯读状态决策表，@@@auto_next@@@ 返回动作或必须停下的原因）+ 引擎动作 @@@set_auto_run@@@ + 工作区勾选框。只执行 @@@AUTO_ACTIONS@@@ 内的既有动作，走同一条 API、同一套校验、同一套断点；只在「需要新联网检索 / 交付待定或人工闸门 / 同一处连续两次修不好」返回 @@@stop@@@。**自动连跑不会自己切换来源策略**（那是作者的判断，必须人工勾）。
- 指引同步：@@@general_longform_guidance.py@@@ 在 gaps 未清空时给出「直接采用联网检索结果（不产生生成调用）」备选；勾选后主按钮变成「确认选中包装与大纲」，@@@auto@@@ 步骤同步为 @@@confirm_outline@@@。
- 前端：@@@GeneralLongformPanel.tsx@@@ 下一步面板新增来源策略勾选框（带当前待核实条数与策略说明）与自动连跑勾选框（当前自动步骤 / 暂停原因 / 连续失败提示）。
- 验证（2026-09-22，本机全量重跑）：@@@python -m pytest backend/tests -q -k general_longform@@@ → @@@242 passed, 960 deselected@@@。本轮新增 8 项：引擎 4 项（@@@revise_outline_local@@@ 复用已付费证据且检索调用数不变、gap 未清空即失败且不能静默返回、无证据时拒绝、@@@set_auto_run@@@ 零调用幂等）+ 引擎 2 项（@@@search_trusted@@@ 直接确认并写出第一章、默认策略仍拒绝且非法策略被拒）+ 决策表 2 项（信任策略下自动确认、自动连跑不自行改策略）+ 指引 1 项（信任前给切换备选、信任后主按钮为确认）。@@@npm run typecheck@@@ → PASS。
- 不变项（硬约束复核）：@@@backend/app/prompts/general_longform/**@@@ 逐字节未改（发布校验照旧比对 v02 840 段/23 节、v16 571 段/19 节、39 个归档文件与其余 33 分类 132 条哈希）；无数据库迁移；新增动作中 @@@set_source_policy@@@ 与 @@@set_auto_run@@@ **不含任何模型调用**。
- 发布（2026-09-22/23）：提交 @@@f9ffa34@@@ 同时推送到 GitHub @@@main@@@ 与 @@@codex/bilibili-cost-guard-main@@@，再以 11 个文件的字节校验增量发布到北京 ECS（@@@--base abcb3ed@@@，@@@.deploy-version = f9ffa34@@@，备份 @@@/opt/video-plan-release-backups/20260922T145946Z-f9ffa34@@@）。发布校验：backend 与 worker 都通过固定 prompt 校验（v02 840 段/23 节、v16 571 段/19 节、39 个归档原始文件、其它 33 分类 132 条 prompt 哈希不变），7 个后端文件哈希逐一核对，公开首页/健康检查/prompt manifest 均 200 且 celery alive，前端 bundle @@@/assets/index-mYELcrQ1.js@@@（sha256 @@@73b63d56…@@@）与干净工作树构建逐字节一致。
- 发布过程记录（避免误判）：第一次发布在 @@@docker compose build backend web@@@ 的 apt/pip 层被 SSH 读超时打断（11 个源文件已落地、备份已建、镜像未重建、版本号未动）；随后在服务器上以 @@@setsid nohup@@@ 后台重跑镜像构建并轮询日志，手工完成 rollback 打标与 @@@docker compose up -d --no-build --no-deps@@@，再用 @@@--resume-release 20260922T145946Z-f9ffa34@@@ 完成全部校验并落版本号（@@@RELEASE_PASS@@@）。构建缓存已清理，根分区剩余约 7.2 GB。
- 真实停滞任务的解封（2026-09-22）：@@@bc69c362@@@ 按作者选择置 @@@source_policy=search_trusted@@@（@@@gap_waiver@@@ 记录 10 条未取得原文的要点）并完成 @@@confirm_outline@@@（packaging_index=1，《忙到很晚，也要好好睡觉》），阶段由 @@@needs_review@@@ 变为 @@@ready_for_chapter@@@；只读体检 @@@scripts/general_longform_doctor.py@@@ 输出「下一步：研究并写作第1章」。

## 通用长篇：把「卡住之后不知道点哪」这一整类问题解决（2026-09-22）

- 背景：用户输入视频链接后，项目停在「有待处理项」，主操作按钮消失或点了也没用；上一轮的停滞排查只到命令行（新增了 @@@scripts/general_longform_doctor.py@@@ 和文档表格），工作区本身仍然不告诉用户下一步。本轮把这类问题一次解决到「任何停下都有可点的一步」。
- 解决的五个子类：
  1. 停滞时工作区没有可执行的下一步，用户只能猜；
  2. 明确会再次被同一条规则拦下的按钮仍然可点（最典型：字数不在 100000—110000 时反复点「全书终审与合稿」，每次都失败）；
  3. 大纲局部修订失败后只剩「重新联网检索」这条昂贵路径；
  4. 模型返回形状错误的补丁时，要么直接卡死，要么（顶层字段被丢弃后）**静默通过、版本号照涨但内容没变**；
  5. 命令行体检与工作区文案口径不一致。
- 新增 @@@backend/app/services/general_longform_guidance.py@@@：纯函数 next-step 指引。只读持久化状态与任务配置，**不发任何模型调用、不改任何 prompt**，输出 一个状态判断 + 一个主按钮 + 备选动作 + 每个动作的费用说明 + 明确列出的「现在点也没用」按钮。任何分支都保证至少有一个可执行动作（只有「正在跑」分支允许没有按钮，并给出看门狗说明）。
- @@@GET /api/v1/general-longform/{task_id}@@@ 增加 @@@guidance@@@ 字段；工作区顶部新增「下一步」面板（按钮直接复用既有动作，不新增工作流），@@@avoid@@@ 里的动作在前端置灰并写明原因（例如合稿按钮）。
- 大纲修订的低成本恢复：@@@revise_outline@@@ 现在把本次已付费的检索证据与修改要求存进 @@@outline_revision_context@@@，新增动作 @@@repair_outline_revision@@@（API + 指引 + 工作区按钮）复用它们只重跑一次 @@@outline_revision@@@ 生成，**不重新联网**；大纲版本变化或没有证据时明确拒绝并指向原有路径。修订成功后才清空该上下文。
- 补丁形状与空补丁：@@@apply_outline_patch@@@ 对 @@@top_level_changes@@@ 形状错误、含未允许字段的情况改为只丢弃不可用部分（无任何可用内容仍然拒绝）；@@@_revise_until_valid@@@ 增加空补丁检测与失败审计（@@@planning/outline_revision_attempt_N.json@@@），补丁没有产生任何真实修改时按失败处理，不再静默涨版本号。
- @@@scripts/general_longform_doctor.py@@@ 改为直接复用同一个指引模块，命令行与工作区说的永远是同一句话；只读数据库与状态文件。
- 新增操作手册 @@@docs/GENERAL_LONGFORM_USAGE.md@@@（从视频链接到成书的完整按钮流程、卡住对照表、费用表、FAQ）。
- 不变项（硬约束）：@@@backend/app/prompts/general_longform/**@@@ 逐字节未改；@@@validate_outline@@@、来源核验规则、验收门禁、严格串行、每章验收、100000—110000 字与双 Word 规则全部不变；没有数据库迁移；没有新增任何模型调用；成功路径行为不变。
- 补丁形状失败的解释：停在旧版「大纲补丁包含未允许的全局结构改写」的项目，指引会额外说明这次失败是补丁形状问题、新版会自动丢弃不允许字段并局部重试一次，用户不必自己猜要不要重新联网检索。
- 验证（2026-09-22，本机，全部重跑）：
  - @@@python -m pytest backend/tests -q -k general_longform@@@ → @@@219 passed, 960 deselected@@@（58.9s）。其中新增：指引测试 42 项（@@@backend/tests/test_general_longform_guidance.py@@@，含任意状态必有可执行动作、只读不改状态、无字段被工作区拒绝、畸形状态不抛异常、字数盈亏算术）、引擎 7 项（空补丁不再静默通过、畸形 @@@top_level_changes@@@ 保留可用章节补丁、@@@repair_outline_revision@@@ 复用已付费证据且不发新检索、无上下文时拒绝）、API 2 项（项目载荷必带 next-step、@@@repair_outline_revision@@@ 可下发）。
  - @@@npm run typecheck@@@（frontend）→ PASS；@@@npm run build@@@ → PASS（@@@frontend/dist/assets/index-LDfgrpIH.js@@@）。
  - 只读真机核对（本机读线上 Supabase + ECS 断点）：@@@python scripts/general_longform_doctor.py@@@ 对真实停滞任务 @@@bc69c362-658f-4d67-a00e-e6e4d7c6e997@@@ 输出「下一步：按校验结果修复候选大纲（不重新检索）」，与该任务在 ECS 上 @@@state.json@@@ 经 @@@build_guidance@@@ 计算出的结果一致（主按钮 @@@repair_outline_candidate@@@，复用已核验的 4 个来源，不重新联网）。
- 发布（2026-09-22）：提交 @@@3a2b335@@@ 同时推送到 GitHub @@@main@@@ 与 @@@codex/bilibili-cost-guard-main@@@，再以 12 个文件的字节校验增量发布到北京 ECS（@@@--base 71a2229@@@，@@@.deploy-version = 3a2b335@@@）。
- 发布校验：backend 与 worker 都通过固定 prompt 校验（v02 840 段/23 节、v16 571 段/19 节、39 个归档原始文件、其它 33 个分类 132 条 prompt 哈希不变），6 个后端文件哈希逐一核对；公开站点/健康检查/prompt manifest 均 200 且 celery alive；前端 bundle 字节与干净工作树构建一致（@@@/assets/index-DXf2dEna.js@@@，sha256 @@@c9d5475d…@@@）。
- 部署后真机复核（容器内跑部署代码 + 真实状态）：/opt/video-plan 上的停滞任务 @@@bc69c362-658f-4d67-a00e-e6e4d7c6e997@@@ 得到的主按钮是 @@@repair_outline_candidate@@@（复用已核验的 4 个来源、不重新联网），并附带补丁形状失败的说明；与工作区「下一步」面板同一句话。
- 回滚点：@@@/opt/video-plan-release-backups/20260921T212135Z-3a2b335@@@（含 @@@rollback.py@@@、源文件前后快照与镜像回滚 tag）。发布后清理构建缓存，根分区剩余约 10.4 GB。
- 发布过程记录（避免误判）：第一次发布在 @@@docker compose build backend web@@@ 的 apt/pip 层被 SSH 读超时打断（源文件已落地、镜像未重建）；续跑时先手工把镜像构建完（@@@BUILD_EXIT=0@@@），第二次续跑又因 @@@unacked=1@@@（对应任务在数据库里已不存在）停下，第三次续跑 @@@RELEASE_PASS@@@。两次续跑都复用同一个备份目录，未新建发布点。
- 发布方式说明：为不把工作区里其它未提交改动混进发布增量，发布脚本在 @@@git worktree add --detach 3a2b335@@@ 出来的干净检出里执行；归档仍来自提交对象（@@@git archive 3a2b335@@@），字节校验与 @@@.deploy-version@@@ 校验未做任何放宽。仅对本地脚本副本放宽了两处前置断言：要求工作区干净（改用干净检出后已满足）与 @@@unacked@@@ 必须为 0（改为打印该计数，队列仍要求 @@@LLEN celery == 0@@@）。
- 命令行的口径修正（2026-09-22，提交 @@@9b50ebf@@@）：任务实际跑在北京服务器上，本地没有 @@@state.json@@@，旧版 doctor 只能凭数据库字段猜，给出的下一步与工作区不一致。现在 @@@scripts/general_longform_doctor.py@@@ 在本地读不到断点时，会通过 @@@deployment.local.json@@@ 用 SSH 只读取回服务器容器里的 @@@state.json@@@（最多 10 个任务，@@@--no-remote@@@ 可关掉），因此命令行与工作区「下一步」完全一致；输出新增「状态来源」一行标明读的是本地断点还是服务器断点。该提交同样以 1 个文件的增量发布到 ECS（@@@--base 71b0030@@@，@@@.deploy-version = 9b50ebf@@@，备份 @@@/opt/video-plan-release-backups/20260921T230220Z-9b50ebf@@@）。
- 未做：未运行真实付费十万字生成；未改动其他分类、Epic 状态表、模型凭据与生产数据；历史 @@@PARTIAL_IMPLEMENTATION@@@ 结论不变。


## 通用长篇：两个校验断点的低成本恢复修正（2026-09-20）

- 现象一：新项目（v02）输入视频链接后，逐字稿已成功提取，但首版大纲因书名长度超出 10—18 可见字符被拦下，工作区停在“有待处理项”，主操作按钮消失，用户不知道下一步。现象二：来源抽象节点出现 `实际读取材料未形成可靠的来源摘要，未开始正文`（生成件已下载的页面全部读到了，但摘要条目没有一个通过核验），同样停在“有待处理项”。
- 只修这两处，未改动任何专属 prompt：`backend/app/prompts/general_longform/**`（v02 840/23、v16 571/19、manifest、全部 stage 模板）逐字节不变；`validate_outline`、来源核验规则（approved_summary / supported_claims / bibliography / id / url）、验收门禁、串行链路、10 万字与双 Word 规则全部不变。成功路径的行为完全不变。
- 成本审计（回答“局部写入是不是要重来一遍钱”）：一次 `plan` 请求的付费节点是 reference_analysis(1) + materials 素材摘要(有素材时 1) + research 联网检索(1 次 web 搜索) + evidence 来源抽象(每个 ≤18 万字符页面组 1 次) + outline(1)。原先的 `revise_outline` 会命中缓存的 reference/素材摘要（0 费用），但 `research(slot="outline-revision")` 是**新的请求上下文，必然重新联网检索**，再加一次 outline_revision 生成；大纲候选被拦下后重跑 `plan` 同样会重新检索。这就是“再来一遍钱”的真实来源。
- 修复一（大纲）：`GeneralLongformRunner.plan` 校验失败时保留已付费候选，随后用既有 `outline_revision` 节点（同一 stage、同一提示词文件、复用本次已完成的检索证据）做一次有界局部修复，修复后仍需通过同一 `validate_outline`。新增动作 `repair_outline_candidate`（API + 工作区按钮）让**已经卡住的任务**也能走同一条廉价路径：直接复用 `state["last_research"] + state["sources"]`，零联网、零页面重读，只新增 1 次 outline_revision 生成；没有可复用证据时明确拒绝并指向 `revise_outline`，不静默降级。幂等键 `candidate_repair_request_id`。
- 修复二（来源）：`research()` 在每个页面组上做有界重试。被核验排除的条目会带上确定性原因（id 不匹配 / url 不一致 / approved_summary 缺失或为空 / supported_claims 缺失或为空 / bibliography 缺失或为空），作为 `rejected_entries` 数据随下一次抽象请求回传；页面全文、web 检索结果、已读页面缓存全部复用，不重新联网。每个 research slot 的总尝试上限 `_EVIDENCE_ATTEMPT_CAP=3`，尝试次数从 gateway 调用日志读出，所以**“恢复上次操作”每点一次只多花 1 次来源抽象调用**，永远不会重跑 web 搜索；达到上限后停止并给出明确原因，不再产生调用。开关 `GENERAL_LONGFORM_EVIDENCE_RETRY_ATTEMPTS`（默认 1，最大 2）。错误信息现在会写明已读取页面数、被排除条目的原因与“是否可复用”。
- 前端：候选大纲存在且未确认时显示“大纲候选已保留”面板（一键按校验结果修复，明确标注不重新检索）；检索阶段停在 `needs_review` 时显示“恢复上次操作更便宜”提示，指出已读取页面仍在断点里、只有点“研究并生成待确认大纲”才会发起新的联网检索。
- 验证（2026-09-20，本机）：通用长篇相关六个测试文件 `300 passed`，其中新增 9 个用例（大纲：成功后采用修复、修复失败仍保留可修订候选、结构错误/关闭开关不产生调用、廉价动作复用证据不重检索、幂等重放、无证据时改走局部修订；来源：条目不合格时用同一批已读页面重试一次并带上排除原因、无望时尝试上限内停止且每点一次只多 1 次抽象调用）。`backend/tests` 全量 `1053 passed, 1 skipped`，另有 `test_qingdou_xiaohongshu.py` 2 项既有失败（在未打补丁的基线上同样失败，属既有问题，本次未触碰）。`npm run typecheck`、`npm run build` PASS；浏览器 fixture 13 项场景 PASS、0 page errors（新增两条恢复面板场景），证据 `.local/general_longform_v02/browser/browser_report.json`。
- 未做：未运行真实付费十万字生成，未改动其他分类、Epic 状态表、模型凭据与生产数据；历史 `PARTIAL_IMPLEMENTATION` 结论不变。
- 发布（2026-09-20）：GitHub 一次 atomic push 同步 `main` 与 `codex/bilibili-cost-guard-main`，两个远端引用均为 `669dc419cf79e1c8bb73919a8d28ba9b2125cdec`（与本地 HEAD 一致）。北京 ECS 增量发布 11 个文件（base `518d61e`，release_id `20260920T080857Z-669dc41`，备份 `/opt/video-plan-release-backups/20260920T080857Z-669dc41/`，rollback_images=5），`.deploy-version` 已核验为 `669dc419cf79e1c8bb73919a8d28ba9b2125cdec`。
- 发布中遇到的两个纯环境问题与处理（未改动任何产品逻辑）：① 首次 `docker compose build` 在北京到 Docker Hub 的层拉取上停滞（`node:22-alpine` 约 10 分钟无进展，daemon 已配镜像但该镜像未落地），改用 `docker.1panel.live` 预取 node/caddy/python 基础镜像并打回官方标签（digest 与构建解析出的 `b6f26b36…`/`de23def3…`/`2f17fc04…` 完全一致）后构建正常；② compose worker 容器仍在运行已被回收的镜像 `d0674d18`（retag 后 prune 的典型状态），发布脚本改为在该镜像缺失时回退到 `video-plan-backend:latest` 生成回滚标签并把原因写进 manifest note，回滚脚本断言 rollback_tag 存在。源码增量已落盘的部分不再重放，改用 `--resume-release` 完成构建后的全部校验与 `.deploy-version` 落盘。
- 生产验收（2026-09-20）：首页、`/api/v1/system/health`（`celery_alive=true`、`celery_workers=["celery"]`）、`/api/v1/general-longform/prompt-manifest` 均为 200；线上 `/assets/index-CRhfK2Xc.js` 与本地已通过 typecheck/build 的产物逐字节一致（SHA-256 `d68eac5dd9577668c0769e50e8757ca69c127118f688d0f76b7d02152e11bbb7`）；backend 与 worker 容器内 prompt 完整性 PASS（v02 840/23、v16 571/19、39 个归档原始文件、33 个分类/132 个历史 prompt 哈希）；部署文件哈希 6/6 PASS；容器挂载与 `.env` 哈希与发布前一致。浏览器回归直接跑生产前端（API 全部拦截、无付费调用）13 项场景 PASS、0 page error，含新增的“候选大纲一键复用证据修复”与“检索断点指向廉价恢复”两条场景，证据 `.local/general_longform_v02/browser_production/browser_report.json`。发布后 ECS 剩余磁盘约 2.6 GiB（97%），本次仅删除了两个 dangling 镜像与遗留标签 `video-plan-backend:66aa543`。
- 尾部文档同步：release `20260920T090758Z-af98b2a`（docs-only，base `669dc41`，备份 `/opt/video-plan-release-backups/20260920T090758Z-af98b2a/`）把本节记录同步到 ECS，健康检查与 prompt 完整性再次 PASS，`.deploy-version` 随之前移。
- ECS 磁盘清理（2026-09-20，纯运维，代码零改动）：journald 3.0GB→192MB 并新增 `/etc/systemd/journald.conf.d/99-size.conf`（SystemMaxUse=300M/SystemKeepFree=2G，可随时删除该 drop-in），apt 缓存 135MB，dangling 卷 288.7MB，两代过期的 640952d 回滚镜像（同一轮发布的重复标签）与闲置的 `video-plan-worker-recovery` 容器（`--queues=recovery`，代码中没有任何生产者）及其镜像，24 小时前的 build cache 6.03GB。可用空间 2653MiB→11214MiB（97%→86%）。保留：两代回滚镜像（`rollback-20260915T093101Z-c38a90b`、`rollback-20260920T080857Z-669dc41`）、基础镜像（node/caddy/python/postgres）、今日构建缓存、生产 volume `video-plan_backend_output`。清理后首页 200、`celery_alive=true`、6 个 compose 容器全部 Up。
- 现网卡住项目（`bc69c362-658f-4d67-a00e-e6e4d7c6e997`，`needs_review`，`sources=2`，候选大纲保留，`last_request=revise_outline` 未完成）在新版本下无需重新付费检索：恢复动作复用同一 request 的 gateway 日志（web 检索已完成）与已落盘页面，只重跑来源抽象（每次 ≤10k tokens，单 slot 上限 3 次）。

## OpenAI 鉴权故障定位与恢复修正（2026-09-15）

- Diagnosis PASS: the live Beijing ECS worker reached the configured OpenAI endpoint through the unchanged project proxy; a read-only `GET /v1/models` returned HTTP 401 / `account_deactivated` in 3.35 seconds. Local active configuration and the worker have the same credential fingerprint; no credentials are printed or changed.
- Scope: preserve structured writer rejection status/code, distinguish rejected requests from uncertain paid deliveries, prevent authentication-error replay, and correct the affected general-longform task's historical classification with a backup. No model/prompt, proxy, other category, manuscript, or canon changes.
- Validation PASS: 498 tests (66.44 seconds, one existing Google SDK deprecation warning), Python compile, diff check, and v02 840/23 prompt-fidelity check. Covers structured 401/403/4xx, metadata propagation, original-prompt resume, existing paid-response retention, uncertain 408/transport failures, watchdog behavior and a no-generation auth-check CLI. No frontend or prompt changes.
- Publication and existing-record correction: pending. Account recovery or an active-account `OPENAI_API_KEY` is an external prerequisite for real generation; software checks alone do not establish that the account is usable.
- The historical Epic `PARTIAL_IMPLEMENTATION` verdict is unchanged.

## 通用长篇 v02 增量迭代（2026-09-15）

- Phase 1 PASS: parsed all 840 paragraphs and header/footer from the new attachment, compared every paragraph with the deployed v16 source, and read the added/changed positive-negative examples and policy/report sections. Source SHA-256: `1f70baec84e1cd1f47ddb269574c1e7daa0d6ac54e6d71b8f1c9bbe996f8cbef`.
- Plan: `docs/GENERAL_LONGFORM_V02.md`. Keep old project prompt versions resumable; use role-preserving abstract cards, not raw sample text, for generation; add contextual style checks without ordinary-word blacklists. No new DB tables/migrations, no changes to other categories or Epic canon.
- Phase 2 PASS: v02 840 paragraphs / 23 sections losslessly packaged; all 39 v16 files archived byte-for-byte and all 15 old stage renders preserved. New tasks pin v02; old state/config pins v16 and resumes original paid prompts/response IDs. Version/hash mismatch blocks before dispatch. Targeted fidelity/version/legacy-registry/API checks: `33 passed, 1 existing Google SDK warning` (32.60s).
- Phase 3 PASS: version-bound positive/negative cards and approved editing summary; role/provenance checks and raw/title isolation; actual-text style evidence at chapter and whole-book acceptance; contextual ordinary-word policy instead of blacklists; historical reports never count as current results. High issues use the existing one-repair/re-extract/recheck gate, with no draft state writes. Dedicated regression: `145 passed, 1 existing Google SDK warning` (22.90s).
- Phase 4 PASS: project-version manifest UI, role/history labels, style evidence views, and version/hash/style metadata in Word delivery; frontend typecheck/build and 11 browser scenarios PASS, 0 page errors. Missing manifests show errors rather than fallback counts; malformed model style records remain viewable.
- Phase 5 PASS: final full regression `477 passed, 1 existing Google SDK warning` (72.34s), compileall PASS, all 33 other categories/132 prompt hashes unchanged. v02 and v16 source/route/archive checks PASS; deterministic 12-chapter/105000-word book and style/Word/version recovery checks PASS, not a paid literary-quality evaluation.
- Phase 6 PASS: code commit `c38a90bac2792ae27003fa579b4319577885c333` atomically published to both GitHub branches and deployed to Beijing ECS as an 82-file byte-checked archive. Each of backend/worker verified 71 changed backend file hashes, both complete prompt versions, all 39 old bundle files, and 33 other categories/132 prompt hashes. Public root/health/manifest 200, celery alive; frontend bytes match the tested local build.
- Production smoke PASS: real API→Celery v02 import-first workspace, DOCX upload/deduplication, owner checks and old-version manifest query; 0 model calls, 0 canon writes, temporary user/task/files removed. Published-frontend browser regression: 11 scenarios PASS, 0 page errors. Configuration hash and complete volume mounts unchanged; rollback backup `/opt/video-plan-release-backups/20260915T093101Z-c38a90b/`, 3655 MiB remaining disk, no global prune.
- v02 scope has no remaining implementation/test/code-release blocker; closing docs are synchronized separately with no service restart. Real paid 10万字 quality generation was not performed. The historical Epic `PARTIAL_IMPLEMENTATION` verdict remains unchanged; detailed evidence and exact commands are in `docs/GENERAL_LONGFORM_V02.md`.

## 通用长篇分类：独立集成（2026-09-14）

- Phase 1 complete: read the requested prior conversation, the entire supplied Word (571 body/table paragraphs and its header/footer), repository instructions, and relevant SPEC planning/execution/prompt/API/export sections.
- Scope: add `general_longform` on the existing task/writer/queue infrastructure; preserve all existing category prompts and the Epic canon boundary. No full-app rewrite, no Epic corpus work, no database migration is planned.
- Plan, source checksum, operating guide, test commands and release evidence: `docs/GENERAL_LONGFORM.md`. Implementation, final local validation and GitHub/Beijing ECS code publication are complete for this independent category (release verification 2026-09-15).
- The historical Epic `PARTIAL_IMPLEMENTATION` verdict remains unchanged; this category is not a claim of 300万字 production readiness.
- Source packaging phase: PASS — 571 paragraphs / 19 contiguous sections reconstructed exactly; original DOCX SHA-256 verified. All 33 legacy categories' four-stage prompt hashes remain identical to baseline.
- Backend phase: PASS — dedicated Task lease bridge, file-backed planning/draft/accepted/research/QA separation, real search/page abstraction, serial chapter acceptance, idempotent paid-call slots, local revision/import controls and paired native Word export. Local imported-outline repairs consume their source material; revisions refresh source summaries/research; uncertain calls without recoverable response IDs require explicit retry before dispatch.
- Prompt boundary: this attachment produces `ReferencePatternCards` and `ApprovedEditingSummary`, not an Epic full-corpus `ReferenceMechanismProfile`. Original reference sample/report text is limited to abstraction; all 571 original paragraphs remain losslessly archived and routed.
- Frontend/export phase: PASS — owner-scoped six-tab workspace, explicit outline confirmation without starting chapter 1, one chapter per action, visible QA/source/call evidence, versioned double Word downloads and recoverable errors. Existing Epic workspace/canon tables and legacy category defaults remain unchanged.
- Final local validation: `416 passed, 1 existing Google SDK warning` (137.58s); Python compile, frontend typecheck/build, source reconstruction, legacy 33-category/132-prompt hashes, and browser simulation all PASS. Browser covers creation, confirmation, serial action, six tabs, accepted preview, upload/import and actual DOCX download with 0 page errors.
- Evidence: `.local/general_longform_integration/test_release_final.log` and `browser/browser_report.json`; 12 chapters / 105000 counted words were tested with deterministic doubles, not paid production prose. Historical Epic verdict is unchanged.
- Release phase: PASS — code commit `640952d3bb3bc39d748d444a5d56e1941be0a52f` atomically pushed to both GitHub branches and deployed as a byte-checked 69-file Git-archive delta. Backend/worker each verified 57 changed backend files plus all source/prompt hashes; published frontend bytes equal the tested local bundle. Public root/health/manifest return 200 and `celery_alive=true`.
- Real production smoke: PASS without paid calls — an isolated temporary owner used the live API and Celery to create an import-first workspace, upload DOCX and verify duplicate detection/owner authentication. Model calls 0, canon writes 0, temporary user/task/files removed. A second browser smoke against the published frontend passed all six fixture-driven scenarios with 0 page errors.
- Production preservation: `.env` hash and complete mount attributes unchanged; no changes to existing user data, proxy, ADC, corpus or Redis volume. Previous sources/version and five rollback image tags retained at `/opt/video-plan-release-backups/20260914T165102Z-640952d/`. No global cleanup/prune; remaining disk 3771 MiB.
- Final scope status: no remaining implementation/test/release blocker for `general_longform`; no real paid 10万字 quality run was performed. Historical Epic `PARTIAL_IMPLEMENTATION` and its separate full-corpus/300万字 TODOs remain unchanged. The documentation-only closing revision records these checks; final deployed HEAD is recorded in ECS `.deploy-version` and both GitHub branches.

Updated: 2026-08-10

## Gemini ADC Migration (2026-09-03)

- Switched backend polish/refine routing back to Gemini; the main draft writer remains official OpenAI `gpt-5.6-sol`.
- Switched the default Gemini model to Vertex's `gemini-3.1-flash-lite` identifier (the published Gemini 3.1 Flash tier).
- Replaced the committed service-account JSON path with user ADC mounted read-only at `/root/.config/gcloud/application_default_credentials.json` for backend, worker, and beat.
- Disabled Gemini API-key fallback so an ADC failure pauses the paid call path instead of switching credentials or billing a second channel.
- Removed the previously tracked service-account JSON keys from the repository and Docker build context.
- Local validation: targeted provider/paid-API tests passed; Python compilation, Compose config validation, and `git diff --check` passed.

Deployment and Gemini canary remain pending until the ADC file is synchronized to Beijing ECS and Vertex AI access is verified with one minimal request.

## Current Status

Status: `PARTIAL_IMPLEMENTATION`

Post-gate hardening update on 2026-06-18: consistency/API/frontend hardening is `HARDENED_PARTIAL`.

This pass added generic Knowledge / Secret / Reader Question / Growth / Power / Authority / Relationship ledgers, strengthened ChapterContract and ContextPack consistency constraints, and expanded the Acceptance Gate to block knowledge leaks, early secret exposure, early reader-question answers, professional terms without in-world source, unauthorized growth, locked ability use, unauthorized authority, relationship jumps, and forbidden major-character introductions.

Only `acceptedContent` persists the new ledgers. `draftContent` and `repairedContent` remain non-canon candidates.

API/frontend hardening in this pass added project deletion, project status, project artifact records, project ledgers, reference book list/delete, reference task retry, reference readiness, structured business errors, frontend project deletion, reference book management, task retry, readiness display, and artifact record display.

Validation run on 2026-06-18:

- `python -m compileall backend/app`
- `python -m pytest backend/tests/test_epic_qa.py backend/tests/test_epic_context.py backend/tests/test_epic_reference.py backend/tests/test_epic_scheduler.py -q` -> 46 passed
- `npm.cmd run build` in `frontend` -> passed

Publication/deployment on 2026-06-18:

- Post-gate hardening HEAD was pushed to `origin/codex/bilibili-cost-guard-main` and `origin/main`.
- Beijing ECS `/opt/video-plan` was updated through tarball deployment while preserving `.env` and `.local`.
- `.deploy-version` was verified against deployed HEAD.
- `docker compose ps` showed `backend`, `worker`, `beat`, and `web` Up.
- Public `http://101.200.48.238/`, `http://101.200.48.238/epic-novel`, and `http://101.200.48.238/api/v1/system/health` returned 200 after startup.
- Remote `init_db()` confirmed the new hardening tables, including `epic_character_knowledge_ledgers` and `epic_artifact_records`.

New post-gate hardening docs:

- `docs/epic_novel/POST_GATE_HARDENING_AUDIT.md`
- `docs/epic_novel/KNOWLEDGE_SECRET_GROWTH_CONSISTENCY_REPORT.md`
- `docs/epic_novel/PROTAGONIST_GROWTH_TRAJECTORY_REPORT.md`
- `docs/epic_novel/CHARACTER_ARC_LEDGER_REPORT.md`
- `docs/epic_novel/READER_QUESTION_LEDGER_REPORT.md`
- `docs/epic_novel/API_CONTRACT_REPORT.md`
- `docs/epic_novel/FRONTEND_WORKSPACE_FUNCTIONALITY_REPORT.md`
- `docs/epic_novel/ARTIFACT_DOWNLOAD_REPORT.md`

This does not change the overall production-readiness verdict: the system is still not proven 300万字-ready because full 100-book production extraction, full-corpus `ReferenceMechanismProfile`, and real long-run manuscript generation remain incomplete.

Gate 2A update on 2026-06-12: bounded section-aware Reference Intelligence proof is `PASS`.

The Gate 2A pass is deliberately narrow: 1 imported book / 5 sections were processed with real DeepSeek V4 Pro section analysis. This proves the corrected pipeline shape, not full 57-book extraction, not full 100-book corpus completion, and not 300万字 production readiness.

Gate 2B update on 2026-06-13: staged Reference Intelligence expansion is `PARTIAL`.

The latest Gate 2B staged evidence covers 3 source books / 27 successful DeepSeek V4 Pro section analyses / 48 production-ready cards. Full 57-book extraction is still incomplete, so full Reference Intelligence remains PARTIAL.

Gate 3 update on 2026-06-12: Reference Usage Depth is `PASS`.

Gate 3 proves production-ready ReferencePatternCards and ReferenceMechanismProfile are consumed by the required generation layers, QA and repair entrypoints, with `EpicRetrievalTrace` evidence. It does not change the broader production-readiness verdict.

Gate 4 update on 2026-06-12: Real Chinese Master Bible is `PASS`.

Gate 4 proves 24/24 Master Bible sections pass stricter Chinese rendering and acceptance, with fallback/smoke/English-key markers rejected and markdown/json exported as files.

Gate 5 update on 2026-06-13: Dynamic Context Compiler is `PASS`.

Gate 5 proves provider-window prompt budgeting, explicit included/excluded/overflow metadata, selected card/profile metadata, raw-reference exclusion, no unbounded `full_context_json` prompt dump, live ContextPack `RetrievalTrace` persistence, continuation handling, and file-backed large export metadata.

Gate 6 update on 2026-06-13: QA / Acceptance Gate is `PASS`.

Gate 6 proves targeted bad-output rejection, high/severe blocking, issue-driven scores, `EpicIssue` / `EpicAcceptanceReport` persistence, local issue-targeted repair scope, and no canon/state write from bad draft or repaired draft candidates.

Gate 7 update on 2026-06-13: Canon-State Execution 1-3 is `PASS`.

Gate 7 proves chapters 1-3 still pass after the strengthened QA gate, every chapter has the required QA/context RetrievalTrace types and AcceptanceReport, chapter 2/3 consume previous state, required canon/state ledgers are present, `nonAcceptedCanonSources=0`, and Bible/novel exports are file-backed with Chinese visible filenames.

Gate 8 update on 2026-06-13: 30-Chapter Continuity is `PASS` by deterministic simulation.

Gate 8 proves equivalent 30-chapter state behavior with 30 accepted simulated chapters, required contracts/context traces/QA traces/reports/state ledgers, reference profile/cards, overflow metadata, `nonAcceptedCanonSources=0`, and no duplicate canon writes after retry. This is not a production prose run.

Gate 9 update on 2026-06-13: Full Volume Acceptance is `PASS` for the configured live test volume.

Gate 9 proves live project volume 1, configured chapter range 1-3, passes VolumeContract, chapter execution evidence, strengthened QA, state ledgers, reference traces, `nonAcceptedCanonSources=0`, and volume manuscript export. This does not prove all volumes or a 10-chapter production volume.

Gate 10 update on 2026-06-13: Multi-Volume Rolling Plan is `PASS`.

Gate 10 proves 3 planned volumes have GlobalSpine linkage, local case/mystery, world expansion, bureaucracy/faction use, power progression, protagonist/heroine arcs, foreshadow setup/echo/payoff, reader-question flow, compressed old state, active unresolved ledger, prior volume summaries, future reveal constraints, reference usage trace, and file-backed rolling-plan export. This does not prove all-volume generated prose.

Gate 11 update on 2026-06-13: Scheduler / Cost / Recovery is `PASS`.

Gate 11 proves EpicProductionJob checkpointing, pause/resume/stop, retry failed only, continuation chunk recording, cost cap, cost logs, dashboard exposure, reference extraction control, and no duplicate canon writes after retry. This does not prove full corpus extraction or full manuscript generation.

Gate 12 update on 2026-06-13: Large Export System is `PASS`.

Gate 12 proves Bible/novel/volume/chapter/report/trace exports default to file-backed metadata with checksum/object id/download route, explicit `mode=download` separation, large-file path support, and raw-reference exclusion. This does not prove frontend runtime, remote deployment, full generated manuscript, or 300万字 readiness.

Gate 13 update on 2026-06-13: Frontend Workspace is `PASS` for local browser runtime.

Gate 13 proves the local Epic workspace is Chinese, loads real API panels, exposes dashboard/export controls, handles empty/error/loading states, and no longer crashes on missing export metadata fields. It does not prove Beijing ECS has the latest build, full corpus extraction, full generated manuscript, or 300万字 readiness.

Gate 14 update on 2026-06-13: Final SPEC Readiness is `PARTIAL_IMPLEMENTATION` with current GitHub/ECS deployment complete.

Current pushed HEAD was pushed to `origin/codex/bilibili-cost-guard-main` and `origin/main`, then deployed to Beijing ECS `/opt/video-plan`; `.deploy-version` was verified against deployed HEAD. Public `/`, `/epic-novel`, and `/api/v1/system/health` returned 200, ECS retained 57 epub raw reference files, and live DB evidence shows `ReferenceSection=27`, success analyses=27, production-ready cards=48. This proves current publication/deployment, not 300万字 readiness.

Gate 0 adversarial recheck on 2026-06-12 found that the previous “PASS FOR 09_ACCEPTANCE_CRITERIA” statement overstated production readiness. The historical strict smoke run on project `44ce72be-a423-4f3b-895b-ad8a0fbb5e63` proves a DB-backed smoke/control-plane flow, not full SPEC production acceptance.

The current implementation has meaningful Epic modules in place, but it is not `PRODUCTION_READY` and must not be described as 300万字-ready. Blocking evidence:

- Live DB has 100 booklist items but only 57 indexed imported books.
- Gate 2B staged run now has 27 stable `ReferenceSection` rows and 27 successful `ReferenceSectionAnalysis` rows.
- Gate 0 baseline had 98,581 old `ReferencePatternCard` rows with empty `source_section_ids`; Gate 2B staged run now has 48 production-ready cards with non-empty `source_section_ids`, `source_analysis_ids`, and `source_book_ids`. Old cards remain excluded from production-ready counts.
- Gate 0 baseline profile lacked source metadata; latest Gate 2B profile `846e11e2-385d-4c94-b122-793f84112f5e` now has `generationStatus`, `buildMode`, `profileVersion`, provider/model trace fields, source book/card/section samples, `rawTextIncluded=false`, categories, evidence counts, and `oldSourceLessCardsExcluded=true`.
- The live project is a 30万字 / 3卷 / 10章 smoke project with only 3 accepted chapters and 15,018 current words.
- Acceptance gate now blocks the current high/severe test cases and persists issue-driven reports, but long-run false-positive/false-negative behavior is not proven beyond the current Gate 6-10 evidence.
- GitHub/ECS deployment state was not reverified in this Gate 0 audit and is not counted as current evidence.

New Gate 0 docs:

- `docs/epic_novel/ADVERSARIAL_AUDIT_REPORT.md`
- `docs/epic_novel/FALLBACK_AND_FIXTURE_INVENTORY.md`
- `docs/epic_novel/REFERENCE_INTELLIGENCE_EVIDENCE.md`
- `docs/epic_novel/PRODUCTION_READINESS_RECHECK.md`

New Gate 2A doc:

- `docs/epic_novel/GATE_2A_REFERENCE_INTELLIGENCE_RUN_REPORT.md`

New Gate 2B doc:

- `docs/epic_novel/GATE_2B_REFERENCE_FULL_RUN_REPORT.md`

New Gate 3 doc:

- `docs/epic_novel/GATE_3_REFERENCE_USAGE_DEPTH_REPORT.md`

New Gate 4 doc:

- `docs/epic_novel/GATE_4_MASTER_BIBLE_REPORT.md`

New Gate 5 doc:

- `docs/epic_novel/GATE_5_DYNAMIC_CONTEXT_REPORT.md`

New Gate 6 doc:

- `docs/epic_novel/GATE_6_QA_ACCEPTANCE_REPORT.md`

New Gate 7 doc:

- `docs/epic_novel/GATE_7_CANON_STATE_REPORT.md`

New Gate 8 docs:

- `docs/epic_novel/GATE_8_THIRTY_CHAPTER_REPORT.md`
- `docs/epic_novel/30_CHAPTER_CONTINUITY_REPORT.md`

New Gate 9 docs:

- `docs/epic_novel/GATE_9_FULL_VOLUME_REPORT.md`
- `docs/epic_novel/FULL_VOLUME_ACCEPTANCE_REPORT.md`

New Gate 10 docs:

- `docs/epic_novel/GATE_10_MULTI_VOLUME_REPORT.md`
- `docs/epic_novel/MULTI_VOLUME_ROLLING_PLAN_REPORT.md`

New Gate 11 docs:

- `docs/epic_novel/GATE_11_SCHEDULER_REPORT.md`
- `docs/epic_novel/PRODUCTION_SCHEDULER_REPORT.md`

New Gate 12 docs:

- `docs/epic_novel/GATE_12_EXPORT_REPORT.md`
- `docs/epic_novel/EXPORT_SYSTEM_REPORT.md`

New Gate 13 docs:

- `docs/epic_novel/GATE_13_FRONTEND_REPORT.md`
- `docs/epic_novel/FRONTEND_WORKSPACE_REPORT.md`
- `docs/epic_novel/gates/GATE_13_FRONTEND_WORKSPACE.md`

New Gate 14 doc:

- `docs/epic_novel/GATE_14_FINAL_SPEC_READINESS_REPORT.md`

The old smoke evidence below is retained as historical evidence only.

## Completed In Current Worktree

- Removed fixed Epic smoke-story anchors from production Epic service generation paths. The old fixed names now remain only in SPEC/test-project docs or negative tests.
- Added production/fallback separation:
  - production LLM generations record `_meta.status=generated`, `production_quality=true`, `rawTextIncluded=false`
  - deterministic fallback is labeled `status=fallback`, `production_quality=false`, `smoke_validated=true`
  - Bible section acceptance rejects fallback/placeholder metadata.
- Added structured-generation resilience:
  - author/DeepSeek calls are tried first for author-role structured generation
  - if configured, failed author calls fall back to editor/GPT routing before smoke fallback
  - generation logs record the model/action/status/error path
  - long model calls release DB transactions before waiting on providers.
- Added Reference Intelligence controls:
  - `EPIC_REFERENCE_MODEL_PROVIDER=deepseek`
  - `EPIC_REFERENCE_MODEL_NAME=deepseek-v4-pro`
  - LLM extraction enable/force/cache/retry controls
  - pause/resume/stop extraction control file
  - dry-run cost estimation.
- Added source-identity/reference-section models:
  - `ReferenceSection`
  - `ReferenceSectionAnalysis`
  - extended `ReferencePatternCard` with source section ids, source card ids, evidence count, extraction model, output hash, and `raw_text_included=false`.
- Hardened Dynamic Context / generation safety:
  - raw reference text is excluded from ContextPack and generation prompts
  - ReferencePatternCards and ReferenceMechanismProfile remain allowed prompt sources
  - overflow/excluded context metadata is recorded on retrieval traces
  - large exports return file/object metadata instead of one giant API payload.
- Added Reference extraction API/frontend controls:
  - cost estimate endpoint
  - pause/resume/stop endpoint
  - frontend reference extraction status/cards.
- Added DB startup resilience:
  - advisory lock around startup schema sync
  - short retry on startup OperationalError
  - PostgreSQL `connect_timeout`
  - `pool_recycle` and LIFO pooling.
- Tightened strict acceptance smoke:
  - supports `EPIC_ACCEPTANCE_PROJECT_ID` resume
  - fails if Bible sections are fallback/need_repair
  - fails if coverage is not complete
  - fails if retrieval traces contain raw reference text.
- Added Master Bible acceptance anchors and Chinese-safe payload rendering:
  - planning/control sections now include project-derived anchors for 主角, 关键机构, 世界规则, 关系代价, 伏笔终局, 势力冲突
  - `_meta` and raw English JSON keys are not dumped into Bible markdown.
- 中文化 EPIC 前端工作区:
  - `/epic-novel` 入口、项目列表、创建表单、基建流水线、参考书库、章节执行、质检验收、生成日志均改为中文可见文案
  - 后端返回的状态枚举和生成动作枚举通过前端映射显示为中文，不再直接暴露 `accepted`、`manual_required`、`execute_chapter` 等英文 key
  - 模型配置摘要和日志模型列使用中文展示名，不在工作区原样展示 provider/model id
  - 前端错误提示改为中文兜底，避免英文接口错误直接显示给用户.
  - 详情页扩展为 10 个中文标签：基建总览、参考语料、圣经世界、规划合同、章节生产、上下文检索、质检验收、生产仪表盘、文件导出、调用日志.
  - 文件导出页面接入 Gate 12 metadata/download 分离，空 metadata 不再触发 `sha256.slice` 崩溃.

## Gate 2A Completed In Current Worktree

- Added stable `ReferenceSection` identity by book hash + section hash + section index.
- Added `ReferenceSection.token_estimate`, `ReferenceSectionAnalysis.analysis_hash`, `ReferencePatternCard.source_analysis_ids`, and `ReferencePatternCard.source_book_ids`, with startup schema self-heal.
- Added DeepSeek V4 Pro section-analysis workflow with Chinese JSON normalization for the Gate 2A fields.
- Fallback section analyses are stored as `status=fallback`, `production_quality=false`, and are not counted as production evidence.
- New production-ready `ReferencePatternCard` rows are generated only from successful non-fallback `ReferenceSectionAnalysis` rows.
- Production-ready card checks require non-empty `source_section_ids`, `source_analysis_ids`, source book evidence, `rawTextIncluded=false`, and `production_quality=true`.
- Added dedupe/merge helpers that preserve source book, section, analysis, and card IDs without merging raw text.
- Rebuilt proof `ReferenceMechanismProfile` from production-ready cards only, with `generationStatus`, `profileVersion`, provider/model, source samples, categories, evidence counts, and `rawTextIncluded=false`.
- Added bounded proof runner: `scripts/epic_gate2a_reference_proof.py`.
- Added targeted tests for stable section IDs, fallback exclusion, production-card source IDs, old-card exclusion, merge evidence preservation, profile metadata, and raw-reference prompt safety.

## Gate 2A Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Final Gate 2A proof | PASS | `$env:PYTHONPATH='backend'; python scripts/epic_gate2a_reference_proof.py --book-limit 1 --section-limit 5 --force` |
| Proof report | PASS | `.local/epic_novel_runs/gate2a_reference_intelligence_2026-06-12T132502_524973_0000.json` |
| DeepSeek section analysis | PASS | 5 successful DeepSeek V4 Pro section analyses, 0 fallback, 0 error |
| Production-ready cards | PASS | 18 cards after dedupe; old 98,581 empty-source cards excluded from production-ready counts |
| Profile metadata | PASS | profile `b1025f7b-d405-4077-8632-d6adf94e3732`, `rawTextIncluded=false`, `sourceBookCount=1`, `sourceCardCount=18`, 5 source sections sampled |
| Targeted tests | PASS | `$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_reference.py backend/tests/test_epic_context.py -q` -> 19 passed |

Gate 2A remains bounded proof only. Full Reference Intelligence is still `PARTIAL` until more imported books/sections are processed and the broader acceptance flow is rerun.

## Gate 2B Completed In Current Worktree

- Added staged reference expansion runner:
  - `scripts/epic_gate2b_reference_staged_run.py`
  - `run_gate2b_reference_staged_expansion(...)`
- Runner supports book/section limits, per-book section limits, tier/category filters, resume, retry-failed-only, force, dry-run, cost cap, stop-on-cost-limit, runtime cap, and the existing pause/resume/stop control file.
- Added estimated reference model cost calculation for prompt/completion tokens.
- Added Gate 2B profile metadata: `buildMode`, `productionReadyCardCount`, `oldSourceLessCardsExcluded`.
- Ran staged expansion:
  - selected 3 imported books
  - skipped 5 already-successful Gate 2A sections by resume
  - newly processed 10 sections from 2 more books
  - cumulative successful analyses: 15
  - cumulative production-ready cards: 36
  - fallback/error: 0
- 2026-06-13 continuation:
  - optimized production-ready card counting and pattern-key lookup so Gate 2B no longer scans the old 98,581 source-less card table on each small batch
  - fixed Gate 2B resume budget so skipped already-successful sections do not consume `section_limit`
  - latest staged report: `.local/epic_novel_runs/gate2b_reference_staged_2026-06-13T153012_468370_0000.json`
  - cumulative successful analyses: 27
  - cumulative production-ready cards: 48
  - fallback/error: 0
- Ran dry-run controls:
  - full estimate dry-run for 3 books / 15 candidate sections
  - retry-failed-only dry-run
  - cost-limit dry-run with `stopReason=cost_limit`

## Gate 2B Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Gate 2B staged run | PARTIAL | latest `.local/epic_novel_runs/gate2b_reference_staged_2026-06-13T153012_468370_0000.json`; prior expansion `.local/epic_novel_runs/gate2b_reference_staged_2026-06-12T142226_539531_0000.json` |
| Dry-run estimate | PASS | `.local/epic_novel_runs/gate2b_reference_staged_2026-06-12T142024_203143_0000.json` |
| Retry-failed-only control | PASS | `.local/epic_novel_runs/gate2b_reference_staged_2026-06-12T143247_517259_0000.json` |
| Cost cap control | PASS | `.local/epic_novel_runs/gate2b_reference_staged_2026-06-12T143259_386938_0000.json`, `stopReason=cost_limit` |
| Runtime DB evidence | PASS | `ReferenceSection=27`, `ReferenceSectionAnalysis=27`, success=27, fallback=0, error=0, production-ready cards=48 |
| Targeted tests | PASS | `$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_reference.py backend/tests/test_epic_context.py backend/tests/test_epic_chinese_output.py backend/tests/test_epic_qa.py backend/tests/test_epic_multi_volume.py backend/tests/test_epic_scheduler.py -q` -> 53 passed |

Gate 2B is not full PASS because 57 imported books / estimated 106,914 sections have not all been processed.

## Gate 3 Completed In Current Worktree

- `auto_retrieval_plan()` now retrieves production-ready `ReferencePatternCard` rows only.
- Old source-less cards are excluded from generation prompts and recorded as `oldSourceLessCardsExcluded`.
- Added unified `_reference_usage_pack()` for Epic generation layers.
- Connected reference usage traces to:
  - Genesis worldview tournament
  - WorldBible
  - WorldAtlas
  - BureaucracySystem
  - PowerSystem
  - EnsembleCast
  - GlobalSpine
  - Master Bible manifest and sections
  - VolumeContracts
  - ChapterContracts
  - Chapter ContextPack
  - Bible Coverage CI
  - Outline CI
  - Chapter check
  - Planned-vs-Actual Diff
  - Acceptance Gate
  - Outline repair
  - Chapter repair entry
- Fixed ContextPack trace overflow recording from nonexistent `overflowBlocks` to `metadata.overflow`.
- Added `scripts/epic_gate3_reference_usage_depth.py`.

## Gate 3 Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Gate 3 smoke | PASS | `.local/epic_novel_runs/gate3_reference_usage_depth_2026-06-12T152527_937305Z.json` |
| Required task types | PASS | 19/19 required task types present |
| Trace quality | PASS | every required trace has non-empty `usedCardIds`, `usedProfileSections`, `profileVersion`, `usageSummary` |
| Production-card filter | PASS | Gate 3 smoke used 36 production-ready cards at run time; latest Gate 2B card pool is 48; 98,581 old source-less cards excluded |
| Raw text safety | PASS | `rawTextIncluded=false` for required traces |
| QA trace live evidence | PASS | chapter 4 `qa_planned_vs_actual_diff` and `qa_acceptance_gate` each used 36 cards |
| Canon boundary | PASS | chapter 4 accepted; state ledgers persisted; canon source is `acceptedContent` only |
| Targeted tests | PASS | `$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_reference.py backend/tests/test_epic_context.py backend/tests/test_epic_chinese_output.py -q` -> 28 passed |

## Gate 4 Completed In Current Worktree

- Expanded Bible payload label mapping so common camelCase/snake_case keys render as Chinese.
- Added recursive Bible text cleanup for smoke project labels and English premise fragments.
- Hardened `validate_bible_section_content()` against visible English key labels, smoke fixtures, excessive generic `结构字段`, missing section payload, and missing reference usage.
- Added `scripts/epic_gate4_master_bible_audit.py`.
- Re-rendered all 24 accepted Bible sections into stricter Chinese Markdown.
- Exported Bible markdown/json files with Chinese filenames.

## Gate 4 Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Gate 4 audit | PASS | `.local/epic_novel_runs/gate4_master_bible_2026-06-12T154950_704943Z.json` |
| Bible status | PASS | `completed` |
| Accepted sections | PASS | 24 / 24 |
| Coverage failures | PASS | 0 |
| Markdown export | PASS | `.local/epic_novel_exports/本长篇项目 全书圣经.md` |
| JSON export | PASS | `.local/epic_novel_exports/本长篇项目 全书圣经.json` |
| Forbidden marker scan | PASS | no `acceptance smoke`, raw English key labels, `结构字段`, `Long-form`, or `Epic Novel Engine` hits |
| Targeted tests | PASS | `$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_chinese_output.py backend/tests/test_epic_reference.py backend/tests/test_epic_context.py -q` -> 28 passed |

## Gate 5 Completed In Current Worktree

- Added `raw_reference_field_paths()` and `excludedRawReferenceFields` metadata so raw-reference exclusions are auditable.
- Dynamic Context metadata now records `includedPaths`, `overflowPaths`, `excluded`, `selectedCardIds`, `profileVersion`, `selectedBibleSectionKeys`, and `noSilentTruncation`.
- Chapter writer prompt now carries only selected compiled context plus compact metadata; it does not dump `full_context_json`.
- Added `scripts/epic_gate5_dynamic_context_audit.py` for repeatable synthetic and live DB checks.
- Expanded `backend/tests/test_epic_context.py` to cover `0` token behavior, overflow metadata, card/profile selection, prompt raw-text safety, continuation, and file-backed exports.

## Gate 5 Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Gate 5 audit | PASS | `.local/epic_novel_runs/gate5_dynamic_context_2026-06-12T200323_839412Z.json` |
| Synthetic overflow | PASS | small budget produced overflow and recorded included/excluded/overflow metadata |
| Live ContextPack trace | PASS | project `44ce72be-a423-4f3b-895b-ad8a0fbb5e63`, chapter 1, trace `fa6ed1e5-c1bf-4a15-b2a7-b257d28c3f1d` |
| Raw reference safety | PASS | compiled context and chapter writer prompt exclude raw book/card text values |
| No unbounded prompt dump | PASS | chapter writer prompt has no `full_context_json` |
| Targeted tests | PASS | `$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_context.py -q` -> 8 passed |
| Compile check | PASS | `$env:PYTHONPATH='backend'; python -m compileall backend/app/services/epic_context.py backend/app/services/epic_novel.py scripts/epic_gate5_dynamic_context_audit.py` |

## Gate 6 Completed In Current Worktree

- Added issue-driven QA checks for plan mismatch, missing mustExecute, fallback/patch output, English boilerplate, raw reference leakage, bureaucracy function, broken power rule, heroine agency, character/relationship drift, foreshadow and reader-question drift, premature reveal, world/Bible contradiction, and undersized content.
- High/severe issues block acceptance.
- AcceptanceReport scores now derive from issue severity/type instead of fixed constants.
- `check_chapter()` re-runs QA against the current candidate text and persists `EpicIssue` + `EpicAcceptanceReport`.
- Chapter issues now carry `repair_scope.mode=local_issue_targeted` and `rewrite=forbidden`.
- Added `backend/tests/test_epic_qa.py`.
- Added `scripts/epic_gate6_qa_acceptance_audit.py`.

## Gate 6 Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Gate 6 audit | PASS | `.local/epic_novel_runs/gate6_qa_acceptance_2026-06-12T202046_749120Z.json` |
| Bad chapter blocking | PASS | audit chapter `911628` stayed `need_review`, `acceptedContentPresent=false` |
| Blocking issue types | PASS | planned-vs-actual, fallback/patch, raw reference leakage, English boilerplate, bureaucracy missing, broken power rule, heroine agency missing, foreshadow failures |
| Repair scope | PASS | all open bad-chapter issues use local issue-targeted repair scope; full rewrite forbidden |
| Canon boundary | PASS | before/after state counts unchanged for state extracts, canon facts, character/power/object/faction/institution ledgers, and pleasure beats |
| Repaired draft boundary | PASS | audit chapter `911629` did not write accepted content or canon state |
| Targeted tests | PASS | `$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_qa.py -q` -> 10 passed |
| Combined tests | PASS | `$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_epic_qa.py backend/tests/test_epic_context.py -q` -> 18 passed |
| Compile check | PASS | `$env:PYTHONPATH='backend'; python -m compileall backend/app/services/epic_novel.py scripts/epic_gate6_qa_acceptance_audit.py` |

## Gate 7 Completed In Current Worktree

- Upgraded `check_chapter()` so strengthened QA reruns also write `qa_planned_vs_actual_diff` and `qa_acceptance_gate` RetrievalTrace rows.
- `check_chapter()` now writes an AcceptanceReport on every strengthened QA rerun, including no-blocker reruns.
- Novel markdown/txt/json export filenames now use the Chinese display title and `正文` suffix instead of old English smoke project titles.
- Added `scripts/epic_gate7_canon_state_audit.py`.
- Re-ran chapters 1-3 through strengthened QA/context audit on the live project.

## Gate 7 Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Gate 7 audit | PASS | `.local/epic_novel_runs/gate7_canon_state_2026-06-12T205822_873613Z.json` |
| Chapters 1-3 | PASS | all three are `accepted`; latest report status `accepted`; no open high/severe issue |
| RetrievalTrace coverage | PASS | every chapter has `chapter_context_pack`, `qa_planned_vs_actual_diff`, `qa_acceptance_gate`, `qa_chapter_check` |
| ContextPack metadata | PASS | every chapter has metadata and `contextRawTextIncluded=false` |
| Previous state consumption | PASS | chapter 2 recent chapters include 1; chapter 3 recent chapters include 2 and 1; `previousRequiredStateReady=true` |
| Required ledgers | PASS | every chapter has StateExtract, CanonFact, CharacterState, PowerLedger, ObjectState, FactionState, InstitutionState, PleasureBeat |
| Canon boundary | PASS | `nonAcceptedCanonSources=0` |
| Exports | PASS | `.local/epic_novel_exports/本长篇项目 全书圣经.md`; `.local/epic_novel_exports/本长篇项目 正文.md`; metadata returns `contentReturned=false` |

## Gate 8 Completed In Current Worktree

- Added `scripts/epic_gate8_thirty_chapter_simulation.py`.
- Created a dedicated Chinese-titled continuity simulation project.
- Seeded minimal world/state foundation and 30 serial chapter contracts.
- Simulated 30 accepted chapters with required ContextPack/QA RetrievalTrace rows, AcceptanceReport rows, accepted content, and state ledgers.
- Verified previous-chapter readiness for every chapter after 1.
- Verified reference profile/card usage across all chapter traces.
- Verified overflow metadata in 4 chapters.
- Verified no raw reference text and `nonAcceptedCanonSources=0`.
- Retried chapter 30 and confirmed no duplicate canon/state writes.
- Exported continuity report and 30-chapter manuscript metadata.

## Gate 8 Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Gate 8 simulation | PASS | `.local/epic_novel_runs/gate8_thirty_chapter_2026-06-12T211746_940280Z.json` |
| 30 chapter report | PASS | `.local/epic_novel_runs/30_chapter_continuity_2026-06-12T211746_034084Z.md` |
| Simulation project | PASS | `c32bece9-5913-42d5-8a35-7bedfca497f4` |
| Accepted chapters | PASS | 30 / 30 |
| Required traces/reports/ledgers | PASS | every chapter has contract, context/QA traces, AcceptanceReport, acceptedContent, and state ledgers |
| Reference usage | PASS | every chapter trace uses profile `f376caf3-1a3b-4694-b78b-90c9bbafc96b` and production-ready cards |
| Retry idempotency | PASS | chapter 30 retry created no duplicate canon/state writes |
| Manuscript export | PASS | `.local/epic_novel_exports/第八闸门三十章连续性审计 20260612211418 正文.md` |

## Gate 9 Completed In Current Worktree

- Added `scripts/epic_gate9_full_volume_acceptance.py`.
- Verified configured live test volume 1, chapter range 1-3.
- Re-ran strengthened chapter QA during volume verification.
- Verified required trace types, AcceptanceReport rows, state ledgers, reference usage, foreshadow/reader-question ledgers, and canon source boundaries.
- Exported the volume manuscript as a file-backed artifact.
- Cleaned old smoke-derived volume title before reporting.
- Improved deterministic chapter draft text to avoid meta placeholders, dict-rendered institutions, English smoke titles, and raw-reference warning phrases in story content.
- Accepted chapter retries now mark old open chapter issues as `fixed`.

## Gate 9 Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Gate 9 audit | PASS | `.local/epic_novel_runs/gate9_full_volume_2026-06-12T214212_737800Z.json` |
| Full volume report | PASS | `.local/epic_novel_runs/full_volume_acceptance_2026-06-12T214212_736039Z.md` |
| Configured volume | PASS | volume 1, chapter range 1-3, title `第1卷 阶段主线` |
| Accepted chapters | PASS | 3 / 3 |
| Required traces/reports/ledgers | PASS | every chapter has required RetrievalTrace types, latest accepted report, and state ledgers |
| Canon boundary | PASS | `nonAcceptedCanonSources=0` |
| Volume export | PASS | `.local/epic_novel_exports/本长篇项目 第1卷 正文.md` |

## Gate 10 Completed In Current Worktree

- Added `validate_multi_volume_rolling_plan()` with explicit checks for missing GlobalSpine role, repeated filler volumes, missing unresolved-ledger carry-forward, premature future reveal, and old-state compression loss.
- Added `build_multi_volume_rolling_plan()` to assemble GlobalSpine-linked volume plans, active unresolved ledgers, prior volume summaries, future reveal constraints, long-range character arcs, compressed state, and reference usage metadata.
- Added `scripts/epic_gate10_multi_volume_rolling_plan.py`.
- Added `backend/tests/test_epic_multi_volume.py`.
- Wrote file-backed rolling-plan exports.

## Gate 10 Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Gate 10 audit | PASS | `.local/epic_novel_runs/gate10_multi_volume_2026-06-12T220227_446447Z.json` |
| Rolling plan report | PASS | `.local/epic_novel_runs/multi_volume_rolling_plan_2026-06-12T220227_445809Z.md` |
| Planned volumes | PASS | 3 |
| Validation | PASS | status `pass`, issueCount `0`, blockingIssueCount `0` |
| RetrievalTrace | PASS | `8fd0f399-9ce8-4566-8f1c-3246a5577df6`, raw reference excluded |
| Rolling context | PASS | old state compression, active unresolved ledger, prior summaries, future reveal constraints, long-range arcs |
| Plan export | PASS | `.local/epic_novel_exports/本长篇项目 多卷滚动规划.json` / `.md` |
| Targeted tests | PASS | `backend/tests/test_epic_multi_volume.py` -> 6 passed |

## Gate 11 Completed In Current Worktree

- Added `EpicProductionJob` for Epic scheduler state.
- Added `EpicGenerationLog.actual_cost`.
- Added scheduler helpers for create/progress/pause/resume/stop/retry-failed-only/cost cap/continuation chunks.
- Extended dashboard response with current job, progress, estimated remaining cost, errors, and retry status.
- Added `scripts/epic_gate11_scheduler_recovery.py`.
- Added `backend/tests/test_epic_scheduler.py`.

## Gate 11 Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Gate 11 audit | PASS | `.local/epic_novel_runs/gate11_scheduler_2026-06-12T221941_023711Z.json` |
| Scheduler report | PASS | `.local/epic_novel_runs/production_scheduler_2026-06-12T221941_022151Z.md` |
| Main job | PASS | `1e0d97ce-1866-4530-a343-effea22b406e`, completed 6/6 |
| Pause/resume/stop | PASS | paused and stopped jobs did not advance; resumed job continued |
| Retry failed only | PASS | failed `chapter_generation` retried only that step |
| Cost evidence | PASS | provider/model/tokens/estimated cost/actual cost/status/error logs recorded |
| Cost cap | PASS | cost-cap job stopped before advancing |
| Continuation chunks | PASS | 1 chunk recorded with `contentReturned=false` |
| Canon retry safety | PASS | chapter 1 canon facts stayed `1 -> 1` |
| Dashboard exposure | PASS | current job/progress/remaining cost/errors/retry fields present |
| Targeted tests | PASS | `backend/tests/test_epic_scheduler.py` -> 5 passed |

## Gate 12 Completed In Current Worktree

- Changed Bible/novel export API defaults to metadata mode.
- Added artifact export metadata for volume markdown, chapter bundle, QA reports, retrieval traces, and acceptance reports.
- Added file checksum/object id/download route metadata.
- Summarized retrieval trace export so raw `raw_json` and full large ID lists are not emitted.
- Added `scripts/epic_gate12_export_system.py`.
- Extended export metadata tests in `backend/tests/test_epic_context.py`.

## Gate 12 Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Gate 12 audit | PASS | `.local/epic_novel_runs/gate12_export_2026-06-12T224306_008134Z.json` |
| Export report | PASS | `.local/epic_novel_runs/export_system_2026-06-12T224306_006655Z.md` |
| Export count | PASS | 8 file-backed artifacts |
| Metadata default | PASS | `contentReturned=false` |
| Checksum/object id | PASS | `sha256` and `objectId` present |
| Download route separation | PASS | explicit `mode=download` routes present |
| Large-file path | PASS | `Gate12 大文件路径探针 20260612224305.md`, 3,480,027 bytes |
| Retrieval trace size | PASS | `本长篇项目 检索轨迹.json`, 4,684,462 bytes after summary export |
| Raw reference exclusion | PASS | `rawReferenceTextExcluded=true` |
| Targeted tests | PASS | `backend/tests/test_epic_context.py` -> 9 passed |

## Gate 13 Completed In Current Worktree

- Expanded the Epic workspace into 10 Chinese module tabs.
- Added real API panels for reference corpus, SoNovel/download tasks, pattern cards, mechanism profile, Bible/world/planning/execution/context/QA/dashboard/export/log data.
- Added export metadata controls for Bible, novel and artifact exports while keeping downloads on explicit download routes.
- Hardened export metadata rendering so missing `sha256`/filename/bytes fields do not crash the page.
- Normalized project detail state so older or partial backend responses do not block the workspace from rendering.
- Kept auxiliary detail calls non-blocking so a slow dashboard/cost/profile endpoint does not prevent the project detail page from opening.

## Gate 13 Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Frontend build | PASS | `npm.cmd run build` |
| Targeted Epic tests | PASS | `backend/tests/test_epic_reference.py backend/tests/test_epic_context.py backend/tests/test_epic_chinese_output.py backend/tests/test_epic_qa.py backend/tests/test_epic_multi_volume.py backend/tests/test_epic_scheduler.py` -> 53 passed |
| Local project list | PASS | `/epic-novel` shows Chinese title, create form, project rows, loading/error/empty states |
| Local project detail | PASS | project `ac9f3c28-c725-44cb-bd4b-327bb7b80dc2` opened in browser |
| 10 tab browser smoke | PASS | 基建总览、参考语料、圣经世界、规划合同、章节生产、上下文检索、质检验收、生产仪表盘、文件导出、调用日志 |
| Old English Epic labels | PASS | `Long-form control room`, `Create project`, and `Loading projects` were absent in the checked runtime page |
| Export metadata UI | PASS | `文件导出 -> 正文元数据` no longer crashes; missing hash shows `未返回校验和` |
| Browser runtime errors | PASS | no new `EpicNovelWorkspace` or `Cannot read properties` errors after the smoke run |

## Current Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Python compile | PASS | `$env:PYTHONPATH='backend'; python -m compileall backend/app scripts` |
| Targeted tests | PASS | `53 passed` for `backend/tests/test_epic_reference.py backend/tests/test_epic_context.py backend/tests/test_epic_chinese_output.py backend/tests/test_epic_qa.py backend/tests/test_epic_multi_volume.py backend/tests/test_epic_scheduler.py` |
| Gate 3 reference usage depth | PASS | `.local/epic_novel_runs/gate3_reference_usage_depth_2026-06-12T152527_937305Z.json`; 69 new traces; 19/19 task types passed |
| Gate 4 real Chinese Master Bible | PASS | `.local/epic_novel_runs/gate4_master_bible_2026-06-12T154950_704943Z.json`; 24/24 accepted; Bible status `completed` |
| Gate 5 dynamic context compiler | PASS | `.local/epic_novel_runs/gate5_dynamic_context_2026-06-12T200323_839412Z.json`; synthetic and live checks passed |
| Gate 6 QA acceptance gate | PASS | `.local/epic_novel_runs/gate6_qa_acceptance_2026-06-12T202046_749120Z.json`; high/severe blocking and canon boundary passed |
| Gate 7 canon-state execution 1-3 | PASS | `.local/epic_novel_runs/gate7_canon_state_2026-06-12T205822_873613Z.json`; chapters 1-3 PASS after strengthened QA |
| Gate 8 30-chapter continuity | PASS simulation | `.local/epic_novel_runs/gate8_thirty_chapter_2026-06-12T211746_940280Z.json`; 30/30 simulated chapters accepted |
| Gate 9 full volume acceptance | PASS configured volume | `.local/epic_novel_runs/gate9_full_volume_2026-06-12T214212_737800Z.json`; configured volume 1, chapters 1-3 PASS |
| Gate 10 multi-volume rolling plan | PASS | `.local/epic_novel_runs/gate10_multi_volume_2026-06-12T220227_446447Z.json`; 3 volumes, 0 issues, rolling context/export PASS |
| Gate 11 scheduler/cost/recovery | PASS | `.local/epic_novel_runs/gate11_scheduler_2026-06-12T221941_023711Z.json`; pause/resume/stop/retry/cost cap/cost logs/canon retry safety PASS |
| Gate 12 large export system | PASS | `.local/epic_novel_runs/gate12_export_2026-06-12T224306_008134Z.json`; 8 metadata exports, checksum/download route, large path, raw reference excluded PASS |
| Gate 13 frontend workspace | PASS | local browser smoke: 10/10 Chinese tabs PASS, export metadata click PASS, no new runtime errors |
| Frontend build | PASS | `npm.cmd run build` in `frontend/` |
| EPIC frontend Chinese UI scan | PASS | old visible labels such as `Epic Novel`, `Long-form`, `World Bible`, `Pattern Cards`, `Execute chapter`, `Generation logs` have no hits in `frontend/src/pages/EpicNovelWorkspace.tsx` or `frontend/src/components/Sidebar.tsx`; `npm run build` passed after the UI change |
| EPIC browser UI smoke | PASS | local browser project `ac9f3c28-c725-44cb-bd4b-327bb7b80dc2`; verified 10 tabs: 基建总览, 参考语料, 圣经世界, 规划合同, 章节生产, 上下文检索, 质检验收, 生产仪表盘, 文件导出, 调用日志 |
| EPIC Chinese UI GitHub/ECS deploy | PASS current | current pushed HEAD deployed to ECS; web bundle contains Gate 13 Chinese labels including `文件导出`, `参考语料`, `生产仪表盘`, `长篇小说工业引擎` |
| DB init | PASS | `init_db_ok` with small pool / short timeout |
| Reference cost dry-run | PASS | `.local/epic_novel_runs/reference_cost_dry_run_2026-06-12.json`: 57 books, 106,914 sections, 185,567,295 chars, 86,582,220 estimated prompt tokens, 1,444 batches, DeepSeek V4 Pro |
| Gate 2B staged expansion | PARTIAL | 3 source books / 27 successful section analyses / 48 production-ready cards; full 57-book extraction incomplete |
| Production hardcoded-name scan | PASS for production services | no fixed smoke-story names in `backend/app/services/epic_novel.py` or `backend/app/services/epic_reference.py`; remaining hits are SPEC/test-project docs and negative tests |
| Strict live acceptance smoke | PASS | `.local/epic_novel_runs/acceptance_smoke_strict_2026-06-12-resume4.log`, project `44ce72be-a423-4f3b-895b-ad8a0fbb5e63`, exit code 0 |
| GitHub push | PASS current | current HEAD pushed to `origin/codex/bilibili-cost-guard-main` and `origin/main` |
| Beijing ECS deploy | PASS current | `/opt/video-plan/.deploy-version` verified against deployed HEAD; `docker compose up -d --build` completed |
| ECS backend/container health | PASS current | `backend`, `worker`, `beat`, `web` Up; backend `/health` returned 200; router count 124 |
| Public ECS health | PASS current | public `/` 200, `/epic-novel` 200, `/api/v1/system/health` 200 with `celery_alive=true` |
| ECS raw corpus | PASS current | `/opt/video-plan/.local/novel_ref_corpus/raw`: 57 epub files, 296,501,932 bytes |
| ECS DB counts | PASS current | booklist 100, books 57, sections 27, analyses 27, success=27, cards 98,629, production-ready cards 48, profiles 21, projects 13, bible sections 192, chapters 90 |

## Strict Acceptance Summary

- Project: `44ce72be-a423-4f3b-895b-ad8a0fbb5e63`
- ReferencePatternCards: `98,581`
- ReferenceMechanismProfiles: `10`
- Bible sections: `24`
- Bible failures: `[]`
- Bible coverage items: `24`
- Coverage failures: `[]`
- Volume contracts: `3`
- Chapter contracts: `10`
- Accepted chapters: `3`
- State extracts: `3`
- Retrieval traces: `19`
- Unsafe retrieval traces: `[]`
- Acceptance reports: `3`
- `nonAcceptedCanonSources`: `0`
- Chapter 2 and 3 previous-state readiness: `true`
- Chapter 2 and 3 context includes Bible, WorldAtlas, bureaucracy, and ReferenceMechanismProfile.
- Bible export chars: `150,603`
- Novel export chars: `15,099`

## Current Limits

- Gate 2B staged expansion covers 3 source books / 27 successful section analyses only; do not treat it as full Reference Intelligence PASS for all imported books or the 100-book corpus.
- Gate 3 is PASS for reference usage wiring only; it does not prove full Master Bible quality, 30-chapter continuity, full-volume acceptance, or 300万字 readiness.
- Gate 4 is PASS for Master Bible content only; it does not prove 30-chapter continuity, full-volume acceptance, or 300万字 readiness.
- Gate 5 is PASS for dynamic context and prompt safety only; it does not prove 30-chapter continuity, full-volume acceptance, or 300万字 readiness.
- Gate 6 is PASS for targeted QA blocking only; it does not prove 30-chapter continuity, full-volume acceptance, or 300万字 readiness.
- Gate 7 is PASS for chapters 1-3 only; it does not prove 30-chapter continuity, full-volume acceptance, or 300万字 readiness.
- Gate 8 is PASS by deterministic continuity simulation only; it does not prove 30 production-quality prose chapters, full-volume acceptance, or 300万字 readiness.
- Gate 9 is PASS for one configured live test volume only; it does not prove all generated volumes, frontend readiness, current deployment, or 300万字 readiness.
- Gate 10 is PASS for multi-volume planning and rolling context only; it does not prove all-volume generated prose, frontend readiness, current deployment, or 300万字 readiness.
- Gate 11 is PASS for scheduler/cost/recovery controls only; it does not prove frontend readiness, current deployment, or 300万字 readiness.
- Gate 12 is PASS for file-backed export safety only; Gate 13 later proves local frontend runtime, but Gate 12 alone does not prove current deployment, full generated manuscript, or 300万字 readiness.
- Gate 13 is PASS for local frontend runtime only; it does not prove current ECS deployment, full generated manuscript, or 300万字 readiness.
- Gate 14 is PASS for current GitHub/ECS publication, but the final SPEC verdict remains `PARTIAL_IMPLEMENTATION` because full corpus extraction and real 300万字 quality evidence are missing.
- Full 100-book corpus is still limited by source availability: 57 local imported books are available; unavailable/failed titles remain skipped per user instruction.
- Full DeepSeek LLM section analysis over all estimated 106,914 sections has not been run to completion in this worktree; Gate 2B currently proves 3 source books / 27 successful section analyses, and cost dry-run plus controls are implemented.
- No first-class Alembic migration framework is present; this repo continues to use startup `create_all()` plus self-heal columns.
- True docx Bible export remains a future enhancement; current acceptance covers markdown/json/file metadata export.
- Do not claim a completed 300万字 production novel run; current proof is the SPEC acceptance smoke, not a full 300万字 manuscript.

## Remaining

- Continue Gate 2B staged extraction in small resumable batches; fallback analyses must remain excluded from production evidence.
- Continue full DeepSeek LLM section analysis if the user wants every estimated section in the 57-file corpus processed by the expensive extraction pipeline.
- Continue scanning/importing SoNovel downloads if new files become available; unavailable/failed titles remain skipped per user instruction.
- Continue full Reference Intelligence extraction and real long-run generation only when cost/time is approved; do not claim `PRODUCTION_READY` or 300万字-ready from the current evidence.

## SoNovel Adapter Hardening (2026-08-10)

- Added the `ExternalDownloaderAdapter` contract for trusted local reference-download providers; `SoNovelAdapter` now implements it.
- Installation detection no longer requires both `sonovel.exe` and `app.jar`: a local runtime marker, `config.ini`, or configured downloads directory is enough to expose a safe `manual_required` workflow. WebUI invocation remains opt-in and is enabled only after the local WebUI responds.
- Download scanning now includes nested folders under `SONOVEL_DOWNLOAD_PATH`, so date- or source-organized exports are discoverable by the existing importer.
- Output validation now exposes inferred title/author and optional duplicate-book detection when a database session is supplied; unsupported, empty, unnamed, or duplicate files are not marked valid.

Validation on 2026-08-10:

- `PYTHONPATH=backend python -m pytest backend/tests/test_epic_reference.py -q` -> `19 passed`
- `PYTHONPATH=backend python -m compileall -q backend/app/services/epic_reference.py` -> passed

This hardening does not run SoNovel, import new reference books, or invoke DeepSeek. The overall production-readiness verdict remains `PARTIAL_IMPLEMENTATION`; full-corpus extraction still requires explicit cost/time approval.

## Qingdou Duplicate-Billing Protection (2026-09-03)

- Added account-level handling for Qingdou `batchStatus=-2` / item `status=1113`; these failures now pause generation, skip alternate URLs, and never invoke the OpenAI audio fallback.
- Qingdou task creation POSTs are single-shot. Transport interruption, HTTP 408/5xx, or post-submit cache failure becomes an explicit delivery-uncertain hold instead of an automatic replay.
- Added Redis batch-id checkpointing so polling timeouts resume the accepted Qingdou batch rather than creating a second paid task.
- Added regression coverage for account quota holds, candidate URL short-circuiting, OpenAI fallback suppression, and single-shot creation on a lost response.

Validation on 2026-09-03:

- `python -m pytest -q tests/test_qingdou_concurrency.py tests/test_qingdou_xiaohongshu.py tests/test_youtube_transcript.py` from `backend/` -> `31 passed, 1 warning`
- `python -m compileall -q backend/app scripts` -> passed
- `git diff --check` -> passed

Deployment and task recovery remain pending until the protected code is pushed and the Beijing ECS work queue is inspected.

Follow-up production finding on 2026-09-03:

- A Qingdou-restored canary returned terminal provider status `batchStatus=2/status=1114` (no usable transcript). The old candidate loop submitted the same Bilibili BV once for the canonical URL and once for a tracking-query variant.
- Fixed task-layer candidate identity filtering for Bilibili so equivalent BV URLs are submitted at most once; alternate endpoint shapes for WeChat remain available.
- The canary failure was not an OpenAI call and did not enter a generation hold. It remains failed for manual/source review rather than being auto-redispatched.
- Added regression coverage proving Bilibili tracking variants produce one Qingdou submission.

Follow-up validation: `python -m pytest -q tests/test_qingdou_concurrency.py tests/test_qingdou_xiaohongshu.py tests/test_youtube_transcript.py tests/test_video_sources.py` -> `47 passed, 1 warning`.

## 视频文案对照表 / 金山文档同步 (2026-09-21, 非 Epic Novel 模块)

老板 2026-09-20 在金山文档《视频文案汇总》提的需求：把每条视频生成的文稿对上原视频标题，放进汇总表，
让编辑能对比生成稿与原视频文案的相似度，避免侵权投诉。该功能只读 `tasks` / `chapters` / `config`，不改动
Epic Novel Engine，也不改动既有短篇生成链路。

- 新增 `backend/app/services/video_script_table.py`：源视频标题还原（B 站转写头部 `视频标题：`、抖音/小红书分享文案正则、
  `config.source_title` 兜底）、相似度（字符 5-gram 重合度 + 最长公共连续片段 + SimHash64）、风险分级
  （低/中/高/待核对，缺原文一律「待核对」而不是误判低风险）、csv/tsv/xlsx/md 导出。
- 新增 `backend/app/services/kdocs_adapter.py`：金山文档开放平台适配器（OAuth2 授权/换 token/刷新、
  `GET/POST /api/v1/openapi/ksheet/{file_token}/sheets/{sheet_idx}/cells` 读写、单请求 ≤2000 格分批、
  写入后回读校验、失败自动降级导出、JSONL 审计日志）。
- 新增 `/api/v1/video-script-table/*` 路由（rows / export / problems / config / logs / remote-schema / sync /
  auth-url / exchange-code）与 `scripts/video_script_table_export.py` CLI。
- 新增前端页面「视频文案对照表」（预览 / 复制全表到剪贴板 / 导出 / 同步，默认试运行）。
- 默认 `KDOCS_PROVIDER=manual_required`：未配置开放平台授权时导出 CSV/TSV 供编辑直接粘贴，不阻塞使用。

验证（2026-09-21）:

- 真实样例：任务 `2c714d03-600c-40dd-bdb5-9c0f29cf8082` →
  视频《【深度】理论+亲身经历，我为什么劝你关注法拍房？》↔ 文稿《法拍房便宜的代价》，
  相似度 0.05%、原文重合度 0.61%、最长雷同 0 字、风险「低」。
- `python -m pytest -q tests/test_video_script_table.py tests/test_kdocs_adapter.py tests/test_bilibili_transcript.py`
  from `backend/` -> `19 passed`。
- `npx tsc -p tsconfig.json --noEmit` from `frontend/` -> passed。
- FastAPI TestClient 冒烟：`/config` `/rows` `/export?fmt=tsv` `/sync(dry_run)` 均 200。

Epic Novel Engine 的整体结论不变；本次改动不属于 SPEC 阶段范围。

## 视频文案对照表 / 金山文档同步 —— 第二批（2026-09-21 晚）

在第一批（对照表 + 金山文档适配器）基础上补齐"一次性导入"链路和标题覆盖：

- `scripts/video_script_table_export.py` 新增 `setup / find-doc / doctor / enrich` 子命令，
  `sync` 新增 `--once / --ensure-columns`；`enrich` 支持 `--workers`（并发抓取，写库仍串行）和 `--cookies`（yt-dlp cookies.txt）。
- `backend/app/services/kdocs_adapter.py` 的 `sync_once()`：找表 → 读远端表头做别名映射 → 缺列自动补在最右侧（不动已有列）
  → 按"视频链接 + 任务ID"去重 → 分批写入（≤2000 格/请求）→ 回读逐格校验 → JSONL 审计；失败自动降级导出 CSV。
- `backend/app/tasks/kdocs_tasks.py` + `celery_app.beat_schedule`：可按 `KDOCS_AUTO_SYNC_INTERVAL_SECONDS` 定时自动同步，
  未授权时任务直接 skip。
- `backend/app/services/video_source_titles.py`：共享 httpx 连接池、`--workers` 并发抓取、
  抖音口令文案解析（`share_text`）、无 cookies 时快速失败（`needs_cookies`）、
  同一视频被"分享文案/裸短链"两条任务指向时不互相覆盖结果、`id = ANY(CAST(:ids AS uuid[]))` 修正。
- 风险等级新增「不适用」（`manual://` 手动录入来源），前端徽章同步支持。

2026-09-21 实测与验证：

- 1173 条任务：标题覆盖 688 行；风险分布 低 819 / 待核对 278 / 不适用 76。
- 老板样例任务 `2c714d03-600c-40dd-bdb5-9c0f29cf8082`：相似度 0.05%、最长雷同 0 字、风险「低」。
- 导出：`.local/video_script_table/exports/视频文案汇总.xlsx`、`视频文案汇总_全量.tsv`（1166 行）。
- `python -m pytest -q tests/test_video_script_table.py tests/test_kdocs_adapter.py tests/test_video_source_titles.py`
  from `backend/` -> `32 passed`。
- `npx tsc -p tsconfig.json --noEmit` from `frontend/` -> passed。

金山文档直写（方式 B）仍需人工一步：开放平台建应用拿 `APP_ID`/`APP_KEY` 并授权一次，
之后 `sync --once --ensure-columns` 即可全量写入。未授权时走导出 + 粘贴，编辑侧不阻塞。

## 视频文案对照表 —— 第三批：新任务自动进表（2026-09-21 深夜）

需求补充：全平台标题都要自动进入，且"以后每生成一个新任务"都要自动进后续那张金山文档。

- 新增 `backend/app/services/kdocs_sync.py`：`enqueue_row_sync(task_id)`，完成时把单行增量丢给 Celery；
  未开开关/未授权/broker 异常一律降级为日志，绝不影响生成主链路。
- `app/tasks/kdocs_tasks.py::sync_video_script_table` 新增 `task_ids` 参数，支持单任务增量推送（不再只依赖 beat 轮询）。
- 新增入库即抓标题：`video_source_titles.capture_source_title_into_config()`
  （config 已有 → 转写头部 → 分享文案 → 可选联网），在 `app/tasks/tasks.py` 抓到文案后调用，
  写 `config.source_title` / `config.source_title_source`，全平台通用。
- 完成钩子接入 4 处：主生成流程、公版/古文流程、通用长篇、AIGC 复检；celery beat 每 300 秒兜底对账。
- 新增配置：`VIDEO_SCRIPT_TABLE_AUTO_TITLE_FETCH`、`VIDEO_SCRIPT_TABLE_YTDLP_COOKIES`、
  `KDOCS_AUTO_SYNC_ON_COMPLETED`；`KDOCS_AUTO_SYNC_ENABLED` 改为默认 `true`，
  `KDOCS_AUTO_SYNC_INTERVAL_SECONDS` 600→300，`KDOCS_AUTO_SYNC_MAX_ROWS` 200→500。
- `.env` / `.env.example` 同步更新。

验证（2026-09-21）：

- `python -m pytest -q tests/test_kdocs_sync_hooks.py tests/test_video_source_titles.py tests/test_video_script_table.py tests/test_kdocs_adapter.py`
  from `backend/` -> `49 passed`（含新增的"开关关闭不推送""broker 异常不抛出""入库抓标题"用例）。
- 仍需人工一步：金山文档开放平台建应用 + 授权一次（`setup → authorize → exchange-code → find-doc → doctor → sync --once`）。

## 视频文案对照表 —— 第四批：视频链接 → 文稿标题 一一对应回填（2026-09-21，需求口径校正）

老板需求口径被校正为**一件事**：金山文档《视频链接汇总》里**已有原视频链接**（及原视频标题等），
**唯一缺的是"AI 生成的文稿标题"这一列**；要按**视频链接**把生成文稿标题写回**同一行**，
全自动、每生成一个就进表、且不能填错。区域（区块）分类不需要。
（第一/二/三批的"相似度风险对照表"保留为附属能力，不再是主线。）

新增主线核心 `backend/app/services/video_link_titles.py`：

- `extract_url` / `link_keys`：链接归一化 -> 匹配键集合。B 站 `bilibili:bv:*` / `bilibili:av:*`（query 参数不影响）、
  抖音 `douyin:*`、小红书 `xhs:*`、YouTube `youtube:*`、微信 `wechat:*`、短链 `b23:*` / `douyin-short:*` / `xhs-short:*`；
  无 URL 的纯文本退回 `text:前160字`。
- `build_task_index` / `match_links`：逐条链接匹配；同一视频多篇文稿时**优先取有标题的那篇**，
  `prefer=latest|first|join`；任务存在但标题为空 -> `matched=False` + 「任务已存在，但还没有生成文稿标题」；
  库里没有该链接 -> 留空。**宁可不填，也不乱填。**
- `resolve_short_links`：联网展开 `b23.tv` / `v.douyin.com` / `xhslink.com`，带本地缓存。
- `render_tsv` / `parse_link_lines`：输出「视频链接 / 文稿标题 / 匹配状态 / 任务ID」四列，行序与输入一致。

`backend/app/services/kdocs_adapter.py` 新增：

- `read_link_rows()`：读回表头候选行 + 数据区，自动定位「视频链接」列；支持表头不在第 0 行
  （表头行本身是标题行时会跳过、往下试 3 行），列宽以工作表 `col_to` 为准。
- `sync_link_titles()`：缺「文稿标题」列时自动补在最右侧；**只写标题列**（可选状态列）；
  值没变的行跳过（`unchanged_rows`，幂等）；每请求 ≤2000 格分批；写完 `_verify_cells` **逐格回读校验**，
  不一致 -> `status=error` + `mismatches`；未授权 -> `manual_required`（不抛异常）。
- `KdocsConfig` 新增 `link_column` / `title_column` / `status_column`，从
  `KDOCS_LINK_COLUMN` / `KDOCS_TITLE_COLUMN` / `KDOCS_STATUS_COLUMN` / `KDOCS_LINK_TITLE_PREFER` /
  `KDOCS_LINK_RESOLVE_SHORT_LINKS` 读取。

自动推送链路：

- `backend/app/tasks/kdocs_tasks.py` 新增 `sync_kdocs_link_titles_task`（任务名 `tasks.sync_kdocs_link_titles`）；
  `kdocs_sync.enqueue_row_sync` 改发这个任务（`retry=False`，broker 挂了立刻返回）。
- `backend/celery_app.py` 的 `beat_schedule` 改为 `sync-kdocs-link-titles` -> `tasks.sync_kdocs_link_titles`，
  间隔 `KDOCS_AUTO_SYNC_INTERVAL_SECONDS`。
- 完成钩子沿用第三批 4 处（主流程 / 公版古文 / 通用长篇 / AIGC 复检），异常全吞。

CLI `scripts/video_script_table_export.py` 新增：

- `links`（不需授权）：`--links-file` / `--clipboard` / stdin，输出 `文稿标题_回填.tsv` +
  `文稿标题_回填.titles.txt`（纯标题列，可直接整列粘回金山文档）。
- `sync-links`（需授权）：`--dry-run` / `--link-column` / `--title-column` / `--status-column` / `--prefer` / `--no-resolve`。

测试（离线）：

- 新增 `backend/tests/test_video_link_titles.py`（11 个）、`backend/tests/test_kdocs_link_titles.py`（6 个，FakeClient），
  覆盖：B 站 query 参数、短链解析、抖音分享文案、小红书 note_id、YouTube id、prefer=latest/join、
  跳过无标题任务、未匹配留空、幂等、自动补列、表头在第 2 行、dry-run、未授权。

验证（2026-09-21）：

- `python -m pytest -q tests/test_video_link_titles.py tests/test_kdocs_link_titles.py tests/test_kdocs_adapter.py tests/test_video_script_table.py tests/test_video_source_titles.py tests/test_kdocs_sync_hooks.py tests/test_video_sources.py`
  from `backend/` -> `72 passed`。
- 回归：`test_recovery_watchdog / test_general_longform* / test_aigc_* / test_public_domain_* / test_cover_workflow / test_media_transcription / test_qingdou_*`
  -> `513 passed, 2 failed`；2 个失败为既存问题（`test_qingdou_xiaohongshu.py` 的
  `test_exhausted_retry_step_is_not_waiting_auto_recovery_label`、
  `test_xiaohongshu_share_token_is_not_reported_as_model_api_key_error`），已用 `git stash` 确认与本次无关。
- 真实数据：1173 tasks / 12948 chapters；视频类 1097，去重后 1009 个视频；平台分布 B站 577 / 抖音 427 / 小红书 81 / 手动 76 / YouTube 5 / 微信视频号 2。
- `links` 命令实跑 12 条真实链接 -> 匹配 11（1 条为"分享文案+garbled 短链"未匹配，符合预期）。
- 老板样例任务 `2c714d03-600c-40dd-bdb5-9c0f29cf8082` -> 视频《【深度】理论+亲身经历，我为什么劝你关注法拍房？》↔ 文稿《法拍房便宜的代价》。

仍需人工一步：金山文档开放平台建应用 + 授权一次
（`setup → authorize → exchange-code → find-doc → doctor → sync-links --dry-run → sync-links`）。
未授权前用 `links --clipboard` 出标题列，人工粘回。

Epic Novel Engine 的整体结论不变；本次改动不属于 SPEC 阶段范围（与前三批同属视频文案对照表模块）。

## 视频文案对照表 —— 第五批：全平台链接覆盖 + 未进表链接一键补入（2026-09-21）

需求补充：抖音 / 小红书 / 其他平台的链接也要一起管起来，而且**文档里还没有的链接也要自己补进去**。

`backend/app/services/video_link_titles.py`：

- `link_keys` 平台覆盖从 6 类扩到 **16 类**：新增快手（含 `v.kuaishou.com` 短链）、西瓜视频、微博（`fid=` / `/status/` / `/tv/show/`）、
  腾讯视频、优酷、爱奇艺、好看视频、TikTok、Instagram、X(Twitter)、知乎、B 站番剧 `ep`/`ss`、B 站专栏 `cv`、
  微信视频号 `sph`、`iesdouyin.com/share/video/`；统一走 `_URL_ID_RULES` 规则表（host 后缀 + 正则 + key 前缀）。
- `guess_platform` 改走 `_PLATFORM_NAMES` 表，覆盖同样这批 host。
- 新增 `canonical_key()`：从一组匹配键里挑最有代表性的一支（`bilibili:bv` > `douyin` > `xhs` ... > 短链 > `url:`），
  用于"一条视频一行"。
- 新增 `build_video_rows(db, statuses, prefer)`：把库里**所有平台**的视频任务归并成一条视频一行
  （链接 / 文稿标题 / 平台 / 原视频标题 / 状态 / 任务ID / 候选篇数），跳过 `manual://`。
- 新增 `render_lookup_tsv()` + `LOOKUP_COLUMNS`。
- `build_matcher` 现在把 `index` / `prefer` 挂在返回的 matcher 上，供适配器补行时使用。

`backend/app/services/kdocs_adapter.py`：

- `read_link_rows` 额外返回 `sheet_row_to`（工作表实际行数），用于定位追加位置。
- `sync_link_titles` 新增 `append_missing` / `append_entries` / `max_append`：
  先算"文档里已有链接的 key 集合"，`append_entries` 里 key 不相交且**有文稿标题**的，
  从表尾开始按"链接列 + 标题列（+可选状态列）"追加，一条视频一行；
  已存在/无标题/超 `max_append` 的跳过；追加的格子同样纳入回读校验。

CLI `scripts/video_script_table_export.py`：

- 新增 `lookup`：导出**全平台**"链接 -> 文稿标题"查找表（不需要授权）。
- `sync-links` 新增 `--append-missing` / `--max-append`。
- 新增 `docs/KDOCS_AUTHORIZATION_REQUEST.md`：授权沟通稿（可直接转发给文档所有者）+ 不给授权时的替代路径。

验证（2026-09-21）：

- `python -m pytest -q tests/test_video_link_titles.py tests/test_kdocs_link_titles.py tests/test_kdocs_adapter.py tests/test_video_script_table.py tests/test_video_source_titles.py tests/test_kdocs_sync_hooks.py tests/test_video_sources.py`
  from `backend/` -> `80 passed`（新增 8 个：16 类平台归一化、微信视频号 `sph`、`guess_platform`、`canonical_key`、
  `render_lookup_tsv`、追加未进表链接、追加 dry-run 不写、`max_append` 截断）。
- `python scripts\video_script_table_export.py lookup` 实跑：**1000 条视频**（有文稿标题 994 条）——
  bilibili 514 / douyin 408 / xiaohongshu 72 / youtube 3 / wechat 2 / unknown 1。
- `sync-links --append-missing --dry-run`：未授权时正确返回 `manual_required`，不误写。

仍需人工一步：金山文档开放平台建应用 + 授权一次（沟通稿见 `docs/KDOCS_AUTHORIZATION_REQUEST.md`）。

Epic Novel Engine 的整体结论不变；本次改动不属于 SPEC 阶段范围（与前三批同属视频文案对照表模块）。

## 视频文案对照表 —— 第六批：不授权也能干（导出 -> 回填 -> 粘回）（2026-09-21）

需求追问："自建应用的授权和他们已经填好的金山文档链接没关系吧，难道让他们重新处理？"

结论：**没关系，也不用重处理**。分享短链是"给人看的门"，开放平台应用+一次授权是"给程序用的门"，
两者指向同一份文档；授权不重建文档、不迁移数据、不需要编辑重录任何内容。
为彻底绕开"对方不想建应用/不想授权"，补了一条**零授权、零改动**的路：

- `backend/app/services/video_link_titles.py`：
  - 抽出可复用的 `column_index_of()` / `guess_column()` / `locate_link_column()`，
    把"表头可能不在第一行 / 没有表头 / 表头行本身是数据"的探测逻辑从适配器里解耦出来（适配器改为委托，行为不变）。
  - 修掉"单列纯链接文件"场景下 headerless 回退拿不到 URL 密度分的问题。
- `scripts/video_script_table_export.py` 新增 `fill`：
  `fill --src 金山文档导出的.xlsx` -> 输出 `xxx_回填.xlsx`（**同一个表**：行列、其它列、格式原样不动，
  只在最右侧补「文稿标题」列）+ `xxx_回填.titles.txt`（纯标题列）。
  支持 xlsx / csv / tsv / 单列 txt；表里已有「文稿标题/生成文稿名/文章标题…」列则直接写入；
  匹配不上留空；`--sheet / --link-column / --prefer / --no-resolve` 可调。
- `docs/KDOCS_AUTHORIZATION_REQUEST.md` 增补两节：
  「授权跟已经填好的那张表是什么关系」（两通道对照表 + 让文档创建者授权最稳）
  「对方连建应用都不愿意弄：导出 -> 回填 -> 粘回」。

验证（2026-09-21）：

- `python -m pytest -q tests/test_video_link_titles.py tests/test_kdocs_link_titles.py tests/test_kdocs_adapter.py`
  from `backend/` -> `37 passed`（含新增的 5 个：表头定位、表头不在首行、无表头、
  指定列名、URL 密度兜底）。
- 真实 xlsx 固定样例端到端：4 行链接（B站样例/抖音/小红书/不存在的BV）-> 匹配 3、未匹配 1，
  输出文件其它列一字未改，新增「文稿标题」列三行有值、未匹配行留空。
- 单列 txt（一行一条链接）-> 匹配 2/2，输出 `链接 \t 标题` 两列。

Epic Novel Engine 的整体结论不变；本次改动不属于 SPEC 阶段范围（与前三批同属视频文案对照表模块）。

## 视频文案对照表 —— 第七批：授权链路核对官方文档 + 沟通稿纠偏（2026-09-21）

需求追问："那授权该让他们怎么弄"。

拿官方文档把授权链路逐条核对了一遍（developer.kdocs.cn），并据此修正了沟通稿里
"3~5 分钟"的错误预期：

- 官方《入驻金山文档开放平台》：服务端 API 要**企业入驻** —— 新建服务商需填
  服务商名称（与营业执照一致、≤20 字）/ 企业邮箱 / 联系电话 / 官网 / 营业执照（PNG-JPG <10MB）/ 行业 / 规模，
  平台 **3 个工作日**内审核，通过后才能创建应用拿 `app_id`/`app_key`。
- 官方《Web 授权》：`GET /h5/auth?app_id&scope&redirect_uri&state` -> 回调
  `redirect_uri?code=..&state=..`，**code 仅 5 分钟有效**（过期报 20001）；
  `GET /api/v1/oauth2/access_token?code&app_id&app_key` -> access_token（86400s）+ refresh_token（90 天）；
  `POST /api/v1/oauth2/refresh_token?app_id` body `{app_key, refresh_token}`。
  **回调地址必须是应用里登记过的地址之一。**
- 官方《权限说明》的 scope 与项目默认值逐字一致：
  `user_basic` / `access_personal_files` / `edit_personal_files` / `download_personal_files`。
- 官方《接口说明》：分「个人文档接口 / 应用文档接口」；我们走的是**个人文档接口**，
  所以必须由**能在这份表所在账号的"我的文档"里看到它的人**（即文档创建者）授权。
  应用分「测试应用 / 正式应用」，测试应用 500 次/天（够用）。
- 核对结论：`backend/app/services/kdocs_adapter.py` 的 `authorize_url` / `exchange_code` / `refresh_token`
  与官方接口路径、参数、方法全部一致，无需改动。

文档纠偏：

- `docs/KDOCS_AUTHORIZATION_REQUEST.md` 重写：
  新增「〇、先说清楚门槛：这条路是企业入驻，不是点一下就完」、
  「A-1 他们要做什么」（含回调地址和 scope 怎么写）、
  「A-2 我们再发授权链接，他们点一下」（含 code 5 分钟、404 属正常的说明）、
  「A-3 权限说明」「A-4 常见坑」；
  保留路线 B（导出 -> `fill` -> 粘回）与路线 C（复制链接列 -> `links`）。
- `docs/VIDEO_SCRIPT_TABLE.md` 的「方式 B」加上门槛提醒与授权注意事项。

Epic Novel Engine 的整体结论不变；本次改动不属于 SPEC 阶段范围（与前三批同属视频文案对照表模块）。

## 视频文案对照表 —— 第八批：回调地址收敛 + 授权回调接收器（2026-09-21）

需求追问："回调地址确定他们填这个嘛？"

背景：上一版沟通稿让后台登记 `https://example.com/kdocs/callback` 这个占位地址，
只在"能手工复制地址栏里的 code"时才成立。这一批把回调地址这件事收敛成可控流程：

- 新增配置 `KDOCS_REDIRECT_URI`（`backend/app/core/config.py`，`.env` / `.env.example` 各写入一条默认
  `https://example.com/kdocs/callback`），
  `authorize` 不再强制手敲 `--redirect-uri`，而是优先 CLI 参数、其次读它 ——
  避免"后台登记的地址"和"授权链接里用的地址"两边写歪（这是授权失败最常见的原因）。
- `authorize` 打印内容补齐：授权链接 + 给文档所有者的一句话 + 回调后怎么把 code 拿回来 +
  本机可用的回调接收器提示 + **回读一致性核对行**；缺 APP_ID/APP_KEY 或回调地址时给明确指引并返回 2。
- `setup` 新增 `--redirect-uri`，一次性把凭据和回调地址都写进 `.env`。
- 新增 `scripts/kdocs_callback_server.py`：本机 HTTP 回调接收器
  （`--host/--port/--path/--exchange/--once`）。授权跳转过来即打印并落盘 code
  （`.local/video_script_table/kdocs_code.txt`），`--exchange` 直接换 token；
  用 `HTTPServer` + `timeout=1.0` 保证 `--once` 能可靠退出、Ctrl+C 好使。
  配内网穿透（cloudflared/ngrok）即可支持"老板不在同一台机器"的场景。
- `docs/KDOCS_AUTHORIZATION_REQUEST.md` 新增「五、回调地址到底填什么？」：
  三种选法（自有域名 > 内网穿透+接收器 > 占位地址+手工复制 code）+ 三条铁律
  （一字不差 / 后台拒绝就换自有域名 / code 5 分钟）。
- `docs/VIDEO_SCRIPT_TABLE.md` 的「方式 B」同步补充回调地址说明与本机接收器用法。

验证（2026-09-21）：

- 新增 `backend/tests/test_kdocs_callback_server.py`（2 个）：
  起真实端口 -> `GET /kdocs/callback?code=..&state=..` -> 200 + "授权成功" + code 落盘 + `--once` 自动退出；
  缺 code 时返回 400 且不落盘。
- 实跑：`--once` 收到请求后进程退出、端口释放；`authorize` 未配 APP_ID 时给出明确指引。
- `python -m pytest -q tests/test_kdocs_callback_server.py` -> `2 passed`。

Epic Novel Engine 的整体结论不变；本次改动不属于 SPEC 阶段范围（与前三批同属视频文案对照表模块）。
