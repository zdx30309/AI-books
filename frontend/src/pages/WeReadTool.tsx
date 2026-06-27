import { useMemo, useState } from "react";
import {
  AlertCircle,
  BadgeDollarSign,
  BookOpenCheck,
  Download,
  FileSpreadsheet,
  Loader2,
  RefreshCcw,
  Search,
} from "lucide-react";
import { api, ApiError } from "@/api/client";
import type { WeReadBookRow } from "@/api/types";

const SAMPLE_CATALOG = "西游记（人民文学版）\n活着\nhttps://weread.qq.com/web/reader/aea32010813ab9e28g016851";
const SAMPLE_PROFIT = "西游记（人民文学版）,38.5\n活着,21\n三体,56";

function formatPercent(value: number | null) {
  return value === null || Number.isNaN(value) ? "--" : `${value.toFixed(1)}%`;
}

function formatMoney(value: number | null) {
  return value === null || Number.isNaN(value) ? "--" : `¥${value.toFixed(2)}`;
}

function displayText(value: string | null | undefined) {
  return value && value.trim() ? value : "--";
}

function statusLabel(status: string) {
  return (
    {
      matched: "已匹配",
      partial: "部分匹配",
      not_found: "未找到",
      failed: "抓取失败",
    }[status] ?? status
  );
}

