import { useEffect, useState } from "react";
import { BookOpenCheck, ListPlus, Loader2, PencilLine, X } from "lucide-react";
import { api, ApiError } from "@/api/client";
import { DEFAULT_CATEGORIES } from "@/constants/categories";
import type { TaskCategoryOption } from "@/api/types";

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: (taskId: string) => void;
}

const URL_RE = /https?:\/\/[^\s<>"'，。；;、（）()【】\[\]{}]+/gi;

function extractCandidateLinks(text: string) {
  const matches = text.match(URL_RE) ?? [];
  return matches
    .map((item) => item.replace(/[，。；;、！？!?.,)）\]】]+$/g, ""))
    .filter(Boolean);
}

function normalizeCandidateLinks(text: string) {
  const rawLinks = extractCandidateLinks(text);
  const seen = new Set<string>();
  const urls: string[] = [];
  let duplicateCount = 0;

  rawLinks.forEach((link) => {
    if (seen.has(link)) {
      duplicateCount += 1;
      return;
    }
    seen.add(link);
    urls.push(link);
  });

  return {
    urls,
    rawCount: rawLinks.length,
    duplicateCount,
  };
}

export function CreateTaskModal({ open, onClose, onCreated }: Props) {
  const [mode, setMode] = useState<"single" | "batch">("single");
  const [url, setUrl] = useState("");
  const [rawText, setRawText] = useState("");
  const [candidateLinks, setCandidateLinks] = useState("");
  const [category, setCategory] = useState("social_science");
  const [styleReference, setStyleReference] = useState("");
  const [styleNotes, setStyleNotes] = useState("");
  const [categories, setCategories] = useState<TaskCategoryOption[]>(DEFAULT_CATEGORIES);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selectedCategory =
    categories.find((item) => item.id === category) ?? DEFAULT_CATEGORIES[0]!;
  const candidateLinkStats = normalizeCandidateLinks(candidateLinks);
  const isClassicalChinese = category === "classical_chinese";
  const isHistoryTest = category === "web_short_story";

  useEffect(() => {
    if (!open) {
      setMode("single");
      setUrl("");
      setRawText("");
      setCandidateLinks("");
      setCategory("social_science");
      setStyleReference("");
      setStyleNotes("");
      setCategories(DEFAULT_CATEGORIES);
      setError(null);
      setSubmitting(false);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    api
      .getTaskCategories()
      .then((res) => {
        if (!cancelled && res.items.length > 0) {
          setCategories(res.items);
          setCategory((prev) =>
            res.items.some((item) => item.id === prev) ? prev : res.items[0].id,
          );
        }
      })
      .catch(() => {
        if (!cancelled) setCategories(DEFAULT_CATEGORIES);
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  useEffect(() => {
    if (isClassicalChinese) setMode("single");
  }, [isClassicalChinese]);

  useEffect(() => {
    if (!isHistoryTest) {
      setStyleReference("");
      setStyleNotes("");
    }
  }, [isHistoryTest]);

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
    if (mode === "batch") {
      if (isClassicalChinese) {
        setError("古文白话请单条粘贴文言文原文，不使用候选链接库");
        return;
      }
      const { urls, duplicateCount } = candidateLinkStats;
      if (urls.length === 0) {
        setError("请放入至少 1 个候选链接");
        return;
      }
      if (urls.length > 500) {
        setError("候选链接一次最多 500 个");
        return;
      }
      setSubmitting(true);
      try {
        const config: Record<string, unknown> = { category };
        if (isHistoryTest) {
          if (styleReference.trim()) config.style_reference = styleReference.trim();
          if (styleNotes.trim()) config.style_notes = styleNotes.trim();
        }
        const res = await api.createTaskBatch({ urls, config });
        const first = res.created.find((item) => item.task_id)?.task_id;
        if (!first) {
          const duplicateHint =
            duplicateCount > 0 ? `已自动合并 ${duplicateCount} 个重复链接。` : "";
          setError(`这些链接都已存在任务，未重复创建。${duplicateHint}`);
          return;
        }
        if (res.total_skipped > 0 || duplicateCount > 0) {
          const parts = [`已创建 ${res.total_created} 个任务`];
          if (duplicateCount > 0) parts.push(`自动合并 ${duplicateCount} 个重复链接`);
          if (res.total_skipped > 0) parts.push(`跳过 ${res.total_skipped} 个已存在任务`);
          window.alert(`${parts.join("，")}。`);
        }
        onCreated(first);
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
      return;
    }

    if (isClassicalChinese && !rawText.trim()) {
      setError("请粘贴需要转换的文言文原文");
      return;
    }
    if (!isClassicalChinese && !url.trim() && !rawText.trim()) {
      setError("请填入抖音链接，或直接粘贴视频文稿");
      return;
    }
    setSubmitting(true);
    try {
      const config: Record<string, unknown> = { category };
      if (isHistoryTest) {
        if (styleReference.trim()) config.style_reference = styleReference.trim();
        if (styleNotes.trim()) config.style_notes = styleNotes.trim();
      }
      if (rawText.trim()) config.raw_text = rawText.trim();
      const res = await api.createTask({
        url: isClassicalChinese ? "manual://classical_chinese" : url.trim() || "manual://raw_text",
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
        className="card-warm relative max-h-[calc(100dvh-32px)] w-[min(600px,90vw)] overflow-y-auto p-6 shadow-card-warm"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭"
          className="sticky top-4 z-10 -mb-8 ml-auto flex h-8 w-8 items-center justify-center rounded-full bg-base-700/80 text-warm-400 transition-colors hover:bg-warm-600/15 hover:text-warm-100"
        >
          <X className="h-4 w-4" strokeWidth={1.5} />
        </button>

        <h2 className="font-serif text-2xl tracking-wide text-warm-100">
          开始创作
        </h2>
        <p className="mt-2 text-xs tracking-wide text-warm-400">
          选择书籍方向后，粘贴单个视频链接、已有文稿，或把候选链接批量放入自动生成库
        </p>

        <div className="mt-6 space-y-4">
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setMode("single")}
              className={`rounded-full border px-4 py-1.5 text-xs tracking-widest transition-colors ${
                mode === "single"
                  ? "border-gold-400/60 bg-gold-500/10 text-warm-100"
                  : "border-gold-500/15 text-warm-400 hover:border-gold-400/30 hover:text-warm-200"
              }`}
            >
              单条生成
            </button>
            <button
              type="button"
              onClick={() => setMode("batch")}
              disabled={isClassicalChinese}
              className={`inline-flex items-center gap-1.5 rounded-full border px-4 py-1.5 text-xs tracking-widest transition-colors ${
                mode === "batch"
                  ? "border-gold-400/60 bg-gold-500/10 text-warm-100"
                  : isClassicalChinese
                    ? "cursor-not-allowed border-gold-500/10 text-warm-600"
                    : "border-gold-500/15 text-warm-400 hover:border-gold-400/30 hover:text-warm-200"
              }`}
            >
              <ListPlus className="h-3.5 w-3.5" strokeWidth={1.5} />
              候选库
            </button>
          </div>

          <div>
            <label
              htmlFor="task-category"
              className="mb-2 flex items-center gap-2 text-[11px] tracking-widest text-warm-300"
            >
              <BookOpenCheck className="h-3.5 w-3.5 text-gold-300" strokeWidth={1.7} />
              书籍方向
            </label>
            <select
              id="task-category"
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="w-full rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm text-warm-100 outline-none transition-colors focus:border-gold-400/50"
            >
              {categories.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
            <p className="mt-2 text-xs leading-relaxed text-warm-500">
              {selectedCategory.core_feature} · {selectedCategory.directions}
            </p>
          </div>

          {isHistoryTest && (
            <div className="rounded-xl border border-gold-500/15 bg-base-700/35 p-4">
              <label className="mb-3 flex items-center gap-2 text-[11px] tracking-widest text-warm-300">
                <PencilLine className="h-3.5 w-3.5 text-gold-300" strokeWidth={1.7} />
                笔锋参考
              </label>
              <div className="space-y-3">
                <input
                  type="text"
                  value={styleReference}
                  onChange={(e) => setStyleReference(e.target.value)}
                  placeholder="输入想参考的作者、作品气质或类型笔法"
                  className="w-full rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm text-warm-100 placeholder:text-warm-500 outline-none transition-colors focus:border-gold-400/50"
                />
                <textarea
                  value={styleNotes}
                  onChange={(e) => setStyleNotes(e.target.value)}
                  placeholder="可补充节奏、叙事视角、人物关系、爽点密度等要求"
                  rows={4}
                  className="w-full resize-none rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm leading-relaxed text-warm-100 placeholder:text-warm-500 outline-none transition-colors focus:border-gold-400/50"
                />
                <p className="text-xs leading-relaxed text-warm-500">
                  系统会提炼叙事节奏、镜头感、人物推进和语言密度，生成原创初稿，不会复制原文句子。
                </p>
              </div>
            </div>
          )}

          {mode === "single" ? (
            <>
              {!isClassicalChinese && (
                <>
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
                </>
              )}

              <div>
                <label
                  htmlFor="task-raw-text"
                  className="mb-2 block text-[11px] tracking-widest text-warm-300"
                >
                  {isClassicalChinese ? "文言文原文" : "视频文稿（可选）"}
                </label>
                <textarea
                  id="task-raw-text"
                  value={rawText}
                  onChange={(e) => setRawText(e.target.value)}
                  placeholder={
                    isClassicalChinese
                      ? "请直接粘贴需要转成白话文的文言文原文"
                      : "如果已经有视频文稿，可以直接粘贴在这里，跳过抖音解析步骤"
                  }
                  rows={isClassicalChinese ? 9 : 5}
                  className="w-full resize-none rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm leading-relaxed text-warm-100 placeholder:text-warm-500 outline-none transition-colors focus:border-gold-400/50"
                />
              </div>
            </>
          ) : (
            <div>
              <div className="mb-2 flex items-center justify-between gap-3">
                <label
                  htmlFor="candidate-links"
                  className="block text-[11px] tracking-widest text-warm-300"
                >
                  候选链接
                </label>
                <span className="text-[10px] tracking-widest text-warm-500">
                  {candidateLinkStats.urls.length}/500
                </span>
              </div>
              <textarea
                id="candidate-links"
                value={candidateLinks}
                onChange={(e) => setCandidateLinks(e.target.value)}
                placeholder="可以直接粘贴整段文字、评论、表格或多平台分享文本；系统会自动识别其中完整链接，最多 500 个"
                rows={10}
                className="w-full resize-none rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm leading-relaxed text-warm-100 placeholder:text-warm-500 outline-none transition-colors focus:border-gold-400/50"
              />
              {candidateLinkStats.duplicateCount > 0 && (
                <p className="mt-2 text-xs leading-relaxed text-gold-200">
                  已自动合并 {candidateLinkStats.duplicateCount} 个重复链接，将保留第一次出现的链接生成书籍。
                </p>
              )}
            </div>
          )}

          {error && (
            <div className="rounded-lg border border-red-500/30 bg-red-500/8 px-4 py-2.5 text-xs text-red-300">
              {error}
            </div>
          )}
        </div>

        <div className="mt-6 flex items-center justify-end gap-3">
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
              <span className="tracking-widest">
                {mode === "batch" ? "加入候选库" : "立即生成"}
              </span>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
