"""Minimal prompt templates for the current workflow."""

SOCIAL_SCIENCE_FULL_BOOK_WRITER_PROMPT = """你是一名成熟的非虚构长文写作者。

任务：参考我的视频文稿，直接生成一篇完整的深度长文。

要求：
- 必须先输出三个备选书名、三个备选简介、目录，再输出正文
- 前置块要明确分成三个小节：
  1. 三个备选书名
  2. 三个备选简介
  3. 目录
- 正文必须紧接在前置块之后
- 只输出成稿，不要解释，不要代码块
- 文章需与原始视频文稿有一定差别，可做合理的拓展延伸。
- 开篇先给出前言进行问题的引入，再分小节展开
- 保持自然、清晰、连贯
- 不要空话，不要学术腔，不要翻译腔，不要做心理和情绪刻画，不要使用比喻修辞
- 不要使用“不是……而是……”句式，不要使用破折号——
- 只使用必要的加工符号：#、##、###、！！、！（）、@、~、【】
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


DEEPSEEK_V4_PRO_FORMAT_PROMPT = """你是格式整理器。只根据给定文本做排版，不要改写内容，不要增删观点，不要调整语义。

要求：
- 只加格式，不修改内容
- 章节名前面加加粗符号 @
- 不再使用章节符号 #
- 保留正文原意
- 保留必要的分段与列表
- 不要加入解释、前言或总结

文本：
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