function averageRecommendation(rows: WeReadBookRow[]) {
  const values = rows
    .map((row) => row.recommendation)
    .filter((value): value is number => typeof value === "number");
  if (values.length === 0) return null;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

export function WeReadTool() {
  const [catalogText, setCatalogText] = useState("");
  const [profitText, setProfitText] = useState("");
  const [rows, setRows] = useState<WeReadBookRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const stats = useMemo(() => {
    const matched = rows.filter((row) => row.status === "matched" || row.status === "partial").length;
    const profitTotal = rows.reduce((sum, row) => sum + (row.profit ?? 0), 0);
    const projectMatched = rows.filter((row) => row.project_match_status).length;
    const companyMatched = new Set(rows.map((row) => row.company_name).filter(Boolean)).size;
    return {
      total: rows.length,
      matched,
      average: averageRecommendation(rows),
      profitTotal,
      profitMatched: rows.filter((row) => row.profit !== null).length,
      projectMatched,
      companyMatched,
    };
  }, [rows]);

  const analyze = async () => {
    if (!catalogText.trim()) {
      setError("请先粘贴图书目录");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const result = await api.analyzeWeReadBooks({
        catalog_text: catalogText,
        profit_text: profitText || null,
      });
      setRows(result.items);
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? e.detail
          : e instanceof Error
            ? e.message
            : "抓取失败";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const downloadExcel = async () => {
    if (rows.length === 0) {
      setError("请先抓取推荐值，再下载 Excel");
      return;
    }
    setDownloading(true);
    setError(null);
    try {
      await api.downloadWeReadExcel(rows);
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? e.detail
          : e instanceof Error
            ? e.message
            : "下载失败";
      setError(msg);
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="space-y-8">
      <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="badge-gold mb-4">微信读书数据工具</div>
          <h1 className="font-serif text-3xl tracking-wide text-warm-100">
            推荐值与项目字段匹配
          </h1>
          <p className="mt-3 max-w-2xl text-sm leading-7 text-warm-400">
            粘贴图书目录或微信读书链接后抓取推荐值、阅读人数、评论数量；系统会自动匹配项目进度表里的公司、编辑、书名和已有字段，并生成可下载的 Excel。
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <button
            type="button"
            onClick={() => {
              setCatalogText(SAMPLE_CATALOG);
              setProfitText(SAMPLE_PROFIT);
            }}
            className="btn-ghost"
          >
            <RefreshCcw className="h-4 w-4" strokeWidth={1.7} />
            <span className="tracking-widest">填入示例</span>
          </button>
          <button
            type="button"
            onClick={downloadExcel}
            disabled={rows.length === 0 || downloading}
            className="btn-gold"
          >
            {downloading ? (
              <Loader2 className="h-4 w-4 animate-spin" strokeWidth={2} />
            ) : (
              <Download className="h-4 w-4" strokeWidth={2} />
            )}
            <span className="tracking-widest">下载 Excel</span>
          </button>
        </div>
      </div>

      <section className="grid gap-5 lg:grid-cols-[minmax(0,1.1fr)_minmax(320px,0.9fr)]">
        <div className="card-warm p-5">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <h2 className="font-serif text-xl tracking-wide text-warm-100">
                图书或链接
              </h2>
              <p className="mt-1 text-xs leading-5 text-warm-500">
                一行一本；支持「书名」「书名,作者」或微信读书链接。链接会优先匹配项目进度表。
              </p>
            </div>
            <BookOpenCheck className="h-5 w-5 text-gold-300" strokeWidth={1.7} />
          </div>
          <textarea
            value={catalogText}
            onChange={(event) => setCatalogText(event.target.value)}
            placeholder="例如：&#10;西游记（人民文学版）&#10;活着,余华&#10;https://weread.qq.com/web/reader/..."
            className="min-h-[310px] w-full resize-y rounded-xl border border-gold-500/15 bg-base-800/70 px-4 py-3 text-sm leading-7 text-warm-100 outline-none transition-colors placeholder:text-warm-600 focus:border-gold-400/45"
          />
        </div>

        <div className="card-warm p-5">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <h2 className="font-serif text-xl tracking-wide text-warm-100">
                利润表
              </h2>
              <p className="mt-1 text-xs leading-5 text-warm-500">
                可稍后补充；格式为「书名,利润」或「书名 利润」。
              </p>
            </div>
            <BadgeDollarSign className="h-5 w-5 text-gold-300" strokeWidth={1.7} />
          </div>
          <textarea
            value={profitText}
            onChange={(event) => setProfitText(event.target.value)}
            placeholder="例如：&#10;西游记（人民文学版）,38.5&#10;活着 21"
            className="min-h-[220px] w-full resize-y rounded-xl border border-gold-500/15 bg-base-800/70 px-4 py-3 text-sm leading-7 text-warm-100 outline-none transition-colors placeholder:text-warm-600 focus:border-gold-400/45"
          />
          <div className="mt-4 flex flex-col gap-3 sm:flex-row">
            <button
              type="button"
              onClick={analyze}
              disabled={loading}
              className="btn-gold flex-1"
            >
              {loading ? (
                <Loader2 className="h-4 w-4 animate-spin" strokeWidth={2} />
              ) : (
                <Search className="h-4 w-4" strokeWidth={2} />
              )}
              <span className="tracking-widest">
                {rows.length > 0 ? "重新抓取并更新" : "抓取推荐值"}
              </span>
            </button>
            <button
              type="button"
              onClick={downloadExcel}
              disabled={rows.length === 0 || downloading}
              className="inline-flex flex-1 items-center justify-center gap-2 rounded-full border border-gold-400/45 bg-gold-500/10 px-5 py-3 text-sm font-medium tracking-widest text-warm-100 transition-colors hover:bg-gold-500/15 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {downloading ? (
                <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.7} />
              ) : (
                <FileSpreadsheet className="h-4 w-4" strokeWidth={1.7} />
              )}
              生成 Excel
            </button>
          </div>
          {error && (
            <div className="mt-4 flex gap-2 rounded-xl border border-red-400/25 bg-red-500/10 px-4 py-3 text-sm leading-6 text-red-100">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={1.7} />
              <span>{error}</span>
            </div>
          )}
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-3 xl:grid-cols-6">
        <StatTile label="图书总数" value={stats.total || "--"} />
        <StatTile label="推荐值匹配" value={stats.matched || "--"} />
        <StatTile label="平均推荐值" value={formatPercent(stats.average)} />
        <StatTile label="项目匹配" value={stats.projectMatched || "--"} />
        <StatTile label="公司命中" value={stats.companyMatched || "--"} />
        <StatTile label="利润合计" value={stats.profitMatched > 0 ? formatMoney(stats.profitTotal) : "--"} />
      </section>

      <section className="card-warm overflow-hidden">
        <div className="flex items-center justify-between gap-4 border-b border-gold-500/10 px-5 py-4">
          <div>
            <h2 className="font-serif text-xl tracking-wide text-warm-100">
              数据预览
            </h2>
            <p className="mt-1 text-xs text-warm-500">
              Excel 会按当前预览内容导出。
            </p>
          </div>
          <FileSpreadsheet className="h-5 w-5 text-gold-300" strokeWidth={1.7} />
        </div>

        {rows.length === 0 ? (
          <div className="px-5 py-16 text-center">
            <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full border border-gold-500/15 bg-base-800/70 text-gold-300">
              <Search className="h-5 w-5" strokeWidth={1.7} />
            </div>
            <div className="font-serif text-lg tracking-wide text-warm-100">
              等待抓取
            </div>
            <p className="mt-2 text-sm text-warm-500">
              输入目录后点击「抓取推荐值」，这里会显示匹配结果。
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1320px] border-collapse text-left text-sm">
              <thead>
                <tr className="border-b border-gold-500/10 text-[11px] tracking-widest text-warm-500">
                  <th className="px-5 py-3 font-medium">原始书名</th>
                  <th className="px-5 py-3 font-medium">公司名字</th>
                  <th className="px-5 py-3 font-medium">匹配书名</th>
                  <th className="px-5 py-3 font-medium">作者</th>
                  <th className="px-5 py-3 font-medium">推荐值</th>
                  <th className="px-5 py-3 font-medium">阅读人数</th>
                  <th className="px-5 py-3 font-medium">评论数量</th>
                  <th className="px-5 py-3 font-medium">项目书名</th>
                  <th className="px-5 py-3 font-medium">编辑</th>
                  <th className="px-5 py-3 font-medium">利润</th>
                  <th className="px-5 py-3 font-medium">状态</th>
                  <th className="px-5 py-3 font-medium">项目来源</th>
                  <th className="px-5 py-3 font-medium">备注</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, index) => (
                  <tr
                    key={`${row.query_title}-${index}`}
                    className="border-b border-gold-500/8 text-warm-200"
                  >
                    <td className="px-5 py-4 align-top">{row.query_title}</td>
                    <td className="px-5 py-4 align-top text-warm-100">
                      {displayText(row.company_name)}
                    </td>
                    <td className="px-5 py-4 align-top">
                      {row.source_url ? (
                        <a
                          href={row.source_url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-gold-200 underline decoration-gold-400/30 underline-offset-4 hover:text-gold-100"
                        >
                          {displayText(row.matched_title)}
                        </a>
                      ) : (
                        displayText(row.matched_title)
                      )}
                    </td>
                    <td className="px-5 py-4 align-top text-warm-400">
                      {displayText(row.matched_author || row.query_author)}
                    </td>
                    <td className="px-5 py-4 align-top">
                      <span className="rounded-full bg-gold-500/10 px-3 py-1 text-gold-100">
                        {formatPercent(row.recommendation)}
                      </span>
                    </td>
                    <td className="px-5 py-4 align-top text-warm-400">
                      {displayText(row.reading_count)}
                    </td>
                    <td className="px-5 py-4 align-top text-warm-400">
                      {displayText(row.comment_count)}
                    </td>
                    <td className="max-w-[220px] px-5 py-4 align-top text-warm-200">
                      {displayText(row.project_title)}
                    </td>
                    <td className="px-5 py-4 align-top text-warm-400">
                      {displayText(row.project_editor)}
                    </td>
                    <td className="px-5 py-4 align-top text-warm-100">
                      {formatMoney(row.profit)}
                    </td>
                    <td className="px-5 py-4 align-top">
                      <span
                        className={`rounded-full border px-3 py-1 text-xs ${
                          row.status === "matched"
                            ? "border-emerald-300/30 bg-emerald-400/10 text-emerald-100"
                            : "border-gold-500/20 bg-base-800/70 text-warm-400"
                        }`}
                      >
                        {statusLabel(row.status)}
                      </span>
                    </td>
                    <td className="px-5 py-4 align-top text-xs leading-5 text-warm-500">
                      {row.project_match_status
                        ? `${row.project_match_status}${row.project_source_sheet ? ` · ${row.project_source_sheet}` : ""}`
                        : "--"}
                    </td>
                    <td className="max-w-[260px] px-5 py-4 align-top text-xs leading-5 text-warm-500">
                      {row.error || "--"}
                    </td>
                  </tr>
                ))}
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
      <div className="mt-2 font-serif text-2xl tracking-wide text-warm-100">
        {value}
      </div>
    </div>
  );
}
