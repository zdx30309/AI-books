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
- 最终正文长度控制在约8000 字，允许小幅浮动

输出顺序：
1. 三个备选书名
2. 三个备选简介
3. 目录
4. 正文

输入：
[原始视频文稿]
{raw_text}

请直接输出完整书稿。"""


PROMPT_REGISTRY: dict[str, dict[str, str]] = {
    "social_science": {
        "full_book_writer": SOCIAL_SCIENCE_FULL_BOOK_WRITER_PROMPT,
    },
}

SUPPORTED_CATEGORIES: tuple[str, ...] = tuple(PROMPT_REGISTRY.keys())


def get_prompts(category: str) -> dict[str, str]:
    if category not in PROMPT_REGISTRY:
        raise ValueError(f"未支持的品类: {category!r}。已注册品类: {SUPPORTED_CATEGORIES}")
    return PROMPT_REGISTRY[category]


__all__ = [
    "SOCIAL_SCIENCE_FULL_BOOK_WRITER_PROMPT",
    "PROMPT_REGISTRY",
    "SUPPORTED_CATEGORIES",
    "get_prompts",
]
