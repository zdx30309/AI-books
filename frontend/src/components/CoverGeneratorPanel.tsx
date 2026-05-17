import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  CheckSquare,
  Download,
  FileSpreadsheet,
  ImageIcon,
  Loader2,
  PauseCircle,
  PlayCircle,
  RefreshCcw,
  Square,
  Trash2,
  UploadCloud,
  X,
} from "lucide-react";
import { api, ApiError } from "@/api/client";
import { DEFAULT_CATEGORIES } from "@/constants/categories";
import type { CoverJobItem, CoverJobResponse, TaskCategoryOption } from "@/api/types";

const CURRENT_COVER_JOB_KEY = "scriptbook.cover.current_job_id";

function isActiveJob(job: CoverJobResponse | null | undefined) {
  return job?.status === "pending" || job?.status === "processing";
}

function isPausedJob(job: CoverJobResponse | null | undefined) {
  return job?.status === "paused";
}

function statusLabel(status: string) {
  if (status === "completed") return "已完成";
  if (status === "failed") return "失败";
  if (status === "paused") return "已暂停";
  if (status === "processing") return "生成中";
  if (status === "pending") return "排队中";
  if (status === "deleted") return "已删除";
  return status || "空闲";
}

function itemMatchesCategory(item: CoverJobItem, filter: string) {
  if (item.status === "deleted") return false;
  if (filter === "all") return true;
  if (filter === "uncategorized") return !item.category && !item.category_label;
  return item.category === filter || item.category_label === filter;
}

function jobMatchesCategory(job: CoverJobResponse, filter: string) {
  if (filter === "all") return true;
  if (filter === "uncategorized") {
    return (
      !job.default_category &&
      !job.default_category_label &&
      !job.items.some((item) => item.category || item.category_label)
    );
  }
  return (
    job.default_category === filter ||
    job.default_category_label === filter ||
    job.items.some((item) => itemMatchesCategory(item, filter))
  );
}

function CoverPreview({
  jobId,
  item,
  selected,
  selectionMode,
  deleting,
  retrying,
  onToggle,
  onDelete,
  onRetry,
}: {
  jobId: string;
  item: CoverJobItem;
  selected: boolean;
  selectionMode: boolean;
  deleting: boolean;
  retrying: boolean;
  onToggle: () => void;
  onDelete: () => void;
  onRetry: () => void;
}) {
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    if (item.status !== "completed") {
      setSrc(null);
      return undefined;
    }
    let alive = true;
    let objectUrl: string | null = null;
    api
      .getCoverJobImageBlob(jobId, item.index)
      .then((blob) => {
        if (!alive) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      })
      .catch(() => {
        if (alive) setSrc(null);
      });
    return () => {
      alive = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [item.index, item.status, jobId]);

  return (
    <div
      className={`group relative overflow-hidden rounded-lg border bg-base-700/45 transition-colors ${
        selected ? "border-gold-300/80" : "border-gold-500/10"
      } ${selectionMode ? "cursor-pointer" : ""}`}
      onClick={selectionMode ? onToggle : undefined}
      role={selectionMode ? "button" : undefined}
      tabIndex={selectionMode ? 0 : undefined}
      onKeyDown={(event) => {
        if (!selectionMode) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onToggle();
        }
      }}
    >
      {selectionMode && (
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            onToggle();
          }}
          className="absolute left-2 top-2 z-10 flex h-7 w-7 items-center justify-center rounded-full border border-base-900/60 bg-base-900/80 text-warm-100 shadow"
          aria-label={selected ? "取消选择封面" : "选择封面"}
        >
          {selected ? (
            <CheckSquare className="h-4 w-4 text-gold-200" strokeWidth={1.8} />
          ) : (
            <Square className="h-4 w-4 text-warm-300" strokeWidth={1.8} />
          )}
        </button>
      )}
      {!selectionMode && item.status === "failed" && (
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            onRetry();
          }}
          disabled={retrying}
          className="absolute left-2 top-2 z-10 flex h-7 w-7 items-center justify-center rounded-full border border-gold-300/35 bg-base-900/80 text-gold-100 shadow transition-colors hover:border-gold-200/70 disabled:opacity-50"
          aria-label="重试单张封面"
          title="重试单张封面"
        >
          {retrying ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={1.7} />
          ) : (
            <RefreshCcw className="h-3.5 w-3.5" strokeWidth={1.7} />
          )}
        </button>
      )}
      {!selectionMode && (
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            onDelete();
          }}
          disabled={deleting}
          className="absolute right-2 top-2 z-10 flex h-7 w-7 items-center justify-center rounded-full border border-red-300/25 bg-base-900/80 text-red-100 opacity-100 shadow transition-colors hover:border-red-200/60 disabled:opacity-50 md:opacity-0 md:group-hover:opacity-100"
          aria-label="删除单张封面"
          title="删除单张封面"
        >
          {deleting ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={1.7} />
          ) : (
            <Trash2 className="h-3.5 w-3.5" strokeWidth={1.7} />
          )}
        </button>
      )}
      <div className="aspect-[2/3] bg-base-900/60">
        {src ? (
          <img src={src} alt={item.title} className="h-full w-full object-cover" />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-warm-500">
            {item.status === "failed" ? (
              <AlertCircle className="h-6 w-6 text-red-200" strokeWidth={1.6} />
            ) : item.status === "processing" ? (
              <Loader2 className="h-6 w-6 animate-spin text-gold-300" strokeWidth={1.6} />
            ) : (
              <ImageIcon className="h-6 w-6" strokeWidth={1.6} />
            )}
          </div>
        )}
      </div>
      <div className="space-y-1 p-3">
        <div className="truncate text-sm text-warm-100" title={item.title}>
          {item.title}
        </div>
        <div className="truncate text-[11px] text-warm-500" title={item.author || ""}>
          {item.author || "未填写作者"}
        </div>
        <div className="truncate text-[11px] text-gold-200/80" title={item.category_label || ""}>
          {item.category_label || "未分类"}
        </div>
        {item.error && (
          <div className="line-clamp-2 text-[11px] leading-relaxed text-red-200">
            {item.error}
          </div>
        )}
      </div>
    </div>
  );
}

