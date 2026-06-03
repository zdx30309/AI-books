import { useEffect, useState } from "react";
import {
  BookOpenCheck,
  ListPlus,
  Loader2,
  PencilLine,
  Sparkles,
  UploadCloud,
  X,
} from "lucide-react";
import { api, ApiError } from "@/api/client";
import { DEFAULT_CATEGORIES, normalizeCategoryOptions } from "@/constants/categories";
import type { TaskCategoryOption } from "@/api/types";

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: (taskId: string) => void;
  defaultCategoryId?: string;
}

const URL_RE = /https?:\/\/[^\s<>"'，。；;、（）()【】\[\]{}]+/gi;
const COMIC_CATEGORY_IDS = new Set(["comic_series", "healing_comic"]);
const HEALING_COMIC_DEFAULT_PANEL_COUNT = 32;
const HEALING_COMIC_MIN_PANEL_COUNT = 1;
const HEALING_COMIC_MAX_PANEL_COUNT = 64;
const CLASSICAL_VARIANTS = [
  { id: "plain", label: "通俗白话" },
  { id: "zhiguai_web", label: "志怪小说爽读版" },
  { id: "wuxia_web", label: "武侠小说爽读版" },
  { id: "poetry", label: "诗歌翻译" },
] as const;
type CreateMode = "single" | "batch" | "comic" | "article";

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

function parseComicPhrases(text: string) {
  return text
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .split("\n")
    .map((line) =>
      line
        .replace(/^\s*(?:[-*+•]|\d+[.、)]|第[一二三四五六]辑[：:、.)]?|[一二三四五六][、.)])\s*/, "")
        .trim(),
    )
    .filter(Boolean)
    .map((line) => line.slice(0, 120));
}

function parsePanelCount(value: string) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : Number.NaN;
}

