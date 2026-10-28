# Epic Novel Engine Implementation Status

## 10月28日版通用Astra提示词更新

- 用户要求只替换财经、心理情感、健康养生三个智能体最后一步提示词。新增general_rewrite_20261028.md，完整提取58段、3785字符，SHA256 c3b174ba519917cd0a187da71ad7a95382a1fece8987c3283913e4477ebf593c；模型仍为gpt-6-astra standard/high。
- 新任务固定20261028工作流；旧20261027任务继续解析和使用原模板，保留原终稿、缓存和响应记录。前四阶段、社会痛点2610、社科2610以及封面漫画保持既有流程。
- 验证：新旧版本路由、模板哈希、跨类别拒绝及响应缓存专项通过；backend/tests全量581 passed/1 skipped，前端npm run build通过。
- 实施顺序：版本化模板与兼容路由→回归→备份发布→新建三份对比稿，核对并复用上轮前四阶段，从Astra继续→核对新响应、入库、正文长度和无空行要求。真实对比生成及最终验收待执行；Epic整体仍PARTIAL。

## 2026-10-27 三个2610测试智能体

### 发布与真实测试验收

- ffd10ad已推送个人AI-books/main并部署服务器；backend/worker各146个运行文件哈希、前端入口及健康检查通过。1310个既有任务、14497个章节、28414个输出文件元数据保持一致。备份为/opt/video-plan-release-backups/three-agents-ffd10ad；队列空后已安全停止旧批次临时加速worker。
- 按Word指定视频各新建一本测试书。财经复用5409字符原视频缓存；心理和健康通过现有轻抖适配器取得4400、11140字符原文。未复用旧书稿或生成封面漫画。
- 三本均完成五阶段及章节入库。初稿均为gpt-6.1-sol xhigh，两轮润色为DeepSeek V4 Pro（共52次润色调用），末步为gpt-6-astra standard/high；日志无错误、回退或降级。
- 通用模板SHA256为0a36cdadf88ffaa980fadd7c5656f4bb9b1d2ba6e492f05f27552e769d92f6cb。每本第五步提示词只替换raw_text为第四阶段完整稿；Astra原始响应、05_astra、数据库markdown与预览读取逐字一致。原生Word导出ZIP/XML及全部段落文字核对通过，未做Word视觉排版验收或全文专业事实审校。
- 财经测试《别为多赚利息亏掉本金》：de5de248-788f-4353-b28d-970a76f8f153，13章、21525字符，正文计数18340。
- 心理测试《在靠近与退开之间学会相处》：0da2d8ff-c4c3-4c40-a557-df790ce1a7ce，13章、18887字符，正文计数15996。
- 健康测试《不靠硬扛的日常减重指南》：f79b8b17-db05-4d8a-a306-0bc49a66e21c，18章、21965字符，正文计数18632。
- 正文计数排除前置块、章节标题、标点与空白，三本均达到11000要求。脚本、响应/用量及正文仅保留私有.local和服务器任务目录，不提交公开Git；Epic整体仍PARTIAL。

- 新增2.0财经·科技2610、心理情感2610、健康养生2610，分别复用1.0财经·科技、情感·关系、健康·养生的五月提示词及四阶段；两轮润色沿用DeepSeek V4 Pro，最终增加GPT-6 Astra standard/high。财经原类别本来映射社会痛点五月基础提示词，本次保留映射。
- 通用提示词从用户1027 Word逐段提取，存放general_rewrite_20261027.md；第五步仅替换raw_text为第四阶段完整稿，三轮内部终审由这一次Astra调用执行。已有社会痛点2610和社科2610提示词与流程保留。
- 三类别各自固定版本，2.0入口、进度、阶段稿、封面类别映射、原始终稿导出与#章节入库接通，缓存/响应凭据复用现有防重复计费机制。
- 实施顺序：类别/提示词与路由→前端/导出→回归→备份发布→指定三个B站视频各测试一本，现已全部完成。后端全量577 passed/1 skipped、前端构建及三本真实测试核验通过。Epic整体仍PARTIAL。

## 2026-10-17 普通书稿改用 DeepSeek 润色

- 新建1.0普通书稿及2.0社会痛点2610的两轮润色改用现有DEEPSEEK_MODEL（线上deepseek-v4-pro），原提示词、温度0.3/0.2、约1000字段落切块与串行顺序保留。复用现有DeepSeek共享并发门禁，不再受Gemini单并发门禁限制。
- 新任务缓存记录润色供应商/模型；既有无供应商标记缓存继续使用Gemini，避免混合付费产物。DeepSeek润色拒绝截断稿及流中断的部分格式稿，调用失败不缓存原文冒充润色成功。
- 工作流进度标签改为模型中立名称；社科2610 Astra直出、社会痛点2610末尾Astra步骤、封面漫画及Epic引擎逻辑保留。
- 验证：润色路由、旧缓存恢复、失败保护、普通书稿隔离及Astra专项65 passed，backend/tests全量567 passed/1 skipped；frontend npm run build通过。尚未进行100本压力测试或真实整书效果对比；Epic整体仍PARTIAL。

