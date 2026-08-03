# 项目 Context 交接文档

更新时间：2026-07-13  
仓库：`E:\video-plan`  
当前分支：`codex/bilibili-cost-guard-main`  
当前 HEAD：`15f7323`  
用途：让下一位 Context 在不重复调查、不误判完成状态的前提下继续维护项目。

> 事实口径：本文只把仓库代码、Git 历史、已运行测试和已验证线上状态视为事实。聊天里提出但缺少数据库、账单或真实长样本证据的事项，一律标成“待验证”，不写成 100% 完成。

## 1. 接手时先记住的最终决策

这些是当前最终状态，覆盖此前聊天中的相反或过时要求。

1. **不得破坏现有 workflow 或以降质量换成本。** 现有生成、润色、精修、格式化、QA、长篇连续性和状态账本节点不得随意删除。
2. **朱雀/AIGC 最终闸门保留代码但默认关闭。** 老板已购买其他 SaaS，目前本系统不得调用腾讯朱雀，也不得触发对应改写和费用。
3. **两个古文分类使用 Word 文档指定的 Prompt，并只走直出。** 当前最终状态是：
   - `古文白话（参照译写版）`
   - `古文白话小说风格版`
   - Prompt 已按用户提供的 Word 文档替换。
   - 这两个分类不再走 Gemini 一润、二润。
4. **《嫂子的账本》最终目标是约 20 万字，不是 30 万字。** 必须保留已完成正文与状态，只让现有家长里短长篇系统重新规划未完成部分。
5. **《嫂子的账本》扩容/重规划只能用于这一项目。** 如果同名项目超过一个，必须先按 `project_id` 选择，禁止批量修改。
6. **长篇 OpenAI 请求必须使用项目服务器上的国外代理链路。** 不得依赖开发者本地代理或要求个人电脑常开。
7. **微信读书或番茄公司的“全量书单”不能靠公开搜索结果冒充。** 没有完整 HAR/抓包证据时，必须明确说明无法保证全量。
8. **Epic Novel Engine 与家长里短长篇是两套不同系统。** 涉及 Epic 时，必须先读 `AGENTS.md` 与 `docs/epic_novel/` 的规格，不能把《嫂子的账本》的实现混进 Epic。
9. **不得宣称未验证事项 100% 完成。** 特别是实际美元成本、20 万字线上规划、478 本全量书单和真实长稿质量。

## 2. 当前仓库与线上快照

### 2.1 Git

- 工作树在本次交接前为干净状态。
- `HEAD`：`15f7323 Keep AIGC final gate dormant by default`
- `origin/main`：`15f7323`
- `origin/codex/bilibili-cost-guard-main`：`15f7323`
- 本文创建前没有待提交业务改动。

### 2.2 北京 ECS

- 公网地址：`http://101.200.48.238/`
- 部署目录：`/opt/video-plan`
- 2026-07-13 只读复核：
  - 首页返回 HTTP 200。
  - `/api/v1/system/health` 返回 HTTP 200。
  - `celery_alive=true`，worker 名为 `celery`。
- 上一次完整部署记录对应 `15f7323`。
- 当前生产开关应保持：
  - `AIGC_GATE_ENABLED=False`
  - `VITE_AIGC_GATE_ENABLED=false`

> 本次只新增本文档，没有重新部署，也没有修改线上数据库。

### 2.3 已执行回归

2026-07-13 执行：

```powershell
python -m pytest backend/tests/test_category_prompt_routing.py backend/tests/test_family_long_novel.py backend/tests/test_aigc_quality_gate.py -q
```

结果：`153 passed, 1 warning`。Warning 来自 `google.genai` 的 Python 3.17 弃用提示，不是本项目测试失败。

```powershell
cd frontend
npm run build
```

结果：TypeScript 与 Vite 生产构建通过。

## 3. 不可违反的仓库规则

### 3.1 全项目通用

- 保留现有短篇生成系统。
- 不重写整个项目。
- Prompt 使用项目已有 Prompt/template 系统，不在业务逻辑中新增大段硬编码 Prompt。
- 修改应按分类和服务边界收敛，禁止借机重构无关模块。
- 不删除 workflow 节点来省钱。
- 不降低 reasoning、上下文、QA 或连续性要求来制造“低成本”假象。
- 用户已有未提交改动不得覆盖或回滚。
- 所有“已解决”必须有测试、数据库、日志、账单或真实样本证据。

