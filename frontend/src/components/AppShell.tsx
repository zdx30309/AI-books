import { useCallback, useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { Menu, Search } from "lucide-react";
import { navItemForPath } from "@/navigation";
import { BrandMark } from "./BrandMark";
import { CommandPalette } from "./CommandPalette";
import { Sidebar } from "./Sidebar";

const COLLAPSED_KEY = "video-plan-sidebar-collapsed";

export function AppShell() {
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(() => window.localStorage.getItem(COLLAPSED_KEY) === "1");
  const [mobileOpen, setMobileOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);

  const toggleCollapsed = useCallback(() => {
    setCollapsed((value) => {
      window.localStorage.setItem(COLLAPSED_KEY, value ? "0" : "1");
      return !value;
    });
  }, []);

  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((value) => !value);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const current = navItemForPath(location.pathname);
  // Remount (and animate) only when moving between sections, so pages that
  // push a sub-path of their own section keep their in-memory state.
  const sectionKey = location.pathname.split("/")[1] || "home";

  return (
    <div className="min-h-screen">
      <Sidebar
        collapsed={collapsed}
        onToggleCollapsed={toggleCollapsed}
        mobileOpen={mobileOpen}
        onCloseMobile={() => setMobileOpen(false)}
        onOpenCommand={() => setPaletteOpen(true)}
      />
      {mobileOpen && (
        <div className="app-overlay fixed inset-0 z-30 lg:hidden" onClick={() => setMobileOpen(false)} aria-hidden="true" />
      )}

      <div
        className={`min-h-screen transition-[padding] duration-300 ease-out-expo ${
          collapsed ? "lg:pl-[76px]" : "lg:pl-[248px]"
        }`}
      >
        {/* 移动端顶栏 */}
        <header className="sticky top-0 z-20 flex h-14 items-center gap-2 border-b border-warm-100/[0.07] bg-base-900/80 px-3 backdrop-blur-xl lg:hidden">
          <button type="button" className="btn-icon" onClick={() => setMobileOpen(true)} aria-label="打开导航">
            <Menu className="h-5 w-5" strokeWidth={1.8} />
          </button>
          <BrandMark size={24} />
          <span className="truncate text-sm font-medium text-warm-100">{current?.label ?? "ScriptBook"}</span>
          <button
            type="button"
            className="btn-icon ml-auto"
            onClick={() => setPaletteOpen(true)}
            aria-label="搜索或跳转"
          >
            <Search className="h-[18px] w-[18px]" strokeWidth={1.8} />
          </button>
        </header>

        <main className="relative mx-auto w-full max-w-[1440px] px-4 pb-20 pt-6 sm:px-6 lg:px-10 lg:pt-10">
          <div key={sectionKey} className="animate-page-in">
            <Outlet />
          </div>
        </main>
      </div>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </div>
  );
}