## 2026-10-17 社会痛点2610（独立短篇工作流）

### 部署与真实验收

- 个人GitHub main已同步8909c81。服务器从78c3ddf发布至8909c81，backend/worker各143个运行文件哈希通过，前端入口哈希及健康检查通过；1209个既有任务、13257个章节及25800个输出文件元数据保持一致。备份位于服务器 `/opt/video-plan-release-backups/social-pain2610-8909c81`。
- 用户授权的抖音测试复用此前1401字符视频原稿，新建任务5a27331a-412d-4e2e-8950-37861ca4e9ba，五阶段全部重新生成；无旧书覆盖、无重复转写、无封面/漫画付费调用。
- 成书《认真生活不必苛责自己》，13章、18916字符；排除前置内容及章节标题后的正文计数15977，超过Word提示词11000要求。阶段字符数15372→18497→18447→18247→18916；旧流程日志无错误、降级或回退。
- Word模板全部50段逐字提取，3445字符，SHA256 d43eefb7c864ddb778e86f8daaa7b46d416d9167cee41fbd56f3e2f0b9cbca1e。渲染提示词仅替换raw_text为第四阶段成稿；Astra原始响应、05_astra、数据库markdown、预览读取完全一致。Word导出ZIP有效，XML每段与终稿逐行相同；未做Word视觉排版验收或全文事实审校。
- OpenAI实际返回用量：Sol初稿input3243/output14861（reasoning4142已包含在output，cache_write3240）；Astra润色input16994/output15364（reasoning2033已包含，cache_write16991）；cached均0。按官方标准USD费率含缓存写入加价估算0.156716+0.9806175=1.1373335美元。仅OpenAI费用，不含Gemini/DeepSeek；不是账单实扣证明。
- 补充费用回归测试覆盖Sol缓存读取5%、Astra缓存读取10%、缓存写入1.25倍及不重复计推理token；`python -m pytest backend/tests/test_social_pain2610.py -q` 7 passed，书架隔离专项5 passed。真实验收脚本与用量凭据存放私有.local/任务输出目录，不提交书籍正文或响应凭据到Git。
- Chrome实测2.0新建选项、生成进度、13章终稿和第五阶段独立预览通过。测试及记录补充不改运行代码，无需重启服务。Epic整体仍为PARTIAL。

- 2.0新增社会痛点2610，复用1.0社会痛点五月四阶段及现有模型，在第四阶段后使用用户Word原文模板调用gpt-6-astra standard/high润色；单独保存05_astra.md，原有1.0、社科2610 Pro直出、封面和漫画逻辑保留。
- 新增任务级OpenAI响应与用量记录，成功响应本地恢复，失败/不确定请求暂停避免自动重放；费用按标准USD费率估算，不等同供应商账单。
- 前端支持智能体选择、五阶段进度和阶段稿，最终稿及Word导出保留Astra原始文本；2.0智能体之间禁止仅改分类切换工作流。
- 验证：backend pytest tests 560 passed/1 skipped；frontend npm run build通过。根目录pytest收集受既有同名test_supabase_connection模块冲突影响，改为正式tests目录执行。运行版本8909c81已部署；真实抖音测试已完成（见下）；Epic整体仍为PARTIAL。

## 前端修复分支上线（2026-10-16，已发布）

- 用户授权将fix/frontend-review-2026-10-16更新服务器；78c3ddf已快进合并个人AI-books/main。范围仅前端：编辑态ref修复、401登录失效处理、认证网络错误重试、本地字体、轻提示、生成中书架每15秒刷新、隐藏页停止轮询及关闭生产sourcemap。
- 前端typecheck/build通过；隔离fetch替身验证版本查询参数、创建请求原样传递、认证头和401清理/回调通过，无真实创建请求。线上工作台、2.0成稿详情及未保存编辑输入验收通过，测试输入刷新丢弃，未写入书稿。
- 仅更新web，backend/worker容器ID保持一致，Celery健康；公网首页、index-CZw_zc0P.js、index-xoCUB_fH.css及本地字体SHA256与构建一致。静态目录重新构建，避免保留旧sourcemap。备份：/opt/video-plan-release-backups/frontend-review-78c3ddf。
- 社科2610提示词、Astra Pro直出、封面漫画业务代码未变，无模型调用或数据库迁移。本次是分支更新验收，不表示前端review所列全部问题已修复；Epic整体仍PARTIAL。

## 工作台简化（2026-10-16，已发布）

- 首页改为1.0与2.0双书架入口，各自直接新建；最近书稿默认2.0，可切换1.0。移除首页重复创作区、大幅宣传和无操作翻页按钮，保留侧栏其他功能。
- 请求按版本读取，切换时取消旧请求结果写入；加载失败显示重试，不冒充空书架。新建弹窗固定版本，完成后进入对应详情。仅前端变更，不改变提示词、模型或生成流程；Epic总体仍PARTIAL。
- 首次类型检查发现重试函数导入名称错误，已修正；前端typecheck/build最终通过。f193d8f已推送个人Git并发布web，后端/worker容器未重启，Celery健康；公网首页及index-C13CIqa-.js与本地构建哈希一致。Chrome已验收双入口、两版新建弹窗、最近书稿切换及2.0书架跳转，未提交生成请求。备份：/opt/video-plan-release-backups/workspace-frontend-f193d8f。

