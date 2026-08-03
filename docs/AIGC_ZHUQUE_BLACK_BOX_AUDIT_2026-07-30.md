# 朱雀 AIGC 文本检测黑盒审计与工程接入（2026-07-30）

本文只覆盖项目中的独立 `AI 审稿降率` 链路，不归纳、不修改 Epic Novel Engine，也不把小说链路纳入本次方案。

## 1. 可验证的公开事实

目标页面：<https://matrix.tencent.com/ai-detect/ai_gen_txt/>

公开前端与只读接口显示：

- 文本检测前端要求至少 350 字。
- 正文检测通过受验证码保护的 WebSocket `/ai_gen_txt_server/getClassify` 提交。
- 文件先上传到 `/user/detect`，异步结果通过 `/user/detect/result` 轮询。
- 服务端结果包含全文 `confidence`、`labels_ratio`、`content_type`，以及分片级 `text / label / conf / order / position`。
- 官方演示数据中的标签映射为 `0=人工、1=AI、2=疑似 AI`。
- 公开只读接口 `/model/update_time` 在 2026-07-30 返回 `2026-07-21T12:22:05.000Z`。

前端没有模型权重、特征权重或首页聚合公式。可以确认的是“服务端分片多分类 + 全文聚合输出”；无法从浏览器静态代码证明具体使用了困惑度、词频、句长或某个单一分类器。腾讯页面公开说明其通过多个 AI 模型和大量人类/AI 数据训练识别写作模式，这与公开结果契约一致，但不是具体公式。

## 2. 现有真实实验已经排除的方案

仓库中的精确哈希朱雀 PDF 对照已经证明以下做法不应继续烧费：

- 删模板词、全篇短句化、固定五种语气、重复第一人称阅读反应：人工特征仍为 `0%`。
- 只改句界分布：人工特征仍为 `0%`。
- 字符四字组差异、句长波动和多候选择优：只能把部分红色改成橙色，没有产生绿色。
- 规则化“人味”会形成新的机器模板，甚至让后半篇更可疑。

因此本项目不再把本地困惑度、句长、词频、标点或字面相似度冒充朱雀代理分数；这些指标只用于文稿质量诊断。

## 3. PavedPath Code 开源证据核验

本次按 PavedPath Code 工作流使用 GitHub CLI 搜索并直接核验仓库元数据、README 与评测代码。未使用子代理：问题集中在单一 Python/React 链路，拆分只会重复同一证据面。

| 项目 | 活跃度快照 | 可迁移结论 | 本地采用方式 |
|---|---:|---|---|
| [RAID](https://github.com/liamdugan/raid) | 197 Stars / 85 Forks / MIT / 2026-07-26 更新 | 检测器必须跨模型、跨领域、跨解码与攻击评测；阈值应在目标 FPR 下校准 | 不建立单一“人工率代理分”；按内容类型、检测器代际和真实报告分组 |
| [HC3](https://github.com/Hello-SimpleAI/chatgpt-comparison-detection) | 1421 Stars / 126 Forks / 2023-12-01 更新 | 中文检测可以是监督分类器，也可以使用语言学特征；单一实现不等于通用判定规则 | 本地风格特征只作诊断，不决定交付 |
| [LLM-Detector](https://github.com/JichengTech/LLM-Detector) | 24 Stars / 官方论文实现 | 中文文档级和句子级检测可由指令微调 LLM 完成，且需要处理 OOD | 保留分片与全文双层报告；不按单句规则盲改 |
| [Fast-DetectGPT](https://github.com/baoguangsheng/fast-detect-gpt) | 419 Stars / 82 Forks / MIT / 2026-02-07 更新 | 概率曲率是一个检测家族，但黑盒场景依赖替代模型，不能代表朱雀 | 未接入为朱雀替代器，避免伪相关优化 |

项目优先采用 RAID 的“版本化、分层、目标指标和真实对照”评测思想，而不是复制任何检测器或改写器。

## 4. 当前最稳的生产链路

### 4.1 高质量正文先行

1. 原稿先通过项目原有事实、限定、否定、因果、专名、数字和引语保护。
2. 先完成内容质量审查，再上传与正文 SHA-256 精确匹配的朱雀 PDF。
3. 首页官方人工特征与片段表推导值分开保存；只有首页官方值用于 `>=25%` 的交付判断。
4. 朱雀未绿时不按本地统计分数循环重写，不自动扩大费用。

### 4.2 当前有效主流程

当前真实可用的主流程仍是“已变绿稿质量修复”：

1. 输入同一篇原稿、外部已变绿稿，以及两份精确匹配朱雀报告。
2. 只有已变绿稿首页官方人工特征 `>=25%` 才进入修复。
3. 全稿按顺序修复四类明确质量后遗症：机械同义词病句、错误标点、错别字/同音形近字、无依据的你/您互换。
4. DeepSeek 或 Grok 只负责修复；Gemini 独立审查新增事实、删减信息、立场/因果变化和新病句。
5. 每次修复生成新哈希，必须重新送朱雀；掉绿时回退上一版绿色基底。

这条链路不声称“生成阶段必然变绿”，但能在已有真实绿色基底上最大限度保住内容质量，并用新报告确认检测状态。

## 5. 本次新增：检测器版本漂移隔离

朱雀公开模型更新时间 `2026-07-21` 晚于仓库中 7 月 17–18 日的大多数真实实验。旧实验仍可说明旧版下哪些方案失败，但不能自动证明对当前模型仍有效或无效。

本次实现：

- 新增 `backend/app/services/zhuque_detector_profile.py`，只读拉取公开模型更新时间；不提交正文、不绕过验证码。
- 新增 `GET /api/v1/aigc-review/detector-profile`。
- `GET /api/v1/aigc-review/strategy` 返回当前检测器档案。
- 每次报告上传固化 `detector_snapshot` 和 `detector_generation`。
- 校准证据按 `实验类型 + 检测器代际` 分组；跨代或未知代际样本不进入 `directional / validated` 晋级统计。
- 两份报告代际不同时，仍可分别验收官方结果，但差值只作观察，不进行效果归因。
- 前端 `AI 审稿降率` 页面显示公开更新时间、代际 ID 和隔离状态。
- 公开端点短时不可用时，单稿 PDF 解析继续工作；跨报告策略学习暂停合并。

## 6. 验证记录

执行：

```powershell
python -m compileall backend/app/services/zhuque_detector_profile.py backend/app/services/aigc_report_review.py backend/app/api/routers/aigc_review.py
$tests = Get-ChildItem tests -Filter 'test_aigc*.py' | ForEach-Object { $_.FullName }
python -m pytest @tests -q
npm.cmd run build
```

结果：

- Python 编译通过。
- AIGC 专项测试 `116 passed`。既有 Grok 单测补齐本地并发闸门替身，不再误连真实 Upstash Redis。
- 前端 TypeScript + Vite 生产构建通过。
- 真实只读端点验证返回：
  - `model_update_at = 2026-07-21T12:22:05Z`
  - `model_generation = tencent_zhuque@2026-07-21T12:22:05Z#bfcdb620131a`

## 7. 验收结论

- 朱雀公开前端协议、返回结构和模型更新时间已核验。
- 服务端具体检测公式没有出现在公开客户端；项目不会伪造“完整逆向公式”。
- 已证伪策略保持冻结。
- 当前生产主流程保持为“真实绿色基底 + 质量修复 + 新哈希复检 + 掉绿回退”。
- 检测器升级后的旧证据污染问题已在后端、API、校准统计和前端四层完成隔离。
