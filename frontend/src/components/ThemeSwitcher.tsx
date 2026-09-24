import { useEffect, useRef, useState } from "react";
import { Check, Palette } from "lucide-react";
import { useTheme } from "@/theme/ThemeContext";
import { THEME_GROUPS, THEMES, type ThemeOption } from "@/theme/themes";

type Variant = "sidebar" | "floating";

function ThemeTile({
  item,
  active,
  onPick,
}: {
  item: ThemeOption;
  active: boolean;
  onPick: () => void;
}) {
  const { bg, surface, accent, ink } = item.preview;
  return (
    <button
      type="button"
      onClick={onPick}
      className={`group flex flex-col gap-2 rounded-xl p-1.5 text-left transition-colors ${
        active ? "bg-gold-400/10" : "hover:bg-warm-100/5"
      }`}
      aria-pressed={active}
      title={item.description}
    >
      <span
        className={`relative block h-[58px] w-full overflow-hidden rounded-lg ring-1 transition-shadow ${
          active ? "ring-2 ring-gold-400" : "ring-warm-100/10 group-hover:ring-warm-100/25"
        }`}
        style={{ backgroundColor: bg }}
      >
        <span className="absolute inset-y-2 left-2 w-3 rounded-[3px]" style={{ backgroundColor: surface }} />
        <span
          className="absolute bottom-2 left-7 right-2 top-2 rounded-[5px] p-1.5"
          style={{ backgroundColor: surface }}
        >
          <span className="block h-1 w-3/5 rounded-full opacity-90" style={{ backgroundColor: ink }} />
          <span className="mt-1 block h-1 w-2/5 rounded-full opacity-40" style={{ backgroundColor: ink }} />
          <span className="absolute bottom-1.5 left-1.5 h-2 w-6 rounded-full" style={{ backgroundColor: accent }} />
        </span>
        {active && (
          <span className="absolute right-1 top-1 flex h-4 w-4 items-center justify-center rounded-full bg-gold-400 text-on-accent">
            <Check className="h-2.5 w-2.5" strokeWidth={3} />
          </span>
        )}
      </span>
      <span className="px-0.5">
        <span className={`block truncate text-[12px] font-medium ${active ? "text-warm-50" : "text-warm-200"}`}>
          {item.name}
        </span>
        <span className="block truncate text-[10.5px] text-warm-500">{item.description}</span>
      </span>
    </button>
  );
}

export function ThemePanel({ onPicked }: { onPicked?: () => void }) {
  const { themeId, setThemeId } = useTheme();
  return (
    <div className="max-h-[min(70vh,560px)] overflow-y-auto p-3">
      {THEME_GROUPS.map((group) => (
        <section key={group.mode} className="mb-3 last:mb-0">
          <div className="mb-1.5 px-1.5 text-[10.5px] font-medium tracking-[0.18em] text-warm-500">{group.label}</div>
          <div className="grid grid-cols-2 gap-1">
            {THEMES.filter((item) => item.mode === group.mode).map((item) => (
              <ThemeTile
                key={item.id}
                item={item}
                active={item.id === themeId}
                onPick={() => {
                  setThemeId(item.id);
                  onPicked?.();
                }}
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

export function ThemeSwitcher({
  variant = "sidebar",
  collapsed = false,
}: {
  variant?: Variant;
  collapsed?: boolean;
}) {
  const { theme } = useTheme();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const panelPosition =
    variant === "floating"
      ? "right-0 top-full mt-2 origin-top-right"
      : collapsed
        ? "bottom-0 left-full ml-3 origin-bottom-left"
        : "bottom-full left-0 mb-2 origin-bottom-left";

  const swatch = (
    <span className="flex h-3.5 w-3.5 shrink-0 overflow-hidden rounded-full ring-1 ring-warm-100/20">
      <span className="h-full w-1/2" style={{ backgroundColor: theme.preview.bg }} />
      <span className="h-full w-1/2" style={{ backgroundColor: theme.preview.accent }} />
    </span>
  );

  return (
    <div ref={rootRef} className={variant === "floating" ? "fixed right-5 top-5 z-[80]" : "relative"}>
      {variant === "floating" ? (
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="flex items-center gap-2 rounded-full border border-warm-100/10 bg-base-800/80 px-3.5 py-2 text-xs text-warm-200 shadow-card-warm backdrop-blur-xl transition-colors hover:border-gold-400/40 hover:text-warm-50"
          aria-expanded={open}
        >
          <Palette className="h-3.5 w-3.5 text-gold-300" strokeWidth={1.8} />
          <span>{theme.name}</span>
          {swatch}
        </button>
      ) : (
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className={`nav-item w-full ${collapsed ? "justify-center px-0" : ""} ${open ? "bg-warm-100/5 text-warm-50" : ""}`}
          aria-expanded={open}
          title={collapsed ? `主题：${theme.name}` : undefined}
        >
          <Palette className="h-[17px] w-[17px] shrink-0" strokeWidth={1.7} />
          {!collapsed && (
            <>
              <span className="flex-1 truncate text-left">主题</span>
              <span className="truncate text-[11.5px] text-warm-500">{theme.name}</span>
              {swatch}
            </>
          )}
        </button>
      )}

      {open && (
        <div
          className={`absolute z-[90] w-[332px] animate-scale-in overflow-hidden rounded-2xl border border-warm-100/10 bg-base-800/95 shadow-float backdrop-blur-2xl ${panelPosition}`}
          role="dialog"
          aria-label="外观主题"
        >
          <div className="hairline-b flex items-center justify-between px-4 py-3">
            <div className="flex items-center gap-2 text-[13px] font-medium text-warm-100">
              <Palette className="h-4 w-4 text-gold-300" strokeWidth={1.7} />
              外观主题
            </div>
            <span className="text-[10.5px] text-warm-500">自动保存到本机</span>
          </div>
          <ThemePanel onPicked={() => setOpen(false)} />
        </div>
      )}
    </div>
  );
}
