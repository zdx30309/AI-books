import type { TaskCategoryOption } from "@/api/types";

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
    id: "life_philosophy",
    label: "人生哲学",
    core_feature: "提供认知升级或心灵慰藉",
    directions: "励志、成长、价值观、生活态度",
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
    id: "wisdom_stories",
    label: "小故事·智慧",
    core_feature: "以故事承载哲理，易传播",
    directions: "佛学、禅宗、寓言、民间智慧",
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
    id: "health_wellness",
    label: "健康·养生",
    core_feature: "轻科普，可立即行动",
    directions: "睡眠、运动、饮食、放松、预防",
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
    id: "web_short_story",
    label: "历史测试版",
    core_feature: "与历史小说端口同步，用于历史叙事测试",
    directions: "历史故事、制度缝隙、人物命运、时代暗流",
    prompt_status: "ready",
  },
];

export const DEFAULT_COVER_CATEGORIES: TaskCategoryOption[] = [
  ...DEFAULT_CATEGORIES,
  {
    id: "illustration",
    label: "分类插画",
    core_feature: "插画封面入口，默认复用心理自助封面 prompt",
    directions: "心理自助、情绪疗愈、插画风封面",
    prompt_status: "ready",
  },
  {
    id: "illustration_crayon",
    label: "分类插画（蜡笔）",
    core_feature: "温柔情绪插画封面，强调蜡笔质感与治愈氛围",
    directions: "情绪日记、心理自助、孤独感、成年人治愈读物",
    prompt_status: "ready",
  },
];

export function categoryLabelFor(
  categoryId: string | null | undefined,
  categories: TaskCategoryOption[] = DEFAULT_CATEGORIES,
) {
  if (!categoryId) return null;
  return categories.find((item) => item.id === categoryId)?.label ?? null;
}

export function categoryIdForLabel(
  label: string | null | undefined,
  categories: TaskCategoryOption[] = DEFAULT_CATEGORIES,
) {
  if (!label) return null;
  return categories.find((item) => item.label === label)?.id ?? null;
}
