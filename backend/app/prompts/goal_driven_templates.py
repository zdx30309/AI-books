"""Minimal prompt templates for the current workflow."""

BOOK_TITLE_AND_DESCRIPTION_PROMPT = """【书名与简介专项要求】
- 最终书名和最终简介要带一点新媒体短视频博眼球的感觉，但不能失去图书书名和图书简介的质感。
- 书名要能直击当下社会痛点，让读者一眼知道这本书和自己的现实处境有关。
- 书名要抓普通人的真实困境，可以狠一点、刺一点，但不要低俗、油腻、恐吓或耸人听闻。
- 简介要有现实刺痛感和阅读吸引力，但不要写成廉价标题党、夸张营销话术或平台爆款口播。
- 简介不要套模板，要有具体的生活画面和出版封底感，像读者在书店翻到封底时会停下来读完的一段话。
- 书名和简介要冷静、有分寸、有质感：有钩子，有态度，但仍然像一本认真图书的包装文案。
- 简介避免鸡汤、空话、口号、万能总结和 AI 总结腔，不要用“本书将带你”“我们将探讨”“在这个时代”这类模板句开头。
- 所有书名都不要出现中文冒号或英文冒号。
"""


SOCIAL_SCIENCE_FULL_BOOK_WRITER_PROMPT = """你是一名成熟的非虚构长文写作者。

任务：参考我的视频文稿，直接生成一篇完整的深度长文。

输出要求：
- 严格按以下顺序输出，不得打乱：
  1. @最终书名
  2. @最终简介
  3. @目录
  4. @导言
  5. @正文
- 最终书名、最终简介、目录、导言、正文，五块必须都单独成节，且标题文字必须原样保留为：
  - @最终书名
  - @最终简介
  - @目录
  - @导言
  - @正文
- 导言必须放在目录之后、正文之前，长度 100~400 字，负责说明全书问题意识、阅读入口和核心判断，不要写成客套前言，不要和正文太相似。
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
- 第一次内容生成时，不要让每章都按“现象—原因—建议—总结”的固定模板推进；可以少量穿插故事、问答、反例、短章、场景切片或观点急转，让整本书有起伏。
- 章节之间要有节奏变化：有的章节可以偏叙事，有的章节偏分析，有的章节可以用一个普通人的处境打开，不要像一套分析模板从头写到底。
- 观点要有深度和洞察力：不只描述发生了什么，还要解释背后的结构、利益、代价和选择；保持冷静克制，但要有明确态度，不要写成情绪宣泄。
- 钩子和态度要服务内容本身，不要使用夸张标题党、悬疑营销话术、强行煽动或模板化金句。
- 不要空话，不要学术腔，不要翻译腔，不要做心理和情绪刻画，不要使用比喻修辞
- 不要使用“不是……而是……”句式，不要使用破折号——
- 允许你自行组织结构，不必预设固定章节层级
- 最终正文长度控制在约12000字，允许小幅度缩减，增加是可以的
- 所有书名、目录标题和正文章节标题都不要出现冒号（包括中文冒号和英文冒号）。
- 正文最后一章不要命名为“结语”，也不要出现“结语”两个字作为章节标题；把它作为普通章节自然收束即可。

输出顺序：
1. @最终书名
2. @最终简介
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
  1. @最终书名
  2. @最终简介
  3. @目录
  4. @导言
  5. @正文
- 最终书名、最终简介、目录、导言、正文，五块必须都单独成节，且标题文字必须原样保留为：
  - @最终书名
  - @最终简介
  - @目录
  - @导言
  - @正文
- 导言必须放在目录之后、正文之前，长度 100~400 字，负责说明全书问题意识、阅读入口和核心判断，不要写成客套前言，不要和正文太相似。
- 导言要带钩子，但要和正文区分开：第一段先抛出一个能抓住读者的问题、冲突、反常识判断或现实困境；后面只搭建阅读入口和判断框架，不要提前展开正文里的完整论证。
- 导言的钩子要冷静、有分寸、有现实感，不要写成标题党、悬疑营销、鸡汤口号或夸张情绪。
- 导言排版要求：标题独立一行，下面 2~4 个自然段；段落之间可以保留一个空行，整体要像正式书稿开篇。
- 正文必须紧接在导言之后，不要插入额外说明
- 只输出成稿，不要解释，不要代码块，不要元信息，不要分析过程

内容要求：
- 文章需与原始视频文稿有一定差别，可做合理的拓展延伸
- 保持自然、清晰、连贯
- 写作要有叙事张力和阅读钩子：开篇先抛出一个清晰的问题、冲突或反常识判断，让读者愿意继续读；正文推进要有轻重、转折和层次，不要只平铺分析现象。
- 第一次内容生成时，不要让每章都按“现象—原因—建议—总结”的固定模板推进；可以少量穿插故事、问答、反例、短章、场景切片或观点急转，让整本书有起伏。
- 章节之间要有节奏变化：有的章节可以偏叙事，有的章节偏分析，有的章节可以用一个普通人的处境打开，不要像一套分析模板从头写到底。
- 观点要有深度和洞察力：不只描述一个人为什么痛苦、焦虑、失败、犹豫或失控，还要解释背后的认知结构、情绪惯性、利益牵引、长期代价和替代选择。
- 要能把具体问题提炼成可长期反复阅读的人生原则，但不要写成口号、鸡汤、励志语录或成功学模板。
- 对于人生困境和提出和解决，应尽量形成一个清晰闭环：现实处境 → 常见反应 → 隐含误区 → 长期代价 → 替代认知 → 行动原则。
- 不要空话，不要学术腔，不要翻译腔，不要做心理和情绪刻画，不要使用比喻修辞
- 不要使用“不是……而是……”句式，不要使用破折号——
- 允许你自行组织结构，不必预设固定章节层级
- 最终正文长度控制在约12000字，允许小幅度缩减，增加是可以的
- 所有书名、目录标题和正文章节标题都不要出现冒号（包括中文冒号和英文冒号）。
- 正文最后一章不要命名为“结语”，也不要出现“结语”两个字作为章节标题；把它作为普通章节自然收束即可。

输出顺序：
1. @最终书名
2. @最终简介
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
1. 最低限度地精简同一观点、同一逻辑链条、同一举例的反复阐述。精简重复内容时，只删除几乎逐句雷同或核心论据完全相同的段落，保留首次出现的最完整表述，宁可漏删也不误删，不改变其他内容。删除后需确保段落衔接自然，不影响阅读流畅度。严禁使用"如前文所述"这类一笔带过的写法去合并段落。
2. 对少量措辞做自然微调，保持原意不变，不要引入新的说法。
3. 修改章节序号，将【第N章：】改为【N.】，如【第一章：名校仍有价值，但终极目标需要重排】改为【1.名校仍有价值，但终极目标需要重排】。
4. 在不删减内容的前提下重新分段，每段 50~100 字；如内容偏多或逻辑紧密不适合分段，可宽限到 200 字。分段只是把长段落拆短，不是把长段落删短。

【输出要求】
- 直接输出微调后的全文，不要解释，不要总结，不要省略号代替正文。
"""