## 1.0与2.0书架划分（2026-10-16，纽约，已发布）

- 用户确认先只把社科2610迁往2.0。/bookshelf作为1.0，/bookshelf/v2为2.0；详情分别/book/:id与/book/v2/:id，旧链接按任务实际版本自动跳转，创建、候选批量、进度、重试、重新生成、导出和删除返回保持版本上下文。
- 新任务配置固定bookshelf_version，社科2610为2.0，其余为1.0。历史无字段任务根据固定工作流推断；两本真实Astra Pro测试稿为2.0，旧链路成稿留1.0，不改写数据库正文、章节、阶段缓存或费用。实施计划见BOOKSHELF_VERSIONS_PLAN.md。
- 服务端分类列表和任务列表支持版本过滤，分页前先划分并保留owner隔离；候选URL按版本去重。创建时拒绝版本与分类不匹配，修改元数据不能跨链路归类，明确重新生成时重新固定目标类别对应版本。
- 前端侧栏、创建表单、详情路径、返回及删除导航、本地分类排序分别区分版本。2.0只显示社科2610单条/候选库和Astra Pro直出步骤，不显示其他类别入口与选段润色；Word/Markdown导出继续依据任务固定工作流处理。
- backend全量555 passed、1 skipped、3项既有警告；新增回归验证版本推断、分类约束、分页及owner隔离、跨版本去重、元数据保护和重新生成固定。前端类型检查/构建、React SSR表单/卡片/进度冒烟通过。线上Chrome已验收两版入口、分类和创建表单、候选库入口、2.0详情与Astra Pro进度、旧详情链接跳转及返回导航；本次不启动付费生成，Epic总体仍为PARTIAL。


- a013bab已推送个人AI-books/main并部署；backend/worker各139份运行文件SHA256通过。部署前后1207条任务、13232条章节元数据摘要一致，25754个输出及账本文件大小/mtime不变，队列空、Celery健康。此项不等于全库正文哈希审计。备份为/opt/video-plan-release-backups/bookshelf-versions-a013bab。
- 线上按当前用户核对1.0共1190本、2.0共2本，归属集合无交集。2.0为《凭什么相信这个结论》《为什么越想做好越难》，各8章；两本正文SHA256与此前真实测试一致。既有任务通过固定工作流推断版本，新任务显式固定版本，无数据库迁移或旧任务重跑。
- 浏览器验收发现一次瞬时读取失败，4aa7023补充只读列表最多重试一次及明确错误/重新加载提示，加载中不误报空书架；4xx不自动重试，生成请求不参与。类型检查、构建、版本化SSR及只读重试冒烟通过。补丁只更新web，backend/worker容器未重启，备份为/opt/video-plan-release-backups/bookshelf-frontend-4aa7023。
- 最终公网首页和index-DLgSVe-9.js与本地构建一致，公网2.0分类仅social_science_test；Chrome最终截图确认2本书及正确入口。运行版本4aa7023，本次paid_calls=0；后续仅同步发布文档，无需重建服务。

## 社科2610两视频真实测试（2026-10-16，纽约，已完成）

- 用户授权用B站逻辑思考视频和抖音“无心生大用”视频各测试一本。复用已核对的原视频缓存文稿5492/1401字符，创建两本新任务，未覆盖既有书籍，未重复支付视频提取或转写费用。
- 两次真实Responses请求均使用gpt-6-astra、reasoning.mode=pro、xhigh，响应模型均为gpt-6-astra且status=completed。两本各8章，全文18253/18343字符；按正文汉字、英文单词、数字组计数，排除书名/简介/目录/导言及章节标题后为15752/15586，均达到11000要求。
- 核对渲染提示词SHA256与Word模板仅替换raw_text一致，原始响应、01_writer、04_format、数据库markdown、预览与Markdown下载逐字一致，无02_polish/03_refine产物；真实Word导出ZIP有效且XML全部段落文字与直出稿逐行一致。未修改任何书稿，不额外调用润色、修复或排版模型。此为生成/入库/文本导出验收，不宣称全文事实审校或Word视觉排版验收。
- 返回用量：输入57710/52172，输出40398/47641 token；按官方标准费率估算2.597/2.90377美元，合计5.50077美元，未扣缓存折扣，不冒充实际账单。原始响应、用量、输出与测试审计仅保留私有输出目录，不提交公开Git。
- 测试脚本首次直接导入Celery时未应用线上启动命令的本地Redis非TLS设置，导致投递失败、未调用模型；脚本对齐线上bootstrap后复用同一任务ID重新投递，无重复生成。未修改服务SSL配置或扩大并发。
- 运行代码保持96c37e4，真实付费测试已完成；仅补充验证记录，同步个人Git和服务器文档。无业务代码变化，无需重新构建或重启运行中的服务。Epic整体仍为PARTIAL。

## 社科2610单次直出（2026-10-16，纽约，已发布）

