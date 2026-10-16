import { useEffect, useState } from "react";
import { CheckCircle2, Info, X, XCircle } from "lucide-react";

type ToastKind = "success" | "error" | "info";

interface ToastItem {
  id: number;
  kind: ToastKind;
  message: string;
}

// 轻量全局提示，替代会阻塞页面的 window.alert
let nextId = 1;
const listeners = new Set<(item: ToastItem) => void>();

function push(kind: ToastKind, message: string) {
  const item = { id: nextId++, kind, message };
  listeners.forEach((listener) => listener(item));
}

export const toast = {
  success: (message: string) => push("success", message),
  error: (message: string) => push("error", message),
  info: (message: string) => push("info", message),
};

const DURATION: Record<ToastKind, number> = { success: 3500, info: 4000, error: 7000 };

const STYLES: Record<ToastKind, { icon: typeof Info; className: string }> = {
  success: { icon: CheckCircle2, className: "border-emerald-400/30 text-emerald-200" },
  error: { icon: XCircle, className: "border-rose-400/40 text-rose-200" },
  info: { icon: Info, className: "border-gold-400/30 text-warm-100" },
};

export function Toaster() {
  const [items, setItems] = useState<ToastItem[]>([]);

  useEffect(() => {
    const listener = (item: ToastItem) => {
      setItems((prev) => [...prev, item]);
      window.setTimeout(() => {
        setItems((prev) => prev.filter((t) => t.id !== item.id));
      }, DURATION[item.kind]);
    };
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);

  const dismiss = (id: number) => setItems((prev) => prev.filter((t) => t.id !== id));

  return (
    <div className="pointer-events-none fixed right-6 top-6 z-[100] flex w-[360px] max-w-[calc(100vw-2rem)] flex-col gap-3">
      {items.map((item) => {
        const { icon: Icon, className } = STYLES[item.kind];
        return (
          <div
            key={item.id}
            role={item.kind === "error" ? "alert" : "status"}
            className={`pointer-events-auto flex items-start gap-3 rounded-xl border bg-base-800/95 px-4 py-3 text-sm shadow-gold-soft backdrop-blur-md ${className}`}
          >
            <Icon className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={1.8} />
            <span className="flex-1 whitespace-pre-line break-words leading-relaxed">{item.message}</span>
            <button
              type="button"
              aria-label="关闭提示"
              onClick={() => dismiss(item.id)}
              className="shrink-0 text-warm-400 hover:text-warm-100"
            >
              <X className="h-4 w-4" strokeWidth={1.8} />
            </button>
          </div>
        );
      })}
    </div>
  );
}