DEEPSEEK_V4_PRO_FORMAT_PROMPT = """你是格式整理器，负责把书稿整理成最终交付格式。除以下明确允许的格式调整外，其余正文内容（语义、观点、措辞、段落结构）一律不得修改。

【完整文稿必须具备的结构（五节，按此顺序输出）】
1. 最终书名（小节标题：@最终书名）
2. 最终简介（小节标题：@最终简介）
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
   - 五个小节标题：最终书名 / 最终简介 / 目录 / 导言 / 正文
   - 正文内每一个章节标题（形如「@1. xxx」「@2. xxx」）
3. 标题清理：所有书名、目录标题、导言标题、正文章节标题中，不得出现中文冒号或英文冒号；如果标题中有冒号，直接删掉冒号或改成自然停顿。
4. 结尾标题清理：正文最后一章不得命名为“结语”，也不得出现“结语”两个字作为章节标题；如输入有“结语”，改成一个普通章节标题，但不要新增内容。

【强制要求】
- 必须保持五节顺序：最终书名 → 最终简介 → 目录 → 导言 → 正文。不得调换顺序，不得删除任何一节。
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
        "prompt_status": "ready",
    },
    {
        "id": "wisdom_stories",
        "label": "小故事·智慧",
        "core_feature": "以故事承载哲理，易传播",
        "directions": "佛学、禅宗、寓言、民间智慧",
        "prompt_status": "ready",
    },
    {
        "id": "biography",
        "label": "传记·人物",
        "core_feature": "浓缩人生关键节点与品格",
        "directions": "名人成长、失败与转折、精神内核",
        "prompt_status": "ready",
    },
    {
        "id": "history_culture",
        "label": "历史·文化",
        "core_feature": "切片式讲述，有趣有料",
        "directions": "冷知识、人物趣闻、古代生活、历史启示",
        "prompt_status": "ready",
    },
    {
        "id": "emotion_relationship",
        "label": "情感·关系",
        "core_feature": "贴近日常，治愈或实用",
        "directions": "亲情、友情、爱情、社交边界",
        "prompt_status": "ready",
    },
    {
        "id": "psychology_self_help",
        "label": "心理自助",
        "core_feature": "方法导向，解决具体情绪问题",
        "directions": "焦虑、内耗、拖延、自卑、失眠",
        "prompt_status": "ready",
    },
    {
        "id": "suspense_micro_reasoning",
        "label": "悬疑·微推理",
        "core_feature": "篇幅短小，反转或悬念",
        "directions": "日常谜团、封闭场景、人性故事",
        "prompt_status": "ready",
    },
    {
        "id": "classical_chinese",
        "label": "古文白话",
        "core_feature": "文言文直译与意译并重",
        "directions": "古文、家书、碑铭、史传、经典段落白话化",
        "prompt_status": "ready",
    },
    {
        "id": "health_wellness",
        "label": "健康·养生",
        "core_feature": "轻科普，可立即行动",
        "directions": "睡眠、运动、饮食、放松、预防",
        "prompt_status": "ready",
    },
    {
        "id": "classic_interpretation",
        "label": "经典·解读",
        "core_feature": "降低门槛，提取精华",
        "directions": "名著、电影、哲学、艺术",
        "prompt_status": "ready",
    },
    {
        "id": "web_short_story",
        "label": "网文短篇",
        "core_feature": "网络文学风格，强节奏、爽点密集",
        "directions": "系统文、重生/穿书、甜宠、沙雕脑洞、悬疑反转式短篇",
        "prompt_status": "ready",
    },
)


def _category_option(category_id: str) -> dict[str, str]:
    for category in CATEGORY_OPTIONS:
        if category["id"] == category_id:
            return category
    raise KeyError(category_id)


def _build_category_writer_prompt(category: dict[str, str]) -> str:
    label = category["label"]
    core_feature = category["core_feature"]
    directions = category["directions"]
    return f"""你是一名成熟的{label}类长文写作者。

