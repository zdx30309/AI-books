import type { LucideIcon } from "lucide-react";
import {
  BarChart3,
  BookOpen,
  Castle,
  FileCheck2,
  FileSpreadsheet,
  FlaskConical,
  Images,
  Languages,
  LayoutDashboard,
  Library,
  LibraryBig,
  ScrollText,
  ShieldCheck,
  Table2,
} from "lucide-react";

export type NavItem = {
  to: string;
  label: string;
  icon: LucideIcon;
  /** NavLink `end`: only the exact path counts as active. */
  end: boolean;
  /** Extra words the command palette matches on. */
  keywords?: string;
};

export type NavGroup = {
  id: string;
  label: string;
  items: NavItem[];
};

export const NAV_GROUPS: NavGroup[] = [
  {
    id: "create",
    label: "创作",
    items: [
      { to: "/", label: "工作台", icon: LayoutDashboard, end: true, keywords: "首页 概览 home" },
      { to: "/bookshelf", label: "书架", icon: Library, end: true, keywords: "书稿 全部 下载" },
      { to: "/bookshelf/longform-variants", label: "长文新版书架", icon: LibraryBig, end: false, keywords: "新版 长文" },
    ],
  },
  {
    id: "longform",
    label: "长篇",
    items: [
      { to: "/long-novel", label: "长篇小说", icon: BookOpen, end: false, keywords: "家族 妯娌 小说" },
      { to: "/epic-novel", label: "史诗长篇", icon: Castle, end: false, keywords: "网文 三百万字 epic" },
      { to: "/public-domain-rewrite", label: "公版融合长篇", icon: ScrollText, end: true, keywords: "公版 改写 融合" },
      { to: "/public-domain-classical-vernacular", label: "公版古文白话", icon: Languages, end: false, keywords: "古文 文言 白话 翻译" },
    ],
  },
  {
    id: "publish",
    label: "出版与审校",
    items: [
      { to: "/covers", label: "封面书架", icon: Images, end: false, keywords: "封面 图片 批量" },
      { to: "/aigc-review", label: "AI 审稿降率", icon: ShieldCheck, end: true, keywords: "朱雀 降AI 审稿" },
      { to: "/aigc-review/bookshelf", label: "AI 审稿书架", icon: FileCheck2, end: false, keywords: "审稿 归档" },
    ],
  },
  {
    id: "tools",
    label: "工具",
    items: [
      { to: "/weread", label: "推荐值工具", icon: FileSpreadsheet, end: false, keywords: "微信读书 推荐值 番茄" },
      { to: "/video-script-table", label: "视频文案对照表", icon: Table2, end: false, keywords: "金山 对照 标题 回填" },
      { to: "/data-analysis", label: "数据分析", icon: BarChart3, end: false, keywords: "表格 excel 统计" },
      { to: "/testing-endpoint", label: "测试端点", icon: FlaskConical, end: true, keywords: "testing endpoint 实验" },
    ],
  },
];

export const NAV_ITEMS: NavItem[] = NAV_GROUPS.flatMap((group) => group.items);

/** Title for the mobile top bar: the most specific nav item matching the path. */
export function navItemForPath(pathname: string): NavItem | undefined {
  return [...NAV_ITEMS]
    .sort((a, b) => b.to.length - a.to.length)
    .find((item) => (item.to === "/" ? pathname === "/" : pathname === item.to || pathname.startsWith(`${item.to}/`)));
}
