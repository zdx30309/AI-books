import { useEffect, useMemo, useState } from "react";
import type { DragEvent } from "react";
import { useNavigate } from "react-router-dom";
import { CheckSquare, FileArchive, FileText, Loader2, Plus, RefreshCcw, Trash2, X } from "lucide-react";
import { BookCard } from "@/components/BookCard";
import { CreateTaskModal } from "@/components/CreateTaskModal";
import { EmptyState } from "@/components/EmptyState";
import { api, ApiError } from "@/api/client";
import {
  DEFAULT_CATEGORIES,
  normalizeCategoryLabel,
  normalizeCategoryOptions,
} from "@/constants/categories";
import type { TaskCategoryOption, TaskListItem, TaskStatus } from "@/api/types";

const FILTERS: { label: string; value: TaskStatus | "all" }[] = [
  { label: "全部", value: "all" },
  { label: "已完成", value: "completed" },
  { label: "生成中", value: "processing" },
  { label: "失败", value: "failed" },
  { label: "人工复核", value: "manual_review" },
];

const DATE_FILTERS = [
  { label: "全部日期", value: "all" },
  { label: "今天", value: "today" },
  { label: "近 7 天", value: "week" },
  { label: "本月", value: "month" },
  { label: "更早", value: "older" },
] as const;

type DateFilter = (typeof DATE_FILTERS)[number]["value"];
type CategoryChip =
  | { id: string; label: string; filterValue: "all"; type: "all" }
  | { id: string; label: string; filterValue: string; type: "category" }
  | { id: string; label: string; filterValue: "uncategorized"; type: "uncategorized" };

const CATEGORY_ORDER_STORAGE_KEY = "scriptbook.bookshelf.categoryOrder";