任务：参考我的视频文稿，直接生成一篇完整的{label}类深度长文。

当前分类画像：
- 核心特点：{core_feature}
- 适合方向：{directions}
- 说明：这是“{label}”题材的独立基础 prompt。必须严格按本题材画像写作，不要混用其它题材口径。

输出要求：
- 严格按以下顺序输出，不得打乱：
  1. @最终书名
  2. @最终简介
  3. @目录
  4. @导言
  5. @正文
- 最终书名、最终简介、目录、导言、正文，五块必须都单独成节，且标题文字必须原样保留为：
  - @最终书名
  - @最终简介
  - @目录
  - @导言
  - @正文
- 导言必须放在目录之后、正文之前，长度 100~400 字，负责说明全书问题意识、阅读入口和核心判断，不要写成客套前言，不要和正文太相似。
- 导言要带钩子，但要和正文区分开：第一段先抛出一个能抓住读者的问题、冲突、反常识判断或现实困境；后面只搭建阅读入口和判断框架，不要提前展开正文里的完整论证。
- 只输出成稿，不要解释，不要代码块，不要元信息，不要分析过程

内容要求：
- 文章需与原始视频文稿有一定差别，可做合理的拓展延伸。
- 保持自然、清晰、连贯，写作要有叙事张力和阅读钩子。
- 所有论述、故事、案例和标题都必须服务于“{label}”这个方向。
- 第一次内容生成时，不要让每章都按“现象—原因—建议—总结”的固定模板推进；可以少量穿插故事、问答、反例、短章、场景切片或观点急转，让整本书有起伏。
- 不要空话，不要学术腔，不要翻译腔，不要使用夸张标题党或模板化金句。
- 不要使用“不是……而是……”句式，不要使用破折号——
- 最终正文长度控制在约12000字，允许小幅度缩减，增加是可以的。
- 所有书名、目录标题和正文章节标题都不要出现冒号（包括中文冒号和英文冒号）。
- 正文最后一章不要命名为“结语”，也不要出现“结语”两个字作为章节标题；把它作为普通章节自然收束即可。

