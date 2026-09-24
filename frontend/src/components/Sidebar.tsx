import { NavLink } from "react-router-dom";
import { LogOut, PanelLeftClose, PanelLeftOpen, Search, X } from "lucide-react";
import { useAuth } from "@/auth/AuthContext";
import { NAV_GROUPS } from "@/navigation";
import { BrandMark, BrandWordmark } from "./BrandMark";
import { ThemeSwitcher } from "./ThemeSwitcher";
import { UserBadge } from "./UserBadge";

interface Props {
  collapsed: boolean;
  onToggleCollapsed: () => void;
  mobileOpen: boolean;
  onCloseMobile: () => void;
  onOpenCommand: () => void;
}

const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
export const COMMAND_SHORTCUT = isMac ? "⌘K" : "Ctrl K";

export function Sidebar({ collapsed, onToggleCollapsed, mobileOpen, onCloseMobile, onOpenCommand }: Props) {
  const { user, logout } = useAuth();
  // The mobile drawer always shows labels; only the desktop rail collapses.
  const rail = collapsed && !mobileOpen;

  return (
    <aside
      className={`fixed inset-y-0 left-0 z-40 flex w-[264px] flex-col border-r border-warm-100/[0.07] bg-base-900/90 backdrop-blur-xl transition-[width,transform] duration-300 ease-out-expo lg:bg-base-900/70 ${
        mobileOpen ? "translate-x-0 shadow-float" : "-translate-x-full"
      } lg:translate-x-0 ${collapsed ? "lg:w-[76px]" : "lg:w-[248px]"}`}
      aria-label="主导航"
    >
      {/* 品牌 */}
      <div className={`flex h-16 shrink-0 items-center gap-2.5 ${rail ? "justify-center px-0" : "px-5"}`}>
        <NavLink to="/" className="flex min-w-0 items-center gap-2.5" aria-label="ScriptBook 工作台">
          <BrandMark size={30} />
          {!rail && <BrandWordmark />}
        </NavLink>
        {!rail && (
          <>
            <button
              type="button"
              onClick={onToggleCollapsed}
              className="btn-icon ml-auto hidden h-8 w-8 lg:inline-flex"
              title="收起侧栏"
              aria-label="收起侧栏"
            >
              <PanelLeftClose className="h-4 w-4" strokeWidth={1.7} />
            </button>
            <button
              type="button"
              onClick={onCloseMobile}
              className="btn-icon ml-auto h-8 w-8 lg:hidden"
              aria-label="关闭导航"
            >
              <X className="h-4 w-4" strokeWidth={1.8} />
            </button>
          </>
        )}
      </div>

      {/* 搜索 / 命令 */}
      <div className={`shrink-0 pb-3 ${rail ? "px-3" : "px-4"}`}>
        <button
          type="button"
          onClick={onOpenCommand}
          className={`flex h-9 w-full items-center gap-2 rounded-lg border border-warm-100/[0.08] bg-warm-100/[0.03] text-[13px] text-warm-400 transition-colors hover:border-warm-100/15 hover:text-warm-200 ${
            rail ? "justify-center" : "px-3"
          }`}
          title={rail ? `搜索或跳转（${COMMAND_SHORTCUT}）` : undefined}
        >
          <Search className="h-4 w-4 shrink-0" strokeWidth={1.8} />
          {!rail && (
            <>
              <span className="flex-1 text-left">搜索或跳转</span>
              <span className="kbd">{COMMAND_SHORTCUT}</span>
            </>
          )}
        </button>
      </div>

      {/* 导航 */}
      <nav className={`min-h-0 flex-1 overflow-y-auto pb-4 ${rail ? "px-3" : "px-3"}`}>
        {NAV_GROUPS.map((group, index) => (
          <div key={group.id} className={index === 0 ? "" : "mt-4"}>
            {rail ? (
              index > 0 && <div className="mx-2 mb-3 h-px bg-warm-100/[0.07]" />
            ) : (
              <div className="mb-1 px-3 text-[10.5px] font-medium tracking-[0.2em] text-warm-500">{group.label}</div>
            )}
            <ul className="space-y-0.5">
              {group.items.map(({ to, label, icon: Icon, end }) => (
                <li key={to}>
                  <NavLink
                    to={to}
                    end={end}
                    title={rail ? label : undefined}
                    className={({ isActive }) =>
                      `nav-item ${isActive ? "nav-item-active" : ""} ${rail ? "justify-center px-0" : ""}`
                    }
                  >
                    {({ isActive }) => (
                      <>
                        <Icon
                          className={`h-[17px] w-[17px] shrink-0 ${isActive ? "text-gold-300" : ""}`}
                          strokeWidth={1.7}
                        />
                        {!rail && <span className="truncate">{label}</span>}
                      </>
                    )}
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>

      {/* 底部：主题 + 账户 */}
      <div className={`hairline-t shrink-0 space-y-1 py-3 ${rail ? "px-3" : "px-3"}`}>
        <ThemeSwitcher collapsed={rail} />
        {rail && (
          <button
            type="button"
            onClick={onToggleCollapsed}
            className="nav-item hidden w-full justify-center px-0 lg:flex"
            title="展开侧栏"
            aria-label="展开侧栏"
          >
            <PanelLeftOpen className="h-[17px] w-[17px]" strokeWidth={1.7} />
          </button>
        )}
        {user && (
          <div className={`flex items-center gap-1 ${rail ? "flex-col" : ""}`}>
            <UserBadge collapsed={rail} />
            <button
              type="button"
              onClick={logout}
              className="btn-icon h-8 w-8"
              title="退出登录"
              aria-label="退出登录"
            >
              <LogOut className="h-4 w-4" strokeWidth={1.7} />
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}