### 3.2 Epic Novel Engine

Epic 的真源优先级：

1. `AGENTS.md`
2. `docs/epic_novel/EPIC_NOVEL_ENGINE_FULL_SPEC.md`
3. `docs/epic_novel/09_ACCEPTANCE_CRITERIA.md`
4. 其他 `docs/epic_novel/*.md`

关键红线：

- 章节严格串行生成，禁止并行。
- `draftContent` 和 `repairedContent` 不得写入 canon/state。
- 只有 `acceptedContent` 可以持久化 canon。
- 每章必须运行 Planned-vs-Actual Diff 与 Acceptance Gate。
- high/severe issue 阻断验收。
- 原始参考书文本不得进入生成 Prompt。
- 生成只能使用抽象后的 `ReferencePatternCards`、`ReferenceMechanismProfile` 和批准摘要。
- 每个 Epic 大阶段完成后更新 `docs/epic_novel/IMPLEMENTATION_STATUS.md`。

当前 Epic 总体结论仍为：`PARTIAL_IMPLEMENTATION`。不得宣称已经具备真实 300 万字生产就绪证据。

## 4. 已完成工作

下面的“完成”表示仓库已有实现；其中需要真实业务样本验收的部分会在第 5 节继续列出。

### 4.1 推荐值工具：微信读书、版权方和番茄

主要能力：

- 微信读书书名/链接目录分析、推荐值、阅读人数、评论数量、作者、译者和版权方输出。
- Excel 导入、字段清理、分块抓取和 Excel 导出。
- 原始作者列的链接化与项目索引匹配。
- 评论数量按数字/人数输出，避免把评论人名称误当作评论数量。
- 版权方/公司书单入口、分页扩展、本地公司索引沉淀。
- 微信读书 API Key 路径、公开入口回退和 HAR/JSON 抓包导入。
- 番茄小说 HAR/抓包导入，提取书名、作者、阅读量等字段。

关键提交：

- `66a4ce7` Fix WeRead export limits and metadata fields
- `9f03a26` Add WeRead Excel import analysis
- `3915140` Chunk WeRead Excel import fetching
- `3336454` Fix WeRead export data cleanup
- `b5ca0cf` Add WeRead copyright owner catalog lookup
- `92f69ea` Fix WeRead company pagination
- `a87664c` Support WeRead API key company lookup
- `dc0f5f3` Harden WeRead company list fallbacks
- `a6ef56c` Add WeRead company capture import
- `0e0181d` Fix WeRead HAR search capture parsing
- `f277948` Add Fanqie capture import to book metrics tool

当前入口：

- 后端：`backend/app/services/weread_tools.py`
- 后端路由：`backend/app/api/routers/weread.py`
- 前端：`frontend/src/pages/WeReadTool.tsx`

### 4.2 佛经白话故事链路

已做：

- 新增佛经白话故事分类与专属路由。
- 叙事要求从“直接翻译”强化为忠于原文寓意的小故事。
- 长佛经按篇/单元覆盖，避免一本长经只生成少量章节。
- 针对《百喻经》这类多譬喻文本增加单元恢复、章节覆盖和短章质量检查。
- 对已完成章节使用缓存/恢复，避免失败后整本重生成浪费 API。
- Gemini 分块标记失败增加回退，尽量保护已完成内容。

关键提交：

- `442e861` Add Buddhist scripture story mode
- `26309ce` Fix Buddhist scripture story generation routing
- `13a9e37` Strengthen Buddhist scripture story prompt
- `cb18184` Require full coverage for long Buddhist scriptures
- `e5620c7` Fix Buddhist story generation coverage
- `2fad627` Fix Buddhist parable unit recovery
- `af1e3a1` Harden Buddhist targeted repair resume
- `94b0f6e` Reuse Buddhist repair cache by chapter
- `8e6de69` Write keyed Buddhist repair cache files
- `61c1859` Tune Buddhist parable story prompt
- `1f808d7` Fallback Buddhist Gemini chunk marker failures

### 4.3 古文白话系列

现有分类：

- 古文白话
- 古文白话小说风格版
- 古文白话（原文翻译穿插版）
- 古文白话（参照译写版）

已做：

