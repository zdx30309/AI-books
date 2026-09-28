import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ClipboardCopy,
  Download,
  FileSpreadsheet,
  Loader2,
  RefreshCcw,
  Table2,
  UploadCloud,
} from "lucide-react";
import { api, ApiError } from "@/api/client";
import type { VideoScriptRow, VideoScriptSyncResponse } from "@/api/videoScriptTableTypes";

const STATUS_OPTIONS = [
  { value: "completed,manual_review", label: "已完成 + 待人工复盘" },
  { value: "completed", label: "仅已完成" },
  { value: "", label: "全部任务" },
];

function riskBadgeClass(risk: string) {
  if (risk === "高") return "border-rose-400/40 bg-rose-500/10 text-rose-200";
  if (risk === "中") return "border-amber-400/40 bg-amber-500/10 text-amber-200";
  if (risk === "待核对") return "border-sky-400/30 bg-sky-500/10 text-sky-200";
  if (risk === "不适用") return "border-white/15 bg-white/5 text-warm-400";
  return "border-emerald-400/30 bg-emerald-500/10 text-emerald-200";
}

function toTsv(rows: VideoScriptRow[], columns: string[]) {
  const header = columns.join("\t");
  const body = rows.map((row) =>
    columns
      .map((column) => {
        if (column === "文案相似度") return row.similarity_percent;
        if (column === "原文重合度") return `${(row.source_overlap_ratio * 100).toFixed(2)}%`;
        if (column === "序号") return String(row.index);
        if (column === "视频链接") return row.source_url;
        if (column === "视频标题") return row.source_title;
        if (column === "生成文稿名") return row.script_title;
        if (column === "平台") return row.platform_label;
        if (column === "字数") return String(row.word_count);
        if (column === "生成时间") return row.created_at;
        if (column === "最长雷同片段") return String(row.longest_common_chars);
        if (column === "风险等级") return row.risk_level;
        if (column === "建议动作") return row.action;
        if (column === "任务ID") return row.task_id;
        return "";
      })
      .join("\t"),
  );
  return [header, ...body].join("\n");
}

