import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { HeroSection } from "@/components/HeroSection";
import { BookshelfSection } from "@/components/BookshelfSection";
import { WorkshopSection } from "@/components/WorkshopSection";
import { CreateTaskModal } from "@/components/CreateTaskModal";
import { api } from "@/api/client";
import { DEFAULT_CATEGORIES } from "@/constants/categories";
import type { StatsResponse, TaskCategoryOption, TaskListItem } from "@/api/types";

export function Workspace() {
  const navigate = useNavigate();
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [statsLoading, setStatsLoading] = useState(true);
  const [tasks, setTasks] = useState<TaskListItem[]>([]);
  const [categories, setCategories] = useState<TaskCategoryOption[]>(DEFAULT_CATEGORIES);
  const [tasksLoading, setTasksLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);

  const fetchAll = async () => {
    setStatsLoading(true);
    setTasksLoading(true);
    try {
      const [s, t, c] = await Promise.all([
        api.getStats(),
        api.listTasks(1, 12),
        api.getTaskCategories().catch(() => ({ items: DEFAULT_CATEGORIES })),
      ]);
      setStats(s);
      setTasks(t.items);
      setCategories(c.items.length > 0 ? c.items : DEFAULT_CATEGORIES);
    } catch (e) {
      console.error("workspace fetch failed", e);
    } finally {
      setStatsLoading(false);
      setTasksLoading(false);
    }
  };

  useEffect(() => {
    void fetchAll();
  }, []);

  // 命令面板的「新建书稿」跳到 /?create=1，这里直接打开创作面板。
  const [searchParams, setSearchParams] = useSearchParams();
  useEffect(() => {
    if (searchParams.get("create") === "1") {
      setModalOpen(true);
      setSearchParams({}, { replace: true });
    }
  }, [searchParams, setSearchParams]);

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
      <HeroSection
        stats={stats}
        statsLoading={statsLoading}
        onCreate={() => setModalOpen(true)}
      />

      <BookshelfSection
        tasks={tasks}
        categories={categories}
        loading={tasksLoading}
        onCreate={() => setModalOpen(true)}
        onMetadataUpdated={handleMetadataUpdated}
      />

      <WorkshopSection
        onStartCreate={() => setModalOpen(true)}
        latestTaskId={tasks[0]?.id ?? null}
      />

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
      />
    </>
  );
}