function JobButton({
  job,
  active,
  selected,
  selectionMode,
  onOpen,
  onSelect,
}: {
  job: CoverJobResponse;
  active: boolean;
  selected: boolean;
  selectionMode: boolean;
  onOpen: () => void;
  onSelect: () => void;
}) {
  const click = selectionMode ? onSelect : onOpen;
  return (
    <button
      type="button"
      onClick={click}
      className={`w-full rounded-lg border px-4 py-3 text-left transition-colors ${
        selected
          ? "border-gold-300/70 bg-gold-500/15"
          : active
            ? "border-gold-400/50 bg-gold-500/10"
            : "border-gold-500/10 bg-base-800/45 hover:border-gold-400/30"
      }`}
    >
      <div className="flex items-center justify-between gap-3">
        <span className="truncate text-sm text-warm-100">{job.source_filename || job.job_id}</span>
        {selectionMode ? (
          selected ? (
            <CheckSquare className="h-4 w-4 shrink-0 text-gold-200" strokeWidth={1.7} />
          ) : (
            <Square className="h-4 w-4 shrink-0 text-warm-500" strokeWidth={1.7} />
          )
        ) : (
          <span className="shrink-0 text-[10px] uppercase tracking-widest text-gold-300">
            {job.progress}%
          </span>
        )}
      </div>
      <div className="mt-1 flex items-center justify-between gap-3 text-[11px] text-warm-500">
        <span>{job.default_category_label || "未分类"}</span>
        <span>
          {statusLabel(job.status)} · {job.completed}/{job.total}
        </span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-base-900">
        <div
          className="h-full rounded-full bg-gold-400 transition-all duration-500"
          style={{ width: `${job.progress}%` }}
        />
      </div>
    </button>
  );
}