- 穿插版的原文/翻译标签规范化、重复翻译合并与长稿保护。
- 参照译写版支持原文与参考译文两份 Word 上传。
- 后续增加 PDF 导入。
- 参照译写版支持默认参考范式、原文/翻译成对输出和语义锁定。
- 小说风格版增加叙事型古文与抽象格言两类处理，抽象格言应转成故事载体。
- 增加“只翻译无故事”“讲义腔”“望文生义”等质量命中规则。
- 2026-07-11 按用户提供的两个 Word 文档完整替换相关 Prompt。
- 最终改为两个指定分类只走 OpenAI 直出，不再执行 Gemini 一润、二润。

关键提交：

- `3c479df` Guard classical interleaved full repair
- `6182d73` Normalize classical interleaved translation labels
- `d054ef1` Clean duplicate classical interleaved markers
- `c5cabc8` Collapse duplicate classical translations
- `60c7534` Add classical reference rewrite category
- `aed9264` Expose reference rewrite Word uploads
- `bc03021` Add classical reference rewrite style profile
- `0197cd3` Switch reference rewrite to paired translation
- `91f1caf` Improve classical reference translation grounding
- `777d88a` Fix classical reference translation priority
- `b4cb7e7` Strengthen classical reference semantic lock
- `e1db7c2` Add classical novel style and image OCR rewrite
- `3a0a031` Fix classical novel style aphorism story rewrite
- `159cc4b` Support PDF imports for classical Chinese
- `f97f2dd` Restore classical prompts from Word specifications
- `c92e142` Use direct writer flow for selected classical categories

最终 Prompt 和路由以 `f97f2dd`、`c92e142` 后的代码为准，不要恢复更早版本的 Gemini 润色。

### 4.4 公版图书融合改写

已做：

- 新增独立公版融合改写入口，不进入普通短篇分类链路。
- 支持 2 至 3 本输入。
- 支持文本、TXT、Markdown、DOCX 和图片 OCR。
- 简化前端，不强迫用户填写目标标题、目标字数、融合方向等附加字段。
- 调整 Prompt，要求保留原书人物、事件、因果、顺序和作品身份，只改变表达与组织，不生成另一部无关小说。
- 增加书架/结果展示修复。

关键提交：

- `f4fbc92` Add public domain fusion rewrite tool
- `f1aa0a7` Simplify public domain rewrite workflow
- `c486eb2` Fix public domain rewrite bookshelf flow
- `860e3c5` Tighten public-domain and classical rewrite prompts

入口：

- 后端：`backend/app/services/public_domain_rewrite.py`
- 前端：`frontend/src/pages/PublicDomainRewrite.tsx`

### 4.5 终稿硬伤 QA 与小标题治理

已有的统一终稿 QA 会在终稿落盘前检查：

- 非法小标题、正文伪标题、`@标题`、编号伪标题。
- 高置信语义搭配错误，例如“菜好喝”。
- AI 解释语、任务说明、异常标签、重复章节头等残留。
- 修复只允许处理命中的硬伤，不应重排结构、扩写、删稿或改文风。
- 修复后仍命中高置信硬伤时应失败，而不是返回坏终稿。

后续又修正了合法正文被误判为标题的问题：

- `dac4951` Fix hard QA inline heading false positive
- `4ea45c3` Tighten finance hard QA heading heuristic

核心质量规则：`backend/app/core/quality_metrics.py`

### 4.6 人生哲学与故事智慧

已做：

- 人生哲学（困境自救版）增加反模板限制。
- 限制高频主题词、固定场景切片、固定反例位置和重复句式。
- 后续继续限制章节开头反复使用“晚上十一点”“凌晨两点”等时间场景。
- 限制“并不”“真正”“真正的”“看似……却”“听起来似乎……实际上”等高频表达。
- 新增 `故事·智慧类（历史故事版）` 及其历史叙事专属 Prompt。
- 统一小标题格式要求，避免正文句子伪装成小标题。

关键提交：

- `f942ceb` Tighten life philosophy prompt variety
- 历史故事与人生哲学 Prompt 当前位于 `backend/app/prompts/longform_variant_prompts.py` 和 `backend/app/prompts/goal_driven_templates.py`。

旧的“财经（观点简建议版）”名称已不再作为当前分类出现。现在仍有 `财经科技（建议版）`，不要未经确认把它当作同一个分类删除。

### 4.7 前端多主题

已增加：

