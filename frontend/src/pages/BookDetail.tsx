import {
  ChangeEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { bookPath, bookshelfPath, taskShelfVersion, categoriesForShelf, type BookshelfVersion } from "@/constants/bookshelfVersions";
import { Link, useParams, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  Download,
  FileText,
  Image as ImageIcon,
  Loader2,
  Pause,
  Pencil,
  Eye,
  Save,
  Wand2,
  Check,
  Trash2,
  RefreshCcw,
} from "lucide-react";
import { api, ApiError } from "@/api/client";
import {
  DEFAULT_CATEGORIES,
  categoryIdForLabel,
  normalizeCategoryLabel,
  normalizeCategoryOptions,
} from "@/constants/categories";
import { WorkflowStepper } from "@/components/WorkflowStepper";
import { StuckBanner } from "@/components/StuckBanner";
import type {
  ChapterResponse,
  StageName,
  TaskCategoryOption,
  TaskStatusResponse,
} from "@/api/types";
import { toast } from "@/components/Toaster";

const STAGES: { name: StageName; label: string }[] = [
  { name: "writer", label: "1. 初稿" },
  { name: "polish", label: "2. 一润" },
  { name: "refine", label: "3. 二润" },
  { name: "format", label: "4. 终稿" },
];

const ACTIVE_STATUSES = new Set([
  "pending",
  "processing",
  "fetching_topics",
  "filtering_topics",
  "generating_outline",
  "writing_chapters",
  "fact_checking",
]);

const COMIC_IMAGE_RE = /^\s*!\[([^\]]*)\]\((?:comic_images\/)?(comic_preface\.png|comic_section_\d+(?:_\d+)?\.png)\)\s*$/;

function friendlyTaskError(message: string | null | undefined) {
  if (!message) return null;
  const normalized = message.toLowerCase();
  if (
    normalized.includes("insufficient_quota") ||
    normalized.includes("exceeded your current quota")
  ) {
    return "OpenAI 额度不足或账单不可用，请检查服务器当前 API Key 的余额、账单状态，或更换可用 Key 后重试。";
  }
  if (normalized.includes("rate_limit") || normalized.includes("429")) {
    return "OpenAI 请求被限流，请稍后重试，或降低并发后继续生成。";
  }
  if (normalized.includes("timeout") || normalized.includes("timed out")) {
    return "模型调用超时，任务已停止。可以稍后点击继续生成重试。";
  }
  return message;
}

interface PolishTrigger {
  chapterId: string;
  textareaEl: HTMLTextAreaElement;
  start: number;
  end: number;
  text: string;
  x: number;
  y: number;
}

