对内置正面样稿、反面样稿及各自历史报告做对照抽象。以 context.reference_examples 的 role、sample_section_id、report_section_id 为唯一角色映射，不因单句看似自然、旧标题或检测术语而颠倒整篇角色。全文不是当前创作主题，不验证或移植其中知识，不复制原句、书名、人物、场景、具体案例、数字、品牌判断、比喻和章节顺序。

正面卡提取具体信息持续推进、参与者关系、连续因果、具体动作、自然口语与专业解释混合、非机械节奏的可迁移方法；反面卡提取对比句堆叠、同义反复、信息停滞、模板建议、碎片式收束的识别方法。每张卡标明实际来源 section id 和 source_role；至少一张 positive 和一张 negative，总数最多 12 张，内容用简短抽象说明。

只输出 JSON，顶层恰好以下两个字段：
{
  "ReferencePatternCards": [{"id":"自定唯一卡片id","source_role":"positive或negative","source_section_id":"角色映射中的实际样稿或报告分段id","function":"跨题材写作功能","mechanism":"可以应用或诊断的机制","avoid":"迁移时应避免的误用"}],
  "ApprovedEditingSummary": {
    "pacing":"节奏方法","continuity":"连贯推进方法","anti_repetition":"反重复机制","evidence_boundaries":"样稿事实与当前研究来源的边界",
    "positive_methods":[{"card_id":"positive卡id","method":"迁移方法"}],
    "negative_diagnostics":[{"card_id":"negative卡id","diagnostic":"诊断办法"}],
    "ordinary_word_policy":{"mechanical_ban":false,"evaluation_basis":"按句子功能、重复程度、段落同构与信息推进评估普通词，不机械禁用"},
    "report_scope":"attachment_samples_only",
    "sentence_examples_override_roles":false
  }
}

摘要的两类清单各自引用并覆盖对应角色的全部卡片，每张卡仅列一次；id 不重复，不使用未知分段。文字字段不超过 800 字符。这是当前附件的编辑摘要，不是史诗引擎的百书 ReferenceMechanismProfile；不创建或替换全语料画像。摘要不保留历史检测分数，更不把它外推为新稿通过率；单个常用词或改后句都不是禁词表或白名单。