- 用户要求将“社会痛点测试版”改名“社科2610”，使用提供的Word全文提示词，GPT-6 Astra Pro直出。保留social_science_test类别ID；新建和明确重新生成任务固定sheke2610_astra_pro_20261016，已固定旧版本的任务不混用新产物。其他文字、封面、漫画及Epic流程不变。
- 完整Word正文归档为sheke2610_writer.md，仅替换raw_text，不追加文风参考或改写指令。官方Pro为Responses的gpt-6-astra + reasoning.mode=pro，使用xhigh；当前凭据只读查询模型成功，未发起付费推理。实现计划与提示词SHA256见SHEKE2610_PLAN.md。
- 单次非流式调用，禁止SDK自动重试，未完成/空稿/不确定请求暂停并保留响应；成功响应可恢复阶段文件，不重新扣费。记录模型、动作、状态、用量、估算费用和错误；不把估算当账单。保留服务原并发护栏。
- 原样保存输出，跳过Gemini润色、精修、DeepSeek排版及自动长度改写；章节解析识别Word要求的#标记，预览、Markdown和Word导出不改写正文。流程界面仅展示Astra Pro直出，删除该品类附加文风入口。
- backend全量551 passed、1 skipped、3项既有警告；覆盖一次请求Pro参数、精确提示词、超时/截断不重放、缓存恢复、完整任务章节入库及导出。frontend typecheck/build通过。尚未进行真实付费生成或浏览器点击验收；Epic总体仍为PARTIAL。
- 96c37e4已推送个人AI-books/main并发布服务器；backend/worker各138份运行文件hash通过，公网index及JS与构建产物一致，Celery健康。部署前后1205条任务/13216条章节元数据hash一致，25738个输出及账本文件的大小和mtime不变，队列空。原模型环境与并发不变，Astra Pro只在新专属路由显式选择。备份：/opt/video-plan-release-backups/sheke2610-96c37e4；无付费请求。

## 经典文字与最新封面漫画整合（2026-10-16，纽约，已发布）

- 已核对线上80bc3be与个人仓库一致，原工作区未提交修改不覆盖，使用独立工作树整合。用户确认所有普通书稿恢复5月流程，新版品类映射旧基础品类，封面及漫画恢复回滚前最新实现。
- 实施计划见CLASSIC_TEXT_LATEST_ART_PLAN.md。历史模板来源70c7ad8，不能声称就是5月15日部署哈希。运行代码2814d0e已发布；本次未发起付费生成。
- 文字阶段：提取13个基础品类共52个四阶段模板，逐字对比70c7ad8运行时注册表全部一致，辅助前置块修复及历史文风模板亦归档；新版品类显式映射旧基础品类，其中5月无专属财经模板，财经各版使用当时通用社会议题模板。保留旧段落切块、整篇二润、整篇格式化和旧交付规范化，不串入后来的续写/小标题/长度修复提示词。
- 新任务及明确重新生成任务固定classic_20260513；既有无版本任务按legacy_20260704续跑。缓存校验输入/提示词/文风指纹，拒绝跨版本或无标记旧阶段；分块润色保存已付费结果，费用暂停和不确定超时不得降级成成功，不取消正在运行的SDK调用。沿用现有主笔/Gemini/DeepSeek客户端及账本。
- 封面漫画阶段：恢复f16d277的封面额度闸门、财经优化分类、治愈动物形象与逐图文字约束，图文故事和谐音梗独立编剧/图像/版本缓存及创作工作台，接通暂停/预览/Word导出；保留80bc3be中的图像gpt-image-2.5-sunburst及文本模型配置。补齐已授权仓库内中文字体，在缺少系统字体时使用同包字体，不发起图像调用验收。
- 验证：历史注册表52项全文一致；首轮定向166 passed；以线上80bc3be重新整合后，后端全量545 passed、1 skipped（3项既有警告）；frontend typecheck/build通过（index-B546nGeI.js）。全量曾发现新漫画误入普通文字测试，已把漫画移到其独立端到端替身测试，不改生产输出规避检查。再生前验证、辅助模板指纹和已付费块续跑均有回归。已保留线上80bc3be之后继承的无限预算人工批准与说明句误判修复；上线前只读快照1205条任务，无在途生成，Redis工作DB3队列和unacked均为0。已发布，未发起真实付费生成；不宣称完整Epic验收。


