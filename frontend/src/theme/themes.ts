export type ThemeId =
  | "dark-gold"
  | "paper-brown"
  | "mist-blue"
  | "graphite"
  | "bay-green"
  | "sakura"
  | "aurora"
  | "ink-red";

export type ThemeGroup = "保留现有" | "通用主题" | "个性主题";

export type ThemeOption = {
  id: ThemeId;
  name: string;
  description: string;
  group: ThemeGroup;
  swatches: string[];
};

export const DEFAULT_THEME_ID: ThemeId = "dark-gold";

export const THEMES: ThemeOption[] = [
  {
    id: "dark-gold",
    name: "墨金夜色",
    description: "保留当前深色金边风格",
    group: "保留现有",
    swatches: ["#0a0908", "#d4a874", "#f5f0e8"],
  },
  {
    id: "paper-brown",
    name: "纸页暖棕",
    description: "参考图的浅色纸感与暖棕按钮",
    group: "通用主题",
    swatches: ["#f8f6f1", "#9a5623", "#372619"],
  },
  {
    id: "mist-blue",
    name: "清雾蓝白",
    description: "轻量办公感，适合表格与工具页",
    group: "通用主题",
    swatches: ["#f5f8fc", "#3b82f6", "#182539"],
  },
  {
    id: "graphite",
    name: "石墨银砂",
    description: "中性深色，弱化装饰感",
    group: "通用主题",
    swatches: ["#0c0d0f", "#aa9167", "#f1f2f4"],
  },
  {
    id: "bay-green",
    name: "森庭绿",
    description: "安静护眼，适合长时间写作",
    group: "个性主题",
    swatches: ["#f2f7f2", "#229954", "#1e3123"],
  },
  {
    id: "sakura",
    name: "樱绢",
    description: "柔和浅色，偏内容编辑氛围",
    group: "个性主题",
    swatches: ["#fdf7f9", "#e15974", "#3e2330"],
  },
  {
    id: "aurora",
    name: "极光夜航",
    description: "科技深色，适合数据和长篇工作台",
    group: "个性主题",
    swatches: ["#070c12", "#2dd4bf", "#edfcf9"],
  },
  {
    id: "ink-red",
    name: "朱砂墨卷",
    description: "古典深色，偏中式文稿气质",
    group: "个性主题",
    swatches: ["#0c0808", "#cd5c31", "#fcf2e8"],
  },
];

export function isThemeId(value: unknown): value is ThemeId {
  return typeof value === "string" && THEMES.some((theme) => theme.id === value);
}

export function getThemeById(id: ThemeId): ThemeOption {
  return THEMES.find((theme) => theme.id === id) ?? THEMES[0];
}
