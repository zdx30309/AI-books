import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  Download,
  FileSpreadsheet,
  ImageIcon,
  Loader2,
  RefreshCcw,
  UploadCloud,
} from "lucide-react";
import { api, ApiError } from "@/api/client";
import { DEFAULT_CATEGORIES } from "@/constants/categories";
import type { CoverJobItem, CoverJobResponse, TaskCategoryOption } from "@/api/types";

const CURRENT_COVER_JOB_KEY = "scriptbook.cover.current_job_id";

function isActiveJob(job: CoverJobResponse | null | undefined) {
  return job?.status === "pending" || job?.status === "processing";
}

function CoverPreview({ jobId, item }: { jobId: string; item: CoverJobItem }) {
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
    <div className="overflow-hidden rounded-lg border border-gold-500/10 bg-base-700/45">
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
  onClick,
}: {
  job: CoverJobResponse;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full rounded-lg border px-4 py-3 text-left transition-colors ${
        active
          ? "border-gold-400/50 bg-gold-500/10"
          : "border-gold-500/10 bg-base-800/45 hover:border-gold-400/30"
      }`}
    >
      <div className="flex items-center justify-between gap-3">
        <span className="truncate text-sm text-warm-100">{job.source_filename || job.job_id}</span>
        <span className="shrink-0 text-[10px] uppercase tracking-widest text-gold-300">
          {job.progress}%
        </span>
      </div>
      <div className="mt-1 flex items-center justify-between gap-3 text-[11px] text-warm-500">
        <span>{job.default_category_label || "未分类"}</span>
        <span>
          {job.completed}/{job.total}
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
  const [categories, setCategories] = useState<TaskCategoryOption[]>(DEFAULT_CATEGORIES);
  const [category, setCategory] = useState("social_science");
  const [loadingJobs, setLoadingJobs] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const job = useMemo(() => {
    if (!jobs.length) return null;
    return jobs.find((item) => item.job_id === selectedJobId) ?? jobs[0];
  }, [jobs, selectedJobId]);

  const active = isActiveJob(job);
  const progressText = useMemo(() => {
    if (!job) return "等待上传";
    return `${job.completed}/${job.total} 完成，${job.failed} 失败`;
  }, [job]);

  const loadJobs = async (selectJobId?: string) => {
    const res = await api.listCoverJobs(80);
    setJobs(res.items);
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

  const selectJob = (jobId: string) => {
    setSelectedJobId(jobId);
    localStorage.setItem(CURRENT_COVER_JOB_KEY, jobId);
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
          {job && job.completed > 0 && (
            <button
              type="button"
              onClick={download}
              disabled={downloading}
              className="btn-ghost"
            >
              {downloading ? (
                <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.7} />
              ) : (
                <Download className="h-4 w-4" strokeWidth={1.7} />
              )}
              <span className="tracking-widest">下载封面</span>
            </button>
          )}
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

      <div className="grid gap-8 xl:grid-cols-[320px_1fr]">
        <aside className="space-y-3">
          <div className="text-[10px] tracking-widest text-warm-500">
            封面任务
          </div>
          {loadingJobs ? (
            <div className="rounded-lg border border-gold-500/10 bg-base-800/45 p-4 text-xs text-warm-500">
              加载中
            </div>
          ) : jobs.length === 0 ? (
            <div className="rounded-lg border border-gold-500/10 bg-base-800/45 p-4 text-xs text-warm-500">
              暂无封面任务
            </div>
          ) : (
            jobs.map((item) => (
              <JobButton
                key={item.job_id}
                job={item}
                active={item.job_id === job?.job_id}
                onClick={() => selectJob(item.job_id)}
              />
            ))
          )}
        </aside>

        <div>
          <div className="rounded-lg border border-gold-500/10 bg-base-800/45 p-5">
            <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
              <div className="min-w-0">
                <div className="truncate text-sm tracking-widest text-warm-100">
                  {job ? job.current_step : "封面队列空闲"}
                </div>
                <div className="mt-1 text-xs text-warm-500">{progressText}</div>
              </div>
              <div className="min-w-[220px]">
                <div className="mb-2 flex items-center justify-between text-xs text-warm-400">
                  <span>{job?.default_category_label || job?.status || "idle"}</span>
                  <span className="text-gold-300">{job?.progress ?? 0}%</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-base-900">
                  <div
                    className="h-full rounded-full bg-gold-400 transition-all duration-500"
                    style={{ width: `${job?.progress ?? 0}%` }}
                  />
                </div>
              </div>
            </div>
            {error && (
              <div className="mt-4 rounded-lg border border-red-400/20 bg-red-500/10 px-4 py-3 text-sm text-red-100">
                {error}
              </div>
            )}
          </div>

          {job && job.items.length > 0 && (
            <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6">
              {job.items.map((item) => (
                <CoverPreview key={item.index} jobId={job.job_id} item={item} />
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
