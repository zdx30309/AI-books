import {
  ChangeEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
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
  ShieldCheck,
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
import { GeneralLongformPanel } from "@/components/GeneralLongformPanel";
import { publicTaskError, publicTaskStep } from "@/utils/taskStatus";
import type {
  AigcQualityReport,
  ChapterResponse,
  SimilaritySummary,
  StageName,
  TaskCategoryOption,
  TaskStatusResponse,
} from "@/api/types";

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
  return publicTaskError(message);
}

function downloadPlainText(text: string, filename: string) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
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

export function BookDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
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
    normalizeCategoryOptions(DEFAULT_CATEGORIES),
  );
  const [metaDirty, setMetaDirty] = useState(false);
  const [savingMeta, setSavingMeta] = useState(false);

  // 阶段切换：终稿章节 vs 中间产物
  const [activeView, setActiveView] = useState<"final" | StageName>("final");
  const [stageContent, setStageContent] = useState("");
  const [stageLoading, setStageLoading] = useState(false);
  const [aigcReport, setAigcReport] = useState<AigcQualityReport | null>(null);
  const [manualOriginal, setManualOriginal] = useState("");

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
  // 轮询读取最新的编辑态，但不把它放进 refresh 的依赖：否则每敲一个字都会
  // 重建 refresh、重跑加载 effect，整页切回“加载中”，输入框随之失焦。
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;

  useEffect(() => {
    if (id && task?.source_url === "manual://public-domain-classical-vernacular") {
      navigate(`/public-domain-classical-vernacular/${id}`, { replace: true });
    }
  }, [id, navigate, task?.source_url]);

  // 拉任务状态 + 章节
  const refresh = useCallback(async () => {
    if (!id) return;
    try {
      const t = await api.getTask(id);
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
  }, [id]);

  const restartPolling = useCallback(() => {
    if (pollRef.current) window.clearInterval(pollRef.current);
    pollRef.current = window.setInterval(() => void refresh(), 2500);
  }, [refresh]);

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    void refresh().finally(() => setLoading(false));
    pollRef.current = window.setInterval(() => void refresh(), 2500);
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, [id, refresh]);

  useEffect(() => {
    let cancelled = false;
    api
      .getTaskCategories()
      .then((res) => {
        if (!cancelled && res.items.length > 0) {
          setCategories(normalizeCategoryOptions(res.items));
        }
      })
      .catch(() => {
        if (!cancelled) setCategories(normalizeCategoryOptions(DEFAULT_CATEGORIES));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // 任务完成或失败：停止轮询
  useEffect(() => {
    if (!task) return;
    const coverInFlight =
      task.cover_status === "queued" || task.cover_status === "generating";
    if (
      (task.status === "completed" ||
        task.status === "failed" ||
        task.status === "paused" ||
        task.status === "manual_review") &&
      !coverInFlight
    ) {
      if (pollRef.current) window.clearInterval(pollRef.current);
    }
  }, [task]);

  useEffect(() => {
    let cancelled = false;
    if (!id || !task?.aigc_report_available) {
      setAigcReport(null);
      return undefined;
    }
    api
      .getAigcReport(id)
      .then((report) => {
        if (!cancelled) setAigcReport(report);
      })
      .catch(() => {
        if (!cancelled) setAigcReport(null);
      });
    return () => {
      cancelled = true;
    };
  }, [id, task?.aigc_report_available, task?.aigc_status]);

  useEffect(() => {
    let cancelled = false;
    if (!id || task?.status !== "manual_review") {
      setManualOriginal("");
      return undefined;
    }
    api
      .getStage(id, "format")
      .then((stage) => {
        if (!cancelled) setManualOriginal(stage.content);
      })
      .catch(() => {
        if (!cancelled) setManualOriginal("");
      });
    return () => {
      cancelled = true;
    };
  }, [id, task?.status]);

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
        alert(`保存失败: ${msg}`);
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
      alert(`润色失败: ${msg}`);
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
      alert(`保存书名/书架失败：${msg}`);
    } finally {
      setSavingMeta(false);
    }
  }, [id, metaTitle, metaCategory]);

  const regenerateWholeTask = useCallback(async () => {
    if (!id || !task) return;
    const ok = window.confirm(
      task.editor_v05_status === "similarity_blocked"
        ? "确认重新生成一次整篇文稿吗？首稿和报告将保留备份，费用累计不重置；新稿若仍不低于50%，将停止并提示更换视频。重新生成会产生模型费用。"
        : "确定从头重新生成整篇文稿吗？系统会清空旧正文、章节、封面和阶段产物，但保留原始素材与分类。",
    );
    if (!ok) return;
    setRegenerating(true);
    try {
      const res = await api.regenerateTasks({ task_ids: [id] });
      const skipped = res.skipped.find((item) => item.task_id === id);
      if (res.total_regenerated === 0 || skipped) {
        alert(`重新生成未入队：${skipped?.reason || "当前任务暂不能重新生成"}`);
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
      alert(`重新生成失败：${msg}`);
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
      window.location.href = "/bookshelf";
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : "删除失败";
      alert(`删除失败：${msg}`);
    }
  }, [id, task, chapters]);

  const handleRetry = useCallback(async () => {
    if (!id || !task) return;
    const aigcOnly =
      task.aigc_required &&
      task.aigc_report_available &&
      (task.status === "manual_review" || task.aigc_status === "technical_failed");
    const ok = window.confirm(
      aigcOnly
        ? "仅重新检查当前终稿吗？系统会复用已有稿件，不会重新生成整篇。"
        : "要继续生成这本书稿吗？系统会重新投递任务到队列。",
    );
    if (!ok) return;
    try {
      if (aigcOnly) {
        await api.retryTaskAigc(id, task.category === "public_domain_rewrite");
      } else {
        await api.retryTask(id);
      }
      setError(null);
      restartPolling();
      setLoading(true);
      await refresh();
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : "重试失败";
      alert(`继续生成失败：${msg}`);
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
      alert(`暂停生成失败：${msg}`);
    } finally {
      setPausing(false);
    }
  }, [id, task, refresh, restartPolling]);

  const handleDownload = useCallback(
    async (fmt: "md" | "docx" | "txt", preview = false) => {
      if (!id) return;
      try {
        await api.downloadFile(id, fmt, preview);
      } catch (e) {
        const msg =
          e instanceof ApiError
            ? `[${e.status}] ${e.detail}`
            : "下载失败";
        alert(`下载失败：${msg}`);
      }
    },
    [id],
  );

  // Similarity audit summary, loaded so the verdict is visible without a download.
  const [similarity, setSimilarity] = useState<SimilaritySummary | null>(null);
  useEffect(() => {
    let live = true;
    if (!id || !task || !["completed", "manual_review"].includes(task.status)) {
      setSimilarity(null);
      return;
    }
    api
      .getSimilarityReportData(id)
      .then((data) => {
        if (live) setSimilarity(data);
      })
      .catch(() => {
        if (live) setSimilarity(null);
      });
    return () => {
      live = false;
    };
  }, [id, task?.status, task?.editor_v05_status]);

  const handleDownloadSimilarityReport = useCallback(async () => {
    if (!id) return;
    try {
      await api.downloadSimilarityReport(id);
    } catch (e) {
      const msg =
        e instanceof ApiError ? `[${e.status}] ${e.detail}` : "下载失败";
      alert(`审查报告下载失败：${msg}`);
    }
  }, [id]);

  const handleDownloadCover = useCallback(async () => {
    if (!id) return;
    try {
      await api.downloadCoverFile(id);
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? `[${e.status}] ${e.detail}`
          : "下载失败";
      alert(`下载封面失败：${msg}`);
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
        alert(`重新生成封面失败：${msg}`);
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
        <Link to="/bookshelf" className="btn-ghost mt-6 inline-flex">
          <ArrowLeft className="h-3.5 w-3.5" strokeWidth={1.5} />
          返回书架
        </Link>
      </div>
    );
  }

  if (task.category === "general_longform" && id) {
    return <GeneralLongformPanel taskId={id} />;
  }

  const isActive = ACTIVE_STATUSES.has(task.status);
  const isCompleted = task.status === "completed";
  const isFailed = task.status === "failed";
  const isPaused = task.status === "paused";
  const isManualReview = task.status === "manual_review";
  const similarityBlocked = task.editor_v05_status === "similarity_blocked";
  const videoRejected = Boolean(task.editor_v05_video_rejected);
  const isHealingComic = ["healing_comic", "story_comic", "homophone_comic"].includes(
    task.category || "",
  );
  const canPauseComic = isHealingComic && isActive;
  const coverInFlight =
    task.cover_status === "queued" || task.cover_status === "generating";
  const canRetry = !similarityBlocked && !videoRejected && (isFailed || isPaused || isManualReview || (task.status === "pending" && task.progress < 100));
  const isAigcOnlyRetry =
    task.aigc_required &&
    task.aigc_report_available &&
    (isManualReview || task.aigc_status === "technical_failed");
  const bestSafeCandidate = aigcReport?.bestSafeCandidate;
  const bestSafeCandidateText =
    bestSafeCandidate && typeof bestSafeCandidate.text === "string"
      ? bestSafeCandidate.text
      : "";
  const reportDrivenCandidateText =
    typeof aigcReport?.reportDrivenCandidateText === "string"
      ? aigcReport.reportDrivenCandidateText
      : "";
  const manualCandidateText = reportDrivenCandidateText || bestSafeCandidateText;
  const detectorProbeArchive =
    task.aigc_status === "detector_probe_ready"
    || task.aigc_status === "detector_probe_hit"
    || task.aigc_status === "detector_probe_no_hit"
    || task.aigc_status === "positive_transfer_ready"
    || task.aigc_status === "positive_transfer_hit"
    || task.aigc_status === "positive_transfer_no_hit";
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
          to="/bookshelf"
          className="inline-flex items-center gap-2 text-xs tracking-widest text-warm-400 transition-colors hover:text-warm-100"
        >
          <ArrowLeft className="h-3.5 w-3.5" strokeWidth={1.5} />
          返回书架
        </Link>
      </div>

      {/* 卡 0% 自动诊断（worker 没启动时弹横幅） */}
      <StuckBanner
        taskId={task.id}
        progress={task.progress}
        status={task.status}
        currentStep={publicTaskStep(task.current_step, task.status, task.progress)}
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
              {task.aigc_required && (
                <p
                  className={`mt-2 text-xs tracking-wide ${
                    task.aigc_status === "passed" ? "text-emerald-300" : "text-gold-200"
                  }`}
                >
                  质量检查：{task.aigc_status === "passed" ? "已通过" : "处理中"}
                </p>
              )}
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
                      书架
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
                      <option value="">未分类</option>
                      {categories.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.label}
                          {item.prompt_status === "pending" ? "（待优化）" : ""}
                        </option>
                      ))}
                    </select>
                    <p className="mt-1 text-[10px] tracking-widest text-warm-500">
                      当前书架：{normalizeCategoryLabel(metaBookshelf) || "未分类"}
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
              {isManualReview && (
                <div className="mt-4 rounded-lg border border-gold-400/30 bg-gold-500/8 px-4 py-3 text-xs leading-relaxed text-warm-200">
                  <span className="font-medium text-gold-100">当前稿件已保留：</span>
                  {failureMessage || "系统正在继续处理当前任务。"}
                  <div className="mt-1 text-warm-400">
                    系统会从已保存的进度继续，不需要重复提交。
                  </div>
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
                {isAigcOnlyRetry ? "继续检查" : "继续生成"}
              </button>
            ) : null}
            {similarityBlocked && !videoRejected && (
              <button type="button" onClick={regenerateWholeTask} disabled={regenerating} className="btn-ghost disabled:opacity-50">
                <RefreshCcw className="h-3.5 w-3.5" strokeWidth={1.5} />
                {regenerating ? "重新生成中" : "人工确认重新生成整篇"}
              </button>
            )}
            {(similarityBlocked || videoRejected) && (
              <button type="button" onClick={handleDownloadSimilarityReport} className="btn-ghost">
                <ShieldCheck className="h-3.5 w-3.5" strokeWidth={1.5} />
                相似度审查报告
              </button>
            )}
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
                <button
                  type="button"
                  onClick={() => handleDownload("txt")}
                  className="btn-ghost"
                >
                  <FileText className="h-3.5 w-3.5" strokeWidth={1.5} />
                  TXT
                </button>
                <button
                  type="button"
                  onClick={() => handleDownload("md")}
                  className="btn-ghost"
                >
                  <FileText className="h-3.5 w-3.5" strokeWidth={1.5} />
                  Markdown
                </button>
                <button
                  type="button"
                  onClick={handleDownloadSimilarityReport}
                  className="btn-ghost"
                  title="下载本轮的文本相似度审查报告 Word"
                >
                  <ShieldCheck className="h-3.5 w-3.5" strokeWidth={1.5} />
                  相似度审查报告
                  {similarity && similarity.applicable ? (
                    <span
                      className={
                        similarity.overall_verdict === "高危"
                          ? "ml-1 text-rose-400"
                          : "ml-1 text-emerald-400"
                      }
                    >
                      {similarity.max_composite.toFixed(1)}% {similarity.overall_verdict}
                      {similarity.overall_boundary ? similarity.overall_boundary : ""}
                    </span>
                  ) : null}
                </button>
                {task.ai_or_finalizer_status === "completed" && (
                  <button
                    type="button"
                    className="btn-ghost"
                    onClick={async () => {
                      if (!id) return;
                      try {
                        await api.downloadAiOrDelivery(id);
                      } catch (error) {
                        alert(error instanceof ApiError ? error.detail : "降AI纠错交付包下载失败");
                      }
                    }}
                  >
                    <Download className="h-3.5 w-3.5" strokeWidth={1.5} />
                    降AI纠错交付包
                  </button>
                )}
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
            <span>{isCompleted ? "已完成" : similarityBlocked || videoRejected ? "审核未通过" : isManualReview ? "处理中" : isFailed ? "暂时中断" : isPaused ? "已暂停" : "进行中"}</span>
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
        {(isActive || isFailed || isPaused || isManualReview) && (
          <div className="mt-6 rounded-xl border border-gold-500/8 bg-base-700/40 p-5">
            <div className="mb-4 text-[11px] tracking-widest text-warm-400">
              工作流状态
            </div>
            <WorkflowStepper
              progress={task.progress}
              currentStep={publicTaskStep(task.current_step, task.status, task.progress)}
              status={task.status}
              category={task.category}
              aigcRequired={task.aigc_required}
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

      {isManualReview && (
        <section className="card-warm mb-8 p-6">
          <h2 className="font-serif text-xl text-warm-50">{videoRejected ? "此视频不可用，建议更换视频" : similarityBlocked ? "相似度审核未通过，等待人工确认" : "任务正在处理"}</h2>
          <p className="mt-2 text-xs leading-6 text-warm-400">
            {videoRejected ? "重新生成后相似度仍不低于50%，已停止，不再继续生成或调用降AI。"
              : similarityBlocked ? "已停在降AI之前，不会自动重写。请先下载相似度报告，再决定是否重新生成一次。"
              : "当前已保存的稿件和进度会被保留，系统会自动从断点继续。"}
          </p>
        </section>
      )}

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
              {STAGES.map((s) => (
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
                  onSelect={handleTextareaSelect(c)}
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
      {polishTrigger && editing && activeView === "final" && (
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
      alert(`下载原图失败：${msg}`);
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
      alert(`下载预览版失败：${msg}`);
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
  onSelect: (e: React.SyntheticEvent<HTMLTextAreaElement>) => void;
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
  const useComicPreview = ["healing_comic", "story_comic", "homophone_comic"].includes(
    taskCategory || "",
  );

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
