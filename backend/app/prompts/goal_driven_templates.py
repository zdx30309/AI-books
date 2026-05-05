"""Minimal prompt templates for the current workflow."""

BOOK_TITLE_AND_DESCRIPTION_PROMPT = """【书名与简介专项要求】
- 三个备选书名和三个备选简介要带一点新媒体短视频博眼球的感觉，但不能失去图书书名和图书简介的质感。
- 书名要能直击当下社会痛点，让读者一眼知道这本书和自己的现实处境有关。
- 简介要有现实刺痛感和阅读吸引力，但不要写成廉价标题党、夸张营销话术或平台爆款口播。
- 书名和简介要冷静、有分寸、有质感：有钩子，有态度，但仍然像一本认真图书的包装文案。
- 所有书名都不要出现中文冒号或英文冒号。
"""


SOCIAL_SCIENCE_FULL_BOOK_WRITER_PROMPT = """你是一名成熟的非虚构长文写作者。

任务：参考我的视频文稿，直接生成一篇完整的深度长文。

输出要求：
- 严格按以下顺序输出，不得打乱：
  1. @三个备选书名
  2. @三个备选简介
  3. @目录
  4. @导言
  5. @正文
- 三个备选书名、三个备选简介、目录、导言、正文，五块必须都单独成节，且标题文字必须原样保留为：
  - @三个备选书名
  - @三个备选简介
  - @目录
  - @导言
  - @正文
- 导言必须放在目录之后、正文之前，长度 200~400 字，负责说明全书问题意识、阅读入口和核心判断，不要写成客套前言。
- 导言要带钩子，但要和正文区分开：第一段先抛出一个能抓住读者的问题、冲突、反常识判断或现实困境；后面只搭建阅读入口和判断框架，不要提前展开正文里的完整论证。
- 导言的钩子要冷静、有分寸、有现实感，不要写成标题党、悬疑营销、鸡汤口号或夸张情绪。
- 导言排版要求：标题独立一行，下面 2~4 个自然段；段落之间可以保留一个空行，整体要像正式书稿开篇。
- 正文必须紧接在导言之后，不要插入额外说明
- 只输出成稿，不要解释，不要代码块，不要元信息，不要分析过程

""" + BOOK_TITLE_AND_DESCRIPTION_PROMPT + """

内容要求：
- 文章需与原始视频文稿有一定差别，可做合理的拓展延伸
- 保持自然、清晰、连贯
- 写作要有叙事张力和阅读钩子：开篇先抛出一个清晰的问题、冲突或反常识判断，让读者愿意继续读；正文推进要有轻重、转折和层次，不要只平铺分析现象。
- 观点要有深度和洞察力：不只描述发生了什么，还要解释背后的结构、利益、代价和选择；保持冷静克制，但要有明确态度，不要写成情绪宣泄。
- 钩子和态度要服务内容本身，不要使用夸张标题党、悬疑营销话术、强行煽动或模板化金句。
- 不要空话，不要学术腔，不要翻译腔，不要做心理和情绪刻画，不要使用比喻修辞
- 不要使用“不是……而是……”句式，不要使用破折号——
- 允许你自行组织结构，不必预设固定章节层级
- 最终正文长度控制在约12000字，允许小幅浮动
- 所有书名、目录标题和正文章节标题都不要出现冒号（包括中文冒号和英文冒号）。
- 正文最后一章不要命名为“结语”，也不要出现“结语”两个字作为章节标题；把它作为普通章节自然收束即可。

输出顺序：
1. @三个备选书名
2. @三个备选简介
3. @目录
4. @导言
5. @正文

输入：
[原始视频文稿]
{raw_text}

请直接输出完整书稿。"""