export function CreateTaskModal({ open, onClose, onCreated, defaultCategoryId }: Props) {
  const [mode, setMode] = useState<CreateMode>("single");
  const [url, setUrl] = useState("");
  const [rawText, setRawText] = useState("");
  const [classicalInputMode, setClassicalInputMode] = useState<"text" | "docx">("text");
  const [classicalDocxFile, setClassicalDocxFile] = useState<File | null>(null);
  const [classicalVariant, setClassicalVariant] = useState("plain");
  const [classicalBookTitle, setClassicalBookTitle] = useState("");
  const [candidateLinks, setCandidateLinks] = useState("");
  const [comicTitle, setComicTitle] = useState("");
  const [comicPanelCount, setComicPanelCount] = useState(String(HEALING_COMIC_DEFAULT_PANEL_COUNT));
  const [comicThemeRequirement, setComicThemeRequirement] = useState("");
  const [comicPrefacePhrase, setComicPrefacePhrase] = useState("");
  const [comicPhraseInput, setComicPhraseInput] = useState("");
  const [category, setCategory] = useState(defaultCategoryId ?? "social_science");
  const [styleReference, setStyleReference] = useState("");
  const [styleNotes, setStyleNotes] = useState("");
  const [categories, setCategories] = useState<TaskCategoryOption[]>(
    normalizeCategoryOptions(DEFAULT_CATEGORIES),
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selectedCategory =
    categories.find((item) => item.id === category) ?? DEFAULT_CATEGORIES[0]!;
  const candidateLinkStats = normalizeCandidateLinks(candidateLinks);
  const comicPhrases = parseComicPhrases(comicPhraseInput);
  const isReferenceArticle = mode === "article";
  const isClassicalChinese = category === "classical_chinese";
  const isComicCategory = mode === "comic" || COMIC_CATEGORY_IDS.has(category);
  const comicCategoryLabel = category === "healing_comic" ? "漫画" : "插画";
  const supportsStyleReference =
    category === "web_short_story" || category === "social_science_test";

  useEffect(() => {
    if (!open) {
      setMode("single");
      setUrl("");
      setRawText("");
      setClassicalInputMode("text");
      setClassicalDocxFile(null);
      setClassicalVariant("plain");
      setClassicalBookTitle("");
      setCandidateLinks("");
      setComicTitle("");
      setComicPanelCount(String(HEALING_COMIC_DEFAULT_PANEL_COUNT));
      setComicThemeRequirement("");
      setComicPrefacePhrase("");
      setComicPhraseInput("");
      setCategory(defaultCategoryId ?? "social_science");
      setStyleReference("");
      setStyleNotes("");
      setCategories(normalizeCategoryOptions(DEFAULT_CATEGORIES));
      setError(null);
      setSubmitting(false);
    }
  }, [defaultCategoryId, open]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    api
      .getTaskCategories()
      .then((res) => {
        if (!cancelled && res.items.length > 0) {
          const normalizedItems = normalizeCategoryOptions(res.items);
          setCategories(normalizedItems);
          setCategory((prev) =>
            normalizedItems.some((item) => item.id === prev) ? prev : normalizedItems[0].id,
          );
        }
      })
      .catch(() => {
        if (!cancelled) setCategories(normalizeCategoryOptions(DEFAULT_CATEGORIES));
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  useEffect(() => {
    if (COMIC_CATEGORY_IDS.has(category)) {
      setMode("comic");
      return;
    }
    if (isClassicalChinese) setMode("single");
    else {
      setClassicalInputMode("text");
      setClassicalDocxFile(null);
    }
  }, [category, isClassicalChinese]);

  useEffect(() => {
    if (!supportsStyleReference) {
      setStyleReference("");
      setStyleNotes("");
    }
  }, [supportsStyleReference]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const switchMode = (nextMode: CreateMode, nextCategory?: string) => {
    setMode(nextMode);
    if (nextMode === "comic") {
      setCategory(nextCategory && COMIC_CATEGORY_IDS.has(nextCategory) ? nextCategory : "comic_series");
    } else if (nextMode === "article") {
      setCategory("emotion_relationship");
      setUrl("");
      setRawText("");
    } else if (COMIC_CATEGORY_IDS.has(category)) {
      setCategory("social_science");
    }
  };

  const submit = async () => {
    setError(null);
    if (mode === "comic") {
      const comicCategory = COMIC_CATEGORY_IDS.has(category) ? category : "comic_series";
      if (comicCategory === "healing_comic") {
        const cleanedTitle = comicTitle.trim();
        if (!cleanedTitle) {
          setError("漫画需要输入书名");
          return;
        }
        const panelCount = parsePanelCount(comicPanelCount);
        if (
          !Number.isInteger(panelCount) ||
          panelCount < HEALING_COMIC_MIN_PANEL_COUNT ||
          panelCount > HEALING_COMIC_MAX_PANEL_COUNT
        ) {
          setError(`漫画章节数需要在 ${HEALING_COMIC_MIN_PANEL_COUNT} 到 ${HEALING_COMIC_MAX_PANEL_COUNT} 之间`);
          return;
        }
        setSubmitting(true);
        try {
          const res = await api.createTask({
            url: "manual://healing_comic",
            config: {
              category: "healing_comic",
              comic_title: cleanedTitle,
              comic_panel_count: panelCount,
              title: cleanedTitle,
              raw_text: `书名：${cleanedTitle}`,
            },
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
        return;
      }
      const cleanedTitle = comicTitle.trim();
      if (!cleanedTitle) {
        setError(`${comicCategoryLabel}需要输入书名`);
        return;
      }
      const cleanedPrefacePhrase = comicPrefacePhrase.trim().slice(0, 120);
      if (!cleanedPrefacePhrase) {
        setError(`${comicCategoryLabel}需要输入 1 条文前短语`);
        return;
      }
      if (comicPhrases.length !== 5 && comicPhrases.length !== 6) {
        setError(`${comicCategoryLabel}需要输入 5 或 6 条短语，当前识别到 ${comicPhrases.length} 条`);
        return;
      }
      setSubmitting(true);
      try {
        const config: Record<string, unknown> = {
          category: comicCategory,
          comic_title: cleanedTitle,
          title: cleanedTitle,
          comic_preface_phrase: cleanedPrefacePhrase,
          comic_phrases: comicPhrases,
          raw_text: comicPhrases.join("\n"),
        };
        const res = await api.createTask({
          url: `manual://${comicCategory}`,
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
      return;
    }

    if (mode === "article") {
      const articleUrl = url.trim();
      if (!articleUrl) {
        setError("请填写微信公众号文章链接");
        return;
      }
      setSubmitting(true);
      try {
        const res = await api.createEmotionReferenceArticleTask({ url: articleUrl });
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
      return;
    }

    if (mode === "batch") {
      if (isClassicalChinese || isComicCategory) {
        setError(isComicCategory ? `${comicCategoryLabel}请使用${comicCategoryLabel}入口输入短语` : "古文白话请单条粘贴文言文原文，不使用候选链接库");
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
        if (supportsStyleReference) {
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

    if (isClassicalChinese && classicalInputMode === "docx") {
      if (!classicalDocxFile) {
        setError("请上传 .docx 格式的 Word 文件");
        return;
      }
      setSubmitting(true);
      try {
        const res = await api.createClassicalChineseTaskFromDocx(
          classicalDocxFile,
          classicalVariant,
          classicalBookTitle.trim(),
        );
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
      if (isClassicalChinese) {
        config.classical_variant = classicalVariant;
        if (classicalBookTitle.trim()) {
          config.classical_original_title = classicalBookTitle.trim();
          config.title = classicalBookTitle.trim();
        }
      }
      if (supportsStyleReference) {
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
          选择书籍方向后，粘贴视频链接、公众号文章链接、已有文稿，或把候选链接批量放入自动生成库
        </p>

        <div className="mt-6 space-y-4">
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => switchMode("single")}
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
              onClick={() => switchMode("batch")}
              disabled={isClassicalChinese || isComicCategory}
              className={`inline-flex items-center gap-1.5 rounded-full border px-4 py-1.5 text-xs tracking-widest transition-colors ${
                mode === "batch"
                  ? "border-gold-400/60 bg-gold-500/10 text-warm-100"
                  : isClassicalChinese || isComicCategory
                    ? "cursor-not-allowed border-gold-500/10 text-warm-600"
                    : "border-gold-500/15 text-warm-400 hover:border-gold-400/30 hover:text-warm-200"
              }`}
            >
              <ListPlus className="h-3.5 w-3.5" strokeWidth={1.5} />
              候选库
            </button>
            <button
              type="button"
              onClick={() => switchMode("article")}
              className={`inline-flex items-center gap-1.5 rounded-full border px-4 py-1.5 text-xs tracking-widest transition-colors ${
                isReferenceArticle
                  ? "border-gold-400/60 bg-gold-500/10 text-warm-100"
                  : "border-gold-500/15 text-warm-400 hover:border-gold-400/30 hover:text-warm-200"
              }`}
            >
              <PencilLine className="h-3.5 w-3.5" strokeWidth={1.5} />
              公众号参考
            </button>
            <button
              type="button"
              onClick={() => {
                setMode("single");
                setCategory("classical_chinese");
              }}
              className={`inline-flex items-center gap-1.5 rounded-full border px-4 py-1.5 text-xs tracking-widest transition-colors ${
                isClassicalChinese
                  ? "border-gold-400/60 bg-gold-500/10 text-warm-100"
                  : "border-gold-500/15 text-warm-400 hover:border-gold-400/30 hover:text-warm-200"
              }`}
            >
              <BookOpenCheck className="h-3.5 w-3.5" strokeWidth={1.5} />
              古文白话
            </button>
            <button
              type="button"
              onClick={() => switchMode("comic", "comic_series")}
              className={`inline-flex items-center gap-1.5 rounded-full border px-4 py-1.5 text-xs tracking-widest transition-colors ${
                mode === "comic" && category === "comic_series"
                  ? "border-gold-400/60 bg-gold-500/10 text-warm-100"
                  : "border-gold-500/15 text-warm-400 hover:border-gold-400/30 hover:text-warm-200"
              }`}
            >
              <Sparkles className="h-3.5 w-3.5" strokeWidth={1.5} />
              插画
            </button>
            <button
              type="button"
              onClick={() => switchMode("comic", "healing_comic")}
              className={`inline-flex items-center gap-1.5 rounded-full border px-4 py-1.5 text-xs tracking-widest transition-colors ${
                mode === "comic" && category === "healing_comic"
                  ? "border-gold-400/60 bg-gold-500/10 text-warm-100"
                  : "border-gold-500/15 text-warm-400 hover:border-gold-400/30 hover:text-warm-200"
              }`}
            >
              <Sparkles className="h-3.5 w-3.5" strokeWidth={1.5} />
              漫画
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
              disabled={isReferenceArticle}
              onChange={(e) => {
                const nextCategory = e.target.value;
                setCategory(nextCategory);
                if (COMIC_CATEGORY_IDS.has(nextCategory)) {
                  setMode("comic");
                } else if (mode === "comic") {
                  setMode("single");
                }
              }}
              className={`w-full rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm text-warm-100 outline-none transition-colors focus:border-gold-400/50 ${
                isReferenceArticle ? "cursor-not-allowed opacity-70" : ""
              }`}
            >
              {categories.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
            <p className="mt-2 text-xs leading-relaxed text-warm-500">
              {isReferenceArticle
                ? "公众号参考固定使用情感·关系方向，系统会先识别公开公众号正文，再按心理情感类长文流程生成原创文章。"
                : `${selectedCategory.core_feature} · ${selectedCategory.directions}`}
            </p>
          </div>

          {supportsStyleReference && (
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

          {mode === "single" || mode === "article" ? (
            <>
              {!isClassicalChinese && !isReferenceArticle && (
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

              {isReferenceArticle && (
                <div>
                  <label
                    htmlFor="wechat-article-url"
                    className="mb-2 block text-[11px] tracking-widest text-warm-300"
                  >
                    微信公众号文章链接
                  </label>
                  <input
                    id="wechat-article-url"
                    type="text"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    placeholder="https://mp.weixin.qq.com/s/..."
                    className="w-full rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm text-warm-100 placeholder:text-warm-500 outline-none transition-colors focus:border-gold-400/50"
                  />
                  <p className="mt-2 text-xs leading-relaxed text-warm-500">
                    仅支持公开可访问的公众号文章。系统会自动识别正文，提炼情感关系问题并生成原创文章。
                  </p>
                </div>
              )}

              {isClassicalChinese && (
                <div className="space-y-3 rounded-xl border border-gold-500/15 bg-base-700/35 p-4">
                  <div>
                    <label
                      htmlFor="classical-book-title"
                      className="mb-2 block text-[11px] tracking-widest text-warm-300"
                    >
                      原书名
                    </label>
                    <input
                      id="classical-book-title"
                      value={classicalBookTitle}
                      onChange={(e) => setClassicalBookTitle(e.target.value)}
                      placeholder="例如：十叶野闻"
                      className="w-full rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm text-warm-100 placeholder:text-warm-500 outline-none transition-colors focus:border-gold-400/50"
                    />
                    <p className="mt-2 text-xs leading-relaxed text-warm-500">
                      最终书名会固定为“原书名（白话版）”。上传 Word 时可留空，系统会用文件名作为原书名。
                    </p>
                  </div>
                  <div>
                    <label
                      htmlFor="classical-variant"
                      className="mb-2 block text-[11px] tracking-widest text-warm-300"
                    >
                      古文白话细分方向
                    </label>
                    <select
                      id="classical-variant"
                      value={classicalVariant}
                      onChange={(e) => setClassicalVariant(e.target.value)}
                      className="w-full rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm text-warm-100 outline-none transition-colors focus:border-gold-400/50"
                    >
                      {CLASSICAL_VARIANTS.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="grid grid-cols-2 gap-2 rounded-lg border border-gold-500/15 bg-base-700/35 p-1">
                    <button
                      type="button"
                      onClick={() => setClassicalInputMode("text")}
                      className={`rounded-md px-3 py-2 text-xs tracking-widest transition-colors ${
                        classicalInputMode === "text"
                          ? "bg-gold-500/15 text-warm-100"
                          : "text-warm-500 hover:text-warm-200"
                      }`}
                    >
                      粘贴文本
                    </button>
                    <button
                      type="button"
                      onClick={() => setClassicalInputMode("docx")}
                      className={`rounded-md px-3 py-2 text-xs tracking-widest transition-colors ${
                        classicalInputMode === "docx"
                          ? "bg-gold-500/15 text-warm-100"
                          : "text-warm-500 hover:text-warm-200"
                      }`}
                    >
                      上传 Word
                    </button>
                  </div>
                </div>
              )}

              {!isReferenceArticle && (isClassicalChinese && classicalInputMode === "docx" ? (
                <div>
                  <label
                    htmlFor="classical-docx"
                    className="mb-2 block text-[11px] tracking-widest text-warm-300"
                  >
                    Word 文档
                  </label>
                  <label
                    htmlFor="classical-docx"
                    className="flex min-h-[150px] cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-gold-500/30 bg-base-700/40 px-4 py-6 text-center transition-colors hover:border-gold-400/60 hover:bg-gold-500/5"
                  >
                    <UploadCloud className="mb-3 h-7 w-7 text-gold-300" strokeWidth={1.5} />
                    <span className="text-sm text-warm-100">
                      {classicalDocxFile?.name || "选择 .docx 文件"}
                    </span>
                    <span className="mt-2 text-xs leading-relaxed text-warm-500">
                      系统会读取 Word 正文，并按古文白话流程生成
                    </span>
                  </label>
                  <input
                    id="classical-docx"
                    type="file"
                    accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    className="sr-only"
                    onChange={(event) => {
                      setClassicalDocxFile(event.target.files?.[0] ?? null);
                      setError(null);
                    }}
                  />
                </div>
              ) : (
                <div>
                <label
                  htmlFor="task-raw-text"
                  className="mb-2 block text-[11px] tracking-widest text-warm-300"
                >
                  {isClassicalChinese
                    ? "文言文原文"
                    : isReferenceArticle
                      ? "参考文章全文"
                      : "视频文稿（可选）"}
                </label>
                <textarea
                  id="task-raw-text"
                  value={rawText}
                  onChange={(e) => setRawText(e.target.value)}
                  placeholder={
                    isClassicalChinese
                      ? "请直接粘贴需要转成白话文的文言文原文"
                      : isReferenceArticle
                        ? "请粘贴公众号文章或已有文章全文。系统会提炼情感关系问题，重新组织结构和案例，生成原创文章。"
                        : "如果已经有视频文稿，可以直接粘贴在这里，跳过抖音解析步骤"
                  }
                  rows={isClassicalChinese ? 9 : isReferenceArticle ? 12 : 5}
                  className="w-full resize-none rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm leading-relaxed text-warm-100 placeholder:text-warm-500 outline-none transition-colors focus:border-gold-400/50"
                />
                </div>
              ))}
            </>
          ) : mode === "comic" ? (
            <div className="space-y-4">
              {category === "healing_comic" ? (
                <>
                <div>
                  <label
                    htmlFor="healing-comic-title"
                    className="mb-2 block text-[11px] tracking-widest text-warm-300"
                  >
                    书名
                  </label>
                  <input
                    id="healing-comic-title"
                    value={comicTitle}
                    onChange={(e) => setComicTitle(e.target.value)}
                    placeholder="看你一眼就会笑"
                    className="w-full rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm leading-relaxed text-warm-100 placeholder:text-warm-500 outline-none transition-colors focus:border-gold-400/50"
                  />
                  <p className="mt-2 text-xs leading-relaxed text-warm-500">
                    只需要输入书名和章节数；系统会参考服务端漫画数据库，自动规划封面和同系列章节配图，短句会直接写在图上。
                  </p>
                </div>
                <div>
                  <label
                    htmlFor="healing-comic-panel-count"
                    className="mb-2 block text-[11px] tracking-widest text-warm-300"
                  >
                    章节数量
                  </label>
                  <input
                    id="healing-comic-panel-count"
                    type="number"
                    min={HEALING_COMIC_MIN_PANEL_COUNT}
                    max={HEALING_COMIC_MAX_PANEL_COUNT}
                    step={1}
                    value={comicPanelCount}
                    onChange={(e) => setComicPanelCount(e.target.value.replace(/[^\d]/g, ""))}
                    placeholder={String(HEALING_COMIC_DEFAULT_PANEL_COUNT)}
                    className="w-full rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm leading-relaxed text-warm-100 placeholder:text-warm-500 outline-none transition-colors focus:border-gold-400/50"
                  />
                  <p className="mt-2 text-xs leading-relaxed text-warm-500">
                    默认 {HEALING_COMIC_DEFAULT_PANEL_COUNT} 章，可自定义 {HEALING_COMIC_MIN_PANEL_COUNT} 到 {HEALING_COMIC_MAX_PANEL_COUNT} 章；每章会生成一张同系列漫画图。
                  </p>
                </div>
                </>
              ) : (
                <>
              <div>
                <label
                  htmlFor="comic-title"
                  className="mb-2 block text-[11px] tracking-widest text-warm-300"
                >
                  书名
                </label>
                <input
                  id="comic-title"
                  value={comicTitle}
                  onChange={(e) => setComicTitle(e.target.value)}
                  placeholder={category === "healing_comic" ? "看见你，心就软了" : "今晚，月亮替我抱抱你"}
                  className="w-full rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm leading-relaxed text-warm-100 placeholder:text-warm-500 outline-none transition-colors focus:border-gold-400/50"
                />
                <p className="mt-2 text-xs leading-relaxed text-warm-500">
                  系统会直接使用你输入的书名，不再自动生成或改写书名。
                </p>
              </div>
              {category === "healing_comic" && (
                <div>
                  <div className="mb-2 flex items-center justify-between gap-3">
                    <label
                      htmlFor="comic-theme-requirement"
                      className="block text-[11px] tracking-widest text-warm-300"
                    >
                      主题要求（可选）
                    </label>
                    <span className="text-[10px] tracking-widest text-warm-500">
                      {Math.min(comicThemeRequirement.trim().length, 240)}/240
                    </span>
                  </div>
                  <textarea
                    id="comic-theme-requirement"
                    value={comicThemeRequirement}
                    onChange={(e) => setComicThemeRequirement(e.target.value.slice(0, 240))}
                    placeholder="例如：想用一只原创小熊作为主角。不要填写真实 IP 名称，系统会自动转成原创动物主题。"
                    rows={3}
                    className="w-full resize-none rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm leading-relaxed text-warm-100 placeholder:text-warm-500 outline-none transition-colors focus:border-gold-400/50"
                  />
                  <p className="mt-2 text-xs leading-relaxed text-warm-500">
                    可留空；填写后只作为原创角色方向，不会复刻或暗示现成 IP。
                  </p>
                </div>
              )}
              <div>
                <div className="mb-2 flex items-center justify-between gap-3">
                  <label
                    htmlFor="comic-preface-phrase"
                    className="block text-[11px] tracking-widest text-warm-300"
                  >
                    文前短语
                  </label>
                  <span
                    className={`text-[10px] tracking-widest ${
                      comicPrefacePhrase.trim() ? "text-gold-300" : "text-warm-500"
                    }`}
                  >
                    {Math.min(comicPrefacePhrase.trim().length, 120)}/120
                  </span>
                </div>
                <input
                  id="comic-preface-phrase"
                  value={comicPrefacePhrase}
                  onChange={(e) => setComicPrefacePhrase(e.target.value.slice(0, 120))}
                  placeholder="今晚，月亮把安静放在窗边。"
                  className="w-full rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm leading-relaxed text-warm-100 placeholder:text-warm-500 outline-none transition-colors focus:border-gold-400/50"
                />
                <p className="mt-2 text-xs leading-relaxed text-warm-500">
                  文前短语会参与整本书主题，并生成一张出版感治愈系开场图。
                </p>
              </div>
              <div>
                <div className="mb-2 flex items-center justify-between gap-3">
                  <label
                    htmlFor="comic-phrases"
                    className="block text-[11px] tracking-widest text-warm-300"
                  >
                    目录短语
                  </label>
                  <span
                    className={`text-[10px] tracking-widest ${
                      comicPhrases.length === 5 || comicPhrases.length === 6
                        ? "text-gold-300"
                        : "text-warm-500"
                    }`}
                  >
                    {comicPhrases.length}/6
                  </span>
                </div>
                <textarea
                  id="comic-phrases"
                  value={comicPhraseInput}
                  onChange={(e) => setComicPhraseInput(e.target.value)}
                  placeholder={"每行一条目录短语，输入 5 或 6 条\n今晚，月亮放下了手里的活。\n我们也把心事晾在窗台。\n什么都不必赶。"}
                  rows={7}
                  className="w-full resize-none rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm leading-relaxed text-warm-100 placeholder:text-warm-500 outline-none transition-colors focus:border-gold-400/50"
                />
                <p
                  className={`mt-2 text-xs leading-relaxed ${
                    comicPhrases.length === 5 || comicPhrases.length === 6
                      ? "text-warm-500"
                      : "text-gold-200"
                  }`}
                >
                  文前 1 条，目录 5 或 6 条；当前目录识别到 {comicPhrases.length} 条。
                </p>
              </div>
                </>
              )}
            </div>
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
                {mode === "batch"
                  ? "加入候选库"
                  : mode === "comic"
                    ? `生成${comicCategoryLabel}`
                    : mode === "article"
                      ? "抓取并生成"
                      : "立即生成"}
              </span>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
