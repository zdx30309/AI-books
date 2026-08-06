import type { TaskCategoryOption } from "@/api/types";

const CATEGORY_LABEL_RENAMES: Record<string, string> = {
  "分类插画": "饱和插画",
  "治愈插画": "饱和插画",
  "分类插画（蜡笔）": "萌系治愈",
  "分类插画(蜡笔)": "萌系治愈",
  "治愈插画（蜡笔）": "萌系治愈",
  "治愈插画(蜡笔)": "萌系治愈",
  "治愈插画（蜡笔)": "萌系治愈",
  "文学": "正统出版",
  "心理认知类": "正统测试1",
  "文学类": "正统测试2",
  "社科类": "正统测试3",
};

export const DEFAULT_CATEGORIES: TaskCategoryOption[] = [
  {
    id: "social_science",
    label: "社会痛点",
    core_feature: "直面现实困境，高共鸣",
    directions: "教育、婚姻、住房、职场压力、养老",
    prompt_status: "ready",
  },
  {
    id: "social_science_test",
    label: "社会痛点测试版",
    core_feature: "与社会痛点端口同步，用于现实议题测试",
    directions: "教育、婚姻、住房、职场压力、养老",
    prompt_status: "ready",
  },
  {
    id: "social_science_core_conflict",
    label: "社会痛点（核心矛盾版）",
    core_feature:
      "提炼原文最值得展开的核心矛盾，围绕压力、代价、阶层、关系和选择重搭长文结构",
    directions: "教育、婚姻、住房、职场压力、养老、阶层流动、普通人选择",
    prompt_status: "ready",
  },
  {
    id: "life_philosophy",
    label: "人生哲学",
    core_feature: "提供认知升级或心灵慰藉",
    directions: "励志、成长、价值观、生活态度",
    prompt_status: "ready",
  },
  {
    id: "life_philosophy_self_rescue",
    label: "人生哲学（困境自救版）",
    core_feature:
      "从具体生活困境切入，写清反复被困住的原因以及判断、边界、节奏和选择的重排",
    directions: "清醒、取舍、边界、长期主义、内耗、比较、关系消耗、自我负责",
    prompt_status: "ready",
  },
  {
    id: "popular_science",
    label: "科普读物",
    core_feature: "把复杂知识讲明白",
    directions: "AI、经济、投资、科技、名人思想",
    prompt_status: "ready",
  },
  {
    id: "popular_science_cognitive_upgrade",
    label: "科普读物（认知升级版）",
    core_feature:
      "把复杂概念放回真实生活和时代变化中解释，制造原来如此和这件事与我有关的阅读体验",
    directions: "反常识问题、时代变化、认知升级、AI、经济、科技、健康与生活科学",
    prompt_status: "ready",
  },
  {
    id: "finance_technology",
    label: "财经·科技",
    core_feature: "把钱、产业、公司、技术、政策和周期讲清楚",
    directions: "宏观经济、产业周期、公司动作、技术变革、资产价格、政策影响、普通人机会误判",
    prompt_status: "ready",
  },
  {
    id: "finance_technology_ai_optimized",
    label: "财经科技（优化AI味版）",
    core_feature: "把钱、产业、公司、技术、政策和周期讲清楚",
    directions: "宏观经济、产业周期、公司动作、技术变革、资产价格、政策影响、普通人机会误判",
    prompt_status: "ready",
  },
  {
    id: "finance_technology_recommendation",
    label: "财经科技（建议版）",
    core_feature: "围绕财经科技议题讲清核心问题，并给出普通人可执行、有适用条件的建议",
    directions: "宏观经济、产业周期、公司动作、技术变革、资产价格、政策影响、普通人建议、信息判断",
    prompt_status: "ready",
  },
  {
    id: "wisdom_stories",
    label: "小故事·智慧",
    core_feature: "以故事承载哲理，易传播",
    directions: "佛学、禅宗、寓言、民间智慧",
    prompt_status: "ready",
  },
  {
    id: "wisdom_stories_causal_philosophy",
    label: "故事·智慧（因果哲思版）",
    core_feature: "从人物选择和后果里写出因果关系、命运回望与克制的人生警醒",
    directions: "因果、命运、执念、善恶、误判、得失、报应、无常、选择与代价",
    prompt_status: "ready",
  },
  {
    id: "wisdom_stories_historical",
    label: "故事·智慧类（历史故事版）",
    core_feature: "以真实历史事件、人物选择和时代规则为叙事基础，写出历史张力与人生余味",
    directions: "历史故事、人物轶事、权力、功名、忠义、野心、误判、进退、得失、盛衰、乱世选择",
    prompt_status: "ready",
  },
  {
    id: "biography",
    label: "传记·人物",
    core_feature: "浓缩人生关键节点与品格",
    directions: "名人成长、失败与转折、精神内核",
    prompt_status: "ready",
  },
  {
    id: "history_culture",
    label: "历史·文化",
    core_feature: "切片式讲述，有趣有料",
    directions: "冷知识、人物趣闻、古代生活、历史启示",
    prompt_status: "ready",
  },
  {
    id: "emotion_relationship",
    label: "情感·关系",
    core_feature: "贴近日常，治愈或实用",
    directions: "亲情、友情、爱情、社交边界",
    prompt_status: "ready",
  },
  {
    id: "emotion_relationship_unique_view",
    label: "心理情感（情感·关系）（独特观点版）",
    core_feature: "重组结构、拓展关系场景，用更成熟的分析角度写出不同于原视频的关系长文",
    directions: "讨好、冷淡、依赖、边界、控制、失望、表达、告别、亲密、信任、家庭压力、社交消耗",
    prompt_status: "ready",
  },
  {
    id: "psychology_self_help",
    label: "心理自助",
    core_feature: "方法导向，解决具体情绪问题",
    directions: "焦虑、内耗、拖延、自卑、失眠",
    prompt_status: "ready",
  },
  {
    id: "suspense_micro_reasoning",
    label: "悬疑·微推理",
    core_feature: "篇幅短小，反转或悬念",
    directions: "日常谜团、封闭场景、人性故事",
    prompt_status: "ready",
  },
  {
    id: "classical_chinese",
    label: "古文白话",
    core_feature: "文言文直译与意译并重",
    directions: "古文、家书、碑铭、史传、经典段落白话化",
    prompt_status: "ready",
  },
  {
    id: "classical_chinese_novel_style",
    label: "古文白话小说风格版",
    core_feature: "先准确理解古文，再按原文类型改成故事化白话；格言哲理也会转成小故事载体",
    directions: "古代小说、史传、志怪、笔记、故事性古文、修身格言、哲理短句、纯白话故事化改写",
    prompt_status: "ready",
  },
  {
    id: "buddhist_scripture_story",
    label: "佛经白话",
    core_feature: "把佛经经文译成自然连贯的白话故事稿，忠于原文讲述和道理",
    directions: "佛经、佛陀说法、弟子问答、因缘譬喻、经典段落白话化",
    prompt_status: "ready",
  },
  {
    id: "classical_chinese_interleaved",
    label: "古文白话（原文翻译穿插版）",
    core_feature: "每段先保留原文，再紧跟对应白话翻译",
    directions: "古文原文、逐段翻译、原文翻译穿插、古籍白话对照阅读",
    prompt_status: "ready",
  },
  {
    id: "classical_chinese_reference_rewrite",
    label: "古文白话（参照译写版）",
    core_feature: "参考已有译文的释义和术语，结合古汉语词典，重新写成自然原创白话",
    directions: "古文原文、已有白话译文参照、知名译本参照、古汉语词典校义、长篇古籍白话化",
    prompt_status: "ready",
  },
  {
    id: "comic_series",
    label: "插画",
    core_feature: "目录加文前封面和五或六辑治愈漫画绘本，短语、配图、故事成套生成",
    directions: "目录短语、出版感封面、章节配图、成人情绪漫画、治愈对话绘本",
    prompt_status: "ready",
  },
  {
    id: "healing_comic",
    label: "漫画",
    core_feature: "只输入书名和章节数，自动生成同系列原创治愈漫画",
    directions: "出版感封面、白底留白、图上短句、原创角色、同系列配图",
    prompt_status: "ready",
  },
  {
    id: "story_comic",
    label: "图文故事",
    core_feature: "原创滑动图文小故事，每章 6 至 10 张单拍卡片，以生活观察、停顿和晚揭示制造趣味",
    directions: "普通生活、单卡单拍、文字留白页、小涂鸦、自然误会、晚一点揭示",
    prompt_status: "ready",
  },
  {
    id: "homophone_comic",
    label: "谐音梗",
    core_feature: "故事先行的滑动谐音漫画，前六成藏住答案，末三张完成声音回扣",
    directions: "干巴荒诞、自嘲互损、生活观察、向上讽刺、延迟揭示、无需解释",
    prompt_status: "ready",
  },
  {
    id: "health_wellness",
    label: "健康·养生",
    core_feature: "轻科普，可立即行动",
    directions: "睡眠、运动、饮食、放松、预防",
    prompt_status: "ready",
  },
  {
    id: "health_wellness_daily_pain",
    label: "健康养生（日常痛点版）",
    core_feature: "把日常健康痛点放回真实生活场景，讲清根源、成因、边界和温和可执行调整",
    directions: "睡不好、久坐疲惫、饮食混乱、体检焦虑、压力过大、运动坚持不了",
    prompt_status: "ready",
  },
  {
    id: "classic_interpretation",
    label: "经典·解读",
    core_feature: "降低门槛，提取精华",
    directions: "名著、电影、哲学、艺术",
    prompt_status: "ready",
  },
  {
    id: "masterpiece_interpretation",
    label: "名作解读",
    core_feature: "用经典名著解释现实处境与人性选择",
    directions: "名著、文学人物、经典情节、命运转折、现实化解读",
    prompt_status: "ready",
  },
  {
    id: "web_short_story",
    label: "历史测试版",
    core_feature: "与历史小说端口同步，用于历史叙事测试",
    directions: "历史故事、制度缝隙、人物命运、时代暗流",
    prompt_status: "ready",
  },
];