- 保留原有主题。
- 新增浅色暖白参考主题及多套通用/个性主题。
- 主题切换覆盖现有主要路由和工作区。
- 主题状态由前端 Theme Context 管理。

关键提交：

- `4413aff` Add frontend theme switcher

关键入口：

- `frontend/src/theme/ThemeContext.tsx`
- `frontend/src/theme/`

### 4.8 家长里短长篇与《嫂子的账本》

已做：

- 保留现有家长里短长篇架构，没有改成 Epic Engine。
- 增加 `custom_plot` 自定义剧情输入能力，仍由原有圣经、大纲、章节计划、连续性和 QA 链路消费。
- 强化章节节奏：更快进入冲突，每章必须有信息变化、利益推进和情绪钩子。
- 为《嫂子的账本》增加专用目标扩展/重规划服务。
- 保护已完成章节，不重新生成已完成正文。
- 由现有系统读取已完成剧情、人物、关系、物品、矛盾、状态和章节摘要，再规划未来章节。
- 未来规划采用全局策略加分批章节计划，避免一次超大 JSON 截断。
- 增加批次覆盖校验、单章回退和可选 continuation strategy 兼容。
- 最终目标从 30 万字改为约 20 万字。
- 扩展接口只允许用于标题 `嫂子的账本` 或 `嫂子的帐本`，避免误伤其他长篇。
- 修复 `long_novel_object_states.status VARCHAR(128)` 过短导致第 12 章事务回滚/502；启动自愈会把该列升级为 `TEXT`。

关键提交：

- `59fc956` Improve classical and family long novel flows
- `fbe772f` Use system replan for sister account expansion
- `8e2d567` Split sister account continuation replan into batches
- `4186096` Tighten sister account replan batch coverage
- `375528f` Add single chapter fallback for sister replan
- `36b4cec` Allow missing optional continuation strategy
- `9c901b5` Reduce generation costs and replan sister account target
- `0c20020` Reduce long novel generation cost without dropping QA

关键入口：

- `backend/app/services/family_long_novel.py`
- `backend/app/prompts/family_long_novel_prompts.py`
- `backend/app/api/routers/long_novel.py`
- `backend/app/models/long_novel.py`
- `backend/app/db/session.py`
- `frontend/src/pages/LongNovelWorkspace.tsx`

### 4.9 长篇成本与稳定性优化

已实现但仍需真实账单闭环的工程措施：

- OpenAI 请求增加稳定 `prompt_cache_key`。
- 缓存键限制在 OpenAI 长度上限内，超长时防御性哈希。
- 支持 `prompt_cache_retention=24h`。
- 长篇默认支持 Flex service tier、后台轮询和资源不可用时的同参数回退。
- Flex 回退不改变 model、Prompt、reasoning 或正文目标。
- Prompt 缓存预热仅服务可复用长篇节点；一次性节点不再额外预热，避免预热本身浪费费用。
- 长篇草稿在 QA 完成前保留，失败恢复不应重新购买已生成正文。
- 生成日志记录缓存键、service tier、输入/输出 token、成本和错误字段。
- OpenAI 代理逻辑允许官方 base URL 与服务器国外代理同时使用。

关键提交：

- `0c20020` Reduce long novel generation cost without dropping QA
- `e10cb68` Run long novel workflow on resilient Flex processing
- `e5093fc` Keep prompt cache keys within OpenAI limits
- `f970482` Preserve long novel drafts until QA completes
- `e2bebe4` Warm long novel prompt caches before generation
- `5131569` Prime long novel caches by reasoning effort
- `5b96c02` Avoid one-off long novel cache warmups

注意：这些提交证明工程接线与测试存在，不证明每章真实账单已经永久降到某个金额。

### 4.10 朱雀/AIGC 最终闸门

已实现：

- 全分类终稿后的 AIGC 检测服务骨架。
- 可记录检测结果、分数、判定和局部改写报告。
- 前后端均有 feature flag。
- 相关测试与文档保留。

关键提交：

- `e02c98e` Add global Zhuque AIGC final quality gate
- `15f7323` Keep AIGC final gate dormant by default

当前最终状态：

- 后端 `AIGC_GATE_ENABLED=False`
- 前端 `VITE_AIGC_GATE_ENABLED=false`
- 前端不显示朱雀步骤和结果。
- workflow 进度恢复到接入前的 92% 入库、94% 质量报告。
- 不调用腾讯或 Gemini，不产生该链路费用。

