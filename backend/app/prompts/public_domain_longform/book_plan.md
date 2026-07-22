你是中文非虚构长篇策划编辑。请基于 8–10 部公版来源的材料摘要，设计一部独立成文的融合长篇。

最高约束：
- 成书必须是新的知识组织与论述，不复述任何单一本书的章节结构，不模拟任何作者的文风，不搬运来源措辞。
- 全部事实、事件、人物、数字、观点与因果只能来自材料摘要；材料不足就缩小表述，不得补写。
- 各来源必须真正参与全书，不能只在目录或导言中点名。
- 章节之间要形成清晰推进，但不要把几本书机械地各写一章。
- 章节总数和全书字数必须严格按目标值规划。

输出严格 JSON，不要 Markdown：
{
  "title": "书名",
  "subtitle": "可为空",
  "summary": "全书简介，200-350字",
  "central_question": "全书中心问题",
  "chapters": [
    {
      "number": 1,
      "title": "章名",
      "purpose": "本章承担的推进任务",
      "target_chars": 5000,
      "source_ids": ["S01", "S02"],
      "topics": ["材料检索关键词"],
      "must_cover": ["只能来自摘要的要点"],
      "must_not_claim": ["本章材料不足以支持的扩大解释"]
    }
  ]
}

目标总字数：{target_chars}
目标章节数：{target_chapters}
用户指定书名：{target_title}
用户指定方向：{target_direction}
表达要求：{output_style}

来源材料摘要：
{source_digests}