export function BookDetail({ bookshelfVersion = "1.0" }: { bookshelfVersion?: BookshelfVersion }) {
  const navigate = useNavigate();
  const returnShelf = bookshelfPath(bookshelfVersion);
  const { id } = useParams<{ id: string }>();
  const [task, setTask] = useState<TaskStatusResponse | null>(null);
  const [chapters, setChapters] = useState<ChapterResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 编辑/查看模式
  const [editing, setEditing] = useState(false);
  const [metaTitle, setMetaTitle] = useState("");
  const [metaBookshelf, setMetaBookshelf] = useState("");
  const [metaCategory, setMetaCategory] = useState("");
  const [categories, setCategories] = useState<TaskCategoryOption[]>(
    categoriesForShelf(normalizeCategoryOptions(DEFAULT_CATEGORIES), bookshelfVersion),
  );
  const [metaDirty, setMetaDirty] = useState(false);
  const [savingMeta, setSavingMeta] = useState(false);

  // 阶段切换：终稿章节 vs 中间产物
  const [activeView, setActiveView] = useState<"final" | StageName>("final");
  const [stageContent, setStageContent] = useState("");
  const [stageLoading, setStageLoading] = useState(false);

  // 章节本地编辑态（已修改但未保存）
  const [dirty, setDirty] = useState<Record<string, boolean>>({});
  const [savingId, setSavingId] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<Record<string, number>>({});

  // 选段润色浮标
  const [polishTrigger, setPolishTrigger] = useState<PolishTrigger | null>(null);
  const [polishing, setPolishing] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [pausing, setPausing] = useState(false);
  const [coverSrc, setCoverSrc] = useState<string | null>(null);

  const pollRef = useRef<number | null>(null);
  // refresh 通过 ref 读取编辑态，避免每次输入都重建 refresh 而触发整页重新加载
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;

  // 拉任务状态 + 章节
  const refresh = useCallback(async () => {
    if (!id) return;
    try {
      const t = await api.getTask(id);
      if (taskShelfVersion(t) !== bookshelfVersion) {
        navigate(bookPath(t.id, taskShelfVersion(t)), { replace: true });
        return;
      }
      setTask(t);
      if (t.status === "completed") {
        const c = await api.getChapters(id);
        // 仅在未编辑过时同步章节，避免覆盖用户的本地编辑
        setChapters((prev) => {
          if (prev.length === 0) return c.chapters;
          if (Object.values(dirtyRef.current).some(Boolean)) return prev;
          return c.chapters;
        });
      }
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? `[${e.status}] ${e.detail}`
          : e instanceof Error
            ? e.message
            : "加载失败";
      setError(msg);
    }
  }, [id, bookshelfVersion, navigate]);

  const restartPolling = useCallback(() => {
    if (pollRef.current) window.clearInterval(pollRef.current);
    pollRef.current = window.setInterval(() => {
      if (!document.hidden) void refresh();
    }, 2500);
  }, [refresh]);

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    void refresh().finally(() => setLoading(false));
    pollRef.current = window.setInterval(() => {
      if (!document.hidden) void refresh();
    }, 2500);
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, [id, refresh]);

  useEffect(() => {
    let cancelled = false;
    api
      .getTaskCategories(bookshelfVersion)
      .then((res) => {
        if (!cancelled && res.items.length > 0) {
          setCategories(categoriesForShelf(normalizeCategoryOptions(res.items), bookshelfVersion));
        }
      })
      .catch(() => {
        if (!cancelled) setCategories(categoriesForShelf(categoriesForShelf(normalizeCategoryOptions(DEFAULT_CATEGORIES), bookshelfVersion), bookshelfVersion));
      });
    return () => {
      cancelled = true;
    };
  }, [bookshelfVersion]);

  // 任务完成或失败：停止轮询
  useEffect(() => {
    if (!task) return;
    const coverInFlight =
      task.cover_status === "queued" || task.cover_status === "generating";
    if ((task.status === "completed" || task.status === "failed" || task.status === "paused") && !coverInFlight) {
      if (pollRef.current) window.clearInterval(pollRef.current);
    }
  }, [task]);

  useEffect(() => {
    if (!task || metaDirty) return;
    setMetaTitle(task.title || chapters[0]?.title || "");
    setMetaBookshelf(normalizeCategoryLabel(task.bookshelf) || "");
    setMetaCategory(task.category || categoryIdForLabel(task.bookshelf, categories) || "");
  }, [task, chapters, metaDirty, categories]);

  useEffect(() => {
    let alive = true;
    let objectUrl: string | null = null;
    setCoverSrc(null);
    if (!id || !task?.cover_url) return undefined;

    api
      .getCoverBlob(id)
      .then((blob) => {
        if (!alive) return;
        objectUrl = URL.createObjectURL(blob);
        setCoverSrc(objectUrl);
      })
      .catch(() => {
        if (alive) setCoverSrc(null);
      });

    return () => {
      alive = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [id, task?.cover_url]);

  // 切到中间产物 tab：拉对应文件
  useEffect(() => {
    if (!id || activeView === "final") return;
    setStageLoading(true);
    api
      .getStage(id, activeView)
      .then((r) => setStageContent(r.content))
      .catch((e) => {
        const msg =
          e instanceof ApiError ? `[${e.status}] ${e.detail}` : "加载失败";
        setStageContent(`（${msg}）`);
      })
      .finally(() => setStageLoading(false));
  }, [id, activeView]);

  // 选段润色：从 textarea 取选区
  const handleTextareaSelect = useCallback(
    (chapter: ChapterResponse) =>
      (e: React.SyntheticEvent<HTMLTextAreaElement>) => {
        const ta = e.currentTarget;
        const start = ta.selectionStart;
        const end = ta.selectionEnd;
        if (end <= start) {
          setPolishTrigger(null);
          return;
        }
        const text = ta.value.slice(start, end).trim();
        if (text.length < 6) {
          setPolishTrigger(null);
          return;
        }
        // 浮标位置：放在 textarea 右上角附近
        const rect = ta.getBoundingClientRect();
        setPolishTrigger({
          chapterId: chapter.id,
          textareaEl: ta,
          start,
          end,
          text,
          x: rect.right - 80,
          y: rect.top + window.scrollY - 4,
        });
      },
    [],
  );

  const handleChapterChange = useCallback(
    (chapterId: string) => (e: ChangeEvent<HTMLTextAreaElement>) => {
      const v = e.target.value;
      setChapters((prev) =>
        prev.map((c) =>
          c.id === chapterId ? { ...c, content: v, word_count: v.length } : c,
        ),
      );
      setDirty((d) => ({ ...d, [chapterId]: true }));
    },
    [],
  );

  const handleTitleChange = useCallback(
    (chapterId: string) => (e: ChangeEvent<HTMLInputElement>) => {
      const v = e.target.value;
      setChapters((prev) =>
        prev.map((c) => (c.id === chapterId ? { ...c, title: v } : c)),
      );
      setDirty((d) => ({ ...d, [chapterId]: true }));
    },
    [],
  );

  const saveChapter = useCallback(
    async (chapter: ChapterResponse) => {
      if (!id) return;
      setSavingId(chapter.id);
      try {
        await api.updateChapter(id, chapter.chapter_num, {
          title: chapter.title,
          content: chapter.content,
        });
        setDirty((d) => ({ ...d, [chapter.id]: false }));
        setSavedAt((s) => ({ ...s, [chapter.id]: Date.now() }));
      } catch (e) {
        const msg =
          e instanceof ApiError
            ? `[${e.status}] ${e.detail}`
            : "保存失败";
        toast.error(`保存失败: ${msg}`);
      } finally {
        setSavingId(null);
      }
    },
    [id],
  );

  const runPolish = useCallback(async () => {
    if (!polishTrigger) return;
    const { chapterId, textareaEl, start, end, text } = polishTrigger;
    setPolishing(true);
    try {
      const r = await api.polishParagraph({ text });
      // 用润色后的文本替换原选区
      const target = chapters.find((c) => c.id === chapterId);
      if (!target) return;
      const newContent =
        target.content.slice(0, start) +
        r.polished +
        target.content.slice(end);
      setChapters((prev) =>
        prev.map((c) =>
          c.id === chapterId
            ? { ...c, content: newContent, word_count: newContent.length }
            : c,
        ),
      );
      setDirty((d) => ({ ...d, [chapterId]: true }));
      setPolishTrigger(null);
      // 让光标回到 textarea 替换段尾
      setTimeout(() => {
        textareaEl.focus();
        const newEnd = start + r.polished.length;
        textareaEl.setSelectionRange(newEnd, newEnd);
      }, 0);
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? `[${e.status}] ${e.detail}`
          : "润色失败";
      toast.error(`润色失败: ${msg}`);
    } finally {
      setPolishing(false);
    }
  }, [polishTrigger, chapters]);

  const saveMetadata = useCallback(async () => {
    if (!id) return;
    setSavingMeta(true);
    try {
      const next = await api.updateTaskMetadata(id, {
        title: metaTitle,
        category: metaCategory,
      });
      setTask(next);
      setMetaBookshelf(normalizeCategoryLabel(next.bookshelf) || "");
      setMetaCategory(next.category || "");
      setMetaDirty(false);
    } catch (e) {
      const msg = e instanceof ApiError ? `[${e.status}] ${e.detail}` : "保存失败";
      toast.error(`保存书名/类别失败：${msg}`);
    } finally {
      setSavingMeta(false);
    }
  }, [id, metaTitle, metaCategory]);

  const regenerateWholeTask = useCallback(async () => {
    if (!id || !task) return;
    const ok = window.confirm(
      "确定从头重新生成整篇文稿吗？系统会清空旧正文、章节、封面和阶段产物，但保留原始素材与分类。",
    );
    if (!ok) return;
    setRegenerating(true);
    try {
      const res = await api.regenerateTasks({ task_ids: [id] });
      const skipped = res.skipped.find((item) => item.task_id === id);
      if (res.total_regenerated === 0 || skipped) {
        toast.error(`重新生成未入队：${skipped?.reason || "当前任务暂不能重新生成"}`);
        return;
      }
      setDirty({});
      setSavedAt({});
      setPolishTrigger(null);
      setEditing(false);
      setActiveView("final");
      setChapters([]);
      setError(null);
      restartPolling();
      await refresh();
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? `[${e.status}] ${e.detail}`
          : "重新生成失败";
      toast.error(`重新生成失败：${msg}`);
    } finally {
      setRegenerating(false);
    }
  }, [id, task, restartPolling, refresh]);

  const dirtyCount = useMemo(
    () => Object.values(dirty).filter(Boolean).length,
    [dirty],
  );

  const handleDelete = useCallback(async () => {
    if (!id || !task) return;
    const ok = window.confirm(
      `确定删除这本《${chapters[0]?.title || "未命名书稿"}》吗？删除后不可恢复。`,
    );
    if (!ok) return;
    try {
      await api.deleteTask(id);
      window.location.href = returnShelf;
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : "删除失败";
      toast.error(`删除失败：${msg}`);
    }
  }, [id, task, chapters, returnShelf]);

  const handleRetry = useCallback(async () => {
    if (!id || !task) return;
    const ok = window.confirm("要继续生成这本书稿吗？系统会重新投递任务到队列。");
    if (!ok) return;
    try {
      await api.retryTask(id);
      setError(null);
      restartPolling();
      setLoading(true);
      await refresh();
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : "重试失败";
      toast.error(`继续生成失败：${msg}`);
    } finally {
      setLoading(false);
    }
  }, [id, task, refresh, restartPolling]);

  const handlePauseComic = useCallback(async () => {
    if (!id || !task) return;
    const ok = window.confirm(
      "确定暂停这本漫画吗？系统会在当前图片完成后停止，已生成内容会保留。",
    );
    if (!ok) return;
    setPausing(true);
    try {
      const next = await api.pauseComicTask(id);
      setTask(next);
      setError(null);
      restartPolling();
      await refresh();
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : "暂停失败";
      toast.error(`暂停生成失败：${msg}`);
    } finally {
      setPausing(false);
    }
  }, [id, task, refresh, restartPolling]);

  const handleDownload = useCallback(
    async (fmt: "md" | "docx", preview = false) => {
      if (!id) return;
      try {
        await api.downloadFile(id, fmt, preview);
      } catch (e) {
        const msg =
          e instanceof ApiError
            ? `[${e.status}] ${e.detail}`
            : "下载失败";
        toast.error(`下载失败：${msg}`);
      }
    },
    [id],
  );

  const handleDownloadCover = useCallback(async () => {
    if (!id) return;
    try {
      await api.downloadCoverFile(id);
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? `[${e.status}] ${e.detail}`
          : "下载失败";
      toast.error(`下载封面失败：${msg}`);
    }
  }, [id]);

  const handleRegenerateCover = useCallback(
    async (referenceMode: "refresh" | "reuse") => {
      if (!id) return;
      const ok = window.confirm(
        referenceMode === "refresh"
          ? "确定重新选择参考封面并重新生成封面吗？当前封面会被新图替换。"
          : "确定沿用当前参考封面并重新生成封面吗？当前封面会被新图替换。",
      );
      if (!ok) return;
      try {
        setLoading(true);
        await api.regenerateCover(id, { reference_mode: referenceMode });
        await refresh();
      } catch (e) {
        const msg = e instanceof ApiError ? e.detail : "重新生成封面失败";
        toast.error(`重新生成封面失败：${msg}`);
      } finally {
        setLoading(false);
      }
    },
    [id, refresh],
  );

  if (loading) {
    return (
      <div className="flex items-center gap-3 text-warm-300">
        <Loader2 className="h-4 w-4 animate-spin" strokeWidth={2} />
        <span className="text-sm tracking-widest">加载中</span>
      </div>
    );
  }

  if (error || !task) {
    return (
      <div className="card-warm p-8 text-warm-300">
        <h2 className="font-serif text-xl text-warm-100">无法加载任务</h2>
        <p className="mt-2 text-xs">{error}</p>
        <Link to={returnShelf} className="btn-ghost mt-6 inline-flex">
          <ArrowLeft className="h-3.5 w-3.5" strokeWidth={1.5} />
          返回{bookshelfVersion}书架
        </Link>
      </div>
    );
  }

  const isActive = ACTIVE_STATUSES.has(task.status);
  const isCompleted = task.status === "completed";
  const isFailed = task.status === "failed";
  const isPaused = task.status === "paused";
  const isHealingComic = ["healing_comic", "story_comic", "homophone_comic"].includes(task.category || "");
  const canPauseComic = isHealingComic && isActive;
  const coverInFlight =
    task.cover_status === "queued" || task.cover_status === "generating";
  const canRetry = isFailed || isPaused || (task.status === "pending" && task.progress < 100);
  const titleCandidate = metaTitle || task.title || chapters[0]?.title || "";
  const displayTitle =
    isFailed && (!titleCandidate || titleCandidate === "书稿生成中")
      ? "生成失败"
      : titleCandidate || "书稿生成中";
  const coverTitle = coverInFlight
    ? "封面生成中"
    : isFailed
      ? "生成失败"
      : isCompleted
        ? displayTitle
        : "书稿生成中";
  const failureMessage = friendlyTaskError(task.error_message);

  return (
    <div className="relative">
      <div className="mb-6 flex items-center justify-between gap-3">
        <Link
          to={returnShelf}
          className="inline-flex items-center gap-2 text-xs tracking-widest text-warm-400 transition-colors hover:text-warm-100"
        >
          <ArrowLeft className="h-3.5 w-3.5" strokeWidth={1.5} />
          返回{bookshelfVersion}书架
        </Link>
      </div>

      {/* 卡 0% 自动诊断（worker 没启动时弹横幅） */}
      <StuckBanner
        taskId={task.id}
        progress={task.progress}
        status={task.status}
        currentStep={task.current_step}
        onRetried={refresh}
      />

      {/* 顶部状态卡 */}
      <section className="card-warm mb-8 p-8">
        <div className="flex items-start justify-between gap-6">
          <div className="flex flex-1 gap-5">
            <div className="hidden h-36 w-28 shrink-0 overflow-hidden rounded-lg border border-gold-500/15 bg-base-700/50 sm:block">
              {coverSrc ? (
                <img
                  src={coverSrc}
                  alt="书籍封面"
                  className="h-full w-full object-cover"
                />
              ) : (
                <div className="flex h-full w-full flex-col items-center justify-center gap-2 px-3 text-center font-serif text-sm leading-tight text-warm-300">
                  <ImageIcon className="h-4 w-4 text-gold-300/70" strokeWidth={1.5} />
                  <span>{coverTitle}</span>
                </div>
              )}
            </div>
            <div className="min-w-0 flex-1">
              <h1 className="font-serif text-2xl tracking-wide text-warm-100">
                {displayTitle}
              </h1>
              <p className="mt-2 break-all text-xs tracking-wide text-warm-400">
                来源：{task.source_url}
              </p>
              {isCompleted && (
                <div className="mt-5 grid gap-3 md:grid-cols-[1.4fr_1fr_auto]">
                  <div>
                    <label
                      htmlFor="book-title"
                      className="mb-2 block text-[10px] tracking-widest text-warm-500"
                    >
                      书名
                    </label>
                    <input
                      id="book-title"
                      type="text"
                      value={metaTitle}
                      onChange={(e) => {
                        setMetaTitle(e.target.value);
                        setMetaDirty(true);
                      }}
                      className="w-full rounded-lg border border-gold-500/15 bg-base-700/50 px-3 py-2 text-sm text-warm-100 outline-none transition-colors focus:border-gold-400/45"
                    />
                  </div>
                  <div>
                    <label
                      htmlFor="book-bookshelf"
                      className="mb-2 block text-[10px] tracking-widest text-warm-500"
                    >
                      书籍类别
                    </label>
                    <select
                      id="book-bookshelf"
                      value={metaCategory}
                      onChange={(e) => {
                        setMetaCategory(e.target.value);
                        setMetaDirty(true);
                      }}
                      className="w-full rounded-lg border border-gold-500/15 bg-base-700/50 px-3 py-2 text-sm text-warm-100 placeholder:text-warm-500 outline-none transition-colors focus:border-gold-400/45"
                    >
                      {bookshelfVersion === "1.0" && <option value="">未分类</option>}
                      {metaCategory && !categories.some((item) => item.id === metaCategory) && (
                        <option value={metaCategory}>{task.category_label || metaCategory}（历史稿）</option>
                      )}
                      {categories.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.label}
                          {item.prompt_status === "pending" ? "（待优化）" : ""}
                        </option>
                      ))}
                    </select>
                    <p className="mt-1 text-[10px] tracking-widest text-warm-500">
                      当前类别：{normalizeCategoryLabel(metaBookshelf) || "未分类"}
                    </p>
                  </div>
                  <div className="flex items-end">
                    <button
                      type="button"
                      onClick={saveMetadata}
                      disabled={savingMeta || !metaDirty}
                      className="btn-ghost h-[38px] shrink-0 disabled:opacity-50"
                    >
                      {savingMeta ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={1.5} />
                      ) : (
                        <Save className="h-3.5 w-3.5" strokeWidth={1.5} />
                      )}
                      保存
                    </button>
                  </div>
                </div>
              )}
              {isFailed && failureMessage && (
                <div className="mt-4 rounded-lg border border-red-500/30 bg-red-500/8 px-4 py-2.5 text-xs leading-relaxed text-red-300">
                  <span className="font-medium text-red-200">失败原因：</span>
                  {failureMessage}
                </div>
              )}
              {isCompleted && task.cover_status === "failed" && task.cover_error && (
                <div className="mt-4 rounded-lg border border-gold-500/20 bg-gold-500/8 px-4 py-2.5 text-xs leading-relaxed text-warm-300">
                  <span className="font-medium text-warm-100">封面未生成：</span>
                  {task.cover_error}
                </div>
              )}
              </div>
          </div>

          <div className="flex flex-col gap-2">
            {canPauseComic ? (
              <button
                type="button"
                onClick={handlePauseComic}
                disabled={pausing}
                className="btn-ghost disabled:opacity-50"
              >
                {pausing ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={1.5} />
                ) : (
                  <Pause className="h-3.5 w-3.5" strokeWidth={1.5} />
                )}
                {pausing ? "暂停中" : "暂停生成"}
              </button>
            ) : null}
            {canRetry ? (
              <button type="button" onClick={handleRetry} className="btn-ghost">
                <RefreshCcw className="h-3.5 w-3.5" strokeWidth={1.5} />
                继续生成
              </button>
            ) : null}
            <button type="button" onClick={handleDelete} className="btn-ghost">
              <Trash2 className="h-3.5 w-3.5" strokeWidth={1.5} />
              删除任务
            </button>
            {isCompleted && (
              <>
                <button
                  type="button"
                  onClick={regenerateWholeTask}
                  disabled={regenerating}
                  className="btn-ghost disabled:opacity-50"
                >
                  {regenerating ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={1.5} />
                  ) : (
                    <RefreshCcw className="h-3.5 w-3.5" strokeWidth={1.5} />
                  )}
                  {regenerating ? "重新生成中" : "重新生成整篇"}
                </button>
                <button
                  type="button"
                  onClick={() => handleDownload("docx")}
                  className="btn-ghost"
                >
                  <FileText className="h-3.5 w-3.5" strokeWidth={1.5} />
                  Word
                </button>
                {isHealingComic && (
                  <button
                    type="button"
                    onClick={() => handleDownload("docx", true)}
                    className="btn-ghost"
                  >
                    <FileText className="h-3.5 w-3.5" strokeWidth={1.5} />
                    预览 Word
                  </button>
                )}
                <button
                  type="button"
                  onClick={handleDownloadCover}
                  disabled={!task.cover_url || coverInFlight}
                  className="btn-ghost disabled:opacity-50"
                >
                  <Download className="h-3.5 w-3.5" strokeWidth={1.5} />
                  下载封面
                </button>
                <button
                  type="button"
                  onClick={() => handleRegenerateCover("refresh")}
                  disabled={coverInFlight}
                  className="btn-ghost disabled:opacity-50"
                >
                  <RefreshCcw className="h-3.5 w-3.5" strokeWidth={1.5} />
                  换参考重生
                </button>
                <button
                  type="button"
                  onClick={() => handleRegenerateCover("reuse")}
                  disabled={coverInFlight}
                  className="btn-ghost disabled:opacity-50"
                >
                  <RefreshCcw className="h-3.5 w-3.5" strokeWidth={1.5} />
                  沿用参考重生
                </button>
              </>
            )}
          </div>
        </div>

        {/* 进度条 */}
        <div className="mt-6">
          <div className="mb-1.5 flex items-center justify-between text-[10px] tracking-widest text-warm-400">
            <span>{isCompleted ? "已完成" : isFailed ? "失败" : isPaused ? "已暂停" : "进行中"}</span>
            <span className="text-gold-300">{task.progress}%</span>
          </div>
          <div className="progress-track">
            <div
              className="progress-fill"
              style={{ width: `${task.progress}%` }}
            />
          </div>
        </div>

        {/* 分步进度 - 任务进行中或失败时展示 */}
        {(isActive || isFailed || isPaused) && (
          <div className="mt-6 rounded-xl border border-gold-500/8 bg-base-700/40 p-5">
            <div className="mb-4 text-[11px] tracking-widest text-warm-400">
              工作流状态
            </div>
            <WorkflowStepper
              progress={task.progress}
              currentStep={task.current_step}
              status={task.status}
              category={task.category}
              textWorkflowVersion={task.text_workflow_version}
            />
            {isActive && !isPaused && (
              <p className="mt-4 flex items-center gap-2 text-[10px] tracking-widest text-warm-500">
                <Loader2 className="h-3 w-3 animate-spin" strokeWidth={1.5} />
                每 2.5 秒自动刷新一次
              </p>
            )}
          </div>
        )}
      </section>

      {isCompleted && (
        <>
          {/* 视图切换 + 编辑/查看 */}
          <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap gap-2">
              <ViewTab
                active={activeView === "final"}
                onClick={() => setActiveView("final")}
              >
                终稿（章节视图）
              </ViewTab>
              {(task.text_workflow_version === "sheke2610_astra_pro_20261016"
                ? [{ name: "writer" as StageName, label: "Astra Pro 直出" }]
                : STAGES).map((s) => (
                <ViewTab
                  key={s.name}
                  active={activeView === s.name}
                  onClick={() => setActiveView(s.name)}
                >
                  {s.label}
                </ViewTab>
              ))}
            </div>

            {activeView === "final" && (
              <button
                type="button"
                onClick={() => setEditing((v) => !v)}
                className="btn-ghost"
              >
                {editing ? (
                  <>
                    <Eye className="h-3.5 w-3.5" strokeWidth={1.5} />
                    切到阅读模式
                  </>
                ) : (
                  <>
                    <Pencil className="h-3.5 w-3.5" strokeWidth={1.5} />
                    切到编辑模式
                  </>
                )}
              </button>
            )}
          </div>

          {dirtyCount > 0 && editing && (
            <div className="mb-4 rounded-lg border border-gold-500/20 bg-gold-500/8 px-4 py-2 text-xs tracking-wide text-warm-200">
              有 {dirtyCount} 章未保存，记得点击章节右上角的「保存」按钮
            </div>
          )}

          {activeView === "final" ? (
            <section className="space-y-8">
              {chapters.map((c) => (
                <ChapterCard
                  key={c.id}
                  taskId={task.id}
                  taskCategory={task.category}
                  chapter={c}
                  editing={editing}
                  isDirty={dirty[c.id] === true}
                  isSaving={savingId === c.id}
                  savedAt={savedAt[c.id]}
                  onContentChange={handleChapterChange(c.id)}
                  onTitleChange={handleTitleChange(c.id)}
                  onSave={() => saveChapter(c)}
                  onSelect={bookshelfVersion === "1.0" ? handleTextareaSelect(c) : undefined}
                />
              ))}
            </section>
          ) : (
            <section className="card-warm p-8">
              {stageLoading ? (
                <div className="flex items-center gap-3 text-warm-400">
                  <Loader2 className="h-4 w-4 animate-spin" strokeWidth={2} />
                  <span className="text-xs tracking-widest">加载中间产物</span>
                </div>
              ) : (
                <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-warm-200">
                  {stageContent}
                </pre>
              )}
            </section>
          )}
        </>
      )}

      {/* 浮动润色按钮 */}
      {bookshelfVersion === "1.0" && polishTrigger && editing && activeView === "final" && (
        <div
          className="fixed z-40 -translate-y-full"
          style={{ left: polishTrigger.x, top: polishTrigger.y }}
        >
          <button
            type="button"
            onClick={runPolish}
            disabled={polishing}
            className="flex items-center gap-1.5 rounded-full border border-gold-400/40 bg-base-800/95 px-3 py-1.5 text-xs tracking-widest text-gold-200 shadow-gold-soft backdrop-blur-md transition-colors hover:border-gold-300 hover:bg-base-700"
          >
            {polishing ? (
              <Loader2 className="h-3 w-3 animate-spin" strokeWidth={2} />
            ) : (
              <Wand2 className="h-3 w-3" strokeWidth={1.8} />
            )}
            <span>{polishing ? "润色中" : "润色这段"}</span>
          </button>
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────
// 子组件
// ─────────────────────────────────────────

function ViewTab({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-full border px-4 py-1.5 text-xs tracking-widest transition-colors ${
        active
          ? "border-gold-400/60 bg-gold-500/10 text-warm-100"
          : "border-gold-500/15 text-warm-400 hover:border-gold-400/30 hover:text-warm-200"
      }`}
    >
      {children}
    </button>
  );
}

function TaskInlineImage({
  taskId,
  filename,
  alt,
  preview,
}: {
  taskId: string;
  filename: string;
  alt: string;
  preview: boolean;
}) {
  const [src, setSrc] = useState<string | null>(null);
  const [downloading, setDownloading] = useState<"original" | "preview" | null>(null);

  useEffect(() => {
    let alive = true;
    let objectUrl: string | null = null;
    setSrc(null);
    const loadImage = preview
      ? api
          .getTaskImagePreviewBlob(taskId, filename)
          .catch(() => api.getTaskImageBlob(taskId, filename))
      : api.getTaskImageBlob(taskId, filename);
    loadImage
      .then((blob) => {
        if (!alive) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      })
      .catch(() => {
        if (alive) setSrc(null);
      });
    return () => {
      alive = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [taskId, filename, preview]);

  if (!src) {
    return (
      <div className="flex min-h-[180px] items-center justify-center rounded-lg border border-gold-500/10 bg-base-700/35 text-xs tracking-widest text-warm-500">
        配图加载中
      </div>
    );
  }

  const downloadOriginal = async () => {
    setDownloading("original");
    try {
      await api.downloadTaskImageFile(taskId, filename);
    } catch (e) {
      const msg = e instanceof ApiError ? `[${e.status}] ${e.detail}` : "下载失败";
      toast.error(`下载原图失败：${msg}`);
    } finally {
      setDownloading(null);
    }
  };

  const downloadPreview = async () => {
    setDownloading("preview");
    try {
      await api.downloadTaskImagePreviewFile(taskId, filename);
    } catch (e) {
      const msg = e instanceof ApiError ? `[${e.status}] ${e.detail}` : "下载失败";
      toast.error(`下载预览版失败：${msg}`);
    } finally {
      setDownloading(null);
    }
  };

  return (
    <figure className="group relative overflow-hidden rounded-lg border border-gold-500/10 bg-base-700/30">
      {preview && (
        <div className="absolute right-3 top-3 z-10 flex gap-2 opacity-0 transition-opacity group-hover:opacity-100">
          <button
            type="button"
            title="下载原图"
            onClick={downloadOriginal}
            disabled={downloading !== null}
            className="inline-flex items-center gap-1 rounded-full border border-gold-400/30 bg-base-900/85 px-2.5 py-1 text-[11px] tracking-wide text-warm-100 shadow-sm backdrop-blur transition-colors hover:border-gold-300/70 disabled:opacity-60"
          >
            {downloading === "original" ? (
              <Loader2 className="h-3 w-3 animate-spin" strokeWidth={1.5} />
            ) : (
              <Download className="h-3 w-3" strokeWidth={1.5} />
            )}
            原图
          </button>
          <button
            type="button"
            title="下载预览版"
            onClick={downloadPreview}
            disabled={downloading !== null}
            className="inline-flex items-center gap-1 rounded-full border border-gold-400/30 bg-base-900/85 px-2.5 py-1 text-[11px] tracking-wide text-warm-100 shadow-sm backdrop-blur transition-colors hover:border-gold-300/70 disabled:opacity-60"
          >
            {downloading === "preview" ? (
              <Loader2 className="h-3 w-3 animate-spin" strokeWidth={1.5} />
            ) : (
              <Download className="h-3 w-3" strokeWidth={1.5} />
            )}
            预览
          </button>
        </div>
      )}
      <img src={src} alt={alt} className="w-full object-cover" />
    </figure>
  );
}

interface ChapterCardProps {
  taskId: string;
  taskCategory?: string | null;
  chapter: ChapterResponse;
  editing: boolean;
  isDirty: boolean;
  isSaving: boolean;
  savedAt: number | undefined;
  onContentChange: (e: ChangeEvent<HTMLTextAreaElement>) => void;
  onTitleChange: (e: ChangeEvent<HTMLInputElement>) => void;
  onSave: () => void;
  onSelect?: (e: React.SyntheticEvent<HTMLTextAreaElement>) => void;
}

function ChapterCard({
  taskId,
  taskCategory,
  chapter,
  editing,
  isDirty,
  isSaving,
  savedAt,
  onContentChange,
  onTitleChange,
  onSave,
  onSelect,
}: ChapterCardProps) {
  const recentlySaved = savedAt && Date.now() - savedAt < 4000;
  const useComicPreview = ["healing_comic", "story_comic", "homophone_comic"].includes(taskCategory || "");

  return (
    <article className="card-warm p-8" id={`chapter-${chapter.chapter_num}`}>
      <header className="mb-4 flex items-start justify-between gap-4">
        {editing ? (
          <input
            type="text"
            value={chapter.title}
            onChange={onTitleChange}
            className="flex-1 border-b border-transparent bg-transparent font-serif text-xl tracking-wide text-warm-100 outline-none transition-colors focus:border-gold-400/40"
          />
        ) : (
          <h2 className="flex-1 font-serif text-xl tracking-wide text-warm-100">
            {chapter.title}
          </h2>
        )}

        {editing && (
          <button
            type="button"
            onClick={onSave}
            disabled={isSaving || !isDirty}
            className="btn-ghost shrink-0 disabled:opacity-50"
          >
            {isSaving ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={1.5} />
            ) : recentlySaved ? (
              <Check className="h-3.5 w-3.5 text-gold-300" strokeWidth={2} />
            ) : (
              <Save className="h-3.5 w-3.5" strokeWidth={1.5} />
            )}
            {isSaving ? "保存中" : recentlySaved ? "已保存" : "保存"}
          </button>
        )}
      </header>

      {editing ? (
        <textarea
          value={chapter.content}
          onChange={onContentChange}
          onMouseUp={onSelect}
          onKeyUp={onSelect}
          rows={Math.max(8, Math.ceil(chapter.content.length / 80))}
          className="w-full resize-y rounded-lg border border-gold-500/10 bg-base-700/40 px-4 py-3 text-sm leading-relaxed text-warm-200 outline-none transition-colors focus:border-gold-400/40"
          spellCheck={false}
        />
      ) : (
        <div className="space-y-3 text-sm leading-relaxed text-warm-200">
          {chapter.content.split("\n").map((line, index) => {
            const imageMatch = COMIC_IMAGE_RE.exec(line);
            if (imageMatch) {
              return (
                <TaskInlineImage
                  key={`${imageMatch[2]}-${index}`}
                  taskId={taskId}
                  filename={imageMatch[2]}
                  alt={imageMatch[1] || "章节配图"}
                  preview={useComicPreview}
                />
              );
            }
            return line ? (
              <p key={index} className="whitespace-pre-wrap">
                {line}
              </p>
            ) : (
              <br key={index} />
            );
          })}
        </div>
      )}

      <p className="mt-6 text-[10px] tracking-widest text-warm-500">
        {chapter.word_count.toLocaleString()} 字
        {editing && isDirty && (
          <span className="ml-3 text-gold-400">未保存</span>
        )}
      </p>
    </article>
  );
}
