import { NavLink } from "react-router-dom";
import {
  BarChart3,
  BookMarked,
  BookOpenCheck,
  FileSpreadsheet,
  Images,
  LayoutGrid,
  LogOut,
  User,
} from "lucide-react";
import { useAuth } from "@/auth/AuthContext";

const navItems = [
  { to: "/", label: "工作台", icon: LayoutGrid, end: true },
  { to: "/bookshelf", label: "书架", icon: BookMarked, end: true },
  { to: "/bookshelf/longform-variants", label: "长文新版书架", icon: BookOpenCheck, end: false },
  { to: "/long-novel", label: "长篇小说", icon: BookOpenCheck, end: false },
  { to: "/epic-novel", label: "Epic Novel", icon: BookOpenCheck, end: false },
  { to: "/covers", label: "封面书架", icon: Images, end: false },
  { to: "/weread", label: "推荐值工具", icon: FileSpreadsheet, end: false },
  { to: "/data-analysis", label: "数据分析", icon: BarChart3, end: false },
  { to: "/profile", label: "个人中心", icon: User, end: false },
];

export function Sidebar() {
  const { user, logout } = useAuth();

  return (
    <aside className="fixed bottom-0 left-0 top-0 z-30 flex w-[220px] flex-col border-r border-gold-500/8 bg-base-800/70 backdrop-blur-md">
      <div className="px-7 py-8">
        <div className="font-serif text-[22px] tracking-wide text-warm-100">
          AI <span className="text-gold-300">ScriptBook</span>
        </div>
        <div className="mt-2 text-[11px] tracking-widest text-warm-400">
          让视频思想，成为你的书
        </div>
      </div>

      <nav className="flex-1 px-4">
        {navItems.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              `nav-item ${isActive ? "nav-item-active" : ""} mb-1`
            }
          >
            <Icon className="h-[18px] w-[18px]" strokeWidth={1.6} />
            <span className="text-sm tracking-wide">{label}</span>
          </NavLink>
        ))}

        {user && (
          <button
            type="button"
            onClick={logout}
            className="nav-item mt-1 w-full text-left"
          >
            <LogOut className="h-[18px] w-[18px]" strokeWidth={1.6} />
            <span className="text-sm tracking-wide">退出登录</span>
          </button>
        )}
      </nav>

      <div className="px-7 pb-8 pt-6 text-center">
        <div className="mx-auto mb-4 h-12 w-12 opacity-30">
          <svg viewBox="0 0 48 48" fill="none" className="h-full w-full">
            <path
              d="M24 4 C16 12, 12 22, 12 30 C12 38, 18 44, 24 44 C30 44, 36 38, 36 30 C36 22, 32 12, 24 4 Z"
              stroke="currentColor"
              className="text-gold-400"
              strokeWidth="1"
              fill="none"
            />
            <circle cx="38" cy="14" r="1.5" className="fill-gold-300" />
            <circle cx="10" cy="20" r="1" className="fill-gold-300" />
            <circle cx="42" cy="32" r="1" className="fill-gold-300" />
          </svg>
        </div>
        <div className="space-y-1 text-[11px] leading-relaxed tracking-widest text-warm-400">
          <div>在碎片的世界里</div>
          <div>把想象写成书</div>
          <div>让时间留住价值</div>
        </div>
      </div>
    </aside>
  );
}
