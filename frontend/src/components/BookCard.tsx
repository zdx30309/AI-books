import { useEffect, useState } from "react";
import { ChevronRight, Trash2 } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "@/api/client";
import type { TaskListItem } from "@/api/types";

interface Props {
  task: TaskListItem;
  onDelete?: (task: TaskListItem) => void;
}

function statusLabel(status: string, progress: number): string {
  if (status === "completed") return "已完成";
  if (status === "failed") return "失败";
  if (progress === 0) return "排队中";
  return `生成中 ${progress}%`;
}

function deriveTitle(task: TaskListItem): string {
  if (task.title) return task.title;
  // 从 source_url 末段简单截取
  try {
    const u = new URL(task.source_url);
    const seg = u.pathname.split("/").filter(Boolean).pop();
    if (seg && seg.length < 30) return seg;
  } catch {
    /* ignore */
  }
  return "未命名书稿";
}

function deriveSubtitle(task: TaskListItem): string {
  if (task.status === "completed") {
    if (task.bookshelf && task.word_count) {
      return `${task.bookshelf} · 共 ${task.word_count.toLocaleString()} 字`;
    }
    if (task.bookshelf) return task.bookshelf;
    if (task.word_count) return `共 ${task.word_count.toLocaleString()} 字`;
    return "已完成";
  }
  return task.current_step || statusLabel(task.status, task.progress);
}

export function BookCard({ task, onDelete }: Props) {
  const title = deriveTitle(task);
  const subtitle = deriveSubtitle(task);
  const isCompleted = task.status === "completed";
  const progressPct = isCompleted ? 100 : task.progress;
  const canDelete = task.status !== "completed";
  const [coverSrc, setCoverSrc] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    let objectUrl: string | null = null;
    setCoverSrc(null);
    if (!task.cover_url) return undefined;

    api
      .getCoverBlob(task.id)
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
  }, [task.cover_url, task.id]);

  return (
    <article className="group flex flex-col gap-3">
      {/* 封面 */}
      <Link
        to={`/book/${task.id}`}
        className="relative aspect-[3/4] overflow-hidden rounded-xl border border-gold-500/10 transition-transform duration-300 group-hover:-translate-y-1"
        aria-label={`查看《${title}》`}
      >
        {coverSrc ? (
          <img
            src={coverSrc}
            alt={title}
            className="h-full w-full object-cover"
            loading="lazy"
          />
        ) : (
          <div className="cover-placeholder relative flex h-full w-full items-center justify-center">
            <div className="px-4 text-center">
              <div className="font-serif text-xl leading-tight text-warm-100/85 line-clamp-3">
                {title}
              </div>
            </div>
          </div>
        )}

        {/* 失败态遮罩 */}
        {task.status === "failed" && (
          <div className="absolute inset-0 flex items-center justify-center bg-base-900/80 text-xs tracking-widest text-warm-300">
            生成失败
          </div>
        )}
      </Link>

      {/* 标题 */}
      <div>
        <h3 className="font-serif text-[15px] tracking-wide text-warm-100 line-clamp-1">
          {title}
        </h3>
        <p className="mt-1 text-[11px] tracking-wide text-warm-400 line-clamp-1">
          {subtitle}
        </p>
      </div>

      {/* 进度条 */}
      <div className="space-y-1.5">
        <div className="flex items-center justify-between text-[10px] tracking-widest text-warm-400">
          <span>进度</span>
          <span className="text-gold-300">{progressPct}%</span>
        </div>
        <div className="progress-track">
          <div
            className="progress-fill"
            style={{ width: `${progressPct}%` }}
          />
        </div>
      </div>

      {/* 操作 */}
      <div className="mt-1 flex gap-2">
        {canDelete && onDelete && (
          <button
            type="button"
            onClick={() => onDelete(task)}
            className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-red-500/15 bg-base-700/40 px-3 py-2 text-xs tracking-widest text-red-200 transition-colors hover:border-red-400/35 hover:bg-red-500/10 hover:text-red-100"
          >
            <Trash2 className="h-3.5 w-3.5" strokeWidth={1.5} />
            <span>删除书稿</span>
          </button>
        )}
        <Link
          to={`/book/${task.id}`}
          className="inline-flex flex-1 items-center justify-between rounded-lg border border-gold-500/15 bg-base-700/50 px-3 py-2 text-xs tracking-widest text-warm-300 transition-colors hover:border-gold-400/40 hover:text-warm-100"
        >
          <span>{isCompleted ? "继续阅读" : "查看详情"}</span>
          <ChevronRight className="h-3.5 w-3.5" strokeWidth={1.5} />
        </Link>
      </div>
    </article>
  );
}
