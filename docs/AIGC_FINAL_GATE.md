# 朱雀 v2 质量优先终稿闸门

## 生效边界

- 策略版本：`aigc_policy_version=v2`。
- 只影响开关启用后新建的任务、明确“重新生成整篇”的任务，以及启用后新生成/明确修复的家长里短章节。
- 历史任务没有 v2 快照时，即使后来打开总开关，也不会被追溯检测、改写或回填。
- 覆盖普通文章、古文/佛经、公版融合和家长里短长篇；排除漫画与 Epic Novel Engine。
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
- 家长里短章节只有朱雀 v2 通过后才能抽取状态；未通过时项目暂停、章节与计划标记 `needs_repair`，不写 canon/state 账本。

## 状态与恢复

| 场景 | 状态 | 是否可下载 | 恢复方式 |
|---|---|---:|---|
| 双达标 | `completed` / `passed` | 是 | 无需重跑 |
| 分数或质量无法兼顾 | `manual_review` | 否 | 查看原稿与最佳安全候选；仅重跑朱雀阶段 |
| 腾讯或模型技术故障 | `failed` / `technical_failed` | 否 | 自动重试或 AIGC-only 重试 |
| 未绑定 v2 的历史任务 | 原状态 / `not_required` | 按历史规则 | 不受开关变化影响 |

普通任务 AIGC-only 接口：`POST /api/v1/tasks/{task_id}/retry-aigc`。

公版融合 AIGC-only 接口：`POST /api/v1/public-domain-rewrite/tasks/{task_id}/retry-aigc`。该入口复用 `04_format.md`、朱雀缓存和公版来源 QA 快照，不重新调用蓝图/成稿模型。

家长里短 AIGC-only 接口：`POST /api/v1/long-novel/family/projects/{project_id}/chapters/{chapter_number}/retry-aigc`。

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
4. 用全新输入创建 12 个代表性任务，覆盖社科、财经、科普/健康、传记/哲学、古文/佛经、公版融合及一个家长里短章节。
5. 所有自动完成稿必须全块 `Pass + <50`，质量审稿和分类 QA 全过，硬伤为零。
6. 所有不达标稿必须进入人工复核，原稿 SHA-256 不变且不可下载。
7. 通过稿由指定审稿人盲审：事实/情节零丢失，语言质量各项 `>=4/5`，之后才允许全量开启。

腾讯约束以官方文档为准：[`TextModeration` 接口](https://cloud.tencent.com/document/api/1124/51860)、[文本 AI 生成识别快速入门与等级说明](https://cloud.tencent.com/document/product/1124/118694)、[计费说明](https://cloud.tencent.com/document/product/1124/122362)。当前实现使用 `Type=TEXT_AIGC`，单次不超过 2000 字，并按官方 `Pass/Review/Block` 含义处理。
