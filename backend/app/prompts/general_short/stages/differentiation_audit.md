【本次唯一工作节点】
你正在执行"差异化审查"节点。输入是本轮灵感原文（原稿）和最终全书正文（新稿）。你要判断新稿是否只是"换了一身字"，骨架和素材是否仍是原稿那一套。

只输出一个 JSON 对象，不加解释、前言、代码块围栏或后置说明。

{
  "literal": {
    "source_chars": 原稿去空白字数,
    "draft_chars": 新稿去空白字数,
    "longest_common_chars": 最长连续相同片段字数,
    "longest_common_sample": "该片段原文",
    "max_sentence_similarity": 句级相似度最高值(0-1，保留两位小数),
    "high_ratio": 句级相似度≥0.5 的句子占比(0-1)
  },
  "structure": {
    "units": [
      {"id": 1, "source_unit": "原稿论点单元", "draft_location": "新稿对应章节/位置或写无", "level": "完整/部分/无"}
    ],
    "A_unit_match": 0-100,
    "B_case_order": 0-100,
    "C_narrative_spine": 0-100,
    "S": 0-100
  },
  "material": {
    "data_reuse": 0-100,
    "case_reuse": 0-100,
    "named_entity_reuse": 0-100,
    "literal_reuse": 0-100,
    "M": 0-100
  },
  "composite": 0-100,
  "verdict": "安全/高危",
  "differentiation_list": ["新稿里原稿完全没有的章节、论点、数据与术语"],
  "fix_directive": "一句话告诉下一步该改什么，优先给改骨架的方案，不要只说换词"
}

打分口径（不得自行调整）：
- S = A×0.40 + B×0.30 + C×0.30
- A 论点单元对应率 =（完整对应条数×1.0 + 部分对应条数×0.5）÷ 原稿论点单元总数 ×100
- B 案例推进次序一致度，从 100/85/70/40/25/10 中取一个值
- C 叙事主线与因果链沿用度，从 100/75/40/10 中取一个值
- M = 数据沿用率×0.30 + 举例与案例组合沿用度×0.25 + 具名主体沿用度×0.20 + 字面重合度×0.25
- composite = S×0.60 + M×0.40
- 判定：composite 50% 以下为"安全"；50% 及以上为"高危"。

禁止事项：
- 不得因为字面重合接近 0 就判"安全"。结构框架相似度 ≥70% 时给出"安全"属于严重误判。
- 不得省略 differentiation_list。
- 不得用"较相似""大体不相似"这类模糊措辞替代百分数与判定词。
- 结论必须能被 units / literal 两张表复算。

【原稿】
{source_text}

【新稿】
{draft_text}
