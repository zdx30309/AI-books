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
  onLongNovelCreated?: (projectId: string) => void;
  defaultCategoryId?: string;
}

const URL_RE = /https?:\/\/[^\s<>"'，。；;、（）()【】\[\]{}]+/gi;
const COMIC_CATEGORY_IDS = new Set(["comic_series", "healing_comic"]);
const HEALING_COMIC_DEFAULT_PANEL_COUNT = 32;
const HEALING_COMIC_MIN_PANEL_COUNT = 1;
const CLASSICAL_VARIANTS = [
  { id: "plain", label: "通俗白话" },
  { id: "zhiguai_web", label: "志怪小说爽读版" },
  { id: "wuxia_web", label: "武侠小说爽读版" },
  { id: "poetry", label: "诗歌翻译" },
] as const;
const LONG_NOVEL_CONFLICT_OPTIONS = [
  "婆媳偏心",
  "兄弟分家",
  "拆迁补偿",
  "房产归属",
  "养老赡养",
  "带娃教育",
  "彩礼嫁妆",
  "借钱不还",
  "小姑子啃老",
  "妯娌争宠",
  "丈夫愚孝",
  "亲戚道德绑架",
  "住院陪护",
  "年夜饭爆发",
];
const LONG_NOVEL_STYLE_OPTIONS = [
  "强情节现实家长里短",
  "克制现实",
  "强情绪爽感",
  "短视频爆款感",
  "乡土家庭",
  "城市家庭",
  "中年女性成长",
  "伦理纠葛",
];
const LONG_NOVEL_MODULES = [
  {
    id: "family_sisters_in_law_longform",
    label: "家长里短·妯娌",
    description: "婆媳、妯娌、分家、房产、养老、借钱、带娃等家庭利益矛盾。",
  },
];
const CLASSICAL_CHINESE_CATEGORY_IDS = new Set([
  "classical_chinese",
  "classical_chinese_interleaved",
]);
type CreateMode = "single" | "batch" | "comic" | "article" | "articleBatch" | "longNovel";

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
    .map((line) => line);
}

function parsePanelCount(value: string) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : Number.NaN;
}