- 发布：个人仓库zdx30309/AI-books main已同步2814d0e；服务器从80bc3be切换为2814d0e，backend/worker各135份运行文件SHA256通过，新前端index-B546nGeI.js已替换。新镜像在--network none中运行四阶段替身与全部品类映射检查，明确paid_calls=0；封面模型仍为gpt-image-2.5-sunburst，现有主笔/Gemini/DeepSeek模型和全部环境变量、命令、并发与重启策略保留。
- 保护：上线前后1205条任务和13216章元数据汇总摘要一致，25738个输出及费用账本文件的大小/mtime一致，Redis DB3队列和unacked均为空；不重跑旧书，不恢复历史自动恢复或beat/maintenance。摘要覆盖任务及章节元数据，文件检查覆盖输出与账本，未声称对数据库正文做全量加密哈希审计。
- 备份：服务器/opt/video-plan-release-backups/classic-text-latest-art-2814d0e，保存原宿主文件、前端产物、任务/章节元数据摘要、输出文件清单及最终release.json；原镜像保留。封面漫画所需22MB OFL中文字体从既有7月回滚备份复用，先按本地目标SHA256验证，无需恢复旧代码或凭据。
- 执行记录：在独立工作树backend目录以原项目backend/.venv/bin/python执行python -m pytest -q tests，最终545 passed、1 skipped、3项既有警告；frontend执行npm run typecheck及npm run build通过；git diff 80bc3be --check通过；线上新镜像离线smoke、两容器运行文件校验、Caddy首页字节校验及Celery health均通过。
- 预检失败及修正：逐行读取数据库大字段快照曾失败，未切换线上服务，改用库内任务/章节元数据摘要并保留输出文件清单；原应用明确禁用pidbox，inspect.active无响应不能当作有在途任务，改核对晚ACK配置、DB3队列与unacked及数据库活动状态。未启用远程控制或放宽费用限制。浏览器登录态已过期，未宣称登录后的点击验收；真实付费整书及图像效果验收未执行。

## 经期科普说明句误判修复（2026-10-15，纽约）

- 用户要求修复本轮9654dddf任务并交付结果。定位到“还有一个容易被忽视的风险，就是成分重复”被普通逗号伪标题规则误判，随后整篇AI修复丢失12章标记，被既有结构保护正确拦截。
- 检测器及确定性格式化共用说明句判定，只排除无显式标记、逗号前是“还有一个/另一种”等引导且后接“就是/在于”等说明的普通句子；显式@、冒号和真正的短伪标题继续拦截。不删除用药风险提示、不取消质量门禁。
- 新增实际医学段落不拆行及质检回归，显式标记和冒号仍触发；全量pytest -q tests：488 passed、1 skipped、3项既有警告。目标书将用保存的终稿进行零付费断点入库与原生Word/Markdown导出，不重写或重润整本。
- 已用新镜像一次性执行器恢复9654dddf：正式completed/100、12章17164字符；保存原稿及失败状态快照，单独对比该任务费用调用列表确认无新增模型调用。其余生成任务未中断。
- 待其余任务温和退出后，24a83f9已正式发布backend/worker，各115份源码hash校验通过，模型和并发配置未变，健康检查通过、队列无积压。
- 本轮10本新稿全部completed/100、无错误，合计126章184623字符；经结构/篇幅门禁、原生导出及段落完整性检查，完整ZIP包含10份Word、10份Markdown及来源模型清单，不混旧稿。
- 10份Word已渲染156页，PDF提取全文逐本与DOCX文本一致，并检查预览；ZIP CRC及20个正文文件hash通过。ZIP为529593字节，SHA256为5ee6d3cf4cbe5ac70599c0f8a6c6089d1dad442d7a915f591c3c92629eca9fa2，已下载至用户Downloads的科普读物10本_新链路重生成_20261015.zip，完成监控暂停。此为技术交付验收，不替代医学专业审校或完整Epic验收。

## 本轮十本科普书取消费用上限（2026-10-15，纽约）

- 用户明确要求本轮费用不设上限。新增显式generation_batch_budget_unlimited模式，仅允许已在私有账本记录unlimited_approved_at的批次使用；None表示无限预算，不以伪造大数字或清空费用模拟。
- 原有100元及其他有限预算行为不变。全部历史费用、逐次预留、用量与响应留痕继续保留，未知扣费暂停、输出截断暂停、并发限制、质量门禁不变。
- 新增人工批准必需、累计费用可超过旧限额、历史记录不丢失及无上限模式仍阻断未知扣费的回归；test_batch_spend.py：16 passed。部署采用worker温和退出后切换，不能杀死在途付费请求或自动重放未知结果。
- 仅对science_regeneration_0213bca3-45bc-55ee-a55e-5285ea5afddf十条新任务授权；旧十本及其他书籍不得自动续跑。发布及线上授权核验结果待记录。

## 封面与转写模型升级、合并旗舰分支（2026-10-15）

- 用户要求GPT统一6.1，其余厂商用最新模型，并确认保留旗舰版：Gemini gemini-4-argon、DeepSeek deepseek-v4-pro、OpenAI文本gpt-6.1-sol（沿用feat/latest-models-and-stage-guards及其阶段护栏）。
- 在此基础上补齐：封面图gpt-image-2改为gpt-image-2.5-sunburst（官方最强图像模型，尺寸规则与gpt-image-2相同，沿用现有16倍数与像素上限换算）；转写gpt-4o-mini-transcribe改为gpt-transcribe，回退链gpt-4o-mini-transcribe、whisper-1。
- main上曾推送的361bb70（gemini-3.8-flash/deepseek-flash方案）被本次合并取代，不部署。
- 官方来源：https://developers.openai.com/api/docs/models/all ，https://ai.google.dev/gemini-api/docs/models ，https://api-docs.deepseek.com/updates/ 。

## 主笔切回GPT-6.1 Sol及阶段护栏复核（2026-10-15，纽约）

