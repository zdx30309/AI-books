# 终稿 AIGC 质量闸门

## 目标

在现有生成、润色、格式化和硬伤 QA 全部完成之后，对最终正文调用腾讯云文本 AI 生成识别服务。只处理高于阈值的正文块，不改变已有 writer、polish、refine、format workflow。

## 工作流

```text
existing workflow
  -> final hard-error QA
  -> 350-1800 字语义分块
  -> Tencent TextModeration(Type=TEXT_AIGC)
  -> Score <= 50: 保留原文
  -> Score > 50 / Block: Gemini 受约束局部改写
  -> 数字、引语、专名与结构锚点校验
  -> Tencent 复检（最多两轮）
  -> 原有质量 QA 复检
  -> final artifact
```

## 可解释边界

- `Score`、`Suggestion`、`Label`、`SubLabel`、`DetailResults`、`Extra` 和 `RequestId` 直接保存腾讯云返回值。
- 腾讯云没有承诺为每个高分文本返回完整的语言学原因。系统不得把推测冒充为朱雀结论。
- 系统另外记录本地可解释诊断，包括句长过齐、模板连接词、重复句首和连续对称句式，并明确标注这些不是朱雀官方原因。
- 第三方产品公开前端可观察到“分段/整篇/复改”和可调改写程度，但其后端模型、提示词和算法不公开。本项目只吸收可验证的产品方法，不复制私有实现。

## 质量保护

- 只改朱雀高分块，低分块不调用改写模型。
- 不增加事实、案例、人物、情节、观点或结论。
- 数字、日期、比例、金额、URL、书名、代码和引号内原话必须逐字保留。
- 段落数不变，长度比例必须在 0.65-1.45 之间，不得新增 Markdown 标题。
- 古文参照译写版不检测、不改写原文段，只处理译文。
- 长篇小说在检测通过后再抽取 canon/state；连续性修复改变正文后必须重新检测。
- 最多两轮局部改写。仍高于阈值时任务失败，不返回伪装成已通过的终稿。

## 成本控制

- 腾讯云官方当前按量价格为 0.05 元/条。
- 同一轮复检复用未变化块的检测结果。
- 常规任务把通过报告与最终正文 SHA-256 绑定，断点续跑时正文未变则不重复调用。
- 长篇章节把通过正文的哈希写入生成日志，同一版本再次后处理时直接复用。
- 未命中高风险的正文不会调用 Gemini。

## 配置

```env
AIGC_GATE_ENABLED=True
AIGC_GATE_FAIL_CLOSED=True
AIGC_GATE_THRESHOLD=50
AIGC_GATE_MAX_REWRITE_ROUNDS=2
AIGC_GATE_CHUNK_MIN_CHARS=350
AIGC_GATE_CHUNK_MAX_CHARS=1800
AIGC_GATE_REWRITE_TEMPERATURE=0.85
TENCENTCLOUD_SECRET_ID=...
TENCENTCLOUD_SECRET_KEY=...
TENCENT_AIGC_BIZ_TYPE=...
TENCENT_AIGC_REGION=ap-beijing
```

凭据只写入服务器 `.env`，不得提交到 Git。缺少任一腾讯云凭据时，启用状态下按 fail-closed 处理。