不得删除代码，也不得擅自开启。

## 5. 最重要的未解决事项

### P0：必须优先处理

#### P0-1 《嫂子的账本》线上 20 万字规划需要真实数据库复核

代码已实现专用重规划，但当前交接没有重新查询生产数据库，因此以下事实仍是**待验证**：

- 线上究竟有几个同名项目及准确 `project_id`。
- 当前已完成/accepted 章节数、当前字数和最后 accepted 章节。
- `target_words` 是否确实为 200000 左右。
- 未来章节是否全部唯一，不再出现连续多章相同 goal/conflict/hook。
- 已完成章节正文、摘要、人物状态、物品状态和冲突状态是否完全未改。
- 第一章未来计划是否能自然承接最后一章 accepted 正文。
- 全局圣经、家庭关系、分卷大纲、矛盾线和结局方向是否都按 20 万字重新规划。
- `long_novel_object_states.status` 在线上 PostgreSQL 是否已经是 `TEXT`。
- 第 12 章曾出现的 502/事务回滚是否在生产环境真实消失。

下一位 Context 不得直接再次重规划。先只读查询、导出/备份并做 diff；确认项目唯一、现状和缺口后再决定是否调用接口。

#### P0-2 长篇真实单章美元成本仍未形成可重复结论

用户曾观察到约 2 美元/4000 字，后续有短暂下降，又出现回升。当前工程已接入缓存与 Flex，但还缺少如下闭环：

- 同一章各节点的真实 OpenAI response/service tier。
- 每个节点的 uncached input、cached input、output、reasoning token。
- Flex 是否成功，是否因资源不足回退到 auto/default。
- 失败重试、JSON 修复、QA 修复或缓存预热是否产生额外账单。
- 项目日志中的估算成本与 OpenAI 控制台实际账单是否一致。
- 动态上下文变化造成的缓存前缀失配比例。

必须用**一章受控真实生成**做观测，不能仅凭单元测试宣称成本已解决。观测时不得取消任一 workflow 节点，也不得降低质量参数。

#### P0-3 已暴露凭证需要轮换

聊天中曾直接出现微信读书 Cookie/会话字段和一个 API Key。Git 跟踪文件扫描未发现这些具体值，但聊天暴露本身仍构成风险。

- 不要把任何聊天凭证复制进本文、代码、日志、issue 或 commit。
- 建议在对应服务后台撤销/轮换旧 Key。
- 微信读书会话应重新登录并更新为新的服务器端安全配置。
- `.env`、`deployment.local.json` 只留本机，不得提交。

### P1：需要真实业务样本验收

#### P1-1 微信读书 478 本与番茄公司全量书单

- 当前代码能解析 HAR/JSON 和沉淀本地索引。
- 微信读书公开搜索、推荐卡片或版权方首页通常只返回局部数据，不能保证 478 本。
- 番茄手机端搜索页同样不能证明公司全量作品。
- “全量”验收必须使用包含全部分页响应的 HAR/抓包，并校验分页游标、去重前后数量和列表终止条件。
- 没有抓包时只能返回“公开入口可见结果”，不能标成全量。

#### P1-2 公版融合真实长样本

Prompt 已要求保持原作身份，但仍需要至少一组 2 至 3 本长输入进行验收：

- 人物和主要事件没有被替换成另一部小说。
- 时间顺序、因果和关键结局不变。
- 改动集中在表达、结构组织和合法融合层面。
- OCR 图片只应作为其中一份输入，不能误判成全部材料。
- 输入必须是公版、获得授权或用户自有文本。

#### P1-3 古文小说风格版真实样本

需要分别验收：

- 叙事型古文，例如《杨家将》：应保留人物、事件与顺序，补足场面、动作、心理和过渡，不能只是逐句翻译。
- 格言型古文，例如“昨日之非不可留……”：必须形成有角色、处境、选择、变化和收束的小故事。
- 当前 Word Prompt 直出后不再有 Gemini 修复，因此直出失败应由质量检测明确报错，而不是暗中恢复旧润色链路。

#### P1-4 古文参照译写版真实样本

- 最终 Prompt 已替换为 Word 版本，老板认可的是直出方向。
- 需要用老板指定的样例复核“更大白话但不改义”，尤其避免“便觉”被误译成因果关系。
- 参考译文与词典冲突时，应遵循当前 Word Prompt 的优先级；不得擅自恢复更早的多轮润色或联网 Prompt。
- 对有版权的现代译本必须确保使用权限，不能把“参照”实现成逐句复制。