- 用户要求主笔改为gpt-6.1-sol：更新主笔默认值及生产/后端环境示例；Gemini 4 Argon润色、DeepSeek V4 Pro格式化及默认关闭的终审模型不变，6.1 Sol费率在batch_spend中已存在。服务器.env若显式设置OPENAI_WRITER_MODEL需同步改为gpt-6.1-sol。
- 复核发现人生哲学品类终稿使用“！！！@正文/！！！N. 标题”分页标记，阶段护栏只识别@N.：DeepSeek格式结果每次被误判回退、终稿缓存每次被判失效、篇幅门禁必定报“缺少章节”并暂停。护栏与门禁统一先把分页标记还原为@结构行再比较。
- 终稿硬伤修复由主笔整篇重写，此前无篇幅/结构校验；现对标准品类复用阶段护栏，修复稿被压缩、章节编号或标题被改动时抛出可重试错误，不交付压缩稿。
- 篇幅门禁暂停的任务此前没有放行入口（length_gate_accepted无处设置），重新投递会复用同一终稿再次暂停；现在任务主人对manual_review暂停任务点击重新投递即视为人工放行。
- 用户要求ChatGPT所有文本模型统一为6.1 Sol：终审模型默认值及示例、财经联网搜索兜底均改为gpt-6.1-sol；财经搜索、长篇小说三档、Epic作者/编辑未单独配置时回退主笔即6.1 Sol。语音转写gpt-4o-mini-transcribe与配图gpt-image-2属专用音频/图像模型，6.1 Sol不能替代，保持不变。
- 新增四项回归；backend全量pytest -q tests：483 passed、1 skipped、1 failed（test_family_long_novel_routes_are_registered，改动前同样失败，与本次无关）。未执行真实付费生成。

## 新模型与阶段质量护栏分支复核（2026-10-15，纽约）

- 复核远端 feat/latest-models-and-stage-guards 的 d13109f；原分支离线全量测试477 passed、1 skipped，但复现同编号标题被替换、长正文无章节仍过篇幅门禁、旧终稿快速续跑跳过阶段对比的漏洞。
- 补充章节标题原文校验、零章节阻断、终稿缓存与初稿对比及失效缓存跳过逻辑；移除润色/格式提示词中污染实际书稿的具体名校示例，将新增片段保护提示词移入模板模块，保留短篇系统与其他品类专属逻辑。
- 新增三项回归，含压缩终稿续跑不重复创作；backend全量pytest -q tests：480 passed、1 skipped、3项既有警告。git diff --check通过。前端无改动。
- 用户明确同意同时切换GPT-6 Astra、Gemini 4 Argon、DeepSeek V4 Pro；生产凭据按实际代理路由的只读模型元数据查询通过，不发送付费生成。继续保持Gemini并发1、worker并发4、现有预算账本及暂停状态，不自动重跑旧书、不承诺上游无429。
- 已将修补提交c1b69e3推送至原功能分支并部署北京ECS；main未合并。backend/worker各115份运行源码hash及三个新模型配置一致，Gemini并发保持1，worker保持4；健康检查celery_alive=true。1193条历史任务的状态/进度与部署前一致，旧十本122章及ZIP哈希保留。上线前快照在服务器/opt/video-plan-release-backups/sol61-c1b69e3。
- 用已有十本交付稿离线回放新篇幅门禁：前九本通过，第十本被拦截（正文9141个非空白字符、六章不足800），没有修改其既有完成状态或触发付费重跑。真实新模型推理及全书质量验收尚未执行；本次不宣称Epic完整验收或医学事实审查完成。

## 全部十本统一6.1及交付验收（2026-10-15，纽约）

- 用户要求前四本也重新创作，十本完成后打包。已将前四本旧5.6成稿和任务/章节快照私有备份，再用原任务ID清空阶段缓存重新入队；统一6.1主笔，共享原100元账本，不重置旧费用，不在ZIP中混入旧稿。
- 本次发现一本文稿在终稿硬伤检查失败：检测器把14/16字的普通逗号前置分句误判为小标题，而既有格式化器只拆不超过12字的同类逗号伪标题。仅同步检测器的12字阈值，保留短伪标题、显式@、冒号及其他硬伤检查；不通过删除正文、弱化医学限定条件或跳过整个质检来交付。
- 针对两条实际误报正文及真实短伪标题/长冒号标题新增回归。backend全量pytest -q tests：470 passed、1 skipped、3项既有警告。44946e4已部署，backend/worker各115份运行文件校验通过；对保存的16144字符6.1纠错稿实测原文不变即可通过，再从终稿缓存入库，未重跑创作、润色或格式化。
- 两本在Gemini润色时收到明确429拒绝，未收到成功响应；保留全部已成功块及账本，冷却后错开人工续跑，各一次，不扩大并发，不重写初稿。最终十本均completed/100、无错误、122章入库，前四本新初稿hash均与旧5.6备份不同，供应商返回的创作模型核验为6.1 Sol。
- 最终ZIP为10份Word、10份Markdown及来源清单/说明，共147873字符，433214字节；服务器与本地SHA256一致，压缩包及内层DOCX校验通过。10份Word共128页已渲染，PDF抽取文本与交付Word全文一致；初次渲染因打包LibreOffice缺少系统中文字体配置而缺字，使用独立本地fontconfig修复，仅用于QA，不改书稿或服务器字体。
- 批次最终保守用量折算46.892183元（不是供应商实际账单），包括前四本被替换的旧生成费用，低于原100元上限，无账本hold或在途预留。书稿及费用原始响应保留在私有输出目录，未上传公开Git；此结果不等于医学事实审校、永久无429或完整Epic引擎验收。
- Word导出先做包结构、章节/全文一致性与渲染检查。打包只含最终书稿、来源清单和说明；不包含密钥、费用原始响应、旧稿备份或原视频文稿。健康类稿件不冒充医学专业审校版本。