export function VideoScriptTable() {
  const [rows, setRows] = useState<VideoScriptRow[]>([]);
  const [columns, setColumns] = useState<string[]>([]);
  const [statusFilter, setStatusFilter] = useState(STATUS_OPTIONS[0].value);
  const [limit, setLimit] = useState(200);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [syncResult, setSyncResult] = useState<VideoScriptSyncResponse | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [dryRun, setDryRun] = useState(true);

  const statuses = useMemo(
    () => statusFilter.split(",").filter((item) => item.trim().length > 0),
    [statusFilter],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.videoScriptTableRows({ statuses, limit });
      setRows(data.rows);
      setColumns(data.columns);
      setMessage(`共 ${data.total} 条对照（阈值 中≥${data.thresholds.medium} / 高≥${data.thresholds.high}）`);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setLoading(false);
    }
  }, [statuses, limit]);

  useEffect(() => {
    void load();
  }, [load]);

  const copyToClipboard = async () => {
    const tsv = toTsv(rows, columns);
    try {
      await navigator.clipboard.writeText(tsv);
      setMessage(`已复制 ${rows.length} 行（含表头），去金山文档表格点第一个空单元格 Ctrl+V 即可`);
    } catch {
      setError("浏览器拒绝了剪贴板写入，请改用「导出 TSV」后手动导入");
    }
  };

  const exportFile = async (fmt: "csv" | "tsv" | "xlsx") => {
    try {
      await api.videoScriptTableExport(fmt, { statuses, limit });
      setMessage(`已导出 ${fmt.toUpperCase()} 文件`);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    }
  };

  const sync = async () => {
    setSyncing(true);
    setError(null);
    try {
      const result = await api.videoScriptTableSync({
        statuses,
        limit,
        dry_run: dryRun,
        update_existing: false,
        fmt: "csv",
      });
      setSyncResult(result);
      setMessage(result.message || `同步状态：${result.status}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setSyncing(false);
    }
  };

  const highRisk = rows.filter((row) => row.risk_level === "高").length;
  const pending = rows.filter((row) => row.risk_level === "待核对").length;

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 font-serif text-2xl text-warm-100">
            <Table2 className="h-6 w-6 text-gold-300" strokeWidth={1.6} />
            视频文案对照表
          </h1>
          <p className="mt-2 text-sm text-warm-400">
            每条视频文稿自动对上原视频标题与原文文案，算好相似度，再一键写进金山文档那张汇总表。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
            className="rounded-lg border border-white/10 bg-base-800 px-3 py-2 text-sm text-warm-100"
          >
            {STATUS_OPTIONS.map((option) => (
              <option key={option.label} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <input
            type="number"
            min={1}
            max={5000}
            value={limit}
            onChange={(event) => setLimit(Number(event.target.value) || 200)}
            className="w-24 rounded-lg border border-white/10 bg-base-800 px-3 py-2 text-sm text-warm-100"
          />
          <button
            type="button"
            onClick={() => void load()}
            className="flex items-center gap-2 rounded-lg border border-white/10 px-3 py-2 text-sm text-warm-100 hover:border-gold-400/40"
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCcw className="h-4 w-4" />}
            刷新
          </button>
        </div>
      </header>

      <section className="grid gap-3 md:grid-cols-3">
        <div className="rounded-xl border border-white/10 bg-base-800/60 p-4">
          <div className="text-xs tracking-widest text-warm-400">对照条数</div>
          <div className="mt-1 text-2xl text-warm-100">{rows.length}</div>
        </div>
        <div className="rounded-xl border border-white/10 bg-base-800/60 p-4">
          <div className="text-xs tracking-widest text-warm-400">高风险（需重写）</div>
          <div className="mt-1 text-2xl text-rose-200">{highRisk}</div>
        </div>
        <div className="rounded-xl border border-white/10 bg-base-800/60 p-4">
          <div className="text-xs tracking-widest text-warm-400">待人工核对（缺原文）</div>
          <div className="mt-1 text-2xl text-sky-200">{pending}</div>
        </div>
      </section>

      <section className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => void copyToClipboard()}
          className="flex items-center gap-2 rounded-lg border border-gold-400/40 bg-gold-500/10 px-4 py-2 text-sm text-gold-200"
        >
          <ClipboardCopy className="h-4 w-4" />
          复制全表（直接粘进金山文档）
        </button>
        <button
          type="button"
          onClick={() => void exportFile("xlsx")}
          className="flex items-center gap-2 rounded-lg border border-white/10 px-4 py-2 text-sm text-warm-100 hover:border-gold-400/40"
        >
          <FileSpreadsheet className="h-4 w-4" />
          导出 XLSX
        </button>
        <button
          type="button"
          onClick={() => void exportFile("csv")}
          className="flex items-center gap-2 rounded-lg border border-white/10 px-4 py-2 text-sm text-warm-100 hover:border-gold-400/40"
        >
          <Download className="h-4 w-4" />
          导出 CSV
        </button>
        <button
          type="button"
          onClick={() => void exportFile("tsv")}
          className="flex items-center gap-2 rounded-lg border border-white/10 px-4 py-2 text-sm text-warm-100 hover:border-gold-400/40"
        >
          <Download className="h-4 w-4" />
          导出 TSV
        </button>
        <label className="ml-auto flex items-center gap-2 text-xs text-warm-400">
          <input type="checkbox" checked={dryRun} onChange={(event) => setDryRun(event.target.checked)} />
          试运行（不真写）
        </label>
        <button
          type="button"
          onClick={() => void sync()}
          disabled={syncing}
          className="flex items-center gap-2 rounded-lg border border-emerald-400/40 bg-emerald-500/10 px-4 py-2 text-sm text-emerald-200 disabled:opacity-50"
        >
          {syncing ? <Loader2 className="h-4 w-4 animate-spin" /> : <UploadCloud className="h-4 w-4" />}
          同步到金山文档
        </button>
      </section>

      {message && (
        <div className="rounded-lg border border-emerald-400/30 bg-emerald-500/10 px-4 py-2 text-sm text-emerald-100">
          {message}
        </div>
      )}
      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-rose-400/30 bg-rose-500/10 px-4 py-2 text-sm text-rose-100">
          <AlertTriangle className="h-4 w-4" />
          {error}
        </div>
      )}
      {syncResult && (
        <div className="rounded-lg border border-white/10 bg-base-800/60 px-4 py-3 text-xs text-warm-300">
          <div>provider: {syncResult.provider} / status: {syncResult.status}</div>
          <div>
            写入 {syncResult.written_rows} 行 / 更新 {syncResult.updated_rows} 行 / 跳过 {syncResult.skipped_rows} 行
          </div>
          {syncResult.export_path && <div>导出文件：{syncResult.export_path}</div>}
        </div>
      )}

      <section className="overflow-x-auto rounded-xl border border-white/10 bg-base-800/40">
        <table className="min-w-full text-sm">
          <thead className="bg-base-800/80 text-xs tracking-widest text-warm-400">
            <tr>
              <th className="px-3 py-3 text-left">视频标题</th>
              <th className="px-3 py-3 text-left">生成文稿名</th>
              <th className="px-3 py-3 text-left">链接</th>
              <th className="px-3 py-3 text-right">相似度</th>
              <th className="px-3 py-3 text-right">最长雷同</th>
              <th className="px-3 py-3 text-left">风险</th>
              <th className="px-3 py-3 text-left">建议</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.task_id} className="border-t border-white/5 text-warm-200">
                <td className="max-w-[280px] px-3 py-2">{row.source_title || "—"}</td>
                <td className="max-w-[220px] px-3 py-2 text-warm-100">{row.script_title || "—"}</td>
                <td className="max-w-[240px] truncate px-3 py-2 text-xs text-warm-400">
                  <a href={row.source_url} target="_blank" rel="noreferrer" className="hover:text-gold-200">
                    {row.source_url}
                  </a>
                </td>
                <td className="px-3 py-2 text-right">{row.similarity_percent}</td>
                <td className="px-3 py-2 text-right">{row.longest_common_chars} 字</td>
                <td className="px-3 py-2">
                  <span className={`rounded-full border px-2 py-0.5 text-xs ${riskBadgeClass(row.risk_level)}`}>
                    {row.risk_level}
                  </span>
                </td>
                <td className="max-w-[320px] px-3 py-2 text-xs text-warm-400">{row.action}</td>
              </tr>
            ))}
            {!rows.length && !loading && (
              <tr>
                <td colSpan={7} className="px-3 py-10 text-center text-warm-400">
                  没有数据。先在主页生成一批书稿，再回到这里看对照。
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      <p className="text-xs leading-relaxed text-warm-400">
        相似度算法：字符 5-gram 重合度 + 最长公共连续片段 + SimHash 距离；连续 40 字以上雷同直接判「高」。
        目标表列名只要包含「视频链接 / 视频标题 / 生成文稿名 / 文案相似度 / 风险等级」等常见写法即可自动对上，
        列名不一致时以远端表头为准（可先跑 <code className="text-warm-200">sync --dry-run</code> 看映射结果）。
      </p>
    </div>
  );
}