export function CoverGeneratorPanel() {
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [jobs, setJobs] = useState<CoverJobResponse[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(() =>
    localStorage.getItem(CURRENT_COVER_JOB_KEY),
  );
  const [selectedJobIds, setSelectedJobIds] = useState<Set<string>>(() => new Set());
  const [selectedItemIndexes, setSelectedItemIndexes] = useState<Set<number>>(() => new Set());
  const [selectionMode, setSelectionMode] = useState(false);
  const [itemSelectionMode, setItemSelectionMode] = useState(false);
  const [categories, setCategories] = useState<TaskCategoryOption[]>(DEFAULT_CATEGORIES);
  const [category, setCategory] = useState("social_science");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [loadingJobs, setLoadingJobs] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [working, setWorking] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const visibleJobs = useMemo(
    () => jobs.filter((item) => jobMatchesCategory(item, categoryFilter)),
    [jobs, categoryFilter],
  );

  const job = useMemo(() => {
    if (!visibleJobs.length) return null;
    return visibleJobs.find((item) => item.job_id === selectedJobId) ?? visibleJobs[0];
  }, [visibleJobs, selectedJobId]);

  const visibleItems = useMemo(
    () => (job?.items ?? []).filter((item) => itemMatchesCategory(item, categoryFilter)),
    [job, categoryFilter],
  );

  const active = isActiveJob(job);
  const selectedCount = selectedJobIds.size;
  const selectedItemCount = selectedItemIndexes.size;
  const selectedFailedItemCount = useMemo(
    () =>
      visibleItems.filter(
        (item) => selectedItemIndexes.has(item.index) && item.status === "failed",
      ).length,
    [selectedItemIndexes, visibleItems],
  );
  const progressText = useMemo(() => {
    if (!job) return "等待上传";
    return `${job.completed}/${job.total} 完成，${job.failed} 失败`;
  }, [job]);

  const loadJobs = async (selectJobId?: string | null) => {
    const res = await api.listCoverJobs(120);
    setJobs(res.items);
    setSelectedJobIds((prev) => {
      const valid = new Set(res.items.map((item) => item.job_id));
      const next = new Set([...prev].filter((id) => valid.has(id)));
      return next;
    });
    const nextId = selectJobId ?? selectedJobId ?? res.items[0]?.job_id ?? null;
    const exists = nextId && res.items.some((item) => item.job_id === nextId);
    const finalId = exists ? nextId : (res.items[0]?.job_id ?? null);
    setSelectedJobId(finalId);
    if (finalId) {
      localStorage.setItem(CURRENT_COVER_JOB_KEY, finalId);
    } else {
      localStorage.removeItem(CURRENT_COVER_JOB_KEY);
    }
  };

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

  useEffect(() => {
    let cancelled = false;
    setLoadingJobs(true);
    loadJobs()
      .catch((e) => {
        if (!cancelled) setError(e instanceof ApiError ? e.detail : "加载封面任务失败");
      })
      .finally(() => {
        if (!cancelled) setLoadingJobs(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!jobs.some(isActiveJob)) return undefined;
    const timer = window.setInterval(() => {
      loadJobs().catch((e) => {
        setError(e instanceof ApiError ? e.detail : "刷新封面进度失败");
      });
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [jobs, selectedJobId]);

  useEffect(() => {
    const validIndexes = new Set(visibleItems.map((item) => item.index));
    setSelectedItemIndexes((prev) => new Set([...prev].filter((index) => validIndexes.has(index))));
  }, [visibleItems]);

  const selectJob = (jobId: string) => {
    setSelectedJobId(jobId);
    setSelectedItemIndexes(new Set());
    setItemSelectionMode(false);
    localStorage.setItem(CURRENT_COVER_JOB_KEY, jobId);
  };

  const selectCategoryFilter = (next: string) => {
    setCategoryFilter(next);
    setSelectedJobIds(new Set());
    setSelectedItemIndexes(new Set());
  };

  const toggleJobSelection = (jobId: string) => {
    setSelectedJobIds((prev) => {
      const next = new Set(prev);
      if (next.has(jobId)) next.delete(jobId);
      else next.add(jobId);
      return next;
    });
  };

  const toggleItemSelection = (index: number) => {
    setSelectedItemIndexes((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };

  const refresh = async () => {
    setRefreshing(true);
    setError(null);
    try {
      await loadJobs(job?.job_id ?? undefined);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "刷新失败");
    } finally {
      setRefreshing(false);
    }
  };

  const upload = async (file: File | null | undefined) => {
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      const next = await api.createCoverJob(file, category);
      await loadJobs(next.job_id);
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? `[${e.status}] ${e.detail}`
          : e instanceof Error
            ? e.message
            : "上传失败";
      setError(msg);
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const download = async () => {
    if (!job) return;
    setDownloading(true);
    setError(null);
    try {
      await api.downloadCoverJob(job.job_id);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "下载失败");
    } finally {
      setDownloading(false);
    }
  };

  const batchAction = async (
    action: "pause" | "resume" | "delete",
    ids = [...selectedJobIds],
  ) => {
    if (ids.length === 0) return;
    const actionText = action === "pause" ? "暂停" : action === "resume" ? "继续" : "删除";
    if (action === "delete") {
      const ok = window.confirm(`确定删除选中的 ${ids.length} 个封面任务吗？`);
      if (!ok) return;
    }
    setWorking(action);
    setError(null);
    try {
      await api.batchCoverJobs({ job_ids: ids, action });
      setSelectedJobIds(new Set());
      if (action === "delete" && job && ids.includes(job.job_id)) {
        await loadJobs(null);
      } else {
        await loadJobs(job?.job_id ?? undefined);
      }
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : `${actionText}失败`;
      setError(msg);
    } finally {
      setWorking(null);
    }
  };

  const pauseCurrent = async () => {
    if (!job) return;
    setWorking("pause-current");
    setError(null);
    try {
      await api.pauseCoverJob(job.job_id);
      await loadJobs(job.job_id);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "暂停失败");
    } finally {
      setWorking(null);
    }
  };

  const resumeCurrent = async () => {
    if (!job) return;
    setWorking("resume-current");
    setError(null);
    try {
      await api.resumeCoverJob(job.job_id);
      await loadJobs(job.job_id);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "继续失败");
    } finally {
      setWorking(null);
    }
  };

  const deleteCurrent = async () => {
    if (!job) return;
    const ok = window.confirm("确定删除当前封面任务吗？");
    if (!ok) return;
    setWorking("delete-current");
    setError(null);
    try {
      await api.deleteCoverJob(job.job_id);
      await loadJobs(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "删除失败");
    } finally {
      setWorking(null);
    }
  };

  const deleteItem = async (index: number) => {
    if (!job) return;
    const target = job.items.find((item) => item.index === index);
    const ok = window.confirm(`确定删除《${target?.title || "这张封面"}》吗？`);
    if (!ok) return;
    setWorking(`delete-item-${index}`);
    setError(null);
    try {
      await api.deleteCoverJobItem(job.job_id, index);
      setSelectedItemIndexes((prev) => {
        const next = new Set(prev);
        next.delete(index);
        return next;
      });
      await loadJobs(job.job_id);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "删除单张封面失败");
    } finally {
      setWorking(null);
    }
  };

  const deleteSelectedItems = async () => {
    if (!job || selectedItemCount === 0) return;
    const indexes = [...selectedItemIndexes];
    const ok = window.confirm(`确定删除选中的 ${indexes.length} 张封面吗？`);
    if (!ok) return;
    setWorking("delete-items");
    setError(null);
    try {
      await api.deleteCoverJobItems(job.job_id, { indexes });
      setSelectedItemIndexes(new Set());
      setItemSelectionMode(false);
      await loadJobs(job.job_id);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "批量删除封面失败");
    } finally {
      setWorking(null);
    }
  };

  const retryFailedCurrent = async () => {
    if (!job || job.failed === 0) return;
    setWorking("retry-failed");
    setError(null);
    try {
      await api.retryCoverJob(job.job_id);
      await loadJobs(job.job_id);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "重试失败封面失败");
    } finally {
      setWorking(null);
    }
  };

  const retryItem = async (index: number) => {
    if (!job) return;
    setWorking(`retry-item-${index}`);
    setError(null);
    try {
      await api.retryCoverJobItem(job.job_id, index);
      await loadJobs(job.job_id);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "重试单张封面失败");
    } finally {
      setWorking(null);
    }
  };

  const retrySelectedItems = async () => {
    if (!job || selectedFailedItemCount === 0) return;
    const indexes = visibleItems
      .filter((item) => selectedItemIndexes.has(item.index) && item.status === "failed")
      .map((item) => item.index);
    setWorking("retry-items");
    setError(null);
    try {
      await api.retryCoverJobItems(job.job_id, { indexes });
      setSelectedItemIndexes(new Set());
      setItemSelectionMode(false);
      await loadJobs(job.job_id);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "批量重试封面失败");
    } finally {
      setWorking(null);
    }
  };

  return (
    <section>
      <div className="mb-8 flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="font-serif text-3xl tracking-wide text-warm-100">
            封面书架
          </h1>
          <div className="mt-2 flex items-center gap-2 text-xs tracking-widest text-warm-500">
            <FileSpreadsheet className="h-3.5 w-3.5" strokeWidth={1.6} />
            <span>{job?.source_filename || "等待 Excel 文件"}</span>
          </div>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <label className="block">
            <span className="mb-2 block text-[10px] tracking-widest text-warm-500">
              默认分类
            </span>
            <select
              value={category}
              onChange={(event) => setCategory(event.target.value)}
              className="h-[38px] rounded-full border border-gold-500/15 bg-base-700/70 px-4 text-xs tracking-widest text-warm-100 outline-none transition-colors focus:border-gold-400/45"
            >
              {categories.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <input
            ref={fileRef}
            type="file"
            accept=".xlsx,.xlsm,.csv"
            className="hidden"
            onChange={(event) => upload(event.target.files?.[0])}
          />
          <button type="button" onClick={refresh} disabled={refreshing} className="btn-ghost">
            {refreshing || active ? (
              <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.7} />
            ) : (
              <RefreshCcw className="h-4 w-4" strokeWidth={1.7} />
            )}
            <span className="tracking-widest">刷新进度</span>
          </button>
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            disabled={uploading}
            className="btn-gold"
          >
            {uploading ? (
              <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.7} />
            ) : (
              <UploadCloud className="h-4 w-4" strokeWidth={1.7} />
            )}
            <span className="tracking-widest">上传 Excel</span>
          </button>
        </div>
      </div>

      <div className="mb-6">
        <div className="mb-3 text-[10px] tracking-widest text-warm-500">
          封面书架分类
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => selectCategoryFilter("all")}
            className={`rounded-full border px-3 py-1.5 text-[10px] tracking-widest transition-colors ${
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
              onClick={() => selectCategoryFilter(item.id)}
              className={`rounded-full border px-3 py-1.5 text-[10px] tracking-widest transition-colors ${
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
            onClick={() => selectCategoryFilter("uncategorized")}
            className={`rounded-full border px-3 py-1.5 text-[10px] tracking-widest transition-colors ${
              categoryFilter === "uncategorized"
                ? "border-gold-400/60 bg-gold-500/10 text-warm-100"
                : "border-gold-500/15 text-warm-400 hover:border-gold-400/30 hover:text-warm-200"
            }`}
          >
            未分类
          </button>
        </div>
      </div>

      <div className="grid gap-8 xl:grid-cols-[340px_1fr]">
        <aside className="space-y-3">
          <div className="flex items-center justify-between gap-2">
            <div className="text-[10px] tracking-widest text-warm-500">
              封面任务
            </div>
            <button
              type="button"
              onClick={() => {
                setSelectionMode((prev) => !prev);
                setSelectedJobIds(new Set());
              }}
              className="inline-flex items-center gap-1.5 rounded-full border border-gold-500/15 px-3 py-1.5 text-[10px] tracking-widest text-warm-300 transition-colors hover:border-gold-400/40 hover:text-warm-100"
            >
              {selectionMode ? <X className="h-3 w-3" /> : <CheckSquare className="h-3 w-3" />}
              {selectionMode ? "退出" : "多选"}
            </button>
          </div>

          {selectionMode && (
            <div className="rounded-lg border border-gold-500/10 bg-base-800/45 p-3">
              <div className="mb-3 text-xs text-warm-400">
                已选择 {selectedCount} 个
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => setSelectedJobIds(new Set(visibleJobs.map((item) => item.job_id)))}
                  disabled={visibleJobs.length === 0}
                  className="rounded-full border border-gold-500/15 px-3 py-1.5 text-[10px] tracking-widest text-warm-300 disabled:opacity-50"
                >
                  全选
                </button>
                <button
                  type="button"
                  onClick={() => setSelectedJobIds(new Set())}
                  disabled={selectedCount === 0}
                  className="rounded-full border border-gold-500/15 px-3 py-1.5 text-[10px] tracking-widest text-warm-300 disabled:opacity-50"
                >
                  清空
                </button>
                <button
                  type="button"
                  onClick={() => batchAction("pause")}
                  disabled={selectedCount === 0 || working !== null}
                  className="rounded-full border border-gold-500/15 px-3 py-1.5 text-[10px] tracking-widest text-warm-300 disabled:opacity-50"
                >
                  批量暂停
                </button>
                <button
                  type="button"
                  onClick={() => batchAction("resume")}
                  disabled={selectedCount === 0 || working !== null}
                  className="rounded-full border border-gold-500/15 px-3 py-1.5 text-[10px] tracking-widest text-warm-300 disabled:opacity-50"
                >
                  批量继续
                </button>
                <button
                  type="button"
                  onClick={() => batchAction("delete")}
                  disabled={selectedCount === 0 || working !== null}
                  className="rounded-full border border-red-400/30 bg-red-500/10 px-3 py-1.5 text-[10px] tracking-widest text-red-100 disabled:opacity-50"
                >
                  批量删除
                </button>
              </div>
            </div>
          )}

          {loadingJobs ? (
            <div className="rounded-lg border border-gold-500/10 bg-base-800/45 p-4 text-xs text-warm-500">
              加载中
            </div>
          ) : jobs.length === 0 ? (
            <div className="rounded-lg border border-gold-500/10 bg-base-800/45 p-4 text-xs text-warm-500">
              暂无封面任务
            </div>
          ) : visibleJobs.length === 0 ? (
            <div className="rounded-lg border border-gold-500/10 bg-base-800/45 p-4 text-xs text-warm-500">
              暂无该分类封面任务
            </div>
          ) : (
            visibleJobs.map((item) => (
              <JobButton
                key={item.job_id}
                job={item}
                active={item.job_id === job?.job_id}
                selected={selectedJobIds.has(item.job_id)}
                selectionMode={selectionMode}
                onOpen={() => selectJob(item.job_id)}
                onSelect={() => toggleJobSelection(item.job_id)}
              />
            ))
          )}
        </aside>

        <div>
          <div className="rounded-lg border border-gold-500/10 bg-base-800/45 p-5">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
              <div className="min-w-0">
                <div className="truncate text-sm tracking-widest text-warm-100">
                  {job ? job.current_step : "封面队列空闲"}
                </div>
                <div className="mt-1 text-xs text-warm-500">{progressText}</div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {job && isActiveJob(job) && (
                  <button
                    type="button"
                    onClick={pauseCurrent}
                    disabled={working !== null}
                    className="btn-ghost disabled:opacity-50"
                  >
                    <PauseCircle className="h-3.5 w-3.5" strokeWidth={1.6} />
                    暂停
                  </button>
                )}
                {job && isPausedJob(job) && (
                  <button
                    type="button"
                    onClick={resumeCurrent}
                    disabled={working !== null}
                    className="btn-ghost disabled:opacity-50"
                  >
                    <PlayCircle className="h-3.5 w-3.5" strokeWidth={1.6} />
                    继续
                  </button>
                )}
                {job && job.failed > 0 && (
                  <button
                    type="button"
                    onClick={retryFailedCurrent}
                    disabled={working !== null}
                    className="btn-ghost disabled:opacity-50"
                  >
                    {working === "retry-failed" ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={1.6} />
                    ) : (
                      <RefreshCcw className="h-3.5 w-3.5" strokeWidth={1.6} />
                    )}
                    重试失败
                  </button>
                )}
                {job && job.completed > 0 && (
                  <button
                    type="button"
                    onClick={download}
                    disabled={downloading}
                    className="btn-ghost disabled:opacity-50"
                  >
                    {downloading ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={1.6} />
                    ) : (
                      <Download className="h-3.5 w-3.5" strokeWidth={1.6} />
                    )}
                    下载
                  </button>
                )}
                {job && (
                  <button
                    type="button"
                    onClick={deleteCurrent}
                    disabled={working !== null}
                    className="btn-ghost text-red-100 disabled:opacity-50"
                  >
                    <Trash2 className="h-3.5 w-3.5" strokeWidth={1.6} />
                    删除
                  </button>
                )}
              </div>
            </div>

            <div className="mt-5">
              <div className="mb-2 flex items-center justify-between text-xs text-warm-400">
                <span>{job?.default_category_label || statusLabel(job?.status || "")}</span>
                <span className="text-gold-300">{job?.progress ?? 0}%</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-base-900">
                <div
                  className="h-full rounded-full bg-gold-400 transition-all duration-500"
                  style={{ width: `${job?.progress ?? 0}%` }}
                />
              </div>
            </div>

            {error && (
              <div className="mt-4 rounded-lg border border-red-400/20 bg-red-500/10 px-4 py-3 text-sm text-red-100">
                {error}
              </div>
            )}
          </div>

          {job && job.items.length > 0 && (
            <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="text-xs text-warm-500">
                当前分类显示 {visibleItems.length} 张封面
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setItemSelectionMode((prev) => !prev);
                    setSelectedItemIndexes(new Set());
                  }}
                  className="inline-flex items-center gap-1.5 rounded-full border border-gold-500/15 px-3 py-1.5 text-[10px] tracking-widest text-warm-300 transition-colors hover:border-gold-400/40 hover:text-warm-100"
                >
                  {itemSelectionMode ? (
                    <X className="h-3 w-3" strokeWidth={1.6} />
                  ) : (
                    <CheckSquare className="h-3 w-3" strokeWidth={1.6} />
                  )}
                  {itemSelectionMode ? "退出多选" : "封面多选"}
                </button>
                {itemSelectionMode && (
                  <>
                    <button
                      type="button"
                      onClick={() =>
                        setSelectedItemIndexes(new Set(visibleItems.map((item) => item.index)))
                      }
                      disabled={visibleItems.length === 0}
                      className="rounded-full border border-gold-500/15 px-3 py-1.5 text-[10px] tracking-widest text-warm-300 disabled:opacity-50"
                    >
                      全选
                    </button>
                    <button
                      type="button"
                      onClick={() => setSelectedItemIndexes(new Set())}
                      disabled={selectedItemCount === 0}
                      className="rounded-full border border-gold-500/15 px-3 py-1.5 text-[10px] tracking-widest text-warm-300 disabled:opacity-50"
                    >
                      清空
                    </button>
                    <button
                      type="button"
                      onClick={retrySelectedItems}
                      disabled={selectedFailedItemCount === 0 || working !== null}
                      className="rounded-full border border-gold-400/30 bg-gold-500/10 px-3 py-1.5 text-[10px] tracking-widest text-gold-100 disabled:opacity-50"
                    >
                      重试 {selectedFailedItemCount || ""} 张
                    </button>
                    <button
                      type="button"
                      onClick={deleteSelectedItems}
                      disabled={selectedItemCount === 0 || working !== null}
                      className="rounded-full border border-red-400/30 bg-red-500/10 px-3 py-1.5 text-[10px] tracking-widest text-red-100 disabled:opacity-50"
                    >
                      删除 {selectedItemCount || ""} 张
                    </button>
                  </>
                )}
              </div>
            </div>
          )}

          {job && visibleItems.length > 0 ? (
            <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6">
              {visibleItems.map((item) => (
                <CoverPreview
                  key={item.index}
                  jobId={job.job_id}
                  item={item}
                  selected={selectedItemIndexes.has(item.index)}
                  selectionMode={itemSelectionMode}
                  deleting={working === `delete-item-${item.index}` || working === "delete-items"}
                  retrying={
                    working === `retry-item-${item.index}` ||
                    working === "retry-items" ||
                    working === "retry-failed"
                  }
                  onToggle={() => toggleItemSelection(item.index)}
                  onDelete={() => deleteItem(item.index)}
                  onRetry={() => retryItem(item.index)}
                />
              ))}
            </div>
          ) : job && job.items.length > 0 ? (
            <div className="mt-4 rounded-lg border border-gold-500/10 bg-base-800/45 p-4 text-xs text-warm-500">
              暂无该分类封面
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}