#### P1-5 佛经长篇覆盖与故事质量

代码已有《百喻经》式单元恢复与缓存，但仍应保留一份真实 98 篇回归证据：

- 恰当覆盖原作 98 个譬喻单元，不重复膨胀到 200 篇。
- 每个故事完整、通顺、有故事感，篇幅由表达完整度决定。
- 后半部不能退化成寥寥几个字。
- 已完成故事失败恢复时必须复用，不整本重生成。

### P2：明确暂缓或长期事项

#### P2-1 朱雀/AIGC 闸门

- 代码保留，功能关闭。
- 老板当前使用外部 SaaS。
- 在用户重新明确要求之前，不做 API 逆向、不购买 Key、不启用、不纳入 workflow。
- 未来若恢复，必须重新验证官方接口、凭证、限额、费用、隐私和改写是否损害正文质量。

#### P2-2 Epic Novel Engine

当前 `docs/epic_novel/IMPLEMENTATION_STATUS.md` 明确为 `PARTIAL_IMPLEMENTATION`。主要未完成证据：

- 100 本清单中只有 57 本本地导入。
- Gate 2B 仅证明 3 本/27 个成功 section analysis/48 张 production-ready card。
- 30 章连续性是确定性模拟，不是 30 章生产级正文。
- 未完成真实 300 万字长跑。

除非用户明确切换到 Epic 工作，不要把普通分类或《嫂子的账本》需求扩散到 Epic。

#### P2-3 法兰网站参考项目

聊天早期曾讨论 `jydsflange.com` 全站截图和参考前端，产物目录位于另一个工作区。用户随后明确回到本项目。该任务不属于 `E:\video-plan` 当前维护范围，除非用户再次明确恢复。

## 6. 下一位 Context 的推荐处理顺序

1. 先读本文、`AGENTS.md`、当前 `git status` 和最近提交。
2. 确认最新用户请求属于普通内容系统、家长里短长篇还是 Epic，禁止串链。
3. 如果继续《嫂子的账本》：
   - 先只读查询线上项目和章节状态。
   - 导出已完成章节哈希、状态账本和未来计划。
   - 验证项目唯一性、20 万目标和重复计划问题。
   - 未完成上述检查前不要再次调用重规划。
4. 如果继续成本问题：
   - 先审计一章历史 generation log。
   - 再运行一章受控生成并记录四个必要节点。
   - 对照 OpenAI 账单，不根据估算值下结论。
5. 如果继续微信读书/番茄：
   - 优先验证 HAR 是否包含全部分页。
   - 明确区分公开结果与全量结果。
6. 如果继续古文/佛经/公版：
   - 先使用用户给定真实样本跑验收。
   - 修复必须局部收敛到对应分类。
7. 任何代码改动后运行目标测试与前端构建；用户要求发布时再推送 `main` 并部署北京 ECS。

## 7. 禁止事项清单

- 不要把“代码已存在”写成“线上问题 100% 解决”。
- 不要重生成《嫂子的账本》已完成章节。
- 不要再次把《嫂子的账本》改回 30 万字。
- 不要为降成本删除长篇 workflow 节点或降低质量参数。
- 不要使用开发者电脑的本地代理承载生产调用。
- 不要把微信读书/番茄局部搜索结果称为公司全量书单。
- 不要恢复两个古文指定分类的 Gemini 一润、二润。
- 不要启用朱雀/AIGC gate。
- 不要把普通家长里短长篇和 Epic Engine 混为一体。
- 不要在 Epic 生成 Prompt 中使用原始参考书文本。
- 不要提交 `.env`、部署密码、Cookie、API Key 或 HAR 中的会话凭证。
- 不要为了“顺手”修改无关分类。

## 8. 关键文件索引

### 普通内容与质量

- `backend/app/services/content_engine.py`
- `backend/app/tasks/tasks.py`
- `backend/app/core/quality_metrics.py`
- `backend/app/prompts/goal_driven_templates.py`
- `backend/app/prompts/longform_variant_prompts.py`
- `frontend/src/constants/categories.ts`
- `frontend/src/components/CreateTaskModal.tsx`

### 古文、佛经和公版

