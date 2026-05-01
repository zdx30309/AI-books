import { useEffect, useState } from "react";
import { X, Loader2 } from "lucide-react";
import { api, ApiError } from "@/api/client";

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: (taskId: string) => void;
}

export function CreateTaskModal({ open, onClose, onCreated }: Props) {
  const [url, setUrl] = useState("");
  const [rawText, setRawText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setUrl("");
      setRawText("");
      setError(null);
      setSubmitting(false);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const submit = async () => {
    setError(null);
    if (!url.trim() && !rawText.trim()) {
      setError("请填入抖音链接，或直接粘贴视频文稿");
      return;
    }
    setSubmitting(true);
    try {
      const config: Record<string, unknown> = {};
      if (rawText.trim()) config.raw_text = rawText.trim();
      const res = await api.createTask({
        url: url.trim() || "manual://raw_text",
        config,
      });
      onCreated(res.task_id);
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? `[${e.status}] ${e.detail}`
          : e instanceof Error
            ? e.message
            : "创建任务失败";
      setError(msg);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-base-900/80 backdrop-blur-sm animate-fade-in-up"
      onClick={onClose}
    >
      <div
        className="card-warm relative w-[min(560px,92vw)] p-8 shadow-card-warm"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭"
          className="absolute right-5 top-5 flex h-8 w-8 items-center justify-center rounded-full text-warm-400 transition-colors hover:bg-warm-600/15 hover:text-warm-100"
        >
          <X className="h-4 w-4" strokeWidth={1.5} />
        </button>

        <h2 className="font-serif text-2xl tracking-wide text-warm-100">
          开始创作
        </h2>
        <p className="mt-2 text-xs tracking-wide text-warm-400">
          粘贴抖音视频链接，或直接给一段已有文稿，AI 会一键生成完整书稿
        </p>

        <div className="mt-7 space-y-5">
          <div>
            <label
              htmlFor="task-url"
              className="mb-2 block text-[11px] tracking-widest text-warm-300"
            >
              视频链接
            </label>
            <input
              id="task-url"
              type="text"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://v.douyin.com/..."
              className="w-full rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm text-warm-100 placeholder:text-warm-500 outline-none transition-colors focus:border-gold-400/50"
            />
          </div>

          <div className="relative flex items-center text-[10px] tracking-widest text-warm-500">
            <div className="flex-1 border-t border-gold-500/10" />
            <span className="px-3">或直接粘贴已有文稿</span>
            <div className="flex-1 border-t border-gold-500/10" />
          </div>

          <div>
            <label
              htmlFor="task-raw-text"
              className="mb-2 block text-[11px] tracking-widest text-warm-300"
            >
              视频文稿（可选）
            </label>
            <textarea
              id="task-raw-text"
              value={rawText}
              onChange={(e) => setRawText(e.target.value)}
              placeholder="如果已经有视频文稿，可以直接粘贴在这里，跳过抖音解析步骤"
              rows={6}
              className="w-full resize-none rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm leading-relaxed text-warm-100 placeholder:text-warm-500 outline-none transition-colors focus:border-gold-400/50"
            />
          </div>

          {error && (
            <div className="rounded-lg border border-red-500/30 bg-red-500/8 px-4 py-2.5 text-xs text-red-300">
              {error}
            </div>
          )}
        </div>

        <div className="mt-8 flex items-center justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-full px-5 py-2 text-sm tracking-widest text-warm-400 transition-colors hover:text-warm-100"
          >
            取消
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={submitting}
            className="btn-gold disabled:opacity-60"
          >
            {submitting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" strokeWidth={2} />
                <span className="tracking-widest">提交中</span>
              </>
            ) : (
              <span className="tracking-widest">立即生成</span>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
