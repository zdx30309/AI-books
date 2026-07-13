# 朱雀 v2 质量优先终稿闸门

> 2026-07-13 现状更正：腾讯云文本内容安全 `TEXT_AIGC` 与用户实际使用的朱雀网页报告，当前没有证据表明是同一套检测结果或同一账户体系。因此生产 `AIGC_GATE_ENABLED` 继续保持关闭，不能拿腾讯 TMS 分数冒充朱雀结果。下面原有 v2 自动闸门作为已实现但未启用的历史方案保留；当前可用方案是新增的“真实报告驱动审稿台”。

## 真实朱雀报告驱动审稿台

前端路由：`/aigc-review`。后端接口：

- `POST /api/v1/aigc-review/report`：上传真实朱雀打印 PDF 和同版本可编辑原稿。
- `POST /api/v1/aigc-review/optimize`：只处理用户选中的风险片段，生成一个候选并进行独立整稿终审。
- `GET /api/v1/aigc-review/strategy`：返回当前最低成本模型路由，不调用模型。

该工具不读取、不改写、不回填任何历史任务，也不改变全局闸门。小说、家长里短长篇、漫画和 Epic Novel Engine 均不进入该路由；当前只接受普通文章、古文/佛经译写和公版融合稿。

### 真实判定口径

- 朱雀报告中的绿色“人工特征”必须严格 `>50%`；`50%` 本身不通过。
- 橙色“疑似 AI”和红色“AI 特征”都不计入人工特征。
- 工程规划目标默认 `60%`，但规划值不冒充检测值。
- 图片报告的圆环百分比可能因二维排版发生 OCR 错序，因此系统以完整片段表中的“占全文比例 + AIGC 值”重算总比例：`0-0.5=人工`、`0.5-0.99=疑似 AI`、`0.99-1=AI 特征`。
- 候选通过模型质量终审后仍只是“待复检候选”。用户必须把候选重新送入真实朱雀并上传第二份 PDF；人工特征严格大于 50% 后，前端才解锁终稿下载。
- 系统会从报告的首个详情卡抽取正文样本，与当前原稿/候选做模糊匹配；复检报告不是同一稿件或匹配证据不足时，即使百分比过线也不解锁下载。

### 最低成本模型路由

截至 2026-07-13 的官方价格与能力对比：

| 用途 | 模型 | 官方标准价（每百万 token） | 结论 |
|---|---|---:|---|
| 定向改稿 | DeepSeek V4 Pro | 输入 ¥3 / 输出 ¥6 | 默认主改；比 Flash 贵一点，但更适合事实忠实和中文长文质量 |
| 极低价生成 | DeepSeek V4 Flash | 输入 ¥1 / 输出 ¥2 | 最便宜，但不作为质量优先主改默认值 |
| 独立终审 | Gemini 3.1 Flash-Lite | 输入 $0.25 / 输出 $1.50 | 默认终审；与主改不同供应商且成本极低 |
| 失败升级 | Gemini 3.5 Flash | 输入 $1.50 / 输出 $9 | 仅在人工选择升级时使用，不做默认调用 |
| 高价升级参考 | GPT-5.6 Terra | 输入 $2.50 / 输出 $15 | 仅复杂稿件人工升级，不进入最低成本默认链路 |