输出顺序：
1. @最终书名
2. @最终简介
3. @目录
4. @导言
5. @正文

输入：
[原始视频文稿]
{{raw_text}}

请直接输出完整书稿。"""


POPULAR_SCIENCE_FULL_BOOK_WRITER_PROMPT = _build_category_writer_prompt(_category_option("popular_science"))
WISDOM_STORIES_FULL_BOOK_WRITER_PROMPT = _build_category_writer_prompt(_category_option("wisdom_stories"))
HISTORY_CULTURE_FULL_BOOK_WRITER_PROMPT = _build_category_writer_prompt(_category_option("history_culture"))
EMOTION_RELATIONSHIP_FULL_BOOK_WRITER_PROMPT = _build_category_writer_prompt(_category_option("emotion_relationship"))
PSYCHOLOGY_SELF_HELP_FULL_BOOK_WRITER_PROMPT = _build_category_writer_prompt(_category_option("psychology_self_help"))
SUSPENSE_MICRO_REASONING_FULL_BOOK_WRITER_PROMPT = _build_category_writer_prompt(_category_option("suspense_micro_reasoning"))
HEALTH_WELLNESS_FULL_BOOK_WRITER_PROMPT = _build_category_writer_prompt(_category_option("health_wellness"))
CLASSIC_INTERPRETATION_FULL_BOOK_WRITER_PROMPT = _build_category_writer_prompt(_category_option("classic_interpretation"))
WEB_SHORT_STORY_FULL_BOOK_WRITER_PROMPT = _build_category_writer_prompt(_category_option("web_short_story"))


BIOGRAPHY_FULL_BOOK_WRITER_PROMPT = """你是一名成熟的人物传记类非虚构长文写作者。

任务：参考我的视频文稿，直接生成一篇完整的人物传记类深度长文。

输出要求：
- 严格按以下顺序输出，不得打乱：
  1. @最终书名
  2. @最终简介
  3. @目录
  4. @导言
  5. @正文
- 最终书名、最终简介、目录、导言、正文，五块必须都单独成节，且标题文字必须原样保留。
- 导言必须放在目录之后、正文之前，长度 100~400 字，负责说明全书问题意识、阅读入口和核心判断，不要写成客套前言，不要和正文太相似。
- 正文必须紧接在导言之后，不要插入额外说明。
- 只输出成稿，不要解释，不要代码块，不要元信息，不要分析过程。

""" + BOOK_TITLE_AND_DESCRIPTION_PROMPT + """

