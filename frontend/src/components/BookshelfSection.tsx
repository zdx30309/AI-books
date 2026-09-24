import { ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";
import { BookCard } from "./BookCard";
import { EmptyState } from "./EmptyState";
import { DEFAULT_CATEGORIES } from "@/constants/categories";
import type { TaskCategoryOption, TaskListItem } from "@/api/types";

interface Props {
  tasks: TaskListItem[];
  categories?: TaskCategoryOption[];
  loading: boolean;
  onCreate?: () => void;
  onMetadataUpdated?: (
    taskId: string,
    metadata: {
      title: string | null;
      bookshelf: string | null;
      category: string | null;
      category_label: string | null;
    },
  ) => void;
}

export function SectionHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
}) {
  return (
    <header className="mb-5 flex items-end justify-between gap-4">
      <div>
        <h2 className="font-serif text-[22px] leading-tight text-warm-50">{title}</h2>
        {description && <p className="mt-1 text-[13px] text-warm-500">{description}</p>}
      </div>
      {action}
    </header>
  );
}

export function BookshelfSection({
  tasks,
  categories = DEFAULT_CATEGORIES,
  loading,
  onCreate,
  onMetadataUpdated,
}: Props) {
  return (
    <section className="mt-12">
      <SectionHeader
        title="最近书稿"
        description="继续阅读、编辑或导出最新生成的作品"
        action={
          <Link
            to="/bookshelf"
            className="group inline-flex shrink-0 items-center gap-1.5 text-[13px] text-warm-300 transition-colors hover:text-gold-300"
          >
            查看全部
            <ArrowRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" strokeWidth={1.8} />
          </Link>
        }
      />

      {loading ? (
        <div className="grid grid-cols-2 gap-5 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="space-y-3">
              <div className="skeleton aspect-[3/4] rounded-xl" />
              <div className="skeleton h-3.5 w-3/4 rounded" />
              <div className="skeleton h-3 w-1/2 rounded" />
            </div>
          ))}
        </div>
      ) : tasks.length === 0 ? (
        <EmptyState
          title="书架还空着"
          description="点击「开始创作」，把第一段视频思想写成你的第一本书"
          action={
            onCreate ? (
              <button type="button" onClick={onCreate} className="btn-gold">
                开始创作
              </button>
            ) : undefined
          }
        />
      ) : (
        <div className="grid grid-cols-2 gap-5 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6">
          {tasks.slice(0, 6).map((task) => (
            <BookCard
              key={task.id}
              task={task}
              categories={categories}
              onMetadataUpdated={onMetadataUpdated}
            />
          ))}
        </div>
      )}
    </section>
  );
}
