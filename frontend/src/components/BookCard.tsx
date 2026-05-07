import { useEffect, useState } from "react";
import { ChevronRight, Loader2, Pencil, Save, Trash2, X } from "lucide-react";
import { Link } from "react-router-dom";
import { api, ApiError } from "@/api/client";
import type { TaskListItem } from "@/api/types";

interface Props {
  task: TaskListItem;
  onDelete?: (task: TaskListItem) => void;
  onMetadataUpdated?: (
    taskId: string,
    metadata: { title: string | null; bookshelf: string | null },
  ) => void;
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

export function BookCard({ task, onDelete, onMetadataUpdated }: Props) {
  const [localTitle, setLocalTitle] = useState(task.title);
  const [localBookshelf, setLocalBookshelf] = useState(task.bookshelf);
  const displayTask = { ...task, title: localTitle, bookshelf: localBookshelf };
  const title = deriveTitle(displayTask);
  const subtitle = deriveSubtitle(displayTask);
  const isCompleted = task.status === "completed";
  const progressPct = isCompleted ? 100 : task.progress;
  const canDelete = task.status !== "completed";
  const [coverSrc, setCoverSrc] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState(localTitle || "");
  const [editBookshelf, setEditBookshelf] = useState(localBookshelf || "");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    setLocalTitle(task.title);
    setLocalBookshelf(task.bookshelf);
  }, [task.title, task.bookshelf]);

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

  const openEditor = () => {
    setEditTitle(localTitle || "");
    setEditBookshelf(localBookshelf || "");
    setSaveError(null);
    setEditing(true);
  };

  const saveMetadata = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const next = await api.updateTaskMetadata(task.id, {
        title: editTitle,
        bookshelf: editBookshelf,
      });
      setLocalTitle(next.title);
      setLocalBookshelf(next.bookshelf);
      onMetadataUpdated?.(task.id, {
        title: next.title,
        bookshelf: next.bookshelf,
      });
      setEditing(false);
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? e.detail
          : e instanceof Error
            ? e.message
            : "保存失败";
      setSaveError(msg);
    } finally {
      setSaving(false);
    }
  };

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
        {isCompleted && (
          <button
            type="button"
            onClick={openEditor}
            className="inline-flex items-center justify-center gap-1.5 rounded-lg border border-gold-500/15 bg-base-700/50 px-3 py-2 text-xs tracking-widest text-warm-300 transition-colors hover:border-gold-400/40 hover:text-warm-100"
          >
            <Pencil className="h-3.5 w-3.5" strokeWidth={1.5} />
            <span>编辑</span>
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

      {editing && (
        <div className="rounded-lg border border-gold-500/15 bg-base-800/80 p-3">
          <div className="space-y-3">
            <div>
              <label
                htmlFor={`book-title-${task.id}`}
                className="mb-1.5 block text-[10px] tracking-widest text-warm-500"
              >
                书名
              </label>
              <input
                id={`book-title-${task.id}`}
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                className="w-full rounded-md border border-gold-500/15 bg-base-700/70 px-2.5 py-2 text-xs text-warm-100 outline-none transition-colors focus:border-gold-400/45"
              />
            </div>
            <div>
              <label
                htmlFor={`book-bookshelf-${task.id}`}
                className="mb-1.5 block text-[10px] tracking-widest text-warm-500"
              >
                书架
              </label>
              <input
                id={`book-bookshelf-${task.id}`}
                value={editBookshelf}
                onChange={(e) => setEditBookshelf(e.target.value)}
                placeholder="例如 社科 / 情感 / 商业"
                className="w-full rounded-md border border-gold-500/15 bg-base-700/70 px-2.5 py-2 text-xs text-warm-100 placeholder:text-warm-500 outline-none transition-colors focus:border-gold-400/45"
              />
            </div>
            {saveError && (
              <p className="text-[11px] leading-relaxed text-red-300">
                {saveError}
              </p>
            )}
            <div className="flex gap-2">
              <button
                type="button"
                onClick={saveMetadata}
                disabled={saving}
                className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-md border border-gold-400/35 bg-gold-500/10 px-2.5 py-2 text-xs tracking-widest text-warm-100 transition-colors hover:bg-gold-500/15 disabled:opacity-60"
              >
                {saving ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={1.5} />
                ) : (
                  <Save className="h-3.5 w-3.5" strokeWidth={1.5} />
                )}
                <span>{saving ? "保存中" : "保存"}</span>
              </button>
              <button
                type="button"
                onClick={() => setEditing(false)}
                disabled={saving}
                className="inline-flex items-center justify-center rounded-md border border-gold-500/15 px-2.5 py-2 text-warm-400 transition-colors hover:text-warm-100 disabled:opacity-60"
                aria-label="取消编辑"
              >
                <X className="h-3.5 w-3.5" strokeWidth={1.5} />
              </button>
            </div>
          </div>
        </div>
      )}
    </article>
  );
}
