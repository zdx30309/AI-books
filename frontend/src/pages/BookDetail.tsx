import {
  ChangeEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowLeft,
  Download,
  FileText,
  Loader2,
  Pencil,
  Eye,
  Save,
  Wand2,
  Check,
  Trash2,
  RefreshCcw,
} from "lucide-react";
import { api, ApiError } from "@/api/client";
import { WorkflowStepper } from "@/components/WorkflowStepper";
import { StuckBanner } from "@/components/StuckBanner";
import type {
  ChapterResponse,
  StageName,
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
  const [task, setTask] = useState<TaskStatusResponse | null>(null);
  const [chapters, setChapters] = useState<ChapterResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 编辑/查看模式
  const [editing, setEditing] = useState(false);

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
  const [coverGenerating, setCoverGenerating] = useState(false);

  const pollRef = useRef<number | null>(null);

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
          if (Object.values(dirty).some(Boolean)) return prev;
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
  }, [id, dirty]);

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    void refresh().finally(() => setLoading(false));
    pollRef.current = window.setInterval(() => void refresh(), 2500);
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, [id, refresh]);

  // 任务完成或失败：停止轮询
  useEffect(() => {
    if (!task) return;
    if (task.status === "completed" || task.status === "failed") {
      if (pollRef.current) window.clearInterval(pollRef.current);
    }
  }, [task]);

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
    const ok = window.confirm("要继续生成这本书稿吗？系统会重新投递任务到队列。");
    if (!ok) return;
    try {
      await api.retryTask(id);
      setError(null);
      setLoading(true);
      await refresh();
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : "重试失败";
      alert(`继续生成失败：${msg}`);
    } finally {
      setLoading(false);
    }
  }, [id, task, refresh]);

  const handleDownload = useCallback(
    async (fmt: "md" | "txt") => {
      if (!id) return;
      try {
        await api.downloadFile(id, fmt);
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

  const handleRegenerateCover = useCallback(async () => {
    if (!id) return;
    setCoverGenerating(true);
    try {
      const updated = await api.regenerateCover(id);
      setTask(updated);
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? `[${e.status}] ${e.detail}`
          : "封面生成失败";
      alert(`封面生成失败：${msg}`);
    } finally {
      setCoverGenerating(false);
    }
  }, [id]);

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

  const isActive = ACTIVE_STATUSES.has(task.status);
  const isCompleted = task.status === "completed";
  const isFailed = task.status === "failed";
  const canRetry = isFailed || (task.status === "pending" && task.progress < 100);

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
        onRetried={refresh}
      />

      {/* 顶部状态卡 */}
      <section className="card-warm mb-8 p-8">
        <div className="flex items-start justify-between gap-6">
          <div className="flex flex-1 gap-5">
            <div className="hidden h-36 w-28 shrink-0 overflow-hidden rounded-lg border border-gold-500/15 bg-base-700/50 sm:block">
              {task.cover_url ? (
                <img
                  src={task.cover_url}
                  alt={task.title || chapters[0]?.title || "书稿封面"}
                  className="h-full w-full object-cover"
                />
              ) : (
                <div className="flex h-full w-full items-center justify-center px-3 text-center font-serif text-sm leading-tight text-warm-300">
                  {isCompleted
                    ? task.title || chapters[0]?.title || "封面待生成"
                    : "书稿生成中"}
                </div>
              )}
            </div>
            <div className="min-w-0 flex-1">
              <h1 className="font-serif text-2xl tracking-wide text-warm-100">
                {task.title || chapters[0]?.title || "书稿生成中"}
              </h1>
              <p className="mt-2 break-all text-xs tracking-wide text-warm-400">
                来源：{task.source_url}
              </p>
              {isFailed && task.error_message && (
                <div className="mt-4 rounded-lg border border-red-500/30 bg-red-500/8 px-4 py-2.5 text-xs text-red-300">
                  {task.error_message}
                </div>
              )}
              {isCompleted && !task.cover_url && (
                <div className="mt-4 rounded-lg border border-gold-500/20 bg-gold-500/8 px-4 py-2.5 text-xs text-warm-300">
                  封面尚未生成。若使用 gpt-image-2，请先完成 OpenAI 组织验证。
                </div>
              )}
              </div>
          </div>

          <div className="flex flex-col gap-2">
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
                  onClick={handleRegenerateCover}
                  disabled={coverGenerating}
                  className="btn-ghost disabled:opacity-60"
                >
                  {coverGenerating ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={1.5} />
                  ) : (
                    <Wand2 className="h-3.5 w-3.5" strokeWidth={1.5} />
                  )}
                  {task.cover_url ? "重生成封面" : "生成封面"}
                </button>
                <button
                  type="button"
                  onClick={() => handleDownload("md")}
                  className="btn-ghost"
                >
                  <Download className="h-3.5 w-3.5" strokeWidth={1.5} />
                  Markdown
                </button>
                <button
                  type="button"
                  onClick={() => handleDownload("txt")}
                  className="btn-ghost"
                >
                  <FileText className="h-3.5 w-3.5" strokeWidth={1.5} />
                  纯文本
                </button>
              </>
            )}
          </div>
        </div>

        {/* 进度条 */}
        <div className="mt-6">
          <div className="mb-1.5 flex items-center justify-between text-[10px] tracking-widest text-warm-400">
            <span>{isCompleted ? "已完成" : isFailed ? "失败" : "进行中"}</span>
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
        {(isActive || isFailed) && (
          <div className="mt-6 rounded-xl border border-gold-500/8 bg-base-700/40 p-5">
            <div className="mb-4 text-[11px] tracking-widest text-warm-400">
              工作流状态
            </div>
            <WorkflowStepper
              progress={task.progress}
              currentStep={task.current_step}
              status={task.status}
            />
            {isActive && (
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

interface ChapterCardProps {
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
        <div className="space-y-3 whitespace-pre-wrap text-sm leading-relaxed text-warm-200">
          {chapter.content}
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