export const LONGFORM_VARIANT_CATEGORY_IDS = [
  "social_science_core_conflict",
  "life_philosophy_self_rescue",
  "popular_science_cognitive_upgrade",
  "finance_technology",
  "finance_technology_ai_optimized",
  "finance_technology_recommendation",
  "wisdom_stories_causal_philosophy",
  "wisdom_stories_historical",
  "emotion_relationship_unique_view",
  "health_wellness_daily_pain",
  "buddhist_scripture_story",
  "classical_chinese_novel_style",
  "classical_chinese_interleaved",
  "classical_chinese_reference_rewrite",
] as const;

export const DEFAULT_COVER_CATEGORIES: TaskCategoryOption[] = [
  {
    ...DEFAULT_CATEGORIES.find((item) => item.id === "social_science")!,
    label: "视觉冲击",
  },
  {
    ...DEFAULT_CATEGORIES.find((item) => item.id === "finance_technology")!,
  },
  {
    id: "illustration",
    label: "饱和插画",
    core_feature: "高饱和插画封面入口，默认复用心理自助封面 prompt",
    directions: "高饱和、情绪疗愈、插画风封面",
    prompt_status: "ready",
  },
  {
    id: "illustration_crayon",
    label: "萌系治愈",
    core_feature: "温柔情绪插画封面，强调蜡笔质感与治愈氛围",
    directions: "情绪日记、心理自助、孤独感、成年人治愈读物",
    prompt_status: "ready",
  },
  {
    id: "literature",
    label: "正统出版",
    core_feature: "成熟出版封面强参考，版式真实、标题清晰、纸感干净",
    directions: "排版骨架、标题层级、留白比例、配色气质、主视觉占比",
    prompt_status: "ready",
  },
  {
    id: "psychology_cognition",
    label: "正统测试1",
    core_feature: "正统出版轻度核心元素替换测试版，强参考单张母版但替换可识别主体与装饰",
    directions: "单母版强参考、标题版式贴近、核心元素轻度替换、装饰符号重画",
    prompt_status: "ready",
  },
  {
    id: "literature_simple",
    label: "正统测试2",
    core_feature: "正统出版中度核心元素替换测试版，保留版式魂魄但主视觉与装饰系统明显换新",
    directions: "单母版强参考、标题排版贴近、主视觉中度替换、装饰体系重组",
    prompt_status: "ready",
  },
  {
    id: "social_science_simple",
    label: "正统测试3",
    core_feature: "正统出版强核心元素替换测试版，只保留版式标题色彩关系，核心视觉和装饰彻底重画",
    directions: "单母版强参考、标题排版贴近、核心元素强替换、装饰完全原创化",
    prompt_status: "ready",
  },
];

export function normalizeCategoryLabel(label: string | null | undefined) {
  if (!label) return label ?? null;
  return CATEGORY_LABEL_RENAMES[label] ?? label;
}

export function normalizeCategoryOption(
  category: TaskCategoryOption,
): TaskCategoryOption {
  return {
    ...category,
    label: normalizeCategoryLabel(category.label) ?? category.label,
  };
}

export function normalizeCategoryOptions(categories: TaskCategoryOption[]) {
  return categories.map(normalizeCategoryOption);
}

export function categoryLabelFor(
  categoryId: string | null | undefined,
  categories: TaskCategoryOption[] = DEFAULT_CATEGORIES,
) {
  if (!categoryId) return null;
  return normalizeCategoryLabel(categories.find((item) => item.id === categoryId)?.label) ?? null;
}

export function categoryIdForLabel(
  label: string | null | undefined,
  categories: TaskCategoryOption[] = DEFAULT_CATEGORIES,
) {
  if (!label) return null;
  const normalizedLabel = normalizeCategoryLabel(label);
  return (
    categories.find((item) => normalizeCategoryLabel(item.label) === normalizedLabel)?.id ??
    null
  );
}