export function CreateTaskModal({ open, onClose, onCreated, onLongNovelCreated, defaultCategoryId }: Props) {
  const [mode, setMode] = useState<CreateMode>("single");
  const [url, setUrl] = useState("");
  const [rawText, setRawText] = useState("");
  const [longNovelModule, setLongNovelModule] = useState("family_sisters_in_law_longform");
  const [longNovelPremise, setLongNovelPremise] = useState("");
  const [longNovelFamilySetup, setLongNovelFamilySetup] = useState("");
  const [longNovelConflictTags, setLongNovelConflictTags] = useState<string[]>([
    "婆媳偏心",
    "妯娌争宠",
    "兄弟分家",
  ]);
  const [longNovelSourceType, setLongNovelSourceType] = useState("manual");
  const [longNovelSourceText, setLongNovelSourceText] = useState("");
  const [longNovelTargetWords, setLongNovelTargetWords] = useState(200000);
  const [longNovelTargetChapters, setLongNovelTargetChapters] = useState(50);
  const [longNovelGenerationMode, setLongNovelGenerationMode] =
    useState<"cheap" | "standard" | "high_quality">("standard");
  const [longNovelStyle, setLongNovelStyle] = useState("强情节现实家长里短");
  const [longNovelForbiddenRules, setLongNovelForbiddenRules] = useState(
    "不要玄幻，不要重生，不要系统，不要总裁，不要金手指，不要突然暴富，不要机械降神，不要人物突然变好或变坏。",
  );
  const [assistingField, setAssistingField] = useState<string | null>(null);
  const [classicalInputMode, setClassicalInputMode] = useState<"text" | "docx">("text");
  const [classicalDocxFile, setClassicalDocxFile] = useState<File | null>(null);
  const [classicalVariant, setClassicalVariant] = useState("plain");
  const [classicalBookTitle, setClassicalBookTitle] = useState("");
  const [candidateLinks, setCandidateLinks] = useState("");
  const [articleLinks, setArticleLinks] = useState("");
  const [comicTitle, setComicTitle] = useState("");
  const [comicPanelCount, setComicPanelCount] = useState(String(HEALING_COMIC_DEFAULT_PANEL_COUNT));
  const [comicThemeRequirement, setComicThemeRequirement] = useState("");
  const [comicSourceContent, setComicSourceContent] = useState("");
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
  const isReferenceArticle = mode === "article" || mode === "articleBatch";
  const isReferenceArticleBatch = mode === "articleBatch";
  const isClassicalChinese = CLASSICAL_CHINESE_CATEGORY_IDS.has(category);
  const isComicCategory = mode === "comic" || COMIC_CATEGORY_IDS.has(category);
  const comicCategoryLabel = category === "healing_comic" ? "漫画" : "插画";
  const supportsStyleReference =
    category === "web_short_story" || category === "social_science_test";
  const articleLinkStats = normalizeCandidateLinks(articleLinks);

  useEffect(() => {
    if (!open) {
      setMode("single");
      setUrl("");
      setRawText("");
      setLongNovelModule("family_sisters_in_law_longform");
      setLongNovelPremise("");
      setLongNovelFamilySetup("");
      setLongNovelConflictTags(["婆媳偏心", "妯娌争宠", "兄弟分家"]);
      setLongNovelSourceType("manual");
      setLongNovelSourceText("");
      setLongNovelTargetWords(200000);
      setLongNovelTargetChapters(50);
      setLongNovelGenerationMode("standard");
      setLongNovelStyle("强情节现实家长里短");
      setLongNovelForbiddenRules("不要玄幻，不要重生，不要系统，不要总裁，不要金手指，不要突然暴富，不要机械降神，不要人物突然变好或变坏。");
      setAssistingField(null);
      setClassicalInputMode("text");
      setClassicalDocxFile(null);
      setClassicalVariant("plain");
      setClassicalBookTitle("");
      setCandidateLinks("");
      setArticleLinks("");
      setComicTitle("");
      setComicPanelCount(String(HEALING_COMIC_DEFAULT_PANEL_COUNT));
      setComicThemeRequirement("");
      setComicSourceContent("");
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
    } else if (nextMode === "longNovel") {
      setCategory("social_science");
      setUrl("");
      setRawText("");
    } else if (nextMode === "article" || nextMode === "articleBatch") {
      setCategory("emotion_relationship");
      setUrl("");
      setRawText("");
    } else if (COMIC_CATEGORY_IDS.has(category)) {
      setCategory("social_science");
    }
  };

  const longNovelCurrentValues = () => ({
    module_type: longNovelModule,
    premise: longNovelPremise,
    family_setup: longNovelFamilySetup,
    conflict_tags: longNovelConflictTags,
    source_type: longNovelSourceType,
    source_text: longNovelSourceText,
    target_words: longNovelTargetWords,
    target_chapters: longNovelTargetChapters,
    generation_mode: longNovelGenerationMode,
    style: longNovelStyle,
    forbidden_rules: longNovelForbiddenRules,
  });

  const assistLongNovel = async (
    field: "premise" | "family_setup" | "conflict_tags" | "source_text" | "forbidden_rules",
  ) => {
    setError(null);
    setAssistingField(field);
    try {
      const res = await api.assistLongNovelField({
        module_type: longNovelModule,
        field,
        current_values: longNovelCurrentValues(),
      });
      if (field === "conflict_tags") {
        if (res.conflict_tags.length > 0) setLongNovelConflictTags(res.conflict_tags);
        return;
      }
      if (!res.content.trim()) return;
      if (field === "premise") setLongNovelPremise(res.content);
      if (field === "family_setup") setLongNovelFamilySetup(res.content);
      if (field === "source_text") setLongNovelSourceText(res.content);
      if (field === "forbidden_rules") setLongNovelForbiddenRules(res.content);
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? `[${e.status}] ${e.detail}`
          : e instanceof Error
            ? e.message
            : "AI 辅助生成失败";
      setError(msg);
    } finally {
      setAssistingField(null);
    }
  };

  const assistButton = (
    field: "premise" | "family_setup" | "conflict_tags" | "source_text" | "forbidden_rules",
  ) => (
    <button
      type="button"
      onClick={() => void assistLongNovel(field)}
      disabled={Boolean(assistingField) || submitting}
      title="使用 GPT-5.5 XHIGH 辅助填写"
      className="inline-flex items-center gap-1 rounded-full border border-gold-500/25 px-2.5 py-1 text-[10px] tracking-widest text-gold-200 transition-colors hover:border-gold-400/60 hover:bg-gold-500/8 disabled:cursor-not-allowed disabled:opacity-50"
    >
      {assistingField === field ? (
        <Loader2 className="h-3 w-3 animate-spin" strokeWidth={1.8} />
      ) : (
        <Sparkles className="h-3 w-3" strokeWidth={1.8} />
      )}
      AI生成
    </button>
  );

  const submit = async () => {
    setError(null);
    if (mode === "longNovel") {
      const premise = longNovelPremise.trim();
      if (!premise) {
        setError("请填写长篇小说核心创意");
        return;
      }
      setSubmitting(true);
      try {
        const project = await api.createLongNovelProject({
          premise,
          family_setup: longNovelFamilySetup.trim() || null,
          conflict_tags: longNovelConflictTags,
          source_type: longNovelSourceType,
          source_text: longNovelSourceText.trim() || null,
          target_words: longNovelTargetWords,
          target_chapters: longNovelTargetChapters,
          generation_mode: longNovelGenerationMode,
          style: longNovelStyle,
          forbidden_rules: longNovelForbiddenRules.trim() || null,
        });
        if (onLongNovelCreated) onLongNovelCreated(project.id);
        else onCreated(project.id);
      } catch (e) {
        const msg =
          e instanceof ApiError
            ? `[${e.status}] ${e.detail}`
            : e instanceof Error
              ? e.message
              : "创建长篇项目失败";
        setError(msg);
      } finally {
        setSubmitting(false);
      }
      return;
    }
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
          panelCount < HEALING_COMIC_MIN_PANEL_COUNT
        ) {
          setError(`漫画章节数需要不小于 ${HEALING_COMIC_MIN_PANEL_COUNT}`);
          return;
        }
        const characterPrompt = comicThemeRequirement.trim();
        const sourceContent = comicSourceContent.trim();
        const rawText = [
          `书名：${cleanedTitle}`,
          characterPrompt ? `主角形象：${characterPrompt}` : "",
          sourceContent ? `自定义内容：\n${sourceContent}` : "",
        ]
          .filter(Boolean)
          .join("\n");
        setSubmitting(true);
        try {
          const res = await api.createTask({
            url: "manual://healing_comic",
            config: {
              category: "healing_comic",
              comic_title: cleanedTitle,
              comic_panel_count: panelCount,
              comic_character_prompt: characterPrompt || undefined,
              comic_theme_requirement: characterPrompt || undefined,
              comic_source_content: sourceContent || undefined,
              title: cleanedTitle,
              raw_text: rawText,
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
      const cleanedPrefacePhrase = comicPrefacePhrase.trim();
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

    if (mode === "articleBatch") {
      const { urls, duplicateCount } = articleLinkStats;
      if (urls.length === 0) {
        setError("请放入至少 1 条微信公众号文章链接");
        return;
      }
      if (urls.length > 100) {
        setError("公众号文章一次最多批量提交 100 条");
        return;
      }
      setSubmitting(true);
      try {
        const res = await api.createEmotionReferenceArticleTaskBatch({ urls });
        const first = res.created.find((item) => item.task_id)?.task_id;
        if (!first) {
          const duplicateHint =
            duplicateCount > 0 ? `已自动合并 ${duplicateCount} 条重复链接。` : "";
          setError(`没有创建新任务，可能链接已存在或公众号正文无法读取。${duplicateHint}`);
          return;
        }
        if (res.total_skipped > 0 || duplicateCount > 0) {
          const parts = [`已创建 ${res.total_created} 个公众号任务`];
          if (duplicateCount > 0) parts.push(`自动合并 ${duplicateCount} 条重复链接`);
          if (res.total_skipped > 0) parts.push(`跳过 ${res.total_skipped} 条`);
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
          category,
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
      setError("请填入 YouTube、小红书、微信视频号等视频链接，或直接粘贴视频文稿");
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
        url: isClassicalChinese ? `manual://${category}` : url.trim() || "manual://raw_text",
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
          选择书籍方向后，粘贴 YouTube、小红书、微信视频号等视频链接、公众号文章链接、已有文稿，或把候选链接批量放入自动生成库
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
                mode === "article"
                  ? "border-gold-400/60 bg-gold-500/10 text-warm-100"
                  : "border-gold-500/15 text-warm-400 hover:border-gold-400/30 hover:text-warm-200"
              }`}
            >
              <PencilLine className="h-3.5 w-3.5" strokeWidth={1.5} />
              公众号参考
            </button>
            <button
              type="button"
              onClick={() => switchMode("articleBatch")}
              className={`inline-flex items-center gap-1.5 rounded-full border px-4 py-1.5 text-xs tracking-widest transition-colors ${
                mode === "articleBatch"
                  ? "border-gold-400/60 bg-gold-500/10 text-warm-100"
                  : "border-gold-500/15 text-warm-400 hover:border-gold-400/30 hover:text-warm-200"
              }`}
            >
              <ListPlus className="h-3.5 w-3.5" strokeWidth={1.5} />
              公众号批量
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
              onClick={() => switchMode("longNovel")}
              className={`inline-flex items-center gap-1.5 rounded-full border px-4 py-1.5 text-xs tracking-widest transition-colors ${
                mode === "longNovel"
                  ? "border-gold-400/60 bg-gold-500/10 text-warm-100"
                  : "border-gold-500/15 text-warm-400 hover:border-gold-400/30 hover:text-warm-200"
              }`}
            >
              <BookOpenCheck className="h-3.5 w-3.5" strokeWidth={1.5} />
              长篇小说
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

          {mode !== "longNovel" ? (
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
          ) : (
            <div>
              <label
                htmlFor="long-novel-module"
                className="mb-2 flex items-center gap-2 text-[11px] tracking-widest text-warm-300"
              >
                <BookOpenCheck className="h-3.5 w-3.5 text-gold-300" strokeWidth={1.7} />
                长篇类型
              </label>
              <select
                id="long-novel-module"
                value={longNovelModule}
                onChange={(e) => setLongNovelModule(e.target.value)}
                className="w-full rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm text-warm-100 outline-none transition-colors focus:border-gold-400/50"
              >
                {LONG_NOVEL_MODULES.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                  </option>
                ))}
              </select>
              <p className="mt-2 text-xs leading-relaxed text-warm-500">
                {LONG_NOVEL_MODULES.find((item) => item.id === longNovelModule)?.description}
              </p>
            </div>
          )}

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

          {mode === "longNovel" ? (
            <div className="space-y-4">
              <div className="rounded-xl border border-gold-500/15 bg-base-700/35 p-4">
                <div className="text-[11px] tracking-widest text-gold-200">长篇小说模块</div>
                <p className="mt-2 text-xs leading-relaxed text-warm-500">
                  当前模块为“家长里短·妯娌”，用于婆媳、妯娌、分家、房产、养老、借钱、带娃等家庭利益矛盾，创建后进入长篇工作区生成圣经、大纲和章节。
                </p>
              </div>
              <div>
                <div className="mb-2 flex items-center justify-between gap-3">
                  <label htmlFor="long-novel-premise" className="block text-[11px] tracking-widest text-warm-300">
                    核心创意
                  </label>
                  {assistButton("premise")}
                </div>
                <textarea
                  id="long-novel-premise"
                  value={longNovelPremise}
                  onChange={(e) => setLongNovelPremise(e.target.value)}
                  placeholder="例如：大嫂照顾瘫痪婆婆三年，拆迁款下来后，婆婆却把房子过户给从没出过力的二儿子。"
                  rows={4}
                  className="w-full resize-none rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm leading-relaxed text-warm-100 placeholder:text-warm-500 outline-none transition-colors focus:border-gold-400/50"
                />
              </div>
              <div>
                <div className="mb-2 flex items-center justify-between gap-3">
                  <label htmlFor="long-novel-family" className="block text-[11px] tracking-widest text-warm-300">
                    家庭关系设定
                  </label>
                  {assistButton("family_setup")}
                </div>
                <textarea
                  id="long-novel-family"
                  value={longNovelFamilySetup}
                  onChange={(e) => setLongNovelFamilySetup(e.target.value)}
                  placeholder="例如：婆婆强势偏心，小儿媳嘴甜会算计，大儿媳老实能干，两个儿子一个愚孝一个圆滑。"
                  rows={3}
                  className="w-full resize-none rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm leading-relaxed text-warm-100 placeholder:text-warm-500 outline-none transition-colors focus:border-gold-400/50"
                />
              </div>
              <div>
                <div className="mb-2 flex items-center justify-between gap-3">
                  <label className="block text-[11px] tracking-widest text-warm-300">主要矛盾</label>
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] tracking-widest text-warm-500">{longNovelConflictTags.length} 个</span>
                    {assistButton("conflict_tags")}
                  </div>
                </div>
                <div className="flex flex-wrap gap-2">
                  {LONG_NOVEL_CONFLICT_OPTIONS.map((item) => {
                    const checked = longNovelConflictTags.includes(item);
                    return (
                      <button
                        key={item}
                        type="button"
                        onClick={() =>
                          setLongNovelConflictTags((prev) =>
                            prev.includes(item)
                              ? prev.filter((tag) => tag !== item)
                              : [...prev, item],
                          )
                        }
                        className={`rounded-full border px-3 py-1.5 text-xs transition-colors ${
                          checked
                            ? "border-gold-400/60 bg-gold-500/10 text-warm-100"
                            : "border-gold-500/15 text-warm-500 hover:border-gold-400/30 hover:text-warm-200"
                        }`}
                      >
                        {item}
                      </button>
                    );
                  })}
                </div>
              </div>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div>
                  <label htmlFor="long-novel-target-words" className="mb-2 block text-[11px] tracking-widest text-warm-300">
                    目标字数
                  </label>
                  <select
                    id="long-novel-target-words"
                    value={longNovelTargetWords}
                    onChange={(e) => setLongNovelTargetWords(Number(e.target.value))}
                    className="w-full rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm text-warm-100 outline-none transition-colors focus:border-gold-400/50"
                  >
                    <option value={30000}>30000 测试版</option>
                    <option value={80000}>80000 中篇</option>
                    <option value={200000}>200000 长篇</option>
                  </select>
                </div>
                <div>
                  <label htmlFor="long-novel-target-chapters" className="mb-2 block text-[11px] tracking-widest text-warm-300">
                    章节数
                  </label>
                  <select
                    id="long-novel-target-chapters"
                    value={longNovelTargetChapters}
                    onChange={(e) => setLongNovelTargetChapters(Number(e.target.value))}
                    className="w-full rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm text-warm-100 outline-none transition-colors focus:border-gold-400/50"
                  >
                    <option value={10}>10</option>
                    <option value={30}>30</option>
                    <option value={50}>50</option>
                    <option value={60}>60</option>
                  </select>
                </div>
              </div>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div>
                  <label htmlFor="long-novel-mode" className="mb-2 block text-[11px] tracking-widest text-warm-300">
                    生成强度
                  </label>
                  <select
                    id="long-novel-mode"
                    value={longNovelGenerationMode}
                    onChange={(e) =>
                      setLongNovelGenerationMode(e.target.value as "cheap" | "standard" | "high_quality")
                    }
                    className="w-full rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm text-warm-100 outline-none transition-colors focus:border-gold-400/50"
                  >
                    <option value="cheap">省钱模式</option>
                    <option value="standard">标准模式</option>
                    <option value="high_quality">高质量模式</option>
                  </select>
                </div>
                <div>
                  <label htmlFor="long-novel-style" className="mb-2 block text-[11px] tracking-widest text-warm-300">
                    风格
                  </label>
                  <select
                    id="long-novel-style"
                    value={longNovelStyle}
                    onChange={(e) => setLongNovelStyle(e.target.value)}
                    className="w-full rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm text-warm-100 outline-none transition-colors focus:border-gold-400/50"
                  >
                    {LONG_NOVEL_STYLE_OPTIONS.map((item) => (
                      <option key={item} value={item}>
                        {item}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              <div>
                <label htmlFor="long-novel-source-type" className="mb-2 block text-[11px] tracking-widest text-warm-300">
                  素材来源
                </label>
                <select
                  id="long-novel-source-type"
                  value={longNovelSourceType}
                  onChange={(e) => setLongNovelSourceType(e.target.value)}
                  className="w-full rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm text-warm-100 outline-none transition-colors focus:border-gold-400/50"
                >
                  <option value="manual">手动粘贴</option>
                  <option value="video_link">视频链接</option>
                  <option value="video_transcript">视频文稿</option>
                  <option value="wechat_article">公众号文章</option>
                  <option value="short_story">已有短篇</option>
                </select>
              </div>
              <div>
                <div className="mb-2 flex items-center justify-between gap-3">
                  <label htmlFor="long-novel-source" className="block text-[11px] tracking-widest text-warm-300">
                    素材正文
                  </label>
                  {assistButton("source_text")}
                </div>
                <textarea
                  id="long-novel-source"
                  value={longNovelSourceText}
                  onChange={(e) => setLongNovelSourceText(e.target.value)}
                  placeholder="可粘贴视频文稿、公众号正文、已有短篇或补充素材。"
                  rows={5}
                  className="w-full resize-none rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm leading-relaxed text-warm-100 placeholder:text-warm-500 outline-none transition-colors focus:border-gold-400/50"
                />
              </div>
              <div>
                <div className="mb-2 flex items-center justify-between gap-3">
                  <label htmlFor="long-novel-forbidden" className="block text-[11px] tracking-widest text-warm-300">
                    禁忌
                  </label>
                  {assistButton("forbidden_rules")}
                </div>
                <textarea
                  id="long-novel-forbidden"
                  value={longNovelForbiddenRules}
                  onChange={(e) => setLongNovelForbiddenRules(e.target.value)}
                  rows={3}
                  className="w-full resize-none rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm leading-relaxed text-warm-100 placeholder:text-warm-500 outline-none transition-colors focus:border-gold-400/50"
                />
              </div>
            </div>
          ) : mode === "single" || isReferenceArticle ? (
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
                      placeholder="YouTube / 小红书 / 微信视频号 / 抖音链接"
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

              {isReferenceArticle && !isReferenceArticleBatch && (
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

              {isReferenceArticleBatch && (
                <div>
                  <div className="mb-2 flex items-center justify-between gap-3">
                    <label
                      htmlFor="wechat-article-links"
                      className="block text-[11px] tracking-widest text-warm-300"
                    >
                      微信公众号文章链接
                    </label>
                    <span className="text-[10px] tracking-widest text-warm-500">
                      {articleLinkStats.urls.length}/100
                    </span>
                  </div>
                  <textarea
                    id="wechat-article-links"
                    value={articleLinks}
                    onChange={(e) => setArticleLinks(e.target.value)}
                    placeholder="每行一条，或直接粘贴一整段包含公众号链接的文本；仅支持公开可访问的 mp.weixin.qq.com 文章，最多 100 条。"
                    rows={10}
                    className="w-full resize-none rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm leading-relaxed text-warm-100 placeholder:text-warm-500 outline-none transition-colors focus:border-gold-400/50"
                  />
                  {articleLinkStats.duplicateCount > 0 && (
                    <p className="mt-2 text-xs leading-relaxed text-gold-200">
                      已自动合并 {articleLinkStats.duplicateCount} 条重复链接。
                    </p>
                  )}
                  <p className="mt-2 text-xs leading-relaxed text-warm-500">
                    系统会先逐条识别公众号正文，再统一创建心理情感类原创文章任务；读取失败的链接会跳过并显示原因。
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
                        : "如果已经有视频文稿，可以直接粘贴在这里，跳过链接解析步骤"
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
                    输入书名、章节数，可选填写主角形象；系统会参考服务端漫画数据库，自动规划封面和同系列章节配图，短句会直接写在图上。
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
                    step={1}
                    value={comicPanelCount}
                    onChange={(e) => setComicPanelCount(e.target.value.replace(/[^\d]/g, ""))}
                    placeholder={String(HEALING_COMIC_DEFAULT_PANEL_COUNT)}
                    className="w-full rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm leading-relaxed text-warm-100 placeholder:text-warm-500 outline-none transition-colors focus:border-gold-400/50"
                  />
                  <p className="mt-2 text-xs leading-relaxed text-warm-500">
                    默认 {HEALING_COMIC_DEFAULT_PANEL_COUNT} 章，可自定义任意章节数；每章会按内容生成 6 到 20 张同系列小故事漫画图。
                  </p>
                </div>
                <div>
                  <div className="mb-2 flex items-center justify-between gap-3">
                    <label
                      htmlFor="healing-comic-character-prompt"
                      className="block text-[11px] tracking-widest text-warm-300"
                    >
                      主角形象（可选）
                    </label>
                    <span className="text-[10px] tracking-widest text-warm-500">
                      {comicThemeRequirement.trim().length} 字
                    </span>
                  </div>
                  <textarea
                    id="healing-comic-character-prompt"
                    value={comicThemeRequirement}
                    onChange={(e) => setComicThemeRequirement(e.target.value)}
                    placeholder="例如：一只米白色原创小熊，圆耳朵，蓝灰围巾，小帆布包，眼神安静，所有章节保持同一形象。"
                    rows={4}
                    className="w-full resize-none rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm leading-relaxed text-warm-100 placeholder:text-warm-500 outline-none transition-colors focus:border-gold-400/50"
                  />
                  <p className="mt-2 text-xs leading-relaxed text-warm-500">
                    可留空；填写后会作为全书主角形象圣经，封面和所有章节图片都按同一形象延续。
                  </p>
                </div>
                <div>
                  <div className="mb-2 flex items-center justify-between gap-3">
                    <label
                      htmlFor="healing-comic-source-content"
                      className="block text-[11px] tracking-widest text-warm-300"
                    >
                      自定义内容（可选）
                    </label>
                    <span className="text-[10px] tracking-widest text-warm-500">
                      {comicSourceContent.trim().length} 字
                    </span>
                  </div>
                  <textarea
                    id="healing-comic-source-content"
                    value={comicSourceContent}
                    onChange={(e) => setComicSourceContent(e.target.value)}
                    placeholder="可粘贴一篇文章、公众号正文、随笔或已有文案；系统会提取主题、情绪、场景和细节，改造成同一套治愈漫画。"
                    rows={6}
                    className="w-full resize-none rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm leading-relaxed text-warm-100 placeholder:text-warm-500 outline-none transition-colors focus:border-gold-400/50"
                  />
                  <p className="mt-2 text-xs leading-relaxed text-warm-500">
                    留空则继续按书名生成；填写后会围绕文章内容创作，不额外增加提取 API 调用。
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
                    {comicPrefacePhrase.trim().length} 字
                  </span>
                </div>
                <input
                  id="comic-preface-phrase"
                  value={comicPrefacePhrase}
                  onChange={(e) => setComicPrefacePhrase(e.target.value)}
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
                    : mode === "articleBatch"
                      ? "批量抓取生成"
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
