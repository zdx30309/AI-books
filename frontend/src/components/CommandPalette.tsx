import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { LucideIcon } from "lucide-react";
import { ArrowRight, CornerDownLeft, LogOut, Palette, Plus, Search, User } from "lucide-react";
import { useAuth } from "@/auth/AuthContext";
import { NAV_GROUPS } from "@/navigation";
import { useTheme } from "@/theme/ThemeContext";
import { THEMES } from "@/theme/themes";

type Command = {
  id: string;
  group: string;
  label: string;
  hint?: string;
  keywords: string;
  icon: LucideIcon;
  swatch?: [string, string];
  run: () => void;
};

interface Props {
  open: boolean;
  onClose: () => void;
}

export function CommandPalette({ open, onClose }: Props) {
  const navigate = useNavigate();
  const { logout } = useAuth();
  const { themeId, setThemeId } = useTheme();
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);

  const commands = useMemo<Command[]>(() => {
    const go = (to: string) => () => navigate(to);
    return [
      {
        id: "action:create",
        group: "操作",
        label: "新建书稿",
        hint: "打开创作面板",
        keywords: "开始创作 新建 生成 视频 链接 create",
        icon: Plus,
        run: go("/?create=1"),
      },
      ...NAV_GROUPS.flatMap((group) =>
        group.items.map<Command>((item) => ({
          id: `page:${item.to}`,
          group: "页面",
          label: item.label,
          hint: group.label,
          keywords: `${group.label} ${item.keywords ?? ""} ${item.to}`,
          icon: item.icon,
          run: go(item.to),
        })),
      ),
      {
        id: "page:/profile",
        group: "页面",
        label: "个人中心",
        hint: "账户",
        keywords: "个人 账户 profile 我的",
        icon: User,
        run: go("/profile"),
      },
      ...THEMES.map<Command>((theme) => ({
        id: `theme:${theme.id}`,
        group: "主题",
        label: theme.name,
        hint: theme.id === themeId ? "当前" : theme.mode === "dark" ? "深色" : "浅色",
        keywords: `主题 外观 配色 theme ${theme.mode} ${theme.description}`,
        icon: Palette,
        swatch: [theme.preview.bg, theme.preview.accent],
        run: () => setThemeId(theme.id),
      })),
      {
        id: "action:logout",
        group: "账户",
        label: "退出登录",
        keywords: "注销 logout 退出",
        icon: LogOut,
        run: logout,
      },
    ];
  }, [navigate, logout, setThemeId, themeId]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    const terms = q.split(/\s+/);
    return commands.filter((command) => {
      const haystack = `${command.label} ${command.hint ?? ""} ${command.keywords}`.toLowerCase();
      return terms.every((term) => haystack.includes(term));
    });
  }, [commands, query]);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActiveIndex(0);
    const timer = window.setTimeout(() => inputRef.current?.focus(), 10);
    return () => window.clearTimeout(timer);
  }, [open]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  useEffect(() => {
    listRef.current
      ?.querySelector<HTMLElement>(`[data-index="${activeIndex}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [activeIndex]);

  if (!open) return null;

  const runCommand = (command: Command | undefined) => {
    if (!command) return;
    onClose();
    command.run();
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((index) => Math.min(filtered.length - 1, index + 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((index) => Math.max(0, index - 1));
    } else if (event.key === "Enter") {
      event.preventDefault();
      runCommand(filtered[activeIndex]);
    } else if (event.key === "Escape") {
      event.preventDefault();
      onClose();
    }
  };

  let lastGroup = "";

  return (
    <div className="fixed inset-0 z-[95] flex items-start justify-center px-4 pt-[12vh]" role="presentation">
      <div className="app-overlay absolute inset-0 animate-fade-in-up" onClick={onClose} />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="搜索或跳转"
        className="relative w-full max-w-[600px] animate-scale-in overflow-hidden rounded-2xl border border-warm-100/10 bg-base-800/95 shadow-float backdrop-blur-2xl"
        onKeyDown={onKeyDown}
      >
        <div className="hairline-b flex items-center gap-3 px-4">
          <Search className="h-[18px] w-[18px] shrink-0 text-warm-400" strokeWidth={1.8} />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索页面、操作或主题…"
            className="h-14 flex-1 bg-transparent text-[15px] text-warm-50 outline-none placeholder:text-warm-500"
            aria-label="搜索命令"
          />
          <span className="kbd">Esc</span>
        </div>

        <div ref={listRef} className="max-h-[min(58vh,440px)] overflow-y-auto p-2">
          {filtered.length === 0 && (
            <div className="px-4 py-10 text-center text-sm text-warm-500">没有匹配的结果</div>
          )}
          {filtered.map((command, index) => {
            const showGroup = command.group !== lastGroup;
            lastGroup = command.group;
            const active = index === activeIndex;
            const Icon = command.icon;
            return (
              <div key={command.id}>
                {showGroup && (
                  <div className="px-3 pb-1 pt-3 text-[10.5px] font-medium tracking-[0.18em] text-warm-500 first:pt-1">
                    {command.group}
                  </div>
                )}
                <button
                  type="button"
                  data-index={index}
                  onMouseMove={() => setActiveIndex(index)}
                  onClick={() => runCommand(command)}
                  className={`flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm transition-colors ${
                    active ? "bg-gold-400/12 text-warm-50" : "text-warm-200"
                  }`}
                >
                  <span
                    className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border ${
                      active ? "border-gold-400/30 bg-gold-400/10 text-gold-300" : "border-warm-100/[0.08] bg-warm-100/[0.03] text-warm-400"
                    }`}
                  >
                    {command.swatch ? (
                      <span className="flex h-3.5 w-3.5 overflow-hidden rounded-full ring-1 ring-warm-100/20">
                        <span className="h-full w-1/2" style={{ backgroundColor: command.swatch[0] }} />
                        <span className="h-full w-1/2" style={{ backgroundColor: command.swatch[1] }} />
                      </span>
                    ) : (
                      <Icon className="h-4 w-4" strokeWidth={1.7} />
                    )}
                  </span>
                  <span className="flex-1 truncate">{command.label}</span>
                  {command.hint && <span className="shrink-0 text-xs text-warm-500">{command.hint}</span>}
                  {active ? (
                    <CornerDownLeft className="h-3.5 w-3.5 shrink-0 text-gold-300" strokeWidth={1.8} />
                  ) : (
                    <ArrowRight className="h-3.5 w-3.5 shrink-0 text-warm-600" strokeWidth={1.8} />
                  )}
                </button>
              </div>
            );
          })}
        </div>

        <div className="hairline-t flex items-center gap-4 px-4 py-2.5 text-[11px] text-warm-500">
          <span className="flex items-center gap-1.5">
            <span className="kbd">↑</span>
            <span className="kbd">↓</span>
            选择
          </span>
          <span className="flex items-center gap-1.5">
            <span className="kbd">Enter</span>
            打开
          </span>
          <span className="ml-auto">ScriptBook</span>
        </div>
      </div>
    </div>
  );
}