内容要求：
- 文章需与原始视频文稿有一定差别，只允许基于公开可信资料、可核验事实进行扩写。不得虚构人物心理、私人对话、未证实关系、动机、矛盾、丑闻和细节。
- 凡无法确认的信息，必须降级表达为“有资料称”“据公开报道”“尚无可靠资料证实”，或直接不写。
- 写作要有叙事张力和阅读钩子，但只能来自真实经历、时代背景、公开事件和人物选择；不得通过虚构冲突、夸大苦难、制造阴谋、暗示私德问题来增强戏剧性。
- 可以评价人物贡献、争议、局限和时代位置，但必须基于事实，不做人格攻击，不做未经证实的道德推断。
- 不要空话，不要学术腔，不要翻译腔，不要做心理臆测，不要使用比喻修辞。
- 不要使用“不是……而是……”句式，不要使用破折号——
- 允许你自行组织结构，不必预设固定章节层级。
- 最终正文长度控制在12000字以上，允许小幅缩减，但是没有上限。
- 所有书名、目录标题和正文章节标题都不要出现冒号（包括中文冒号和英文冒号）。
- 正文最后一章不要命名为“结语”，也不要出现“结语”两个字作为章节标题；把它作为普通章节自然收束即可。

输入：
[原始视频文稿]
{raw_text}

请直接输出完整书稿。"""


BIOGRAPHY_FACT_CHECK_PROMPT = """你是一名人物传记类非虚构编辑，负责事实核查、风险审读和事实修订。

任务：检查下面这篇人物传记书稿中是否存在不实、夸大、造谣、抹黑、未经证实的判断或过度心理描写，并在不改变文章整体结构和写作风格的前提下，删除虚假内容，用公开可信资料和可核验事实进行替换。

请重点检查：
1. 是否编造了人物经历、年份、地点、作品、职位、奖项、关系、病情、死因、经济状况。
2. 是否把传闻、野史、营销号说法写成了确定事实。
3. 是否存在故意抹黑、人格攻击、道德审判。
4. 是否存在未经证实的私生活内容。
5. 是否存在心理臆测，例如“他内心一定”“她早就知道”“他其实嫉妒”等。
6. 是否存在过度戏剧化、阴谋化、丑闻化表达。
7. 是否存在来源不足却语气肯定的判断。
8. 是否对在世人物写入高风险负面信息。

修订原则：
- 对确定为虚假、无可靠来源、无法核实的内容，直接删除。
- 对表述过满但可能有事实依据的内容，改成更审慎的表达。
- 对错误事实，用公开可信资料、权威传记、官方网站、博物馆/纪念馆资料、出版社资料、学术资料、主流媒体报道等可核验信息替换。
- 不得为了补足篇幅而自行编造新情节、新对话、新心理活动或新细节。
- 不得使用野史、营销号、短视频文案、论坛传闻、未经核实的网文作为事实依据。
- 如果找不到可靠资料支撑某个细节，宁可删除，也不要保留。
- 对存在争议的内容，不要强行定论，应改为“关于这一点，资料存在不同说法”“目前较可靠的资料只能确认……”。
- 对在世人物尤其谨慎，不写未经证实的负面信息，不暗示违法、出轨、欺诈、操控、背叛等严重指控。
- 保留 @最终书名、@最终简介、@目录、@导言、@正文 结构，不要输出解释。

【待审书稿】
{text}
"""


BIOGRAPHY_GEMINI_FAST_POLISH_PROMPT = """请把下面的人物传记文字重写成自然流畅、适合大众阅读的现代汉语。文字要自然、清楚、有温度，但不要煽情，不要掉书袋，不要太学术，不要翻译腔。

【字数与内容保护（重要，违反等于任务失败）】
- 这是一润，不是改写故事。目标只是让文字更自然，不是删减、不是缩写、不是总结。
- 不得新增任何人物经历、年份、地点、关系、作品、奖项、职位、病情、死因、争议和私生活细节。
- 不得把不确定信息改成确定语气，不得加入心理臆测，不得把人物写得更伟大、更悲惨、更阴暗或更传奇。
- 输出字数必须与原文字数基本一致（允许 ±5% 浮动）。绝不允许删段、删句、合并段落、省略例子。
- 原文中的所有具体例子、数据、人名、地名、机构名、案例细节，必须 100% 全部保留。
- 如果某段已经够自然，原样返回即可，不要为了"显得做了工作"而改写。
- 不要输出任何解释、注释、前言或总结，直接输出润色后的全文。
"""


BIOGRAPHY_GEMINI_FAST_REFINE_PROMPT = """二次润色（微调）。这是"微调"，不是"精简"，更不是"压缩"。