- `backend/app/prompts/classical_reference_rewrite_profile.md`
- `backend/app/services/public_domain_rewrite.py`
- `backend/app/services/image_ocr.py`
- `frontend/src/pages/PublicDomainRewrite.tsx`

### 家长里短长篇

- `backend/app/services/family_long_novel.py`
- `backend/app/prompts/family_long_novel_prompts.py`
- `backend/app/api/routers/long_novel.py`
- `backend/app/models/long_novel.py`
- `backend/app/db/session.py`
- `frontend/src/pages/LongNovelWorkspace.tsx`

### 推荐值工具

- `backend/app/services/weread_tools.py`
- `backend/app/api/routers/weread.py`
- `frontend/src/pages/WeReadTool.tsx`

### AIGC 闸门

- `backend/app/services/aigc_quality_gate.py`
- `backend/app/prompts/aigc_natural_rewrite.md`
- `backend/tests/test_aigc_quality_gate.py`
- `frontend/src/config/featureFlags.ts`
- `docs/AIGC_FINAL_GATE.md`

### Epic

- `AGENTS.md`
- `docs/epic_novel/00_README.md`
- `docs/epic_novel/EPIC_NOVEL_ENGINE_FULL_SPEC.md`
- `docs/epic_novel/09_ACCEPTANCE_CRITERIA.md`
- `docs/epic_novel/IMPLEMENTATION_STATUS.md`

## 9. 建议验证命令

### 基础状态

```powershell
git status --short --branch
git log --oneline --decorate -30
```

### 当前关键后端回归

```powershell
python -m pytest backend/tests/test_category_prompt_routing.py backend/tests/test_family_long_novel.py backend/tests/test_aigc_quality_gate.py -q
```

当前基线：`153 passed`。

### 前端

```powershell
Set-Location frontend
npm run build
```

### AIGC 开关

```powershell
rg -n "AIGC_GATE_ENABLED|VITE_AIGC_GATE_ENABLED|AIGC_GATE_VISIBLE" `.env.example`,`.env.production.example`,`backend/.env.example`,`backend/app`,`frontend/src`
```

预期默认值：后端 `False`，前端 `false`。

### 线上健康

```powershell
Invoke-WebRequest http://101.200.48.238/ -UseBasicParsing
Invoke-WebRequest http://101.200.48.238/api/v1/system/health -UseBasicParsing
```

### 凭证泄漏检查

不要把凭证值写进命令历史。只针对已知前缀做 `git grep`，并确保结果为空。

## 10. 最近提交索引

| 主题 | 关键提交 |
| --- | --- |
| 微信读书 Excel/版权方/分页/HAR | `66a4ce7` 至 `0e0181d` |
| 番茄 HAR 导入 | `f277948` |
| 佛经故事与长篇覆盖 | `442e861` 至 `1f808d7` |
| 古文穿插版和参照译写 | `3c479df` 至 `18cec96` |
| 古文 PDF | `159cc4b` |
| 古文 Word Prompt 最终替换 | `f97f2dd` |
| 两个古文分类直出 | `c92e142` |
| 公版融合 | `f4fbc92`、`f1aa0a7`、`c486eb2`、`860e3c5` |
| 古文小说故事化 | `e1db7c2`、`3a0a031` |
| 前端主题 | `4413aff` |
| 《嫂子的账本》系统重规划 | `fbe772f` 至 `9c901b5` |
| 长篇成本/Flex/缓存/恢复 | `0c20020` 至 `5b96c02` |
| AIGC 闸门 | `e02c98e`、`15f7323` |

## 11. 交接结论

当前仓库不是“什么都没做”的状态：推荐值、佛经故事、古文系列、公版融合、终稿 QA、主题、家长里短长篇、缓存/Flex 和 AIGC 开关均已有明确实现与提交。

当前也不能描述为“所有问题已经 100% 解决”。最需要下一位 Context 保持清醒的三件事是：

1. **先用线上数据库证明《嫂子的账本》20 万字规划真实正确，再继续生成。**
2. **用真实 OpenAI 账单与 generation log 证明单章成本，而不是只看代码或估算。**
3. **没有完整 HAR 时，不要承诺微信读书或番茄公司的全量书单。**

朱雀链路保持关闭；Epic 保持 `PARTIAL_IMPLEMENTATION`；所有后续修改继续遵守“质量与 workflow 不变、只做局部修复、证据优先”的原则。
