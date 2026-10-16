import { useEffect, useState } from "react";
import { CheckSquare, ChevronRight, Loader2, Pencil, Save, Square, Trash2, X } from "lucide-react";
import { bookPath, taskShelfVersion } from "@/constants/bookshelfVersions";
import { Link } from "react-router-dom";
import { api, ApiError } from "@/api/client";
import { categoryIdForLabel, categoryLabelFor, normalizeCategoryLabel } from "@/constants/categories";
import type { TaskCategoryOption, TaskListItem } from "@/api/types";

interface Props {
  task: TaskListItem;
  categories?: TaskCategoryOption[];
  onDelete?: (task: TaskListItem) => void;
  onMetadataUpdated?: (
    taskId: string,
    metadata: {
      title: string | null;
      bookshelf: string | null;
      category: string | null;
      category_label: string | null;
    },
  ) => void;
  selectionMode?: boolean;
  selected?: boolean;
  selectionDisabled?: boolean;
  onSelect?: (task: TaskListItem) => void;
}

function statusLabel(status: string, progress: number): string {
  if (status === "completed") return "已完成";
  if (status === "failed") return "失败";
  if (status === "paused") return "已暂停";
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
    const shelf = normalizeCategoryLabel(task.bookshelf || task.category_label);
    if (shelf && task.word_count) {
      return `${shelf} · 共 ${task.word_count.toLocaleString()} 字`;
    }
    if (shelf) return shelf;
    if (task.word_count) return `共 ${task.word_count.toLocaleString()} 字`;
    return "已完成";
  }
  return task.current_step || statusLabel(task.status, task.progress);
}

function formatBookDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

