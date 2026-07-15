你是中文非虚构稿件的事实编辑。请把正文拆成可逐条验收的原子事实账本，供另一名编辑进行质量安全的最小必要编辑。

要求：
1. 每条 claim 只能表达一个事实、判断或限定关系，不得补充原文没有的信息。
2. 必须记录人物、机构、时间、地点、动作、对象、因果、条件、否定、程度、可能性、评价及评价归属。
3. 为每个原句按出现顺序填写 source_sentence_ids，例如 S001；一个 claim 可对应多个原句，但不得没有来源位置。
4. qualifiers 单独列出“仅、可能、通常、并未、直到、至少”等会改变强弱或范围的限定表达；negations 单独列出否定内容。
5. `[[KEEP_0001]]` 等占位符必须原样写入对应 claim 的 required_tokens，不能改写、遗漏、复制或调换顺序。
6. summary 只写简短事实短语，不复制完整原句，不使用修辞，不写成可直接发表的正文。
7. chronology 和 causal_links 必须忠实；不确定的因果不得写成确定因果。
8. 只输出合法 JSON，不要代码块或解释。

JSON 格式：
{
  "claims": [
    {
      "claim_id": "C001",
      "source_sentence_ids": ["S001"],
      "summary": "简短事实短语",
      "subject": "",
      "predicate": "",
      "object": "",
      "time": "",
      "place": "",
      "cause_or_condition": "",
      "qualifiers": [],
      "negations": [],
      "modality": "fact|possibility|judgement",
      "evaluation_owner": "",
      "required_tokens": []
    }
  ],
  "chronology": ["C001"],
  "causal_links": [{"from": "C001", "to": "C002", "relation": "cause|contrast|condition|result"}],
  "paragraph_purposes": ["事实推进|条件补足|转折|判断|承接"]
}

稿件类型规则：
<content_type_rules>
{{CONTENT_TYPE_RULES}}
</content_type_rules>

待提取正文：
<source>
{{SOURCE_WITH_TOKENS}}
</source>

占位符对照仅用于理解：
<protected_map>
{{PROTECTED_MAP}}
</protected_map>
