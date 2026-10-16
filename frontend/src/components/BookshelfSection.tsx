import { bookshelfPath, type BookshelfVersion } from "@/constants/bookshelfVersions";
import { Link } from "react-router-dom";
import { BookCard } from "./BookCard";
import { EmptyState } from "./EmptyState";
import { DEFAULT_CATEGORIES } from "@/constants/categories";
import type { TaskCategoryOption, TaskListItem } from "@/api/types";

interface Props {
  bookshelfVersion?: BookshelfVersion;
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

export function BookshelfSection({
  tasks,
  bookshelfVersion = "1.0",
  categories = DEFAULT_CATEGORIES,
  loading,
  onCreate,
  onMetadataUpdated,
}: Props) {
  return (
    <section className="mt-5">
      <header className="mb-5 flex items-center justify-between">
        <p className="text-sm text-warm-300">{bookshelfVersion}书架 · 最近更新</p>
        <Link to={bookshelfPath(bookshelfVersion)} className="text-sm text-gold-300 hover:text-warm-100">
          查看全部
        </Link>
      </header>

      {loading ? (
        <div className="grid grid-cols-2 gap-5 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6">
          {Array.from({ length: 6 }).map((_, i) => (
            <div
              key={i}
              className="aspect-[3/4] animate-pulse rounded-xl border border-gold-500/8 bg-base-700/40"
            />
          ))}
        </div>
      ) : tasks.length === 0 ? (
        <EmptyState
          title="书架还空着"
          description="点击「开始创作」，把第一段视频思想写成你的第一本书"
          action={
            onCreate ? (
              <button type="button" onClick={onCreate} className="btn-gold">
                <span className="tracking-widest">开始创作</span>
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