export function BookCard({
  task,
  categories = [],
  onDelete,
  onMetadataUpdated,
  selectionMode = false,
  selected = false,
  selectionDisabled = false,
  onSelect,
}: Props) {
  const [localTitle, setLocalTitle] = useState(task.title);
  const [localBookshelf, setLocalBookshelf] = useState(task.bookshelf);
  const initialCategory = task.category || categoryIdForLabel(task.bookshelf, categories);
  const [localCategory, setLocalCategory] = useState(initialCategory);
  const [localCategoryLabel, setLocalCategoryLabel] = useState(task.category_label);
  const displayTask = {
    ...task,
    title: localTitle,
    bookshelf: normalizeCategoryLabel(localBookshelf),
    category: localCategory,
    category_label: normalizeCategoryLabel(localCategoryLabel),
  };
  const title = deriveTitle(displayTask);
  const subtitle = deriveSubtitle(displayTask);
  const bookDate = formatBookDate(task.created_at);
  const isCompleted = task.status === "completed";
  const progressPct = isCompleted ? 100 : task.progress;
  const canDelete = task.status !== "completed";
  const [coverSrc, setCoverSrc] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState(localTitle || "");
  const [editCategory, setEditCategory] = useState(localCategory || "");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const canSelect = selectionMode && !selectionDisabled;

  useEffect(() => {
    setLocalTitle(task.title);
    setLocalBookshelf(task.bookshelf);
    setLocalCategory(task.category || categoryIdForLabel(task.bookshelf, categories));
    setLocalCategoryLabel(task.category_label);
  }, [task.title, task.bookshelf, task.category, task.category_label, categories]);

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
    setEditCategory(localCategory || "");
    setSaveError(null);
    setEditing(true);
  };

  const saveMetadata = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const next = await api.updateTaskMetadata(task.id, {
        title: editTitle,
        category: editCategory,
      });
      setLocalTitle(next.title);
      setLocalBookshelf(next.bookshelf);
      setLocalCategory(next.category);
      setLocalCategoryLabel(next.category_label);
      onMetadataUpdated?.(task.id, {
        title: next.title,
        bookshelf: next.bookshelf,
        category: next.category,
        category_label: next.category_label,
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

  const coverInner = (
    <>
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

      {selectionMode && (
        <div
          className={`absolute right-2 top-2 inline-flex h-8 w-8 items-center justify-center rounded-full border backdrop-blur-sm ${
            selected
              ? "border-gold-300/80 bg-gold-400/20 text-gold-100"
              : selectionDisabled
                ? "border-warm-600/30 bg-base-900/60 text-warm-600"
                : "border-gold-500/30 bg-base-900/70 text-warm-300"
          }`}
        >
          {selected ? (
            <CheckSquare className="h-4 w-4" strokeWidth={1.7} />
          ) : (
            <Square className="h-4 w-4" strokeWidth={1.7} />
          )}
        </div>
      )}

      {/* 失败态遮罩 */}
      {task.status === "failed" && (
        <div className="absolute inset-0 flex items-center justify-center bg-base-900/80 text-xs tracking-widest text-warm-300">
          生成失败
        </div>
      )}

      {selectionMode && selectionDisabled && (
        <div className="absolute inset-x-0 bottom-0 bg-base-900/80 px-2 py-2 text-center text-[10px] tracking-widest text-warm-400">
          完成后可下载
        </div>
      )}
    </>
  );

  return (
    <article className="group flex flex-col gap-3">
      {/* 封面 */}
      {selectionMode ? (
        <button
          type="button"
          onClick={() => {
            if (canSelect) onSelect?.(task);
          }}
          disabled={!canSelect}
          className={`relative aspect-[3/4] overflow-hidden rounded-xl border text-left transition-transform duration-300 ${
            selected
              ? "border-gold-300/70 ring-2 ring-gold-400/35"
              : "border-gold-500/10"
          } ${canSelect ? "group-hover:-translate-y-1" : "cursor-not-allowed opacity-60"}`}
          aria-label={selected ? `取消选择《${title}》` : `选择《${title}》`}
        >
          {coverInner}
        </button>
      ) : (
        <Link
          to={bookPath(task.id, taskShelfVersion(task))}
          className="relative aspect-[3/4] overflow-hidden rounded-xl border border-gold-500/10 transition-transform duration-300 group-hover:-translate-y-1"
          aria-label={`查看《${title}》`}
        >
          {coverInner}
        </Link>
      )}

      {/* 标题 */}
      <div>
        <h3 className="font-serif text-[15px] tracking-wide text-warm-100 line-clamp-1">
          {title}
        </h3>
        <p className="mt-1 text-[11px] tracking-wide text-warm-400 line-clamp-1">
          {subtitle}
        </p>
        {bookDate && (
          <p className="mt-1 text-[10px] tracking-widest text-warm-500">
            {bookDate}
          </p>
        )}
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
        {selectionMode ? (
          <button
            type="button"
            onClick={() => {
              if (canSelect) onSelect?.(task);
            }}
            disabled={!canSelect}
            className={`inline-flex flex-1 items-center justify-center gap-1.5 rounded-lg border px-3 py-2 text-xs tracking-widest transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
              selected
                ? "border-gold-300/70 bg-gold-500/15 text-warm-100"
                : "border-gold-500/15 bg-base-700/50 text-warm-300 hover:border-gold-400/40 hover:text-warm-100"
            }`}
          >
            {selected ? (
              <CheckSquare className="h-3.5 w-3.5" strokeWidth={1.5} />
            ) : (
              <Square className="h-3.5 w-3.5" strokeWidth={1.5} />
            )}
            <span>{selectionDisabled ? "不可下载" : selected ? "已选择" : "选择"}</span>
          </button>
        ) : (
          <>
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
              to={bookPath(task.id, taskShelfVersion(task))}
              className="inline-flex flex-1 items-center justify-between rounded-lg border border-gold-500/15 bg-base-700/50 px-3 py-2 text-xs tracking-widest text-warm-300 transition-colors hover:border-gold-400/40 hover:text-warm-100"
            >
              <span>{isCompleted ? "继续阅读" : "查看详情"}</span>
              <ChevronRight className="h-3.5 w-3.5" strokeWidth={1.5} />
            </Link>
          </>
        )}
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
                书籍类别
              </label>
              <select
                id={`book-bookshelf-${task.id}`}
                value={editCategory}
                onChange={(e) => setEditCategory(e.target.value)}
                className="w-full rounded-md border border-gold-500/15 bg-base-700/70 px-2.5 py-2 text-xs text-warm-100 placeholder:text-warm-500 outline-none transition-colors focus:border-gold-400/45"
              >
                {taskShelfVersion(task) === "1.0" && <option value="">未分类</option>}
                {categories.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                    {item.prompt_status === "pending" ? "（待优化）" : ""}
                  </option>
                ))}
                {editCategory &&
                  !categoryLabelFor(editCategory, categories) && (
                    <option value={editCategory}>{editCategory}</option>
                  )}
              </select>
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
