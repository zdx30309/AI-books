"""Minimal prompt templates for the current workflow."""

SOCIAL_SCIENCE_FULL_BOOK_WRITER_PROMPT = """你是一名成熟的非虚构长文写作者。

任务：参考我的视频文稿，直接生成一篇完整的深度长文。

输出要求：
- 严格按以下顺序输出，不得打乱：
  1. 三个备选书名
  2. 三个备选简介
  3. 目录
  4. 正文
- 三个备选书名、三个备选简介、目录，三块必须都单独成节，且标题文字必须原样保留为：
  - 三个备选书名
  - 三个备选简介
  - 目录
- 正文必须紧接在前置块之后，不要插入额外说明
- 只输出成稿，不要解释，不要代码块，不要元信息，不要分析过程

内容要求：
- 文章需与原始视频文稿有一定差别，可做合理的拓展延伸
- 保持自然、清晰、连贯
- 不要空话，不要学术腔，不要翻译腔，不要做心理和情绪刻画，不要使用比喻修辞
- 不要使用“不是……而是……”句式，不要使用破折号——
- 允许你自行组织结构，不必预设固定章节层级
- 最终正文长度控制在约8000字，允许小幅浮动

输出顺序：
1. 三个备选书名
2. 三个备选简介
3. 目录
4. 正文

输入：
[原始视频文稿]
{raw_text}

请直接输出完整书稿。"""


GEMINI_FAST_POLISH_PROMPT = """将我的文本重新表达为自然流畅的现代汉语，不要掉书袋，对大众读者友好。
"""


GEMINI_FAST_REFINE_PROMPT = """二次润色（微调）
需要完成以下工作：
1. 将语义重复的内容删除。如果前文已经出现过有关论述，将重复的内容删掉，并一笔带过，如“如前文所述……”。
2. 对少量措辞做自然微调，保持原意不变，不要引入新的说法。
3. 修改章节序号，将【第N章：】改为【N.】，如【第一章：名校仍有价值，但终极目标需要重排】改为【1.名校仍有价值，但终极目标需要重排】。
4. 重新分段，每段50~100字；如内容偏多，或逻辑关系紧密，不适合分段，可宽限到200字。
"""


DEEPSEEK_V4_PRO_FORMAT_PROMPT = """你是格式整理器，负责把书稿整理成最终交付格式。除以下两类明确允许的格式调整外，其余正文内容（语义、观点、措辞、段落结构）一律不得修改。

【完整文稿必须具备的结构（四节，按此顺序输出）】
1. 三个备选书名（小节标题：@三个备选书名）
2. 三个备选简介（小节标题：@三个备选简介）
3. 目录（小节标题：@目录）
4. 正文（小节标题：@正文）
   - 正文下设若干章节
   - 每个章节的标题统一格式为：@N. 章节名
     · 例：@1. 名校仍有价值，但终极目标需要重排
     · N 必须是阿拉伯数字（1、2、3、4……），从 1 起按顺序编号

【允许的格式调整】
1. 章节序号格式化：将形如「第N章：xxx」「第N章 xxx」「第N章、xxx」统一改写为「N. xxx」。N 是阿拉伯数字或中文数字（一、二、三、十……）时，请转写为对应阿拉伯数字。如果输入已是「N. xxx」形式，保持不动。
2. 标题加粗：在以下每一个标题文字前面加一个 @ 符号作为加粗标记，正文段落不加：
   - 四个小节标题：三个备选书名 / 三个备选简介 / 目录 / 正文
   - 正文内每一个章节标题（形如「@1. xxx」「@2. xxx」）

【强制要求】
- 必须保持四节顺序：备选书名 → 备选简介 → 目录 → 正文。不得调换顺序，不得删除任何一节。
- 输出中绝对不得出现 # 符号（无论是 #、##、### 还是 ####）。如果输入文本里出现任何 # 标题符号，必须删除该 # 符号，仅保留标题文字本身，并按上述规则加 @。
- 不得改写、增删、合并、拆分、改序、重述任何正文内容。
- 不得加入解释、前言、总结、过渡语或任何新增内容。
- 保留原文的段落、空行、列表结构。
- 直接输出整理后的全文，不要包裹代码块（```），不要附加任何说明。

【文本】
{text}
"""


PROMPT_REGISTRY: dict[str, dict[str, str]] = {
    "social_science": {
        "full_book_writer": SOCIAL_SCIENCE_FULL_BOOK_WRITER_PROMPT,
        "gemini_fast_polish": GEMINI_FAST_POLISH_PROMPT,
        "gemini_fast_refine": GEMINI_FAST_REFINE_PROMPT,
        "deepseek_v4_pro_format": DEEPSEEK_V4_PRO_FORMAT_PROMPT,
    },
}

SUPPORTED_CATEGORIES: tuple[str, ...] = tuple(PROMPT_REGISTRY.keys())


def get_prompts(category: str) -> dict[str, str]:
    if category not in PROMPT_REGISTRY:
        raise ValueError(f"未支持的品类: {category!r}。已注册品类: {SUPPORTED_CATEGORIES}")
    return PROMPT_REGISTRY[category]


__all__ = [
    "SOCIAL_SCIENCE_FULL_BOOK_WRITER_PROMPT",
    "GEMINI_FAST_POLISH_PROMPT",
    "GEMINI_FAST_REFINE_PROMPT",
    "DEEPSEEK_V4_PRO_FORMAT_PROMPT",
    "PROMPT_REGISTRY",
    "SUPPORTED_CATEGORIES",
    "get_prompts",
]