LIFE_PHILOSOPHY_FULL_BOOK_WRITER_PROMPT = """你是一名成熟的人生哲学类非虚构长文写作者。

任务：参考我的视频文稿，直接生成一篇完整的人生哲学类深度长文。

输出要求：
- 严格按以下顺序输出，不得打乱：
  1. @三个备选书名
  2. @三个备选简介
  3. @目录
  4. @导言
  5. @正文
- 三个备选书名、三个备选简介、目录、导言、正文，五块必须都单独成节，且标题文字必须原样保留为：
  - @三个备选书名
  - @三个备选简介
  - @目录
  - @导言
  - @正文
- 导言必须放在目录之后、正文之前，长度 200~400 字，负责说明全书问题意识、阅读入口和核心判断，不要写成客套前言。
- 导言要带钩子，但要和正文区分开：第一段先抛出一个能抓住读者的问题、冲突、反常识判断或现实困境；后面只搭建阅读入口和判断框架，不要提前展开正文里的完整论证。
- 导言的钩子要冷静、有分寸、有现实感，不要写成标题党、悬疑营销、鸡汤口号或夸张情绪。
- 导言排版要求：标题独立一行，下面 2~4 个自然段；段落之间可以保留一个空行，整体要像正式书稿开篇。
- 正文必须紧接在导言之后，不要插入额外说明
- 只输出成稿，不要解释，不要代码块，不要元信息，不要分析过程

内容要求：
- 文章需与原始视频文稿有一定差别，可做合理的拓展延伸
- 保持自然、清晰、连贯
- 写作要有叙事张力和阅读钩子：开篇先抛出一个清晰的问题、冲突或反常识判断，让读者愿意继续读；正文推进要有轻重、转折和层次，不要只平铺分析现象。
- 观点要有深度和洞察力：不只描述一个人为什么痛苦、焦虑、失败、犹豫或失控，还要解释背后的认知结构、情绪惯性、利益牵引、长期代价和替代选择。
- 要能把具体问题提炼成可长期反复阅读的人生原则，但不要写成口号、鸡汤、励志语录或成功学模板。
- 对于人生困境和提出和解决，应尽量形成一个清晰闭环：现实处境 → 常见反应 → 隐含误区 → 长期代价 → 替代认知 → 行动原则。
- 不要空话，不要学术腔，不要翻译腔，不要做心理和情绪刻画，不要使用比喻修辞
- 不要使用“不是……而是……”句式，不要使用破折号——
- 允许你自行组织结构，不必预设固定章节层级
- 最终正文长度控制在约12000字，允许小幅浮动
- 所有书名、目录标题和正文章节标题都不要出现冒号（包括中文冒号和英文冒号）。
- 正文最后一章不要命名为“结语”，也不要出现“结语”两个字作为章节标题；把它作为普通章节自然收束即可。

输出顺序：
1. @三个备选书名
2. @三个备选简介
3. @目录
4. @导言
5. @正文

输入：
[原始视频文稿]
{raw_text}

请直接输出完整书稿。"""


GEMINI_FAST_POLISH_PROMPT = """请把下面的文字重写成自然流畅、适合大众阅读的现代汉语。
用一位有生活阅历的作者口吻来写，有个人视角，不要像AI那样工整、中立、每段都总结。
不要掉书袋，不要太学术，保留原文核心观点，但表达要更顺。

【字数与内容保护（重要，违反等于任务失败）】
- 这是一润，目标只是让文字更自然，不是删减、不是缩写、不是总结。
- 输出字数必须与原文字数基本一致（允许 ±5% 浮动）。绝不允许删段、删句、合并段落、省略例子。
- 原文中的所有具体例子、数据、人名、地名、机构名、案例细节，必须 100% 全部保留。
- 不得新增内容，也不得删除内容；只允许在句子级别替换为更自然的表达。
- 如果某段已经够自然，原样返回即可，不要为了"显得做了工作"而改写。
- 不要输出任何解释、注释、前言或总结，直接输出润色后的全文。
"""


