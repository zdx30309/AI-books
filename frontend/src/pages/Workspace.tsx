import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowRight, Plus } from "lucide-react";
import { BookshelfSection } from "@/components/BookshelfSection";
import { CreateTaskModal } from "@/components/CreateTaskModal";
import { api } from "@/api/client";
import { readWithTransientRetry } from "@/api/readWithRetry";
import { bookPath, bookshelfPath, categoriesForShelf, type BookshelfVersion } from "@/constants/bookshelfVersions";
import { DEFAULT_CATEGORIES } from "@/constants/categories";
import type { TaskCategoryOption, TaskListItem } from "@/api/types";

const shelves: { version: BookshelfVersion; title: string; description?: string; detail: string }[] = [
  { version: "1.0", title: "经典创作", description: "社会痛点、人生哲学、科普等原有类别", detail: "沿用经典创作流程，管理原有书稿。" },
  { version: "2.0", title: "社科2610", detail: "生成后直接成稿，不经过额外润色。" },
];

export function Workspace() {
  const navigate = useNavigate();
  const [version, setVersion] = useState<BookshelfVersion>("2.0");
  const [modalVersion, setModalVersion] = useState<BookshelfVersion | null>(null);
  const [tasks, setTasks] = useState<TaskListItem[]>([]);
  const [categories, setCategories] = useState<TaskCategoryOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    setTasks([]);
    void Promise.all([
      readWithTransientRetry(() => api.listTasks(1, 6, version)),
      api.getTaskCategories(version).catch(() => ({ items: categoriesForShelf(DEFAULT_CATEGORIES, version) })),
    ]).then(([result, options]) => {
      if (cancelled) return;
      setTasks(result.items);
      setCategories(categoriesForShelf(options.items, version));
    }).catch(() => {
      if (!cancelled) setError(true);
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [version, reload]);

  return (
    <>
      <header className="mb-8">
        <h1 className="font-serif text-3xl text-warm-100 sm:text-4xl">工作台</h1>
        <p className="mt-3 text-sm text-warm-300">选择书架开始创作，或继续查看最近的书稿。</p>
      </header>

      <section aria-label="选择创作书架" className="grid gap-5 lg:grid-cols-2">
        {shelves.map((shelf) => (
          <article key={shelf.version} className={`flex flex-col rounded-2xl border p-6 sm:p-8 ${shelf.version === "2.0" ? "border-gold-400/40 bg-gold-500/8" : "border-gold-500/15 bg-base-800/40"}`}>
            <div className="flex items-center justify-between gap-3">
              <h2 className="font-serif text-2xl text-warm-100">{shelf.version}书架</h2>
              <span className="rounded-full border border-gold-500/20 px-3 py-1 text-xs text-gold-300">{shelf.title}</span>
            </div>
            {shelf.description && <p className="mt-5 text-sm text-warm-100">{shelf.description}</p>}
            <p className={`${shelf.description ? "mt-2" : "mt-5"} text-sm text-warm-300`}>{shelf.detail}</p>
            <div className="mt-7 flex flex-wrap items-center gap-5">
              <button type="button" className="btn-gold" onClick={() => setModalVersion(shelf.version)}>
                <Plus className="h-4 w-4" />新建{shelf.version}书稿
              </button>
              <Link to={bookshelfPath(shelf.version)} className="inline-flex items-center gap-2 text-sm text-warm-200 hover:text-gold-300">
                进入{shelf.version}书架<ArrowRight className="h-4 w-4" />
              </Link>
            </div>
          </article>
        ))}
      </section>

      <section className="mt-10" aria-label="最近书稿">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h2 className="font-serif text-xl text-warm-100">最近书稿</h2>
          <div className="flex gap-2" aria-label="切换最近书稿版本">
            {shelves.map((shelf) => (
              <button key={shelf.version} type="button" aria-pressed={version === shelf.version} onClick={() => setVersion(shelf.version)}
                className={`rounded-full border px-4 py-2 text-sm transition-colors ${version === shelf.version ? "border-gold-400/50 bg-gold-500/10 text-gold-300" : "border-gold-500/15 text-warm-300 hover:text-warm-100"}`}>
                {shelf.version}书架
              </button>
            ))}
          </div>
        </div>
        {error ? (
          <div role="alert" className="mt-6 rounded-xl border border-gold-500/20 p-6 text-sm text-warm-200">
            书稿暂时加载失败，请重试。
            <button type="button" onClick={() => setReload((n) => n + 1)} className="ml-4 text-gold-300 underline">重新加载</button>
          </div>
        ) : (
          <BookshelfSection tasks={tasks} categories={categories} loading={loading} bookshelfVersion={version}
            onCreate={() => setModalVersion(version)}
            onMetadataUpdated={(id, metadata) => setTasks((previous) => previous.map((task) => task.id === id ? { ...task, ...metadata } : task))} />
        )}
      </section>

      {modalVersion && <CreateTaskModal key={modalVersion} open bookshelfVersion={modalVersion}
        onClose={() => setModalVersion(null)}
        onCreated={(id) => navigate(bookPath(id, modalVersion))}
        onLongNovelCreated={(id) => navigate(`/long-novel/${id}`)} />}
    </>
  );
}