## 主笔切换GPT-6.1 Sol（2026-10-15，纽约）

- 用户明确指定6.1 Sol；官方模型ID为gpt-6.1-sol，现有账号的models.retrieve只读检查通过。保持Responses API、xhigh、非流式和标准服务层，不改提示词，不更换Gemini润色与DeepSeek格式化。
- 更新主笔默认值及生产/后端环境示例；费用保护新增6.1 Sol价格上界（输入含cache write按2.5美元/百万token，输出10美元/百万token，仍使用保守人民币换算因子10）。保留5.6 Sol费率，旧调用记录、费用预留与100元累计限额不得清空或重置。官方来源：https://developers.openai.com/api/docs/models/gpt-6.1-sol 。
- 离线全量测试468 passed、1 skipped、3项既有警告；覆盖6.1实际请求参数、SDK不隐式重试，以及同一账本跨5.6/6.1切换不丢失既有费用。
- 切换时前4本已取得5.6 Sol初稿并进入润色；通过worker温和退出等待在途工作结束，不强杀付费请求，不重写这些初稿。剩余6本尚在队列，将在新worker上使用6.1 Sol；不宣称已发出的5.6请求能被追溯改成6.1。
- c5d13fb已部署北京ECS；backend/worker各115份运行文件校验通过，两个容器的有效主笔均为gpt-6.1-sol。旧worker在温和退出前完成4本，未强杀在途请求；剩余6本恢复消费，首次检查4本处理中、2本排队，账本出现4条6.1主笔请求。共享100元账本保留，无费用暂停；这只确认切换与请求启动，不冒充6.1成稿已验收。

## 个人仓库迁移及科普批次费用保护（2026-10-15，纽约）

- 个人仓库为zdx30309/AI-books；迁移保留本地分支历史，移除历史中的环境密钥、服务账号凭据、代理私有配置及生成输出，提交哈希因此变化。原始Git bundle和未提交工作均另存本地，原共享仓库未强推。迁移不等于数据库或书籍数据备份。
- 更正下方旧启动记录：现有科普主链路的初稿调用实际固定走OpenAI；WRITER_PROVIDER=deepseek属于配置不一致，不能据此判断实际主笔。此次将部署配置明确设为openai；保留现有Gemini润色/二润和DeepSeek格式化，不恢复已回退的0.5工作流。
- 用户批准10份视频素材重新生成，新增累计预算100元，旧稿已按用户要求删除，缓存视频文稿保留。新增跨线程/跨进程共享账本、请求前保守费用预留、用量核对、私有响应凭据保存和预算不足暂停；不确定请求保留预留并暂停，不自动重复扣费，不把截断响应标为完成。预算是保守用量上界，不冒充供应商实际账单。
- 批次限定科普、缓存素材、OpenAI非流式主笔及已核定模型价格，跳过自动封面；不得通过重建账本重置预算。暂停处理行锁核对运行归属，不覆盖较新运行。未改动生成提示词或现有书籍数据。
- 验证：在backend目录使用原工作区backend/.venv/bin/python执行pytest -q tests，466 passed、1 skipped、3项既有警告。新增回归覆盖OpenAI关闭SDK隐式重试、Gemini思考token计费、DeepSeek流式末尾用量、空响应不自动重放，以及旧运行暂停不得覆盖新运行。曾先复现旧ORM对象缓存导致归属判断过期，再通过行锁查询populate_existing修复。
- b3dfddc已部署北京ECS；backend/worker各115份运行文件hash核验通过，主笔配置openai、gpt-5.6-sol、非流式。部署前后1183条既有任务状态/进度不变，原历史pending任务未投递，beat/maintenance和历史自动恢复仍关闭。健康检查Celery存活；前端未改动，不声称重新完成浏览器点击验收。
- 10本科普任务以缓存视频素材重新创建、共享新增100元预算，保留原始来源链接；不复用已删除旧稿。付费生成状态另以实时任务和账本核验，不能据此承诺批量任务永不失败或10本已全部交付。Epic全量验收依然未完成。

## 线上重新启动与无付费验收（2026-10-15，纽约）