GEMINI_FAST_REFINE_PROMPT = """二次润色（微调）。这是"微调"，不是"精简"，更不是"压缩"。

【字数硬约束（违反等于任务失败）】
- 输出字数必须与原文字数基本一致，允许 ±10% 浮动；严禁把 8000 字砍成 3000 字这样的大幅删减。
- 不得以"前文已涉及"为由删除任何具体例子、案例、数据、细节、独立段落或独立章节。
- 章节数量、章节标题、章节内段落数量必须全部保留，不得合并章节、不得砍段。

【需要完成的工作】
1. 仅删除"字面完全重复"的句子：同一句话在文中重复出现两次以上时，去掉重复出现的那一份。除此之外的任何"语义近似""主题相关""观点相同"都不算重复，必须保留。严禁使用"如前文所述"这类一笔带过的写法去合并段落。
2. 对少量措辞做自然微调，保持原意不变，不要引入新的说法。
3. 修改章节序号，将【第N章：】改为【N.】，如【第一章：名校仍有价值，但终极目标需要重排】改为【1.名校仍有价值，但终极目标需要重排】。
4. 在不删减内容的前提下重新分段，每段 50~100 字；如内容偏多或逻辑紧密不适合分段，可宽限到 200 字。分段只是把长段落拆短，不是把长段落删短。

【输出要求】
- 直接输出微调后的全文，不要解释，不要总结，不要省略号代替正文。
"""


DEEPSEEK_V4_PRO_FORMAT_PROMPT = """你是格式整理器，负责把书稿整理成最终交付格式。除以下明确允许的格式调整外，其余正文内容（语义、观点、措辞、段落结构）一律不得修改。

【完整文稿必须具备的结构（五节，按此顺序输出）】
1. 三个备选书名（小节标题：@三个备选书名）
2. 三个备选简介（小节标题：@三个备选简介）
3. 目录（小节标题：@目录）
4. 导言（小节标题：@导言）
5. 正文（小节标题：@正文）
   - 正文下设若干章节
   - 每个章节的标题统一格式为：@N. 章节名
     · 例：@1. 名校仍有价值，但终极目标需要重排
     · N 必须是阿拉伯数字（1、2、3、4……），从 1 起按顺序编号

【允许的格式调整】
1. 章节序号格式化：将形如「第N章：xxx」「第N章 xxx」「第N章、xxx」统一改写为「N. xxx」。N 是阿拉伯数字或中文数字（一、二、三、十……）时，请转写为对应阿拉伯数字。如果输入已是「N. xxx」形式，保持不动。
2. 标题加粗：在以下每一个标题文字前面加一个 @ 符号作为加粗标记，正文段落不加：
   - 五个小节标题：三个备选书名 / 三个备选简介 / 目录 / 导言 / 正文
   - 正文内每一个章节标题（形如「@1. xxx」「@2. xxx」）
3. 标题清理：所有书名、目录标题、导言标题、正文章节标题中，不得出现中文冒号或英文冒号；如果标题中有冒号，直接删掉冒号或改成自然停顿。
4. 结尾标题清理：正文最后一章不得命名为“结语”，也不得出现“结语”两个字作为章节标题；如输入有“结语”，改成一个普通章节标题，但不要新增内容。

【强制要求】
- 必须保持五节顺序：备选书名 → 备选简介 → 目录 → 导言 → 正文。不得调换顺序，不得删除任何一节。
- 输出中绝对不得出现 # 符号（无论是 #、##、### 还是 ####）。如果输入文本里出现任何 # 标题符号，必须删除该 # 符号，仅保留标题文字本身，并按上述规则加 @。
- 不得改写、增删、合并、拆分、改序、重述任何正文内容。
- 不得加入解释、前言、总结、过渡语或任何新增内容。
- 保留原文的段落、空行、列表结构。
- 直接输出整理后的全文，不要包裹代码块（```），不要附加任何说明。

【文本】
{text}
"""