官方来源：[DeepSeek V4 发布](https://api-docs.deepseek.com/news/news260424/)、[DeepSeek 价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing)、[Gemini 模型](https://ai.google.dev/gemini-api/docs/models)、[Gemini 价格](https://ai.google.dev/gemini-api/docs/pricing)、[OpenAI GPT-5.6 模型](https://developers.openai.com/api/docs/models)、[Anthropic 价格](https://platform.claude.com/docs/en/about-claude/pricing)、[阿里云百炼模型价格](https://help.aliyun.com/en/model-studio/model-pricing)。

默认链路只做“一份候选 + 一次整稿独立终审”，不再为每块生成双候选，也不逐块调用审稿模型。本地 RapidOCR 读取 PDF，不使用付费视觉模型。按样例约 1.7 万中文字符、推荐改动约 62% 粗估，一轮模型成本通常约 `¥0.1-¥0.2`，实际以 API token 返回和所用通道计费为准。

### 报告片段改写协议（v2）

报告片段可能长达数千字，且边界可能落在一个自然段或句子的中间。仅在 Prompt 中写“保持段落不变”不够可靠，因此 `zhuque-report-review-v2` 在调用模型前执行以下保护：

1. 保留报告坐标和相邻上下文，只把当前风险片段拆成有序的 `[[P0001]]...[[/P0001]]` 编号块。
2. 把标题、数字、日期、金额、书名、URL、引语及古文受保护原文替换成唯一的 `[[KEEP_0001]]` 占位符。
3. 模型只能在编号块内部改写；程序按原稿分隔符重新拼回自然段，并把段内意外换行视为格式噪声进行零费用修复。
4. 占位符必须原位置、原次数保留；还原后再次检查锚点的精确计数，新增、删除或重复均拒绝。
5. 首次候选仅因结构/锚点硬规则失败时，最多追加一次带具体失败反馈的同片段重试；第二次仍失败就立即停止，不调用整稿 Gemini 终审。

改写和终审还会从独立 Prompt 资产 `aigc_report_content_rules.json` 载入分类规则：普通文章/传记保护史实不确定性与来源归属，古文/佛经保护原文和术语义理，公版融合保护作品出处与引文边界。业务代码不硬编码这些长指令。

该重试不是放宽质量标准。它只修复模型输出协议问题，且上限由 `AIGC_REVIEW_MAX_REWRITE_ATTEMPTS=2` 固定，防止无限烧 API。前端展示每次尝试、长度比例、段落/标题/锚点结果、缺失或多余锚点和协议错误，不再只显示一句“质量未过”。

### 质量与费用保护

每个候选先在本地检查自然段数量、长度比例 `0.80-1.25`、标题、模型说明、数字、日期、金额、书名、URL 和引语精确重复次数。硬规则失败时仅允许上述一次有界修复；仍失败就停止后续付费调用并保留原稿。整稿终审必须同时满足语义忠实度 `>=95`、信息完整度 `>=98`、流畅度 `>=90`、声音连续性 `>=90`，且无新增事实、删减、立场或情节变化。终审失败时候选不提供终稿下载，并向前端返回四项分数与具体理由。

2026-07-13 使用真实 13 页朱雀打印报告验证了 OCR 口径：14 个片段合计 `100%`，其中首个风险片段占全文 `24.06%`、`2709` 字、AIGC 值 `0.9707`。该大块样本是本次编号块与锚点协议的重点回归对象。

当前模型组合不能数学保证任意输入一定转绿；真正可保证的是：原稿不可变、质量不达标不交付、没有真实朱雀复检不宣称通过。

报告审稿台专用配置：

```env
AIGC_REVIEW_REWRITE_MODEL=deepseek-v4-pro
AIGC_REVIEW_AUDIT_MODEL=gemini-3.1-flash-lite
AIGC_REVIEW_MAX_REWRITE_ATTEMPTS=2
AIGC_REVIEW_HUMAN_TARGET=60
```

## 生效边界

- 策略版本：`aigc_policy_version=v2`。
- 只影响开关启用后新建或明确“重新生成整篇”的非小说任务。
- 历史任务没有 v2 快照时，即使后来打开总开关，也不会被追溯检测、改写或回填。
- 覆盖普通非小说文章、古文/佛经和公版融合。
- 暂时排除全部现有小说入口：`history_culture`、`web_short_story`、`suspense_micro_reasoning`、`classical_chinese_novel_style`、家长里短长篇（`family_long_novel` / `family_sisters_in_law_longform`）及 Epic Novel Engine；漫画继续排除。
- 排除判断在任务策略快照、正文引擎和朱雀服务三层执行；即使调用方误传 `required=true`，小说正文也直接返回 `not_required`，不调用腾讯、OpenAI 或 Gemini。
- 自动完成的硬条件是所有正文块均为 `Suggestion=Pass` 且 `Score < 50`，并通过确定性保护、交叉质量审稿和现有分类 QA。
- `Score=50` 不通过；腾讯返回 `Review` 或 `Block` 均不允许自动完成。
- 无法兼顾质量和分数时，状态进入 `manual_review`，保留不可变原稿，不交付低质量候选。

本闸门保证“不双达标就不交付”，不承诺任何输入一定可以被安全改到合格。

## 成本受控流程

```text
04_format.md（不可变 pre-AIGC 原稿）
  -> 350–1800 字分块检测
  -> 无风险：直接完整复检并结束
  -> 只处理 Score>=50 / Review / Block 的块
  -> Gemini 先生成候选
  -> 确定性保护失败：不复检、不审稿
  -> 朱雀复检未过：跳过质量模型审稿
  -> 朱雀通过：OpenAI 交叉审稿
  -> 双达标：立即停止，不再生成第二候选
  -> 质量未过：OpenAI 生成第二候选，Gemini 交叉审稿
  -> 第一轮无合格项：只把最低分且确定性安全的候选作为第二轮种子
  -> 第二轮仍始终与最初原稿比较
  -> 拼回整稿后完整分块复检 + 现有硬伤/分类/来源/连续性 QA
  -> 05_aigc_final.md（仅双达标时存在）
```

此顺序刻意把付费调用放到必要时才发生：先检测、再改风险块、候选先过确定性保护、再过朱雀，最后才调用另一模型审稿。首个候选双达标后立即停止。技术故障重跑会复用正文哈希、阈值、策略、Prompt 和模型组合完全一致的检测缓存。

## 质量保护

每个候选必须同时满足：

- 自然段数量与顺序不变。
- 长度比例 `0.80–1.25`。
- 数字、日期、金额、百分比、URL、书名、代码和引语按出现次数完整保留。
- 锚点不能跨自然段移动，不能新增或重复。
- 不新增 Markdown 标题、模型说明或检测说明。
- 交叉审稿：语义忠实度 `>=95`、信息完整度 `>=98`、流畅度 `>=90`、声音连续性 `>=90`。
- 不新增事实、不删信息、不改立场、不改情节或事件。
- 古文参照译写只检测和修改译文，原文段不进入检测块。
- 公版融合在朱雀通过后重新做来源重合/输出残留 QA。
- 家长里短长篇当前不进入朱雀 v2；其原有串行生成、连续性 QA 和状态账本规则保持不变。

## 状态与恢复

| 场景 | 状态 | 是否可下载 | 恢复方式 |
|---|---|---:|---|
| 双达标 | `completed` / `passed` | 是 | 无需重跑 |
| 分数或质量无法兼顾 | `manual_review` | 否 | 查看原稿与最佳安全候选；仅重跑朱雀阶段 |
| 腾讯或模型技术故障 | `failed` / `technical_failed` | 否 | 自动重试或 AIGC-only 重试 |
| 未绑定 v2 的历史任务 | 原状态 / `not_required` | 按历史规则 | 不受开关变化影响 |
| 小说或漫画任务 | 原工作流 / `not_required` | 按原工作流规则 | 不进入朱雀，不产生本链路费用 |

普通任务 AIGC-only 接口：`POST /api/v1/tasks/{task_id}/retry-aigc`。

公版融合 AIGC-only 接口：`POST /api/v1/public-domain-rewrite/tasks/{task_id}/retry-aigc`。该入口复用 `04_format.md`、朱雀缓存和公版来源 QA 快照，不重新调用蓝图/成稿模型。

家长里短 AIGC-only 接口暂时保留兼容路由，但在小说排除期间会明确拒绝重跑，不调用任何付费服务。

普通/公版报告接口：`GET /api/v1/tasks/{task_id}/aigc-report`。前端显示不可变原稿、失败原因、最佳确定性安全候选、调用次数和估算成本。

## 报告与缓存

`aigc_quality_report.json` 记录：

- 策略版本、Prompt 版本、模型组合和策略敏感缓存键。
- 原稿/终稿 SHA-256、初始/最终最高分、严格阈值和改写轮数。
- 每块腾讯原始返回、`RequestId`、本地诊断和前后分数。
- 候选确定性保护、交叉审稿分数、选择理由、模型与轮次。
- 每次调用的 action、provider、model、token、费用与币种、状态、耗时和错误。
- 腾讯实际调用按当前单价记录为 `0.05 CNY/条`；不记录任何密钥。

通过报告只有在正文哈希、阈值、策略版本、Prompt 版本和模型组合全部一致时才复用。人工复核报告中的同哈希检测结果也可以复用，从而避免 AIGC-only 重试重复购买相同检测。

## 配置

代码部署时必须保持总开关关闭：

```env
AIGC_GATE_ENABLED=False
AIGC_GATE_FAIL_CLOSED=True
AIGC_GATE_THRESHOLD=50
AIGC_GATE_MAX_REWRITE_ROUNDS=2
AIGC_GATE_CHUNK_MIN_CHARS=350
AIGC_GATE_CHUNK_MAX_CHARS=1800
AIGC_GATE_DETECT_CONCURRENCY=4
AIGC_GATE_REWRITE_TEMPERATURE=0.85
AIGC_POLICY_VERSION=v2
AIGC_OPENAI_MODEL=
AIGC_OPENAI_REASONING_EFFORT=low
AIGC_OPENAI_SERVICE_TIER=flex
TENCENTCLOUD_SECRET_ID=
TENCENTCLOUD_SECRET_KEY=
TENCENT_AIGC_BIZ_TYPE=
TENCENT_AIGC_REGION=ap-beijing
```

腾讯凭据只允许通过安全渠道写入生产服务器 `.env`，不得进入聊天、Git、前端、报告或日志。缺少任一必需凭据时，v2 任务 fail-closed 为可重试技术故障。

## 上线验收

1. 部署代码但保持 `AIGC_GATE_ENABLED=False`。
2. 部署前后分别计算历史已完成任务、章节和阶段文件哈希，确认零变化。
3. 用户安全配置腾讯 `SecretId`、`SecretKey`、`BizType` 后执行真实连通性测试。
4. 用全新输入创建 12 个非小说代表性任务，覆盖社科、财经、科普/健康、传记/哲学、古文/佛经和公版融合；小说、漫画与 Epic 不纳入本轮灰度。
5. 所有自动完成稿必须全块 `Pass + <50`，质量审稿和分类 QA 全过，硬伤为零。
6. 所有不达标稿必须进入人工复核，原稿 SHA-256 不变且不可下载。
7. 通过稿由指定审稿人盲审：事实/情节零丢失，语言质量各项 `>=4/5`，之后才允许全量开启。

腾讯约束以官方文档为准：[`TextModeration` 接口](https://cloud.tencent.com/document/api/1124/51860)、[文本 AI 生成识别快速入门与等级说明](https://cloud.tencent.com/document/product/1124/118694)、[计费说明](https://cloud.tencent.com/document/product/1124/122362)。当前实现使用 `Type=TEXT_AIGC`，单次不超过 2000 字，并按官方 `Pass/Review/Block` 含义处理。
