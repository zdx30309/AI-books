import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { HeroSection } from "@/components/HeroSection";
import { BookshelfSection } from "@/components/BookshelfSection";
import { WorkshopSection } from "@/components/WorkshopSection";
import { CreateTaskModal } from "@/components/CreateTaskModal";
import { api } from "@/api/client";
import type { StatsResponse, TaskListItem } from "@/api/types";

export function Workspace() {
  const navigate = useNavigate();
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [statsLoading, setStatsLoading] = useState(true);
  const [tasks, setTasks] = useState<TaskListItem[]>([]);
  const [tasksLoading, setTasksLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);

  const fetchAll = async () => {
    setStatsLoading(true);
    setTasksLoading(true);
    try {
      const [s, t] = await Promise.all([api.getStats(), api.listTasks(1, 12)]);
      setStats(s);
      setTasks(t.items);
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

  return (
    <>
      <HeroSection
        stats={stats}
        statsLoading={statsLoading}
        onCreate={() => setModalOpen(true)}
      />

      <BookshelfSection
        tasks={tasks}
        loading={tasksLoading}
        onCreate={() => setModalOpen(true)}
      />

      <WorkshopSection onStartCreate={() => setModalOpen(true)} />

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