- 用户授权启动服务，明确选择「暂不付费，只启动并做离线检查」。backend、worker、web已启动，健康检查200且Celery存活；仅恢复这三个服务的自动重启。beat/maintenance及历史任务自动恢复仍关闭，Redis原DB1/DB2不消费，工作队列仍为DB3/DB4，并发4不扩大。
- 新增16项离线可靠性回归：4本同时执行真实任务函数、阶段文件与章节隔离、MD/Word导出、单本失败不拖垮其他任务、缓存续跑不重复初稿、已完成重复投递、暂停/其他运行持有/费用锁/人工审核保护、重试耗尽不假完成、Redis故障关闭付费入口，以及历史类别读取/导出兼容和新任务类别严格校验。模型全部替身，SQLite测试库和临时输出目录隔离，禁止测试外部网络；不等于真实多本付费出书验收。
- 实际发现回滚后general_short历史书籍的全文接口500：旧版normalize_category不认识新类别。仅在读取和导出时复用安全类别解析，支持general_short、standard_long及未知历史类别按通用格式预览、单本/批量MD和Word导出；不修改原书配置，不把这些类别重新加入生成入口，不修改7月4日生成提示词及流程。已先复现失败、再验证回归通过。
- 验证命令：backend/.venv/bin/python -m pytest -q backend/tests（在backend目录执行时路径为./.venv/bin/python -m pytest -q tests）：452 passed、1 skipped、3项既有警告；frontend npm run typecheck及npm run build通过。误用根目录旧venv时曾因缺openpyxl收集失败，改用项目backend/.venv后全量通过，不修改依赖绕过。
- 线上无模型生成检查：24个Celery ping全部成功；Redis分布式限流16次竞争，实测峰值2且无遗留占位；鉴权及书籍列表/章节只读接口正常。DeepSeek /models鉴权通过且deepseek-flash在列表；Gemini模型元数据GET通过，配置gemini-3.1-flash-lite。以上均未发送生成请求，不能据此确认推理配额或承诺永不失败。回滚版仍是DeepSeek初稿、Gemini润色、DeepSeek格式化，不恢复10月新增工作流。
- 按用户另行明确批准，停止唯一写入/tmp/live2.pcap的tcpdump进程并删除该临时文件，释放约12.12GiB；磁盘从98%降至82%，可用约15GiB。未删除书籍、数据库、数据卷或回滚备份。
- 浏览器可视化验收被保存的站点访问权限阻止，未绕过，尚不能宣称完成浏览器点击验收。付费端到端生成及真实多本验收按用户选择未执行；不宣称完整Epic验收或批量生成零失败保证。
- 热修复61d41f0已发布北京ECS：backend/worker各114份运行文件hash通过，1192条任务的状态/进度未变，健康检查通过。三本历史完成稿的全文接口均200（分别25679、24643、7055字符），未鉴权接口401；后台服务持久配置为自动重启，beat/maintenance保持停止。前端产物未改动，仍为index-Ce27yWCP.js。

## 用户确认回退到2026年7月4日（2026-10-15，纽约）

- 最终目标为北京时间2026-07-04 22:27:21的92f69ea；此前9月23日回退提交只更新过Git，未部署服务器，本次以用户新目标覆盖。业务源码与前端恢复7月4日，不夹带9月及10月新增流程，不宣称完整Epic验收完成。
- 回退采用新提交，不强推删除历史。当前Git及原目录未提交修改已备份；不恢复两份已移除的Google服务账号JSON，防止再次上传密钥，仅补充ignore与本记录。
- 数据库不执行降级，书籍、上传资料、费用账本及断点保留。已只读确认旧ORM未映射的五个long_novel非空列均有数据库DEFAULT，普通Task/User/Chapter无新增必填字段阻断。
- 部署隔离旧队列：保留Redis DB1/DB2及原实例，使用原先为空的DB3/DB4供旧版backend/worker；旧限流REDIS_URL继续指向共享DB0。停用beat/maintenance与自动恢复，防止旧代码自动续跑新任务。当前worker并发4不扩大。
- 旧Celery源码对本地redis仍设置TLS，部署启动命令仅在运行时清除此SSL选项；不修改7月4日业务源码。保留现有生产Caddy配置、证书卷、密钥及Google凭据挂载，不恢复过期密钥或删除数据卷。
- 本地旧版完整测试436 passed、1 skipped；frontend typecheck/build通过，index-Ce27yWCP.js。不发起真实AI调用，不宣称完整Epic验收完成。
- 用户随后要求「线上版本先停止，不再运行，先完成更新」。已停止backend、worker、web、beat、maintenance并关闭它们的自动重启；Redis继续保留，不清空旧队列，不续跑书籍。最终更新采用离线模式，未启动应用服务，不执行在线健康验收。
- 北京ECS已离线替换7月4日源码、后端镜像及前端静态产物；核对宿主机及未启动的backend/worker容器内114份运行文件hash、web容器index.html hash。三个应用容器仅create，不start；beat/maintenance保持停止。版本记录为789c363，业务基线92f69ea，后续文档提交不改变业务源码。
- 部署前后已保存任务和重点书籍文件快照；备份在服务器/opt/video-plan-release-backups/rollback-20260704-789c363。数据库不回退、不删除书籍及数据卷。旧源码未改动文件存在CRLF差异，已统一按Git目标内容核验后复制，避免仅以差异文件覆盖造成误判。

Updated: 2026-06-13

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
