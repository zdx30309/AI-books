import { ChevronLeft, ChevronRight } from "lucide-react";
import { Link } from "react-router-dom";
import { BookCard } from "./BookCard";
import { EmptyState } from "./EmptyState";
import type { TaskListItem } from "@/api/types";

interface Props {
  tasks: TaskListItem[];
  loading: boolean;
  onCreate?: () => void;
}

export function BookshelfSection({ tasks, loading, onCreate }: Props) {
  return (
    <section className="mt-16">
      <header className="mb-6 flex items-center justify-between">
        <h2 className="flex items-center gap-3 font-serif text-xl tracking-widest text-warm-100">
          <span className="h-4 w-[3px] rounded-full bg-gradient-to-b from-gold-300 to-gold-600" />
          我的书架
        </h2>
        <div className="flex items-center gap-2">
          <button
            type="button"
            aria-label="上一页"
            className="flex h-7 w-7 items-center justify-center rounded-full border border-gold-500/15 text-warm-400 transition-colors hover:border-gold-400/40 hover:text-warm-100"
          >
            <ChevronLeft className="h-3.5 w-3.5" strokeWidth={1.5} />
          </button>
          <button
            type="button"
            aria-label="下一页"
            className="flex h-7 w-7 items-center justify-center rounded-full border border-gold-500/15 text-warm-400 transition-colors hover:border-gold-400/40 hover:text-warm-100"
          >
            <ChevronRight className="h-3.5 w-3.5" strokeWidth={1.5} />
          </button>
          <Link
            to="/bookshelf"
            className="ml-2 text-xs tracking-widest text-warm-300 transition-colors hover:text-warm-100"
          >
            查看全部
          </Link>
        </div>
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
            <BookCard key={task.id} task={task} />
          ))}
        </div>
      )}
    </section>
  );
}
