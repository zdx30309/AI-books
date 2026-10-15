import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronUp, Palette } from "lucide-react";
import { useTheme } from "@/theme/ThemeContext";
import { THEMES, type ThemeGroup } from "@/theme/themes";

const GROUPS: ThemeGroup[] = ["保留现有", "通用主题", "个性主题"];

export function ThemeSwitcher() {
  const { theme, themeId, setThemeId } = useTheme();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  const groupedThemes = useMemo(
    () =>
      GROUPS.map((group) => ({
        group,
        themes: THEMES.filter((item) => item.group === group),
      })).filter((item) => item.themes.length > 0),
    [],
  );

  useEffect(() => {
    if (!open) {
      return;
    }

    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
      }
    };

    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="fixed bottom-6 right-6 z-[80]">
      {open && (
        <div className="mb-3 w-[320px] overflow-hidden rounded-2xl border border-gold-500/18 bg-base-800/95 shadow-card-warm backdrop-blur-xl">
          <div className="border-b border-gold-500/10 px-4 py-3">
            <div className="flex items-center gap-2 text-sm font-medium tracking-wide text-warm-100">
              <Palette className="h-4 w-4 text-gold-300" strokeWidth={1.7} />
              主题风格
            </div>
            <p className="mt-1 text-[11px] leading-5 text-warm-500">
              当前选择会自动保存，并覆盖所有路由界面。
            </p>
          </div>

          <div className="max-h-[62vh] overflow-y-auto px-3 py-3">
            {groupedThemes.map(({ group, themes }) => (
              <section key={group} className="mb-4 last:mb-0">
                <div className="mb-2 px-1 text-[10px] tracking-widest text-warm-500">
                  {group}
                </div>
                <div className="space-y-2">
                  {themes.map((item) => {
                    const active = item.id === themeId;
                    return (
                      <button
                        key={item.id}
                        type="button"
                        onClick={() => {
                          setThemeId(item.id);
                          setOpen(false);
                        }}
                        className={`flex w-full items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition-colors ${
                          active
                            ? "border-gold-400/55 bg-gold-500/10 text-warm-100"
                            : "border-gold-500/10 bg-base-700/35 text-warm-300 hover:border-gold-400/35 hover:text-warm-100"
                        }`}
                      >
                        <span className="flex shrink-0 overflow-hidden rounded-full border border-gold-500/15">
                          {item.swatches.map((swatch) => (
                            <span
                              key={swatch}
                              className="h-6 w-4"
                              style={{ backgroundColor: swatch }}
                            />
                          ))}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block text-sm font-medium tracking-wide">
                            {item.name}
                          </span>
                          <span className="mt-0.5 block truncate text-[11px] text-warm-500">
                            {item.description}
                          </span>
                        </span>
                        {active && (
                          <Check className="h-4 w-4 shrink-0 text-gold-300" strokeWidth={2} />
                        )}
                      </button>
                    );
                  })}
                </div>
              </section>
            ))}
          </div>
        </div>
      )}

      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex items-center gap-3 rounded-full border border-gold-400/35 bg-base-800/90 px-4 py-3 text-sm tracking-wide text-warm-100 shadow-card-warm backdrop-blur-xl transition-colors hover:border-gold-300/70 hover:bg-base-700/95"
        title="切换主题"
      >
        <Palette className="h-4 w-4 text-gold-300" strokeWidth={1.8} />
        <span className="hidden sm:inline">{theme.name}</span>
        <span className="hidden overflow-hidden rounded-full border border-gold-500/15 sm:flex">
          {theme.swatches.map((swatch) => (
            <span key={swatch} className="h-4 w-3" style={{ backgroundColor: swatch }} />
          ))}
        </span>
        <ChevronUp
          className={`h-3.5 w-3.5 text-warm-500 transition-transform ${open ? "rotate-180" : ""}`}
          strokeWidth={1.7}
        />
      </button>
    </div>
  );
}
