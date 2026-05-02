import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus } from "lucide-react";
import { BookCard } from "@/components/BookCard";
import { CreateTaskModal } from "@/components/CreateTaskModal";
import { EmptyState } from "@/components/EmptyState";
import { api, ApiError } from "@/api/client";
import type { TaskListItem, TaskStatus } from "@/api/types";

const FILTERS: { label: string; value: TaskStatus | "all" }[] = [
  { label: "全部", value: "all" },
  { label: "已完成", value: "completed" },
  { label: "生成中", value: "processing" },
  { label: "失败", value: "failed" },
];

export function Bookshelf() {
  const navigate = useNavigate();
  const [tasks, setTasks] = useState<TaskListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["value"]>("all");
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

  const filtered =
    filter === "all"
      ? tasks
      : filter === "processing"
        ? tasks.filter((t) =>
            ["pending", "processing", "fetching_topics", "filtering_topics", "generating_outline", "writing_chapters", "fact_checking"].includes(
              t.status,
            ),
          )
        : tasks.filter((t) => t.status === filter);

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
        <div className="grid grid-cols-2 gap-5 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6">
          {filtered.map((task) => (
            <BookCard key={task.id} task={task} onDelete={handleDelete} />
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
