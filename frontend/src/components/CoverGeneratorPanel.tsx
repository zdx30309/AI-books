import { useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, Download, FileSpreadsheet, ImageIcon, Loader2, UploadCloud } from "lucide-react";
import { api, ApiError } from "@/api/client";
import type { CoverJobItem, CoverJobResponse } from "@/api/types";

function CoverPreview({ jobId, item }: { jobId: string; item: CoverJobItem }) {
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    if (item.status !== "completed") return undefined;
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
      <div className="aspect-[3/4] bg-base-900/60">
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
        {item.error && (
          <div className="line-clamp-2 text-[11px] leading-relaxed text-red-200">
            {item.error}
          </div>
        )}
      </div>
    </div>
  );
}

export function CoverGeneratorPanel() {
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [job, setJob] = useState<CoverJobResponse | null>(null);
  const [uploading, setUploading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const active = job?.status === "pending" || job?.status === "processing";
  const progressText = useMemo(() => {
    if (!job) return "等待上传";
    return `${job.completed}/${job.total} 张完成，${job.failed} 张失败`;
  }, [job]);

  useEffect(() => {
    if (!job || !active) return undefined;
    const timer = window.setInterval(() => {
      api
        .getCoverJob(job.job_id)
        .then(setJob)
        .catch((e) => {
          const msg = e instanceof ApiError ? e.detail : "刷新封面进度失败";
          setError(msg);
        });
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [active, job]);

  const upload = async (file: File | null | undefined) => {
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      const next = await api.createCoverJob(file);
      setJob(next);
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

  const refresh = async () => {
    if (!job) return;
    setError(null);
    try {
      setJob(await api.getCoverJob(job.job_id));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "刷新失败");
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
    <section className="mt-12 border-t border-gold-500/10 pt-10">
      <div className="mb-6 flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <h2 className="font-serif text-2xl tracking-wide text-warm-100">
            Excel 封面生成
          </h2>
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-warm-400">
            上传包含书名、作者等字段的 Excel，封面会独立生成，不再和书稿内容一起跑。
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <input
            ref={fileRef}
            type="file"
            accept=".xlsx,.xlsm,.csv"
            className="hidden"
            onChange={(event) => upload(event.target.files?.[0])}
          />
          {job && (
            <button
              type="button"
              onClick={refresh}
              className="btn-ghost"
            >
              <Loader2 className={`h-4 w-4 ${active ? "animate-spin" : ""}`} strokeWidth={1.7} />
              <span className="tracking-widest">刷新进度</span>
            </button>
          )}
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

      <div className="rounded-lg border border-gold-500/10 bg-base-800/45 p-5">
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-gold-500/15 bg-gold-500/10">
              <FileSpreadsheet className="h-5 w-5 text-gold-300" strokeWidth={1.6} />
            </div>
            <div>
              <div className="text-sm tracking-widest text-warm-100">
                {job?.source_filename || "尚未上传封面 Excel"}
              </div>
              <div className="mt-1 text-xs text-warm-500">
                {job ? job.current_step : "Excel 列名支持：书名 / title，作者 / author"}
              </div>
            </div>
          </div>
          <div className="min-w-[220px]">
            <div className="mb-2 flex items-center justify-between text-xs text-warm-400">
              <span>{progressText}</span>
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
        <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6">
          {job.items.map((item) => (
            <CoverPreview key={item.index} jobId={job.job_id} item={item} />
          ))}
        </div>
      )}
    </section>
  );
}