【字数硬约束（违反等于任务失败）】
- 输出字数必须与原文字数基本一致，允许 ±10% 浮动；严禁把 8000 字砍成 3000 字这样的大幅删减。
- 不得以"前文已涉及"为由删除任何具体例子、案例、数据、细节、独立段落或独立章节。
- 章节数量、章节标题、章节内段落数量必须全部保留，不得合并章节、不得砍段。

【需要完成的工作】
1. 仅删除"字面完全重复"的句子：同一句话重复出现两次以上时，去掉重复出现的那一份。除此之外的任何"语义近似""主题相关""观点相同"都不算重复，必须保留。
2. 对少量措辞做自然微调，保持原意不变，不要引入新的说法。
3. 修改章节序号，将【第N章：】改为【N.】。
4. 在不删减内容的前提下重新分段，每段 50~100 字；如内容偏多或逻辑紧密不适合分段，可宽限到 200 字。

【输出要求】
- 直接输出微调后的全文，不要解释，不要总结，不要省略号代替正文。
"""


LIFE_PHILOSOPHY_GEMINI_FAST_POLISH_PROMPT = """将我的文本重新表达为自然流畅的现代汉语，不要掉书袋，对大众读者友好。

【字数与内容保护（重要，违反等于任务失败）】
- 这是一润，目标只是让文字更自然，不是删减、不是缩写、不是总结。
- 输出字数必须与原文字数基本一致（允许 ±5% 浮动）。绝不允许删段、删句、合并段落、省略例子。
- 原文中的所有具体例子、数据、人名、地名、机构名、案例细节，必须 100% 全部保留。
- 不得新增内容，也不得删除内容；只允许在句子级别替换为更自然的表达。
- 如果某段已经够自然，原样返回即可，不要为了"显得做了工作"而改写。
- 不要输出任何解释、注释、前言或总结，直接输出润色后的全文。
"""


LIFE_PHILOSOPHY_GEMINI_FAST_REFINE_PROMPT = """二次润色（微调）。这是"微调"，不是"精简"，更不是"压缩"。

【字数硬约束（违反等于任务失败）】
- 输出字数必须与原文字数基本一致，允许 ±10% 浮动；严禁把 8000 字砍成 3000 字这样的大幅删减。
- 不得以"前文已涉及"为由删除任何具体例子、案例、数据、细节、独立段落或独立章节。
- 章节数量、章节标题、章节内段落数量必须全部保留，不得合并章节、不得砍段。

【需要完成的工作】
1. 仅删除"字面完全重复"的句子：同一句话重复出现两次以上时，去掉重复出现的那一份。除此之外的任何"语义近似""主题相关""观点相同"都不算重复，必须保留。严禁使用"如前文所述"这类一笔带过的写法去合并段落。
2. 对少量措辞做自然微调，保持原意不变，不要引入新的说法。
3. 修改章节序号，将【第N章：】改为【N.】。
4. 在不删减内容的前提下重新分段，每段 50~100 字；如内容偏多或逻辑紧密不适合分段，可宽限到 200 字。

【输出要求】
- 直接输出微调后的全文，不要解释，不要总结，不要省略号代替正文。
"""


CLASSICAL_CHINESE_FULL_BOOK_WRITER_PROMPT = """你是一名古文白话转换编辑，擅长把文言文转成准确、自然、易读的现代汉语。

任务：根据用户粘贴的文言文原文，直接生成一份白话转换稿。这个题材不处理视频链接，不调用轻抖文案解析。

输出要求：
- 严格按以下顺序输出，不得打乱：
  1. @最终书名
  2. @最终简介
  3. @目录
  4. @导言
  5. @正文