function readStoredCategoryOrder() {
  try {
    const raw = localStorage.getItem(CATEGORY_ORDER_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    return Array.isArray(parsed) && parsed.every((item) => typeof item === "string")
      ? parsed
      : [];
  } catch {
    return [];
  }
}

function orderCategoryChips(chips: CategoryChip[], order: string[]) {
  if (order.length === 0) return chips;
  const sourceIndex = new Map(chips.map((chip, index) => [chip.id, index]));
  const orderedIds = order.filter((id) => sourceIndex.has(id));
  const missingIds = chips
    .map((chip) => chip.id)
    .filter((id) => !orderedIds.includes(id));
  return [...orderedIds, ...missingIds]
    .map((id) => chips[sourceIndex.get(id)!])
    .filter((chip): chip is CategoryChip => Boolean(chip));
}

function categoryValue(task: TaskListItem, categories: TaskCategoryOption[]) {
  if (task.category) return task.category;
  const taskBookshelf = normalizeCategoryLabel(task.bookshelf);
  const match = categories.find((item) => item.label === taskBookshelf);
  return match?.id ?? "uncategorized";
}

function taskDate(task: TaskListItem) {
  const date = new Date(task.created_at);
  return Number.isNaN(date.getTime()) ? null : date;
}

function dateKey(date: Date | null) {
  if (!date) return "unknown";
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function dateLabel(key: string) {
  if (key === "unknown") return "未知日期";
  const [year, month, day] = key.split("-");
  return `${year}年${month}月${day}日`;
}

function isSameDay(a: Date, b: Date) {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

function inDateFilter(task: TaskListItem, filter: DateFilter) {
  if (filter === "all") return true;
  const date = taskDate(task);
  if (!date) return filter === "older";
  const now = new Date();
  if (filter === "today") return isSameDay(date, now);
  if (filter === "week") {
    const start = new Date(now);
    start.setDate(now.getDate() - 6);
    start.setHours(0, 0, 0, 0);
    return date >= start;
  }
  if (filter === "month") {
    return date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth();
  }
  const startOfMonth = new Date(now.getFullYear(), now.getMonth(), 1);
  return date < startOfMonth;
}

interface BookshelfProps {
  title?: string;
  subtitle?: string;
  categoryScopeIds?: readonly string[];
  allCategoryLabel?: string;
  emptyTitle?: string;
  emptyDescription?: string;
  showCreateButton?: boolean;
}

export function Bookshelf({
  title = "我的书架",
  subtitle,
  categoryScopeIds,
  allCategoryLabel = "全部分类",
  emptyTitle = "书架还空着",
  emptyDescription = "点击「开始创作」，把第一段视频思想写成你的第一本书",
  showCreateButton = true,
}: BookshelfProps = {}) {
  const navigate = useNavigate();
  const [tasks, setTasks] = useState<TaskListItem[]>([]);
  const [categories, setCategories] = useState<TaskCategoryOption[]>(
    normalizeCategoryOptions(DEFAULT_CATEGORIES),
  );
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["value"]>("all");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [dateFilter, setDateFilter] = useState<DateFilter>("all");
  const [modalOpen, setModalOpen] = useState(false);
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedTaskIds, setSelectedTaskIds] = useState<Set<string>>(() => new Set());
  const [batchDownloading, setBatchDownloading] = useState<"md" | "docx" | null>(null);
  const [batchDeleting, setBatchDeleting] = useState(false);
  const [batchRetrying, setBatchRetrying] = useState(false);
  const [batchRegenerating, setBatchRegenerating] = useState(false);
  const [categoryOrder, setCategoryOrder] = useState<string[]>(readStoredCategoryOrder);
  const [draggingCategoryId, setDraggingCategoryId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .listTasks(1, 0)
      .then((r) => {
        if (!cancelled) setTasks(r.items);
      })
      .catch((e) => console.error("bookshelf fetch failed", e))
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

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

  useEffect(() => {
    localStorage.setItem(CATEGORY_ORDER_STORAGE_KEY, JSON.stringify(categoryOrder));
  }, [categoryOrder]);

  const isScopedShelf = Boolean(categoryScopeIds?.length);
  const categoryScopeSet = useMemo(() => new Set(categoryScopeIds ?? []), [categoryScopeIds]);
  const scopedCategories = useMemo(
    () => (isScopedShelf ? categories.filter((item) => categoryScopeSet.has(item.id)) : categories),
    [categories, categoryScopeSet, isScopedShelf],
  );
  const scopedTasks = useMemo(
    () =>
      isScopedShelf
        ? tasks.filter((task) => categoryScopeSet.has(categoryValue(task, categories)))
        : tasks,
    [categories, categoryScopeSet, isScopedShelf, tasks],
  );

  const categoryChips = useMemo<CategoryChip[]>(() => {
    const chips: CategoryChip[] = [
      { id: "filter:all", label: allCategoryLabel, filterValue: "all", type: "all" },
      ...scopedCategories.map((item) => ({
        id: `category:${item.id}`,
        label: item.label,
        filterValue: item.id,
        type: "category" as const,
      })),
    ];
    if (!isScopedShelf) {
      chips.push({
        id: "filter:uncategorized",
        label: "未分类",
        filterValue: "uncategorized",
        type: "uncategorized",
      });
    }
    return orderCategoryChips(chips, categoryOrder);
  }, [allCategoryLabel, categoryOrder, isScopedShelf, scopedCategories]);

  useEffect(() => {
    if (categoryFilter !== "all" && !categoryChips.some((item) => item.filterValue === categoryFilter)) {
      setCategoryFilter("all");
    }
  }, [categoryChips, categoryFilter]);

  const statusFiltered =
    filter === "all"
      ? scopedTasks
      : filter === "processing"
        ? scopedTasks.filter((t) =>
            ["pending", "processing", "fetching_topics", "filtering_topics", "generating_outline", "writing_chapters", "fact_checking"].includes(
              t.status,
            ),
          )
        : scopedTasks.filter((t) => t.status === filter);

  const filtered = statusFiltered
    .filter((task) =>
      categoryFilter === "all"
        ? true
        : categoryFilter === "uncategorized"
          ? categoryValue(task, categories) === "uncategorized"
          : categoryValue(task, categories) === categoryFilter,
    )
    .filter((task) => inDateFilter(task, dateFilter));

  const downloadableFiltered = filtered.filter((task) => task.status === "completed");
  const failedTaskIds = scopedTasks.filter((task) => task.status === "failed").map((task) => task.id);
  const visibleFailedTaskIds = filtered.filter((task) => task.status === "failed").map((task) => task.id);
  const selectedTasks = scopedTasks.filter((task) => selectedTaskIds.has(task.id));
  const selectedCompletedTaskIds = selectedTasks
    .filter((task) => task.status === "completed")
    .map((task) => task.id);
  const selectedFailedTaskIds = selectedTasks
    .filter((task) => task.status === "failed")
    .map((task) => task.id);
  const selectedRegeneratableTaskIds = selectedTasks
    .filter((task) => task.status === "completed" || task.status === "failed")
    .map((task) => task.id);
  const batchBusy = batchDownloading !== null || batchDeleting || batchRetrying || batchRegenerating;

  const grouped = filtered.reduce<Record<string, TaskListItem[]>>((acc, task) => {
    const key = dateKey(taskDate(task));
    acc[key] = acc[key] || [];
    acc[key].push(task);
    return acc;
  }, {});

  const groupedEntries = Object.entries(grouped).sort(([a], [b]) => {
    if (a === "unknown") return 1;
    if (b === "unknown") return -1;
    return b.localeCompare(a);
  });

  useEffect(() => {
    setSelectedTaskIds((prev) => {
      const validIds = new Set(tasks.map((task) => task.id));
      const next = new Set([...prev].filter((id) => validIds.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [tasks]);

  const toggleSelectionMode = () => {
    setSelectionMode((prev) => {
      const next = !prev;
      if (!next) setSelectedTaskIds(new Set());
      return next;
    });
  };

  const toggleTaskSelection = (task: TaskListItem) => {
    setSelectedTaskIds((prev) => {
      const next = new Set(prev);
      if (next.has(task.id)) {
        next.delete(task.id);
      } else {
        next.add(task.id);
      }
      return next;
    });
  };

  const selectAllVisible = () => {
    setSelectedTaskIds((prev) => {
      const next = new Set(prev);
      filtered.forEach((task) => next.add(task.id));
      return next;
    });
  };

  const clearSelection = () => setSelectedTaskIds(new Set());

  const handleCategoryDragStart = (event: DragEvent<HTMLButtonElement>, chipId: string) => {
    setDraggingCategoryId(chipId);
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", chipId);
  };

  const handleCategoryDrop = (event: DragEvent<HTMLButtonElement>, targetId: string) => {
    event.preventDefault();
    const sourceId = draggingCategoryId || event.dataTransfer.getData("text/plain");
    setDraggingCategoryId(null);
    if (!sourceId || sourceId === targetId) return;

    const ids = categoryChips.map((chip) => chip.id);
    const from = ids.indexOf(sourceId);
    const to = ids.indexOf(targetId);
    if (from < 0 || to < 0) return;

    const next = [...ids];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    setCategoryOrder(next);
  };

  const handleBatchDownload = async (fmt: "md" | "docx") => {
    if (selectedCompletedTaskIds.length === 0) {
      alert("请先选择已完成的书稿");
      return;
    }
    try {
      setBatchDownloading(fmt);
      api.downloadBatchFileDirect({
        task_ids: selectedCompletedTaskIds,
        fmt,
      });
      window.setTimeout(() => setBatchDownloading(null), 900);
    } catch (e) {
      setBatchDownloading(null);
      const msg =
        e instanceof ApiError
          ? `[${e.status}] ${e.detail}`
          : e instanceof Error
            ? e.message
            : "批量下载失败";
      alert(`批量下载失败：${msg}`);
    }
  };

  const handleRetryFailed = async (ids?: string[]) => {
    const targetIds = ids?.length ? ids : failedTaskIds;
    if (targetIds.length === 0) {
      alert("当前没有失败任务需要继续生成");
      return;
    }
    const ok = window.confirm(
      `将继续生成 ${targetIds.length} 本失败书稿。已完成的阶段会断点复用，不会从头重写前面的内容。`,
    );
    if (!ok) return;

    setBatchRetrying(true);
    try {
      const res = await api.retryFailedTasks({ task_ids: targetIds });
      const refreshed = await api.listTasks(1, 0);
      setTasks(refreshed.items);
      setSelectedTaskIds((prev) => {
        const next = new Set(prev);
        targetIds.forEach((id) => next.delete(id));
        return next;
      });
      alert(`已重新入队 ${res.total_retried} 本，跳过 ${res.total_skipped} 本。`);
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? `[${e.status}] ${e.detail}`
          : e instanceof Error
            ? e.message
            : "继续生成失败";
      alert(`继续生成失败：${msg}`);
    } finally {
      setBatchRetrying(false);
    }
  };

  const handleBatchRegenerate = async () => {
    const targetIds = selectedRegeneratableTaskIds;
    if (targetIds.length === 0) {
      alert("请先选择已完成或失败的书稿");
      return;
    }
    const ok = window.confirm(
      `将从头重新生成 ${targetIds.length} 本书稿。系统会清空旧正文、章节、封面和阶段产物，但保留原始素材与分类；失败任务如果只想断点续跑，请使用「继续选中失败」。`,
    );
    if (!ok) return;

    setBatchRegenerating(true);
    try {
      const res = await api.regenerateTasks({ task_ids: targetIds });
      const refreshed = await api.listTasks(1, 0);
      const regeneratedIds = new Set(res.regenerated.map((item) => item.task_id));
      setTasks(refreshed.items);
      setSelectedTaskIds((prev) => {
        const next = new Set(prev);
        regeneratedIds.forEach((id) => next.delete(id));
        return next;
      });
      alert(`已重新生成入队 ${res.total_regenerated} 本，跳过 ${res.total_skipped} 本。`);
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? `[${e.status}] ${e.detail}`
          : e instanceof Error
            ? e.message
            : "重新生成失败";
      alert(`重新生成失败：${msg}`);
    } finally {
      setBatchRegenerating(false);
    }
  };

  const handleBatchDelete = async () => {
    const ids = [...selectedTaskIds];
    if (ids.length === 0) {
      alert("请先选择要删除的书稿");
      return;
    }
    const ok = window.confirm(
      `确定删除选中的 ${ids.length} 本书稿吗？删除后不可恢复。`,
    );
    if (!ok) return;

    setBatchDeleting(true);
    const failed: string[] = [];
    for (const id of ids) {
      try {
        await api.deleteTask(id);
      } catch {
        failed.push(id);
      }
    }
    setTasks((prev) => prev.filter((task) => !ids.includes(task.id) || failed.includes(task.id)));
    setSelectedTaskIds(new Set(failed));
    setBatchDeleting(false);

    if (failed.length > 0) {
      alert(`有 ${failed.length} 本删除失败，已保留在选择中，请稍后重试。`);
    }
  };

  const handleDelete = async (task: TaskListItem) => {
    const ok = window.confirm(
      `确定删除《${task.title || "未命名书稿"}》吗？删除后不可恢复。`,
    );
    if (!ok) return;
    try {
      await api.deleteTask(task.id);
      setTasks((prev) => prev.filter((t) => t.id !== task.id));
      setSelectedTaskIds((prev) => {
        const next = new Set(prev);
        next.delete(task.id);
        return next;
      });
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : "删除失败";
      alert(`删除失败：${msg}`);
    }
  };

  const handleMetadataUpdated = (
    taskId: string,
    metadata: {
      title: string | null;
      bookshelf: string | null;
      category: string | null;
      category_label: string | null;
    },
  ) => {
    setTasks((prev) =>
      prev.map((task) =>
        task.id === taskId
          ? {
              ...task,
              title: metadata.title,
              bookshelf: metadata.bookshelf,
              category: metadata.category,
              category_label: metadata.category_label,
            }
          : task,
      ),
    );
  };

  return (
    <>
      <div className="mb-10 flex items-end justify-between">
        <div>
          <h1 className="font-serif text-3xl tracking-wide text-warm-100">
            {title}
          </h1>
          <p className="mt-2 text-xs tracking-widest text-warm-400">
            {subtitle ?? `共 ${scopedTasks.length} 本书稿`}
          </p>
        </div>
        <div className="flex flex-wrap justify-end gap-3">
          <button
            type="button"
            onClick={toggleSelectionMode}
            className="btn-ghost"
          >
            {selectionMode ? (
              <X className="h-4 w-4" strokeWidth={1.7} />
            ) : (
              <CheckSquare className="h-4 w-4" strokeWidth={1.7} />
            )}
            <span className="tracking-widest">{selectionMode ? "退出多选" : "多选管理"}</span>
          </button>
          {failedTaskIds.length > 0 && (
            <button
              type="button"
              onClick={() => handleRetryFailed()}
              disabled={batchRetrying}
              className="btn-ghost"
            >
              {batchRetrying ? (
                <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.7} />
              ) : (
                <RefreshCcw className="h-4 w-4" strokeWidth={1.7} />
              )}
              <span className="tracking-widest">继续失败任务 ({failedTaskIds.length})</span>
            </button>
          )}
          {showCreateButton && (
            <button
              type="button"
              onClick={() => setModalOpen(true)}
              className="btn-gold"
            >
              <Plus className="h-4 w-4" strokeWidth={2} />
              <span className="tracking-widest">新建书稿</span>
            </button>
          )}
        </div>
      </div>

      <div className="mb-8 flex flex-wrap gap-2">
        {FILTERS.map((f) => (
          <button
            key={f.value}
            type="button"
            onClick={() => setFilter(f.value)}
            className={`rounded-full border px-4 py-1.5 text-xs tracking-widest transition-colors ${
              filter === f.value
                ? "border-gold-400/60 bg-gold-500/10 text-warm-100"
                : "border-gold-500/15 text-warm-400 hover:border-gold-400/30 hover:text-warm-200"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      <div className="mb-6">
        <div className="mb-2 text-[10px] tracking-widest text-warm-500">
          书架分类
        </div>
        <div className="flex flex-wrap gap-2">
          {categoryChips.map((item) => (
            <button
              key={item.id}
              type="button"
              draggable
              onClick={() => setCategoryFilter(item.filterValue)}
              onDragStart={(event) => handleCategoryDragStart(event, item.id)}
              onDragEnd={() => setDraggingCategoryId(null)}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => handleCategoryDrop(event, item.id)}
              title="拖动调整分类位置"
              className={`cursor-grab rounded-full border px-4 py-1.5 text-xs tracking-widest transition-colors active:cursor-grabbing ${
                categoryFilter === item.filterValue
                  ? "border-gold-400/60 bg-gold-500/10 text-warm-100"
                  : "border-gold-500/15 text-warm-400 hover:border-gold-400/30 hover:text-warm-200"
              } ${draggingCategoryId === item.id ? "opacity-45" : ""}`}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      <div className="mb-8">
        <div className="mb-2 text-[10px] tracking-widest text-warm-500">
          生成日期
        </div>
        <div className="flex flex-wrap gap-2">
          {DATE_FILTERS.map((item) => (
            <button
              key={item.value}
              type="button"
              onClick={() => setDateFilter(item.value)}
              className={`rounded-full border px-4 py-1.5 text-xs tracking-widest transition-colors ${
                dateFilter === item.value
                  ? "border-gold-400/60 bg-gold-500/10 text-warm-100"
                  : "border-gold-500/15 text-warm-400 hover:border-gold-400/30 hover:text-warm-200"
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      {selectionMode && (
        <div className="card-warm mb-8 flex flex-col gap-4 p-4 md:flex-row md:items-center md:justify-between">
          <div>
            <div className="text-sm tracking-widest text-warm-100">
              已选择 {selectedTaskIds.size} 本
            </div>
            <p className="mt-1 text-xs leading-relaxed text-warm-500">
              当前筛选下有 {filtered.length} 本书稿，其中 {downloadableFiltered.length} 本已完成可下载、{visibleFailedTaskIds.length} 本失败可继续；已选中 {selectedRegeneratableTaskIds.length} 本可从头重新生成。批量删除会移除所有选中的书稿。
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={selectAllVisible}
              disabled={filtered.length === 0 || batchBusy}
              className="rounded-full border border-gold-500/20 px-4 py-2 text-xs tracking-widest text-warm-300 transition-colors hover:border-gold-400/45 hover:text-warm-100 disabled:cursor-not-allowed disabled:opacity-50"
            >
              全选当前筛选
            </button>
            <button
              type="button"
              onClick={clearSelection}
              disabled={selectedTaskIds.size === 0 || batchBusy}
              className="rounded-full border border-gold-500/20 px-4 py-2 text-xs tracking-widest text-warm-300 transition-colors hover:border-gold-400/45 hover:text-warm-100 disabled:cursor-not-allowed disabled:opacity-50"
            >
              清空选择
            </button>
            <button
              type="button"
              onClick={handleBatchRegenerate}
              disabled={selectedRegeneratableTaskIds.length === 0 || batchBusy}
              className="inline-flex items-center justify-center gap-1.5 rounded-full border border-gold-400/45 bg-gold-500/10 px-4 py-2 text-xs tracking-widest text-warm-100 transition-colors hover:bg-gold-500/15 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {batchRegenerating ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={1.7} />
              ) : (
                <RefreshCcw className="h-3.5 w-3.5" strokeWidth={1.7} />
              )}
              重新生成
            </button>
            <button
              type="button"
              onClick={() => handleRetryFailed(selectedFailedTaskIds)}
              disabled={selectedFailedTaskIds.length === 0 || batchBusy}
              className="inline-flex items-center justify-center gap-1.5 rounded-full border border-gold-400/45 bg-gold-500/10 px-4 py-2 text-xs tracking-widest text-warm-100 transition-colors hover:bg-gold-500/15 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {batchRetrying ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={1.7} />
              ) : (
                <RefreshCcw className="h-3.5 w-3.5" strokeWidth={1.7} />
              )}
              继续选中失败
            </button>
            <button
              type="button"
              onClick={() => handleBatchDownload("docx")}
              disabled={selectedCompletedTaskIds.length === 0 || batchBusy}
              className="inline-flex items-center justify-center gap-1.5 rounded-full border border-gold-400/45 bg-gold-500/10 px-4 py-2 text-xs tracking-widest text-warm-100 transition-colors hover:bg-gold-500/15 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {batchDownloading === "docx" ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={1.7} />
              ) : (
                <FileArchive className="h-3.5 w-3.5" strokeWidth={1.7} />
              )}
              下载 Word
            </button>
            <button
              type="button"
              onClick={() => handleBatchDownload("md")}
              disabled={selectedCompletedTaskIds.length === 0 || batchBusy}
              className="inline-flex items-center justify-center gap-1.5 rounded-full border border-gold-500/20 px-4 py-2 text-xs tracking-widest text-warm-300 transition-colors hover:border-gold-400/45 hover:text-warm-100 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {batchDownloading === "md" ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={1.7} />
              ) : (
                <FileText className="h-3.5 w-3.5" strokeWidth={1.7} />
              )}
              下载 Markdown
            </button>
            <button
              type="button"
              onClick={handleBatchDelete}
              disabled={selectedTaskIds.size === 0 || batchBusy}
              className="inline-flex items-center justify-center gap-1.5 rounded-full border border-red-400/35 bg-red-500/10 px-4 py-2 text-xs tracking-widest text-red-100 transition-colors hover:bg-red-500/15 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {batchDeleting ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={1.7} />
              ) : (
                <Trash2 className="h-3.5 w-3.5" strokeWidth={1.7} />
              )}
              批量删除
            </button>
          </div>
        </div>
      )}

      {loading ? (
        <div className="grid grid-cols-2 gap-5 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6">
          {Array.from({ length: 12 }).map((_, i) => (
            <div
              key={i}
              className="aspect-[3/4] animate-pulse rounded-xl border border-gold-500/8 bg-base-700/40"
            />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <EmptyState
          title={filter === "all" && categoryFilter === "all" && dateFilter === "all" ? emptyTitle : "暂无相关书稿"}
          description={filter === "all" && categoryFilter === "all" && dateFilter === "all" ? emptyDescription : "当前筛选条件下暂时没有书稿"}
          action={
            <button
              type="button"
              onClick={() => setModalOpen(true)}
              className="btn-gold"
            >
              <Plus className="h-4 w-4" strokeWidth={2} />
              <span className="tracking-widest">新建书稿</span>
            </button>
          }
        />
      ) : (
        <div className="space-y-10">
          {groupedEntries.map(([key, items]) => (
            <section key={key}>
              <div className="mb-4 flex items-center justify-between border-b border-gold-500/10 pb-2">
                <h2 className="font-serif text-lg tracking-wide text-warm-100">
                  {dateLabel(key)}
                </h2>
                <span className="text-[10px] tracking-widest text-warm-500">
                  {items.length} 本
                </span>
              </div>
              <div className="grid grid-cols-2 gap-5 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6">
                {items.map((task) => (
                  <BookCard
                    key={task.id}
                    task={task}
                    categories={categories}
                    onDelete={handleDelete}
                    onMetadataUpdated={handleMetadataUpdated}
                    selectionMode={selectionMode}
                    selected={selectedTaskIds.has(task.id)}
                    selectionDisabled={false}
                    onSelect={toggleTaskSelection}
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}

      <CreateTaskModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onCreated={(taskId) => {
          setModalOpen(false);
          navigate(`/book/${taskId}`);
        }}
        onLongNovelCreated={(projectId) => {
          setModalOpen(false);
          navigate(`/long-novel/${projectId}`);
        }}
        defaultCategoryId={
          categoryFilter !== "all" && categoryFilter !== "uncategorized"
            ? categoryFilter
            : categoryScopeIds?.[0]
        }
      />
    </>
  );
}
