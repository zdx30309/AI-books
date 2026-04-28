"""Minimal prompt templates for the current workflow."""

SOCIAL_SCIENCE_BLUEPRINT_ARCHITECT_PROMPT = """你是一名擅长人文社科长文策划的编辑。

任务：根据原始素材，自动理解选题并输出一份简洁的图书蓝图 JSON。

要求：
- 只输出严格 JSON，不要 Markdown，不要解释
- 生成 3 个书名候选、3 个简介候选，并给出一个主选书名和主选简介
- 给出简洁的目录结构（章节数组）与总字数
- 章节数由你自行判断
- 章节内容由你自行判断，不预设固定层级
- chapter_outline 要足够详细，便于正文一次性生成
- 尽量少约束，重点是让模型自己理解素材并组织结构
- 蓝图整体尽量克制，总字数建议控制在 9000～12000 字

输出 Schema：
{{
  "title": "",
  "title_candidates": ["", "", ""],
  "intro": "",
  "intro_candidates": ["", "", ""],
  "preface": "",
  "global_context": "",
  "chapters": [
    {{
      "chapter_num": 1,
      "chapter_title": "",
      "chapter_goal": "",
      "chapter_outline": "",
      "target_length": 4500
    }}
  ],
  "total_target_length": 18000
}}

输入：
[原始素材]
{raw_text}

请直接输出 JSON。"""


SOCIAL_SCIENCE_FULL_BOOK_WRITER_PROMPT = """你是一名成熟的非虚构长文写作者。

任务：参考原始素材和蓝图，直接生成一篇完整的深度长文。

要求：
- 先输出三个备选书名、三个备选简介、目录
- 再输出正文
- 只输出成稿，不要解释，不要代码块
- 文章需与原始素材有一定差别，可做合理的拓展延伸
- 开篇先给出前言，再分小节展开
- 保持自然、清晰、连贯
- 不要空话，不要学术腔，不要人物特写
- 只使用必要的加工符号：#、##、###、！！、！（）、@、~、【】
- 允许你自行组织结构，不必预设固定章节层级
- 最终正文长度控制在约 10000～12000 字，允许小幅浮动

输出顺序：
1. 三个备选书名
2. 三个备选简介
3. 目录
4. 正文

输入：
[原始素材]
{raw_text}

[蓝图]
{blueprint}

请直接输出完整书稿。"""


PROMPT_REGISTRY: dict[str, dict[str, str]] = {
    "social_science": {
        "blueprint_architect": SOCIAL_SCIENCE_BLUEPRINT_ARCHITECT_PROMPT,
        "full_book_writer": SOCIAL_SCIENCE_FULL_BOOK_WRITER_PROMPT,
    },
}

SUPPORTED_CATEGORIES: tuple[str, ...] = tuple(PROMPT_REGISTRY.keys())


def get_prompts(category: str) -> dict[str, str]:
    if category not in PROMPT_REGISTRY:
        raise ValueError(f"未支持的品类: {category!r}。已注册品类: {SUPPORTED_CATEGORIES}")
    return PROMPT_REGISTRY[category]


__all__ = [
    "SOCIAL_SCIENCE_BLUEPRINT_ARCHITECT_PROMPT",
    "SOCIAL_SCIENCE_FULL_BOOK_WRITER_PROMPT",
    "PROMPT_REGISTRY",
    "SUPPORTED_CATEGORIES",
    "get_prompts",
]
