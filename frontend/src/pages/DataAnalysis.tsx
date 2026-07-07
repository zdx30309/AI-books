import { useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  BarChart3,
  Download,
  FileSpreadsheet,
  Loader2,
  RefreshCcw,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { api, ApiError } from "@/api/client";
import type { DataAnalysisResponse, DataAnalysisRow } from "@/api/types";

const columns = [
  { key: "date_key", label: "时间", group: "date" },
  { key: "weekday", label: "星期", group: "date" },
  { key: "ai_duration", label: "AI类", group: "ai" },
  { key: "ai_mom", label: "环比", group: "ai" },
  { key: "public_duration", label: "公版类", group: "public" },
  { key: "public_mom", label: "环比", group: "public" },
  { key: "total_duration", label: "总时长", group: "total" },
  { key: "total_mom", label: "环比", group: "total" },
  { key: "huian_ai", label: "汇安AI", group: "company" },
  { key: "huian_public", label: "汇安公版", group: "company" },
  { key: "wutu_ai", label: "悟图AI", group: "company" },
  { key: "wutu_public", label: "悟图公版", group: "company" },
  { key: "suite_duration", label: "精选集/套装书", group: "company" },
] as const;

function formatInteger(value: number | null) {
  if (value === null || Number.isNaN(value)) return "";
  return String(Math.round(value));
}

function formatPercent(value: number | null) {
  if (value === null || Number.isNaN(value)) return "";
  return `${(value * 100).toFixed(1)}%`;
}

function cellValue(row: DataAnalysisRow, key: keyof DataAnalysisRow) {
  if (key === "date_key" || key === "weekday") return row[key];
  if (key === "ai_mom" || key === "public_mom" || key === "total_mom") {
    return formatPercent(row[key]);
  }
  return formatInteger(row[key]);
}

function groupClass(group: string, isHeader = false) {
  const base = isHeader ? "font-semibold text-black" : "text-black";
  if (isHeader && (group === "date" || group === "total")) return `${base} bg-[#fff200]`;
  if (isHeader && group === "company") return `${base} bg-[#9db45f]`;
  if (group === "ai") return `${base} bg-[#f3dcdf]`;
  if (group === "public") return `${base} bg-[#dceaf5]`;
  return `${base} bg-white`;
}

function averageNumber(rows: DataAnalysisRow[], key: keyof DataAnalysisRow) {
  const values = rows
    .map((row) => row[key])
    .filter((value): value is number => typeof value === "number" && !Number.isNaN(value));
  if (values.length === 0) return null;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

export function DataAnalysis() {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [result, setResult] = useState<DataAnalysisResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const filledRow = useMemo(
    () => result?.rows.find((row) => row.total_duration !== null),
    [result],
  );
  const totalSize = useMemo(
    () => files.reduce((sum, item) => sum + item.size, 0),
    [files],
  );
  const averageRow = useMemo<DataAnalysisRow | null>(() => {
    if (!result) return null;
    return {
      date_key: "日均",
      weekday: "",
      ai_duration: averageNumber(result.rows, "ai_duration"),
      ai_mom: averageNumber(result.rows, "ai_mom"),
      public_duration: averageNumber(result.rows, "public_duration"),
      public_mom: averageNumber(result.rows, "public_mom"),
      total_duration: averageNumber(result.rows, "total_duration"),
      total_mom: averageNumber(result.rows, "total_mom"),
      huian_ai: averageNumber(result.rows, "huian_ai"),
      huian_public: averageNumber(result.rows, "huian_public"),
      wutu_ai: averageNumber(result.rows, "wutu_ai"),
      wutu_public: averageNumber(result.rows, "wutu_public"),
      suite_duration: averageNumber(result.rows, "suite_duration"),
    };
  }, [result]);

  const resetSelection = () => {
    setFiles([]);
    setResult(null);
    setError(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const removeFile = (target: File) => {
    setFiles((current) =>
      current.filter(
        (item) =>
          !(
            item.name === target.name &&
            item.lastModified === target.lastModified &&
            item.size === target.size
          ),
      ),
    );
    setResult(null);
    setError(null);
  };

  const analyze = async () => {
    if (files.length === 0) {
      setError("请先上传一个或多个书籍信息 Excel");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setResult(await api.analyzeDataFiles(files));
    } catch (e) {
      setError(
        e instanceof ApiError
          ? e.detail
          : e instanceof Error
            ? e.message
            : "分析失败",
      );
    } finally {
      setLoading(false);
    }
  };

  const download = async () => {
    if (files.length === 0) {
      setError("请先上传一个或多个书籍信息 Excel");
      return;
    }
    setDownloading(true);
    setError(null);
    try {
      await api.downloadDataAnalysisExcelBatch(files);
    } catch (e) {
      setError(
        e instanceof ApiError
          ? e.detail
          : e instanceof Error
            ? e.message
            : "下载失败",
      );
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="space-y-8">
      <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="badge-gold mb-4">数据分析</div>
          <h1 className="font-serif text-3xl tracking-wide text-warm-100">
            书籍数据整理与日报匹配
          </h1>
          <p className="mt-3 max-w-3xl text-sm leading-7 text-warm-400">
            上传书籍信息 Excel，系统会按文件日期聚合阅读时长，并整理成与数据表一致的日报格式。
          </p>
        </div>
        <button
          type="button"
          onClick={download}
          disabled={files.length === 0 || downloading}
          className="btn-gold"
        >
          {downloading ? (
            <Loader2 className="h-4 w-4 animate-spin" strokeWidth={2} />
          ) : (
            <Download className="h-4 w-4" strokeWidth={2} />
          )}
          <span className="tracking-widest">下载结果表</span>
        </button>
      </div>

      <section className="grid gap-5 lg:grid-cols-[minmax(0,0.9fr)_minmax(360px,1.1fr)]">
        <div className="card-warm p-5">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <h2 className="font-serif text-xl tracking-wide text-warm-100">
                上传书籍信息
              </h2>
              <p className="mt-1 text-xs leading-5 text-warm-500">
                支持一次选择多个 .xlsx / .xlsm；日期优先从文件名中的 8 位日期识别。
              </p>
            </div>
            <FileSpreadsheet className="h-5 w-5 text-gold-300" strokeWidth={1.7} />
          </div>

          <label className="flex min-h-[190px] cursor-pointer flex-col items-center justify-center rounded-2xl border border-dashed border-gold-400/30 bg-base-800/60 px-5 py-8 text-center transition-colors hover:border-gold-300/60 hover:bg-gold-500/5">
            <Upload className="mb-4 h-8 w-8 text-gold-300" strokeWidth={1.6} />
            <span className="max-w-full break-all text-sm font-medium tracking-wide text-warm-100">
              {files.length > 0 ? `已选择 ${files.length} 个 Excel 文件` : "选择书籍信息 Excel"}
            </span>
            <span className="mt-2 text-xs text-warm-500">
              字段包含今日在读人数、人均时长、分类、出品/非出品时效果最佳
            </span>
            <input
              ref={fileInputRef}
              type="file"
              accept=".xlsx,.xlsm"
              multiple
              className="hidden"
              onChange={(event) => {
                setFiles(Array.from(event.target.files ?? []));
                setResult(null);
                setError(null);
              }}
            />
          </label>

          {files.length > 0 && (
            <div className="mt-4 rounded-2xl border border-gold-500/10 bg-base-800/50 p-3">
              <div className="mb-2 flex items-center justify-between text-xs tracking-widest text-warm-500">
                <span>待分析文件</span>
                <div className="flex items-center gap-2">
                  <span>{(totalSize / 1024 / 1024).toFixed(2)} MB</span>
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    className="inline-flex items-center gap-1 rounded-full border border-gold-500/20 px-2 py-1 text-warm-300 transition-colors hover:border-gold-400/50 hover:text-warm-100"
                  >
                    <RefreshCcw className="h-3 w-3" strokeWidth={1.7} />
                    更换
                  </button>
                  <button
                    type="button"
                    onClick={resetSelection}
                    className="inline-flex items-center gap-1 rounded-full border border-red-400/20 px-2 py-1 text-red-100 transition-colors hover:border-red-300/50"
                  >
                    <Trash2 className="h-3 w-3" strokeWidth={1.7} />
                    清空
                  </button>
                </div>
              </div>
              <div className="max-h-36 space-y-2 overflow-y-auto pr-1">
                {files.map((item) => (
                  <div
                    key={`${item.name}-${item.lastModified}-${item.size}`}
                    className="flex items-center gap-2 rounded-xl bg-base-700/60 px-3 py-2 text-xs text-warm-300"
                  >
                    <FileSpreadsheet className="h-3.5 w-3.5 shrink-0 text-gold-300" strokeWidth={1.7} />
                    <span className="min-w-0 flex-1 truncate">{item.name}</span>
                    <span className="shrink-0 text-warm-500">{(item.size / 1024).toFixed(0)} KB</span>
                    <button
                      type="button"
                      onClick={() => removeFile(item)}
                      className="shrink-0 rounded-full p-1 text-warm-500 transition-colors hover:bg-red-500/10 hover:text-red-100"
                      aria-label={`删除 ${item.name}`}
                    >
                      <X className="h-3.5 w-3.5" strokeWidth={1.8} />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          <button
            type="button"
            onClick={analyze}
            disabled={files.length === 0 || loading}
            className="btn-gold mt-5 w-full"
          >
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" strokeWidth={2} />
            ) : (
              <BarChart3 className="h-4 w-4" strokeWidth={2} />
            )}
            <span className="tracking-widest">分析并预览</span>
          </button>

          {error && (
            <div className="mt-4 flex gap-2 rounded-xl border border-red-400/25 bg-red-500/10 px-4 py-3 text-sm leading-6 text-red-100">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={1.7} />
              <span>{error}</span>
            </div>
          )}
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <StatTile label="数据日期" value={result?.summary.report_date ?? "--"} />
          <StatTile label="文件数量" value={result?.summary.file_count ?? (files.length || "--")} />
          <StatTile label="书籍总数" value={result?.summary.book_count ?? "--"} />
          <StatTile label="有效时长书籍" value={result?.summary.matched_book_count ?? "--"} />
          <StatTile label="总时长" value={filledRow ? formatInteger(filledRow.total_duration) : "--"} />
        </div>
      </section>

      {result?.summary.notes.length ? (
        <section className="rounded-2xl border border-gold-500/10 bg-base-800/50 px-5 py-4 text-sm leading-7 text-warm-400">
          {result.summary.notes.map((note) => (
            <div key={note}>{note}</div>
          ))}
        </section>
      ) : null}

      <section className="card-warm overflow-hidden">
        <div className="flex items-center justify-between gap-4 border-b border-gold-500/10 px-5 py-4">
          <div>
            <h2 className="font-serif text-xl tracking-wide text-warm-100">
              结果预览
            </h2>
            <p className="mt-1 text-xs text-warm-500">
              下载的 Excel 会按同样字段和分区颜色生成。
            </p>
          </div>
          <FileSpreadsheet className="h-5 w-5 text-gold-300" strokeWidth={1.7} />
        </div>

        {!result ? (
          <div className="px-5 py-16 text-center text-sm text-warm-500">
            上传 Excel 后点击「分析并预览」，这里会显示整月日报表。
          </div>
        ) : (
          <div className="overflow-x-auto bg-white">
            <table className="w-full min-w-[1250px] border-collapse text-center font-serif text-[18px] text-black">
              <thead>
                <tr>
                  {columns.map((column) => (
                    <th
                      key={column.key}
                      className={`border border-black px-3 py-1 ${groupClass(column.group, true)}`}
                    >
                      {column.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.rows.map((row) => (
                  <tr key={row.date_key}>
                    {columns.map((column) => {
                      const value = cellValue(row, column.key);
                      const isMom = column.key === "ai_mom" || column.key === "public_mom" || column.key === "total_mom";
                      const numberValue = row[column.key as keyof DataAnalysisRow];
                      const tone =
                        isMom && typeof numberValue === "number"
                          ? numberValue >= 0
                            ? "text-red-700"
                            : "text-emerald-700"
                          : "";
                      return (
                        <td
                          key={column.key}
                          className={`h-8 border border-black px-3 py-0.5 ${groupClass(column.group)} ${tone}`}
                        >
                          {value}
                        </td>
                      );
                    })}
                  </tr>
                ))}
                {averageRow && (
                  <tr>
                    {columns.map((column) => {
                      const value = cellValue(averageRow, column.key);
                      const isMom = column.key === "ai_mom" || column.key === "public_mom" || column.key === "total_mom";
                      const numberValue = averageRow[column.key as keyof DataAnalysisRow];
                      const tone =
                        isMom && typeof numberValue === "number"
                          ? numberValue >= 0
                            ? "text-red-700"
                            : "text-emerald-700"
                          : "";
                      const avgBg = column.group === "company" ? "bg-[#9db45f]" : "bg-[#fff200]";
                      return (
                        <td
                          key={column.key}
                          className={`h-8 border border-black px-3 py-0.5 font-semibold text-black ${avgBg} ${tone}`}
                        >
                          {value}
                        </td>
                      );
                    })}
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function StatTile({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="card-warm p-5">
      <div className="text-[11px] tracking-widest text-warm-500">{label}</div>
      <div className="mt-2 break-all font-serif text-2xl tracking-wide text-warm-100">
        {value}
      </div>
    </div>
  );
}
