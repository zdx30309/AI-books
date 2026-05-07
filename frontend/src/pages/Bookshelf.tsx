import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus } from "lucide-react";
import { BookCard } from "@/components/BookCard";
import { CreateTaskModal } from "@/components/CreateTaskModal";
import { EmptyState } from "@/components/EmptyState";
import { api, ApiError } from "@/api/client";
import { DEFAULT_CATEGORIES } from "@/constants/categories";
import type { TaskCategoryOption, TaskListItem, TaskStatus } from "@/api/types";

const FILTERS: { label: string; value: TaskStatus | "all" }[] = [
  { label: "全部", value: "all" },
  { label: "已完成", value: "completed" },
  { label: "生成中", value: "processing" },
  { label: "失败", value: "failed" },
];

const DATE_FILTERS = [
  { label: "全部日期", value: "all" },
  { label: "今天", value: "today" },
  { label: "近 7 天", value: "week" },
  { label: "本月", value: "month" },
  { label: "更早", value: "older" },
] as const;

type DateFilter = (typeof DATE_FILTERS)[number]["value"];

function categoryValue(task: TaskListItem, categories: TaskCategoryOption[]) {
  if (task.category) return task.category;
  const match = categories.find((item) => item.label === task.bookshelf);
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

export function Bookshelf() {
  const navigate = useNavigate();
  const [tasks, setTasks] = useState<TaskListItem[]>([]);
  const [categories, setCategories] = useState<TaskCategoryOption[]>(DEFAULT_CATEGORIES);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["value"]>("all");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [dateFilter, setDateFilter] = useState<DateFilter>("all");
  const [modalOpen, setModalOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .listTasks(1, 100)
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
        if (!cancelled && res.items.length > 0) setCategories(res.items);
      })
      .catch(() => {
        if (!cancelled) setCategories(DEFAULT_CATEGORIES);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const statusFiltered =
    filter === "all"
      ? tasks
      : filter === "processing"
        ? tasks.filter((t) =>
            ["pending", "processing", "fetching_topics", "filtering_topics", "generating_outline", "writing_chapters", "fact_checking"].includes(
              t.status,
            ),
          )
        : tasks.filter((t) => t.status === filter);

  const filtered = statusFiltered
    .filter((task) =>
      categoryFilter === "all"
        ? true
        : categoryFilter === "uncategorized"
          ? categoryValue(task, categories) === "uncategorized"
          : categoryValue(task, categories) === categoryFilter,
    )
    .filter((task) => inDateFilter(task, dateFilter));

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

  const handleDelete = async (task: TaskListItem) => {
    const ok = window.confirm(
      `确定删除《${task.title || "未命名书稿"}》吗？删除后不可恢复。`,
    );
    if (!ok) return;
    try {
      await api.deleteTask(task.id);
      setTasks((prev) => prev.filter((t) => t.id !== task.id));
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
            我的书架
          </h1>
          <p className="mt-2 text-xs tracking-widest text-warm-400">
            共 {tasks.length} 本书稿
          </p>
        </div>
        <button
          type="button"
          onClick={() => setModalOpen(true)}
          className="btn-gold"
        >
          <Plus className="h-4 w-4" strokeWidth={2} />
          <span className="tracking-widest">新建书稿</span>
        </button>
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
          <button
            type="button"
            onClick={() => setCategoryFilter("all")}
            className={`rounded-full border px-4 py-1.5 text-xs tracking-widest transition-colors ${
              categoryFilter === "all"
                ? "border-gold-400/60 bg-gold-500/10 text-warm-100"
                : "border-gold-500/15 text-warm-400 hover:border-gold-400/30 hover:text-warm-200"
            }`}
          >
            全部分类
          </button>
          {categories.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setCategoryFilter(item.id)}
              className={`rounded-full border px-4 py-1.5 text-xs tracking-widest transition-colors ${
                categoryFilter === item.id
                  ? "border-gold-400/60 bg-gold-500/10 text-warm-100"
                  : "border-gold-500/15 text-warm-400 hover:border-gold-400/30 hover:text-warm-200"
              }`}
            >
              {item.label}
            </button>
          ))}
          <button
            type="button"
            onClick={() => setCategoryFilter("uncategorized")}
            className={`rounded-full border px-4 py-1.5 text-xs tracking-widest transition-colors ${
              categoryFilter === "uncategorized"
                ? "border-gold-400/60 bg-gold-500/10 text-warm-100"
                : "border-gold-500/15 text-warm-400 hover:border-gold-400/30 hover:text-warm-200"
            }`}
          >
            未分类
          </button>
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
          title={filter === "all" ? "书架还空着" : "暂无相关书稿"}
          description="点击「新建书稿」，把第一段视频思想写成你的第一本书"
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
      />
    </>
  );
}
