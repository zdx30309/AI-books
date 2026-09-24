export type ThemeId =
  | "dark-gold"
  | "graphite"
  | "midnight"
  | "amethyst"
  | "aurora"
  | "pine"
  | "ink-red"
  | "obsidian"
  | "amber-dawn"
  | "paper-brown"
  | "porcelain"
  | "mist-blue"
  | "bay-green"
  | "sakura";

export type ThemeMode = "dark" | "light";

export type ThemeOption = {
  id: ThemeId;
  name: string;
  description: string;
  mode: ThemeMode;
  /** Preview colours for the picker: page, surface, accent, ink. */
  preview: { bg: string; surface: string; accent: string; ink: string };
};

export const DEFAULT_THEME_ID: ThemeId = "dark-gold";

export const THEMES: ThemeOption[] = [
  {
    id: "dark-gold",
    name: "墨金夜色",
    description: "默认 · 暖墨底色配香槟金",
    mode: "dark",
    preview: { bg: "#0c0b09", surface: "#1c1916", accent: "#d6ac6e", ink: "#eee8de" },
  },
  {
    id: "graphite",
    name: "石墨银砂",
    description: "中性深灰，弱化装饰",
    mode: "dark",
    preview: { bg: "#0b0c0e", surface: "#1a1c20", accent: "#b2a58c", ink: "#eceef1" },
  },
  {
    id: "midnight",
    name: "午夜靛蓝",
    description: "深海蓝底，长夜写作不刺眼",
    mode: "dark",
    preview: { bg: "#090c16", surface: "#14192b", accent: "#818cf8", ink: "#e6e9f5" },
  },
  {
    id: "amethyst",
    name: "暮紫",
    description: "低饱和紫调，安静有氛围",
    mode: "dark",
    preview: { bg: "#0f0b14", surface: "#201829", accent: "#c08efa", ink: "#eee8f4" },
  },
  {
    id: "aurora",
    name: "极光夜航",
    description: "青绿科技感，适合数据台",
    mode: "dark",
    preview: { bg: "#060d12", surface: "#0e1d25", accent: "#2dd4bf", ink: "#e2f4f1" },
  },
  {
    id: "pine",
    name: "松烟",
    description: "墨绿底色配鼠尾草绿",
    mode: "dark",
    preview: { bg: "#090e0b", surface: "#151d18", accent: "#74ba96", ink: "#e6eee8" },
  },
  {
    id: "ink-red",
    name: "朱砂墨卷",
    description: "古典深色，中式文稿气质",
    mode: "dark",
    preview: { bg: "#0d0909", surface: "#1f1513", accent: "#e26844", ink: "#f4eae0" },
  },
  {
    id: "obsidian",
    name: "黑曜",
    description: "纯黑单色，极简高对比",
    mode: "dark",
    preview: { bg: "#040404", surface: "#141414", accent: "#f0f0f0", ink: "#ededed" },
  },
  {
    id: "amber-dawn",
    name: "琥珀晨光",
    description: "默认主题的日间版",
    mode: "light",
    preview: { bg: "#f6f2eb", surface: "#fffdf9", accent: "#a87028", ink: "#211b15" },
  },
  {
    id: "paper-brown",
    name: "纸页暖棕",
    description: "纸感浅色配暖棕按钮",
    mode: "light",
    preview: { bg: "#f8f6f1", surface: "#fffdf9", accent: "#9a5623", ink: "#372619" },
  },
  {
    id: "porcelain",
    name: "素瓷",
    description: "纯白黑墨，编辑部式极简",
    mode: "light",
    preview: { bg: "#f7f7f6", surface: "#ffffff", accent: "#17171a", ink: "#171717" },
  },
  {
    id: "mist-blue",
    name: "清雾蓝白",
    description: "轻量办公感，适合表格工具",
    mode: "light",
    preview: { bg: "#f5f8fc", surface: "#ffffff", accent: "#2563eb", ink: "#182539" },
  },
  {
    id: "bay-green",
    name: "森庭绿",
    description: "安静护眼，适合长时间写作",
    mode: "light",
    preview: { bg: "#f2f7f2", surface: "#fcfffa", accent: "#1a8a48", ink: "#1e3123" },
  },
  {
    id: "sakura",
    name: "樱绢",
    description: "柔和浅粉，内容编辑氛围",
    mode: "light",
    preview: { bg: "#fdf7f9", surface: "#fffcfd", accent: "#c63e5d", ink: "#3e2330" },
  },
];

export const THEME_GROUPS: { mode: ThemeMode; label: string }[] = [
  { mode: "dark", label: "深色" },
  { mode: "light", label: "浅色" },
];

export function isThemeId(value: unknown): value is ThemeId {
  return typeof value === "string" && THEMES.some((theme) => theme.id === value);
}

export function getThemeById(id: ThemeId): ThemeOption {
  return THEMES.find((theme) => theme.id === id) ?? THEMES[0];
}