CATEGORY_OPTIONS: tuple[dict[str, str], ...] = (
    {
        "id": "social_science",
        "label": "社会痛点",
        "core_feature": "直面现实困境，高共鸣",
        "directions": "教育、婚姻、住房、职场压力、养老",
        "prompt_status": "ready",
    },
    {
        "id": "life_philosophy",
        "label": "人生哲学",
        "core_feature": "提供认知升级或心灵慰藉",
        "directions": "励志、成长、价值观、生活态度",
        "prompt_status": "ready",
    },
    {
        "id": "popular_science",
        "label": "科普读物",
        "core_feature": "把复杂知识讲明白",
        "directions": "AI、经济、投资、科技、名人思想",
        "prompt_status": "pending",
    },
    {
        "id": "wisdom_stories",
        "label": "小故事·智慧",
        "core_feature": "以故事承载哲理，易传播",
        "directions": "佛学、禅宗、寓言、民间智慧",
        "prompt_status": "pending",
    },
    {
        "id": "biography",
        "label": "传记·人物",
        "core_feature": "浓缩人生关键节点与品格",
        "directions": "名人成长、失败与转折、精神内核",
        "prompt_status": "pending",
    },
    {
        "id": "history_culture",
        "label": "历史·文化",
        "core_feature": "切片式讲述，有趣有料",
        "directions": "冷知识、人物趣闻、古代生活、历史启示",
        "prompt_status": "pending",
    },
    {
        "id": "emotion_relationship",
        "label": "情感·关系",
        "core_feature": "贴近日常，治愈或实用",
        "directions": "亲情、友情、爱情、社交边界",
        "prompt_status": "pending",
    },
    {
        "id": "psychology_self_help",
        "label": "心理自助",
        "core_feature": "方法导向，解决具体情绪问题",
        "directions": "焦虑、内耗、拖延、自卑、失眠",
        "prompt_status": "pending",
    },
    {
        "id": "suspense_micro_reasoning",
        "label": "悬疑·微推理",
        "core_feature": "篇幅短小，反转或悬念",
        "directions": "日常谜团、封闭场景、人性故事",
        "prompt_status": "pending",
    },
    {
        "id": "workplace_survival",
        "label": "职场生存",
        "core_feature": "实战技巧，新人友好",
        "directions": "沟通、汇报、效率、避坑、跳槽",
        "prompt_status": "pending",
    },
    {
        "id": "health_wellness",
        "label": "健康·养生",
        "core_feature": "轻科普，可立即行动",
        "directions": "睡眠、运动、饮食、放松、预防",
        "prompt_status": "pending",
    },
    {
        "id": "classic_interpretation",
        "label": "经典·解读",
        "core_feature": "降低门槛，提取精华",
        "directions": "名著、电影、哲学、艺术",
        "prompt_status": "pending",
    },
    {
        "id": "web_short_story",
        "label": "网文短篇",
        "core_feature": "网络文学风格，强节奏、爽点密集",
        "directions": "系统文、重生/穿书、甜宠、沙雕脑洞、悬疑反转式短篇",
        "prompt_status": "pending",
    },
)


def _build_category_placeholder_writer_prompt(category: dict[str, str]) -> str:
    label = category["label"]
    core_feature = category["core_feature"]
    directions = category["directions"]
    return f"""你是一名成熟的{label}类长文写作者。

任务：参考我的视频文稿，直接生成一篇完整的{label}类深度长文。

当前分类画像：
- 核心特点：{core_feature}
- 适合方向：{directions}
- 说明：本分类正式 prompt 待甲方补充。当前必须严格按上面的分类画像写作，不要退回泛社科口径。

输出要求：
- 严格按以下顺序输出，不得打乱：
  1. @三个备选书名
  2. @三个备选简介
  3. @目录
  4. @导言
  5. @正文
- 三个备选书名、三个备选简介、目录、导言、正文，五块必须都单独成节，且标题文字必须原样保留为：
  - @三个备选书名
  - @三个备选简介
  - @目录
  - @导言
  - @正文
- 导言必须放在目录之后、正文之前，长度 200~400 字，负责说明全书问题意识、阅读入口和核心判断，不要写成客套前言。
- 导言要带钩子，但要和正文区分开：第一段先抛出一个能抓住读者的问题、冲突、反常识判断或现实困境；后面只搭建阅读入口和判断框架，不要提前展开正文里的完整论证。
- 只输出成稿，不要解释，不要代码块，不要元信息，不要分析过程

内容要求：
- 文章需与原始视频文稿有一定差别，可做合理的拓展延伸。
- 保持自然、清晰、连贯，写作要有叙事张力和阅读钩子。
- 所有论述、故事、案例和标题都必须服务于“{label}”这个方向。
- 不要空话，不要学术腔，不要翻译腔，不要使用夸张标题党或模板化金句。
- 不要使用“不是……而是……”句式，不要使用破折号——
- 最终正文长度控制在约12000字，允许小幅浮动。
- 所有书名、目录标题和正文章节标题都不要出现冒号（包括中文冒号和英文冒号）。
- 正文最后一章不要命名为“结语”，也不要出现“结语”两个字作为章节标题；把它作为普通章节自然收束即可。

输出顺序：
1. @三个备选书名
2. @三个备选简介
3. @目录
4. @导言
5. @正文

输入：
[原始视频文稿]
{{raw_text}}

请直接输出完整书稿。"""


