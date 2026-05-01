import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Download, FileText, Loader2 } from "lucide-react";
import { api, ApiError } from "@/api/client";
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

export function BookDetail() {
  const { id } = useParams<{ id: string }>();
  const [task, setTask] = useState<TaskStatusResponse | null>(null);
  const [chapters, setChapters] = useState<ChapterResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeStage, setActiveStage] = useState<StageName | "final">("final");
  const [stageContent, setStageContent] = useState<string>("");
  const [stageLoading, setStageLoading] = useState(false);
  const pollRef = useRef<number | null>(null);

  // 拉任务状态 + 章节
  const refresh = async () => {
    if (!id) return;
    try {
      const t = await api.getTask(id);
      setTask(t);
      if (t.status === "completed") {
        const c = await api.getChapters(id);
        setChapters(c.chapters);
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
  };

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    void refresh().finally(() => setLoading(false));

    pollRef.current = window.setInterval(() => {
      void refresh();
    }, 4000);
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  // 任务完成后停止轮询
  useEffect(() => {
    if (!task) return;
    if (task.status === "completed" || task.status === "failed") {
      if (pollRef.current) window.clearInterval(pollRef.current);
    }
  }, [task]);

  // 切换 stage 时拉对应内容
  useEffect(() => {
    if (!id || activeStage === "final") return;
    setStageLoading(true);
    api
      .getStage(id, activeStage)
      .then((r) => setStageContent(r.content))
      .catch((e) => {
        const msg =
          e instanceof ApiError ? `[${e.status}] ${e.detail}` : "加载失败";
        setStageContent(`（${msg}）`);
      })
      .finally(() => setStageLoading(false));
  }, [id, activeStage]);

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
        <Link
          to="/bookshelf"
          className="btn-ghost mt-6 inline-flex"
        >
          <ArrowLeft className="h-3.5 w-3.5" strokeWidth={1.5} />
          返回书架
        </Link>
      </div>
    );
  }

  const isActive = ACTIVE_STATUSES.has(task.status);
  const isCompleted = task.status === "completed";
  const isFailed = task.status === "failed";

  return (
    <div>
      <Link
        to="/bookshelf"
        className="mb-6 inline-flex items-center gap-2 text-xs tracking-widest text-warm-400 transition-colors hover:text-warm-100"
      >
        <ArrowLeft className="h-3.5 w-3.5" strokeWidth={1.5} />
        返回书架
      </Link>

      {/* 顶部状态条 */}
      <section className="card-warm mb-8 p-8">
        <div className="flex items-start justify-between gap-6">
          <div className="flex-1">
            <h1 className="font-serif text-2xl tracking-wide text-warm-100">
              {chapters[0]?.title || "书稿生成中"}
            </h1>
            <p className="mt-2 break-all text-xs tracking-wide text-warm-400">
              来源：{task.source_url}
            </p>
            <p className="mt-4 text-xs tracking-widest text-warm-300">
              {task.current_step}
            </p>
            {isFailed && task.error_message && (
              <div className="mt-4 rounded-lg border border-red-500/30 bg-red-500/8 px-4 py-2.5 text-xs text-red-300">
                {task.error_message}
              </div>
            )}
          </div>

          {isCompleted && (
            <div className="flex flex-col gap-2">
              <a
                href={api.downloadUrl(task.id, "md")}
                className="btn-ghost"
                download
              >
                <Download className="h-3.5 w-3.5" strokeWidth={1.5} />
                Markdown
              </a>
              <a
                href={api.downloadUrl(task.id, "txt")}
                className="btn-ghost"
                download
              >
                <FileText className="h-3.5 w-3.5" strokeWidth={1.5} />
                纯文本
              </a>
            </div>
          )}
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

        {isActive && (
          <p className="mt-4 flex items-center gap-2 text-xs tracking-widest text-warm-400">
            <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={1.5} />
            正在生成中，每 4 秒自动刷新一次
          </p>
        )}
      </section>

      {isCompleted && (
        <>
          {/* 阶段切换 */}
          <div className="mb-6 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => setActiveStage("final")}
              className={`rounded-full border px-4 py-1.5 text-xs tracking-widest transition-colors ${
                activeStage === "final"
                  ? "border-gold-400/60 bg-gold-500/10 text-warm-100"
                  : "border-gold-500/15 text-warm-400 hover:border-gold-400/30 hover:text-warm-200"
              }`}
            >
              终稿（章节视图）
            </button>
            {STAGES.map((s) => (
              <button
                key={s.name}
                type="button"
                onClick={() => setActiveStage(s.name)}
                className={`rounded-full border px-4 py-1.5 text-xs tracking-widest transition-colors ${
                  activeStage === s.name
                    ? "border-gold-400/60 bg-gold-500/10 text-warm-100"
                    : "border-gold-500/15 text-warm-400 hover:border-gold-400/30 hover:text-warm-200"
                }`}
              >
                {s.label}
              </button>
            ))}
          </div>

          {activeStage === "final" ? (
            <section className="space-y-8">
              {chapters.map((c) => (
                <article
                  key={c.id}
                  className="card-warm p-8"
                  id={`chapter-${c.chapter_num}`}
                >
                  <h2 className="mb-4 font-serif text-xl tracking-wide text-warm-100">
                    {c.title}
                  </h2>
                  <div className="space-y-3 text-sm leading-relaxed text-warm-200 whitespace-pre-wrap">
                    {c.content}
                  </div>
                  <p className="mt-6 text-[10px] tracking-widest text-warm-500">
                    {c.word_count.toLocaleString()} 字
                  </p>
                </article>
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
    </div>
  );
}