- @正文 下必须包含若干章节，建议结构为：原文整理、白话译文、疑难词句、阅读提示。若原文很短，可以合并为 2~3 个章节。
- 白话译文要忠实原意，不强行发挥，不把古人意思改成现代鸡汤。
- 遇到典故、人名、地名、制度名，可以在疑难词句中做简明解释；无法确认的地方要保守表达。
- 不要使用轻浮网感，不要过度口语化，不要加入原文没有的故事情节。
- 只输出成稿，不要解释，不要代码块，不要元信息，不要分析过程。

输入：
[文言文原文]
{raw_text}

请直接输出完整白话转换稿。"""


def _prompt_set(
    full_book_writer: str,
    *,
    polish: str | None = None,
    refine: str | None = None,
    formatter: str | None = None,
    post_writer_audit: str | None = None,
) -> dict[str, str]:
    data = {
        "full_book_writer": full_book_writer,
        "gemini_fast_polish": polish or GEMINI_FAST_POLISH_PROMPT,
        "gemini_fast_refine": refine or GEMINI_FAST_REFINE_PROMPT,
        "deepseek_v4_pro_format": formatter or DEEPSEEK_V4_PRO_FORMAT_PROMPT,
    }
    if post_writer_audit:
        data["post_writer_audit"] = post_writer_audit
    return data


PROMPT_REGISTRY: dict[str, dict[str, str]] = {
    "social_science": _prompt_set(SOCIAL_SCIENCE_FULL_BOOK_WRITER_PROMPT),
    "life_philosophy": _prompt_set(
        LIFE_PHILOSOPHY_FULL_BOOK_WRITER_PROMPT,
        polish=LIFE_PHILOSOPHY_GEMINI_FAST_POLISH_PROMPT,
        refine=LIFE_PHILOSOPHY_GEMINI_FAST_REFINE_PROMPT,
    ),
    "popular_science": _prompt_set(POPULAR_SCIENCE_FULL_BOOK_WRITER_PROMPT),
    "wisdom_stories": _prompt_set(WISDOM_STORIES_FULL_BOOK_WRITER_PROMPT),
    "biography": _prompt_set(
        BIOGRAPHY_FULL_BOOK_WRITER_PROMPT,
        polish=BIOGRAPHY_GEMINI_FAST_POLISH_PROMPT,
        refine=BIOGRAPHY_GEMINI_FAST_REFINE_PROMPT,
        post_writer_audit=BIOGRAPHY_FACT_CHECK_PROMPT,
    ),
    "history_culture": _prompt_set(HISTORY_CULTURE_FULL_BOOK_WRITER_PROMPT),
    "emotion_relationship": _prompt_set(EMOTION_RELATIONSHIP_FULL_BOOK_WRITER_PROMPT),
    "psychology_self_help": _prompt_set(PSYCHOLOGY_SELF_HELP_FULL_BOOK_WRITER_PROMPT),
    "suspense_micro_reasoning": _prompt_set(SUSPENSE_MICRO_REASONING_FULL_BOOK_WRITER_PROMPT),
    "classical_chinese": _prompt_set(CLASSICAL_CHINESE_FULL_BOOK_WRITER_PROMPT),
    "health_wellness": _prompt_set(HEALTH_WELLNESS_FULL_BOOK_WRITER_PROMPT),
    "classic_interpretation": _prompt_set(CLASSIC_INTERPRETATION_FULL_BOOK_WRITER_PROMPT),
    "web_short_story": _prompt_set(WEB_SHORT_STORY_FULL_BOOK_WRITER_PROMPT),
}

SUPPORTED_CATEGORIES: tuple[str, ...] = tuple(PROMPT_REGISTRY.keys())
CATEGORY_ALIASES: dict[str, str] = {
    "social_pain": "social_science",
    "social": "social_science",
    "workplace_survival": "social_science",
    "职场生存": "social_science",
    "guwen": "classical_chinese",
    "ancient_chinese": "classical_chinese",
    "文言文": "classical_chinese",
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