PROMPT_REGISTRY: dict[str, dict[str, str]] = {
    "social_science": {
        "full_book_writer": SOCIAL_SCIENCE_FULL_BOOK_WRITER_PROMPT,
        "gemini_fast_polish": GEMINI_FAST_POLISH_PROMPT,
        "gemini_fast_refine": GEMINI_FAST_REFINE_PROMPT,
        "deepseek_v4_pro_format": DEEPSEEK_V4_PRO_FORMAT_PROMPT,
    },
    "life_philosophy": {
        "full_book_writer": LIFE_PHILOSOPHY_FULL_BOOK_WRITER_PROMPT,
        "gemini_fast_polish": GEMINI_FAST_POLISH_PROMPT,
        "gemini_fast_refine": GEMINI_FAST_REFINE_PROMPT,
        "deepseek_v4_pro_format": DEEPSEEK_V4_PRO_FORMAT_PROMPT,
    },
}

for _category in CATEGORY_OPTIONS:
    _category_id = _category["id"]
    if _category_id not in PROMPT_REGISTRY:
        PROMPT_REGISTRY[_category_id] = {
            "full_book_writer": _build_category_placeholder_writer_prompt(_category),
            "gemini_fast_polish": GEMINI_FAST_POLISH_PROMPT,
            "gemini_fast_refine": GEMINI_FAST_REFINE_PROMPT,
            "deepseek_v4_pro_format": DEEPSEEK_V4_PRO_FORMAT_PROMPT,
        }

SUPPORTED_CATEGORIES: tuple[str, ...] = tuple(PROMPT_REGISTRY.keys())
CATEGORY_ALIASES: dict[str, str] = {
    "social_pain": "social_science",
    "social": "social_science",
}
for _category in CATEGORY_OPTIONS:
    CATEGORY_ALIASES[_category["id"]] = _category["id"]
    CATEGORY_ALIASES[_category["label"]] = _category["id"]


def normalize_category(category: str | None) -> str:
    key = (category or "social_science").strip() or "social_science"
    normalized = CATEGORY_ALIASES.get(key)
    if normalized is None:
        raise ValueError(f"未支持的品类: {category!r}。已注册品类: {SUPPORTED_CATEGORIES}")
    return normalized


def list_categories() -> list[dict[str, str]]:
    return [dict(category) for category in CATEGORY_OPTIONS]


def get_category_meta(category: str | None) -> dict[str, str]:
    normalized = normalize_category(category)
    for item in CATEGORY_OPTIONS:
        if item["id"] == normalized:
            return dict(item)
    raise ValueError(f"未支持的品类: {category!r}。已注册品类: {SUPPORTED_CATEGORIES}")


def get_prompts(category: str | None) -> dict[str, str]:
    normalized = normalize_category(category)
    return PROMPT_REGISTRY[normalized]


__all__ = [
    "BOOK_TITLE_AND_DESCRIPTION_PROMPT",
    "SOCIAL_SCIENCE_FULL_BOOK_WRITER_PROMPT",
    "LIFE_PHILOSOPHY_FULL_BOOK_WRITER_PROMPT",
    "GEMINI_FAST_POLISH_PROMPT",
    "GEMINI_FAST_REFINE_PROMPT",
    "DEEPSEEK_V4_PRO_FORMAT_PROMPT",
    "CATEGORY_OPTIONS",
    "PROMPT_REGISTRY",
    "SUPPORTED_CATEGORIES",
    "get_category_meta",
    "get_prompts",
    "list_categories",
    "normalize_category",
]
