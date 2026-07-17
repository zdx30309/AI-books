import { useMemo, useState } from "react";
import {
  AlertTriangle,
  BookMarked,
  CheckCircle2,
  Download,
  FileSearch,
  Loader2,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  UploadCloud,
} from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "@/api/client";
import type {
  AigcReviewContentType,
  AigcReviewComparison,
  AigcReviewExperimentType,
  AigcReviewOptimizeResult,
  AigcReviewReport,
  AigcReviewRewriteProvider,
} from "@/api/types";

type RewriteStrategy = "syntax_rebuild" | "evidence_first" | "rhythm_recompose";

const strategyText: Record<RewriteStrategy, string> = {
  syntax_rebuild: "句法重建",
  evidence_first: "证据先行",
  rhythm_recompose: "节奏重组",
};

const labelText = {
  human: "人工特征",
  suspected: "疑似 AI",
  ai: "AI 特征",
};

const labelClass = {
  human: "border-emerald-400/25 bg-emerald-500/10 text-emerald-100",
  suspected: "border-amber-400/25 bg-amber-500/10 text-amber-100",
  ai: "border-red-400/25 bg-red-500/10 text-red-100",
};

function downloadText(text: string, filename: string) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function normalizeManuscriptText(text: string) {
  return text.replace(/\r\n?/g, "\n").trim();
}

export function AigcReview() {
  const [contentType, setContentType] = useState<AigcReviewContentType>("article");
  const [originalText, setOriginalText] = useState("");
  const [reportFile, setReportFile] = useState<File | null>(null);
  const [report, setReport] = useState<AigcReviewReport | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [result, setResult] = useState<AigcReviewOptimizeResult | null>(null);
  const [recheckFile, setRecheckFile] = useState<File | null>(null);
  const [recheck, setRecheck] = useState<AigcReviewReport | null>(null);
  const [comparison, setComparison] = useState<AigcReviewComparison | null>(null);
  const [iteration, setIteration] = useState(1);
  const [rewriteProvider, setRewriteProvider] = useState<AigcReviewRewriteProvider>("deepseek");
  const [rewriteReasoning, setRewriteReasoning] = useState<"low" | "medium" | "high">("low");
  const [experimentType, setExperimentType] = useState<AigcReviewExperimentType>("layout_only");
  const [budgetCny, setBudgetCny] = useState(0.3);
  const [parsing, setParsing] = useState(false);
  const [optimizing, setOptimizing] = useState(false);
  const [rechecking, setRechecking] = useState(false);
  const [error, setError] = useState("");

  const selectedShare = useMemo(
    () =>
      report?.segments
        .filter((item) => selectedIds.has(item.segment_id))
        .reduce((sum, item) => sum + item.share_percent, 0) ?? 0,
    [report, selectedIds],
  );
  const finalPassed = Boolean(result?.status === "candidate_ready" && comparison?.strict_pass);
  const probeMode = experimentType === "detector_probe";
  const resultIsProbe = result?.status === "probe_ready";
  const rewriteProviderOptions = report?.model_strategy.rewrite_providers ?? [];
  const selectedRewriteProvider = rewriteProviderOptions.find((item) => item.id === rewriteProvider);
  const rewriteProviderReady = experimentType === "layout_only" || Boolean(selectedRewriteProvider?.configured);
  const maxCalibrationSegments = report?.model_strategy.recommended_max_calibration_segments ?? 12;
  const parseReport = async () => {
    if (!reportFile || !originalText.trim()) {
      setError("请同时粘贴送检原稿并上传朱雀 PDF。报告本身不能安全还原可编辑全文。");
      return;
    }
    setError("");
    setParsing(true);
    setReport(null);
    setResult(null);
    setRecheck(null);
    setComparison(null);
    try {
      const parsed = await api.parseAigcReviewReport(
        reportFile,
        normalizeManuscriptText(originalText),
        contentType,
      );
      setReport(parsed);
      setSelectedIds(new Set(parsed.recommended_segment_ids));
      setRewriteProvider(parsed.model_strategy.default_rewrite_provider);
      setRewriteReasoning(parsed.model_strategy.recommended_rewrite_reasoning);
      setExperimentType("layout_only");
      setBudgetCny(parsed.model_strategy.default_run_budget_cny);
      setIteration(1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "朱雀报告解析失败");
    } finally {
      setParsing(false);
    }
  };

  const optimize = async (preferredStrategy: RewriteStrategy | null = null, advance = false) => {
    if (!report || selectedIds.size === 0) {
      setError("至少选择一个风险片段。");
      return;
    }
    if (!probeMode && selectedIds.size > maxCalibrationSegments) {
      setError(`单轮最多处理 ${maxCalibrationSegments} 个相邻风险片段。`);
      return;
    }
    if (
      experimentType === "combined"
      && !window.confirm("组合实验会调用 DeepSeek 并同时重组段落边界。确认在前两次受控实验后继续吗？")
    ) return;
    if (
      experimentType === "detector_probe"
      && !window.confirm("本轮将把所有选中的非绿色片段按 600–900 字切分，用 5 种语气轮换并以温度 0.8 完整执行高频词清理、节奏打乱、首段重写和有限阅读反应。为避免质量模板抹平变化，本轮不调用 Gemini、不做质量回滚，输出只能用于朱雀复检。确认继续吗？")
    ) return;
    setError("");
    setOptimizing(true);
    setResult(null);
    setRecheck(null);
    setComparison(null);
    const targetIteration = advance ? iteration + 1 : iteration;
    try {
      const response = await api.optimizeAigcReview({
        original_text: normalizeManuscriptText(originalText),
        original_sha256: report.original_sha256,
        content_type: contentType,
        segments: report.segments,
        selected_segment_ids: [...selectedIds],
        iteration: targetIteration,
        preferred_strategy: preferredStrategy,
        rewrite_provider: rewriteProvider,
        rewrite_reasoning_effort: rewriteReasoning,
        experiment_type: experimentType,
        budget_cny: budgetCny,
        baseline_report: report,
      });
      setResult(response);
      setIteration(targetIteration);
    } catch (err) {
      setError(err instanceof Error ? err.message : "定向审稿失败");
    } finally {
      setOptimizing(false);
    }
  };

  const parseRecheck = async () => {
    if (!recheckFile || !result?.candidate_text) return;
    setError("");
    setRechecking(true);
    try {
      const parsed = await api.parseAigcReviewReport(recheckFile, result.candidate_text, contentType);
      setRecheck(parsed);
      if (report) {
        const compared = await api.compareAigcReview(report, parsed, result);
        setComparison(compared);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "复检报告解析失败");
    } finally {
      setRechecking(false);
    }
  };

  const toggleSegment = (segmentId: number) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(segmentId)) next.delete(segmentId);
      else next.add(segmentId);
      return next;
    });
  };

  return (
    <div className="space-y-7">
      <section className="flex flex-col gap-5 border-b border-gold-500/10 pb-6 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-gold-500/20 px-4 py-2 text-xs text-gold-200">
            <ShieldCheck className="h-4 w-4" />
            真实朱雀报告驱动 · 黑盒实验与终稿分离 · 小说暂不处理
          </div>
          <h1 className="font-serif text-4xl text-warm-50">AI 审稿降率</h1>
          <p className="mt-3 max-w-4xl text-sm leading-7 text-warm-300">
            上传朱雀打印 PDF，并粘贴送检时的同一份原稿。实验稿先验证人工特征能否达到 25%，命中后再单独修复质量；未经质量终审的实验稿永远不是终稿。
          </p>
        </div>
        <div className="flex flex-col items-stretch gap-3">
          <div className="rounded-lg border border-emerald-400/20 bg-emerald-500/8 px-4 py-3 text-xs leading-6 text-emerald-100">
            本地 OCR：0 元<br />DeepSeek 600–900 字分段实验<br />实验稿与质量终稿严格分开
          </div>
          <Link to="/aigc-review/bookshelf" className="btn-ghost inline-flex items-center justify-center gap-2">
            <BookMarked className="h-4 w-4" />进入 AI 审稿书架
          </Link>
        </div>
      </section>

      {error && (
        <div className="rounded-lg border border-red-400/30 bg-red-500/10 p-4 text-sm leading-6 text-red-100">
          <div className="mb-1 flex items-center gap-2 font-medium"><AlertTriangle className="h-4 w-4" />处理失败</div>
          {error}
        </div>
      )}

      <section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="card-warm p-6">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-sm text-gold-200">第一步</div>
              <h2 className="mt-1 font-serif text-2xl text-warm-50">原稿与首次朱雀报告</h2>
            </div>
            <select
              value={contentType}
              onChange={(event) => setContentType(event.target.value as AigcReviewContentType)}
              className="rounded-lg border border-gold-500/15 bg-base-900 px-3 py-2 text-sm text-warm-100"
            >
              <option value="article">普通文章</option>
              <option value="classical">古文 / 佛经译写</option>
              <option value="public_domain">公版融合</option>
            </select>
          </div>
          <textarea
            value={originalText}
            onChange={(event) => {
              setOriginalText(event.target.value);
              setReport(null);
              setResult(null);
            }}
            placeholder="粘贴送进朱雀检测的完整可编辑原稿……"
            className="min-h-[360px] w-full resize-y rounded-lg border border-gold-500/15 bg-base-950/60 px-4 py-3 text-sm leading-7 text-warm-100 outline-none focus:border-gold-300/50"
          />
          {contentType === "classical" && (
            <div className="mt-3 rounded-lg border border-amber-400/20 bg-amber-500/8 px-4 py-3 text-xs leading-6 text-amber-100">
              系统会把以“（原文）”或“【P0001】（原文）”开头的原文行设为逐字保护区；只允许改相邻译文。若稿件没有这些标记，请先补齐标记再解析报告。
            </div>
          )}
          <div className="mt-4 grid gap-3 md:grid-cols-[1fr_auto]">
            <label className="flex cursor-pointer items-center gap-3 rounded-lg border border-dashed border-gold-500/25 bg-base-900/50 px-4 py-4 text-sm text-warm-300 hover:border-gold-300/45">
              <input type="file" accept=".pdf,application/pdf" className="hidden" onChange={(event) => setReportFile(event.target.files?.[0] ?? null)} />
              <UploadCloud className="h-5 w-5 text-gold-200" />
              <span>{reportFile?.name || "选择朱雀打印 PDF（图片型报告也可以）"}</span>
            </label>
            <button type="button" onClick={parseReport} disabled={parsing} className="btn-gold inline-flex min-w-[170px] items-center justify-center gap-2 disabled:opacity-45">
              {parsing ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileSearch className="h-4 w-4" />}
              {parsing ? "本地识别中" : "解析报告"}
            </button>
          </div>
        </div>

        <aside className="card-warm p-6">
          <div className="text-sm text-gold-200">硬规则</div>
          <div className="mt-4 space-y-4 text-sm leading-6 text-warm-300">
            <p>朱雀首页官方绿色人工特征必须 <span className="text-warm-50">≥ 25%</span>；24.99% 仍不通过。</p>
            <p>首页官方值与片段表推导值分开保存；首页识别不可靠时直接人工复核，表格值不冒充正式结果。</p>
            <p>质量不达标时立即保留原稿并停止后续付费调用，劣质低分稿不能导出为终稿。</p>
            <p>当前页面完全独立，不回填历史任务、不打开全局朱雀闸门、不处理长篇或史诗小说。</p>
          </div>
        </aside>
      </section>

      {report && (
        <section className="card-warm p-6">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <div className="text-sm text-gold-200">第二步</div>
              <h2 className="mt-1 font-serif text-2xl text-warm-50">选择定向处理片段</h2>
              <div className="mt-2 text-xs text-warm-400">
                {report.page_count} 页 · {report.extraction_method === "local_rapidocr" ? "本地 OCR" : "PDF 文本层"} · 原稿匹配 {Math.round(report.source_match_confidence * 100)}% · 推荐改 {report.recommended_segment_ids.length} 段
              </div>
            </div>
            <div className="flex flex-col items-stretch gap-2 sm:items-end">
              <div className="flex flex-wrap items-center justify-end gap-2">
                <label htmlFor="aigc-experiment" className="text-xs text-warm-400">处理方式</label>
                <select
                  id="aigc-experiment"
                  value={experimentType}
                  onChange={(event) => {
                    const value = event.target.value as AigcReviewExperimentType;
                    setExperimentType(value);
                    if (value === "detector_probe") {
                      setRewriteProvider("deepseek");
                      setSelectedIds(new Set(
                        report.segments
                          .filter((item) => item.label !== "human")
                          .map((item) => item.segment_id),
                      ));
                    }
                  }}
                  disabled={optimizing}
                  className="rounded-lg border border-gold-500/15 bg-base-900 px-3 py-2 text-sm text-warm-100"
                >
                  <option value="layout_only">纯结构调整（0 元）</option>
                  <option value="syntax_constrained">质量安全编辑</option>
                  <option value="combined">结构 + 句法编辑</option>
                  <option value="detector_probe">强力组合实验（DeepSeek）</option>
                </select>
                <label htmlFor="aigc-rewrite-provider" className="text-xs text-warm-400">改写模型</label>
                <select
                  id="aigc-rewrite-provider"
                  value={rewriteProvider}
                  onChange={(event) => setRewriteProvider(event.target.value as AigcReviewRewriteProvider)}
                  disabled={optimizing || experimentType === "layout_only" || probeMode}
                  className="rounded-lg border border-gold-500/15 bg-base-900 px-3 py-2 text-sm text-warm-100"
                >
                  {rewriteProviderOptions.map((provider) => (
                    <option key={provider.id} value={provider.id}>
                      {provider.label}{provider.automatic ? "" : "（仅手动）"}{provider.configured ? "" : "（未配置）"}
                    </option>
                  ))}
                </select>
                {rewriteProvider === "grok" && (
                  <select
                    value={rewriteReasoning}
                    onChange={(event) => setRewriteReasoning(event.target.value as "low" | "medium" | "high")}
                    disabled={optimizing}
                    aria-label="Grok 推理档位"
                    className="rounded-lg border border-gold-500/15 bg-base-900 px-3 py-2 text-sm text-warm-100"
                  >
                    <option value="low">low（推荐省费）</option>
                    <option value="medium">medium</option>
                    <option value="high">high（高成本）</option>
                  </select>
                )}
                <label htmlFor="aigc-budget" className="ml-2 text-xs text-warm-400">预算上限 ¥</label>
                <input
                  id="aigc-budget"
                  type="number"
                  min="0"
                  max="20"
                  step="0.1"
                  value={budgetCny}
                  onChange={(event) => setBudgetCny(Math.max(0, Number(event.target.value) || 0.3))}
                  disabled={optimizing || experimentType === "layout_only"}
                  className="w-24 rounded-lg border border-gold-500/15 bg-base-900 px-3 py-2 text-sm text-warm-100"
                />
              </div>
              <button type="button" onClick={() => optimize()} disabled={optimizing || selectedIds.size === 0 || report.strict_pass || !rewriteProviderReady} className="btn-gold inline-flex min-w-[230px] items-center justify-center gap-2 disabled:opacity-45">
                {optimizing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                {optimizing
                  ? (probeMode ? "生成探索稿中" : "生成并验收中")
                  : experimentType === "layout_only"
                    ? `零费用重组 ${selectedIds.size} 段`
                    : probeMode
                      ? "生成强力组合实验稿"
                      : `用 ${selectedRewriteProvider?.label ?? "所选模型"} 编辑 ${selectedIds.size} 段`}
              </button>
              {!rewriteProviderReady && (
                <div className="max-w-[360px] text-right text-xs text-amber-200">
                  所选模型尚未配置服务器密钥；系统不会静默切换或产生费用。
                </div>
              )}
              {selectedRewriteProvider?.id === "grok" && selectedRewriteProvider.configured && (
                <div className="max-w-[380px] text-right text-xs text-warm-400">
                  Grok 4.5 {rewriteReasoning} 推理 · 输入 ${selectedRewriteProvider.input_usd_per_m} / 缓存 ${selectedRewriteProvider.cached_input_usd_per_m} / 输出 ${selectedRewriteProvider.output_usd_per_m}（每百万 token）
                </div>
              )}
            </div>
          </div>

          <div className="mt-5 grid gap-3 sm:grid-cols-3">
            <ScoreCard label={report.official_overview_verified ? "首页官方人工特征" : "人工特征定位值（不可验收）"} value={report.human_percent} tone="green" />
            <ScoreCard label="疑似 AI" value={report.suspected_percent} tone="amber" />
            <ScoreCard label="AI 特征" value={report.ai_percent} tone="red" />
          </div>
          <div className="mt-4 rounded-lg border border-gold-500/15 bg-base-950/40 px-4 py-3 text-sm text-warm-300">
            当前选中风险区占全文 {selectedShare.toFixed(2)}%{probeMode ? "；完整方法实验处理全部选中非绿色片段。" : "，最多 12 个相邻片段。"}
            {experimentType === "layout_only"
              ? " 本轮只调整空白和自然段边界，非空白正文必须完全一致，模型费用 0 元。"
              : probeMode
                ? ` 本轮按 600–900 字单元轮换 5 种语气，完整执行高频词清理、长短句与段长打乱、独立短句、去编号结构、首段重写和基于原文的阅读反应；不调用 Gemini、不做质量回滚，最多支出 ¥${budgetCny.toFixed(2)}。`
                : ` 系统按 600–900 字读取原稿和事实账本做最小必要编辑；字面相似度只诊断、不再强迫改得不像。本轮最多支出 ¥${budgetCny.toFixed(2)}。`}
          </div>
          {report.warnings.map((warning) => <div key={warning} className="mt-3 text-sm text-amber-200">⚠ {warning}</div>)}

          <div className="mt-5 overflow-x-auto rounded-lg border border-gold-500/10">
            <table className="w-full min-w-[900px] text-left text-sm">
              <thead className="bg-base-950/60 text-xs text-warm-400">
                <tr><th className="px-4 py-3">选择</th><th className="px-4 py-3">片段</th><th className="px-4 py-3">报告分类</th><th className="px-4 py-3">占比</th><th className="px-4 py-3">AIGC 值</th><th className="px-4 py-3">映射</th><th className="px-4 py-3">原稿预览</th></tr>
              </thead>
              <tbody>
                {report.segments.map((segment) => (
                  <tr key={segment.segment_id} className="border-t border-gold-500/8 text-warm-200">
                    <td className="px-4 py-3"><input type="checkbox" checked={selectedIds.has(segment.segment_id)} disabled={segment.label === "human"} onChange={() => toggleSegment(segment.segment_id)} className="h-4 w-4 accent-amber-400" /></td>
                    <td className="px-4 py-3">#{segment.segment_id}{segment.recommended && <span className="ml-2 text-xs text-gold-200">推荐</span>}</td>
                    <td className="px-4 py-3"><span className={`rounded-full border px-2 py-1 text-xs ${labelClass[segment.label]}`}>{labelText[segment.label]}</span></td>
                    <td className="px-4 py-3">{segment.share_percent.toFixed(2)}%</td>
                    <td className="px-4 py-3">{segment.aigc_value.toFixed(4)}</td>
                    <td className="px-4 py-3">{Math.round(segment.mapping_confidence * 100)}%</td>
                    <td className="max-w-[360px] truncate px-4 py-3 text-xs text-warm-400" title={segment.mapped_text}>{segment.mapped_text.replace(/\s+/g, " ").slice(0, 90)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {result && (
        <section className="card-warm p-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="text-sm text-gold-200">第三步</div>
              <h2 className="mt-1 font-serif text-2xl text-warm-50">{resultIsProbe ? "朱雀探索稿与真实复检" : "质量闸门与真实复检"}</h2>
            </div>
            <div className="text-right text-xs leading-6 text-warm-400">
              本轮估算 ¥{result.cost.estimated_cny.toFixed(4)} / 上限 ¥{result.budget?.limit_cny.toFixed(2) ?? budgetCny.toFixed(2)}<br />
              {result.editorial_unit_count ?? 0} 个编辑单元 · {resultIsProbe ? "实验模式不终审" : `${result.audit_batch_count ?? 0} 批终审`}<br />
              {resultIsProbe
                ? `完整方法轮换 · ${result.models.rewrite_provider} / ${result.models.rewrite} · 温度 0.8`
                : `事实 ${result.models.fact} → 重著 ${result.models.rewrite_provider} / ${result.models.rewrite}（${result.models.rewrite_thinking}）→ 终审 ${result.models.audit}`}
            </div>
          </div>

          {result.archive_task_id && (
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-emerald-400/20 bg-emerald-500/8 px-4 py-3 text-sm text-emerald-100">
              <span>{resultIsProbe ? "本轮原稿、探索候选和朱雀基线报告已自动归档。" : "本轮原稿、审稿候选、质量结果和朱雀报告已自动归档。"}</span>
              <Link to={`/book/${result.archive_task_id}`} className="inline-flex items-center gap-2 text-gold-100 hover:text-gold-50">
                <BookMarked className="h-4 w-4" />查看书架稿件
              </Link>
            </div>
          )}

          {result.status === "manual_review" ? (
            <div className="mt-5 space-y-4">
              <div className="rounded-lg border border-amber-400/30 bg-amber-500/10 p-5 text-sm leading-7 text-amber-100">
                <div className="font-medium">质量未过，已回滚并保留原稿</div>
                <div className="mt-1 text-warm-300">{result.next_action}</div>
              </div>

              {result.quality_audit && (
                <div className="rounded-lg border border-red-400/20 bg-red-500/5 p-5">
                  <div className="text-sm font-medium text-red-100">独立终审未通过</div>
                  <div className="mt-3 grid gap-3 md:grid-cols-4">
                    <AuditCard label="语义忠实" value={result.quality_audit.semantic_fidelity} threshold={98} />
                    <AuditCard label="信息完整" value={result.quality_audit.information_completeness} threshold={100} />
                    <AuditCard label="流畅度" value={result.quality_audit.fluency} threshold={92} />
                    <AuditCard label="声音连续" value={result.quality_audit.voice_continuity} threshold={92} />
                  </div>
                  <div className="mt-3 text-sm leading-6 text-warm-300">{result.quality_audit.reason || "终审返回未满足硬门槛。"}</div>
                </div>
              )}

              {result.guarded_candidate_text && (
                <div>
                  <div className="mb-2 text-sm text-warm-300">已付费候选已完整保留（未通过最终质量闸门，仅供人工复核）</div>
                  <textarea readOnly value={result.guarded_candidate_text} className="min-h-[360px] w-full resize-y rounded-lg border border-amber-400/20 bg-base-950/60 p-4 text-sm leading-7 text-warm-100" />
                </div>
              )}

              {result.segment_results.filter((item) => item.status === "rejected").map((item) => {
                const lastAttempt = item.attempts[item.attempts.length - 1];
                return (
                  <div key={item.segment_id} className="rounded-lg border border-gold-500/15 bg-base-950/45 p-5 text-sm">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="font-medium text-warm-50">片段 #{item.segment_id} 机器校验明细</div>
                      <div className="text-xs text-warm-400">{strategyText[item.strategy_id as RewriteStrategy] || item.strategy_id} · 尝试 {item.rewrite_attempts} 次 · 相似度 {Math.round(item.deterministic_review.literal_similarity * 100)}% · 改写覆盖 {Math.round(item.deterministic_review.rewritten_paragraph_ratio * 100)}% / 当前要求 {Math.round(item.deterministic_review.required_paragraph_coverage * 100)}%</div>
                    </div>
                    <div className="mt-3 space-y-1 text-warm-300">
                      {[...(lastAttempt?.protocol_issues ?? []), ...item.deterministic_review.reasons].filter((value, index, values) => values.indexOf(value) === index).map((reason) => (
                        <div key={reason}>• {reason}</div>
                      ))}
                      {item.deterministic_review.missing_anchors.length > 0 && <div>• 缺失锚点：{item.deterministic_review.missing_anchors.slice(0, 8).join("、")}</div>}
                      {item.deterministic_review.extra_anchors.length > 0 && <div>• 多余锚点：{item.deterministic_review.extra_anchors.slice(0, 8).join("、")}</div>}
                    </div>
                  </div>
                );
              })}

              <button type="button" onClick={() => optimize(null, true)} disabled={optimizing} className="btn-ghost inline-flex items-center justify-center gap-2 disabled:opacity-45">
                {optimizing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                {optimizing ? "重新处理并验收中" : "再次处理（会产生新的 API 费用）"}
              </button>
            </div>
          ) : (
            <>
              {resultIsProbe && (
                <div className="mt-5 rounded-lg border border-amber-400/30 bg-amber-500/10 p-5 text-sm leading-7 text-amber-100">
                  <div className="font-medium">完整方法实验稿已生成（未做质量终审）</div>
                  <div className="mt-1 text-warm-300">本轮没有用事实账本、Gemini 或质量回滚把变化重新抹平。请只拿它做真实朱雀复检；即使达到 25%，也必须进入后续质量修复，不能直接交付。</div>
                </div>
              )}
              {result.quality_audit && (
                <div className="mt-5 grid gap-3 md:grid-cols-4">
                  <AuditCard label="语义忠实" value={result.quality_audit?.semantic_fidelity ?? 0} threshold={98} />
                  <AuditCard label="信息完整" value={result.quality_audit?.information_completeness ?? 0} threshold={100} />
                  <AuditCard label="流畅度" value={result.quality_audit?.fluency ?? 0} threshold={92} />
                  <AuditCard label="声音连续" value={result.quality_audit?.voice_continuity ?? 0} threshold={92} />
                </div>
              )}
              <div className="mt-5 grid gap-5 xl:grid-cols-2">
                <div>
                  <div className="mb-2 text-sm text-warm-300">{resultIsProbe ? "朱雀探索实验稿（非终稿）" : "待复检候选（尚不是终稿）"}</div>
                  <textarea readOnly value={result.candidate_text} className="min-h-[420px] w-full resize-y rounded-lg border border-gold-500/15 bg-base-950/60 p-4 text-sm leading-7 text-warm-100" />
                </div>
                <div>
                  <div className="mb-2 text-sm text-warm-300">上传该候选的第二份朱雀 PDF</div>
                  <label className="flex cursor-pointer items-center gap-3 rounded-lg border border-dashed border-gold-500/25 bg-base-900/50 px-4 py-4 text-sm text-warm-300">
                    <input type="file" accept=".pdf,application/pdf" className="hidden" onChange={(event) => setRecheckFile(event.target.files?.[0] ?? null)} />
                    <RefreshCw className="h-5 w-5 text-gold-200" />{recheckFile?.name || "选择复检 PDF"}
                  </label>
                  <button type="button" onClick={parseRecheck} disabled={!recheckFile || rechecking} className="btn-gold mt-3 inline-flex w-full items-center justify-center gap-2 disabled:opacity-45">
                    {rechecking ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileSearch className="h-4 w-4" />}{rechecking ? "识别复检报告" : "确认真实朱雀结果"}
                  </button>
                  {recheck && <div className={`mt-4 rounded-lg border p-5 ${(resultIsProbe ? comparison?.detector_pass : comparison?.strict_pass) ? "border-emerald-400/30 bg-emerald-500/10" : "border-amber-400/30 bg-amber-500/10"}`}>
                    <div className="flex items-center gap-2 text-lg text-warm-50">{(resultIsProbe ? comparison?.detector_pass : comparison?.strict_pass) ? <CheckCircle2 className="h-5 w-5 text-emerald-300" /> : <AlertTriangle className="h-5 w-5 text-amber-300" />}{resultIsProbe ? (comparison?.detector_pass ? "探索实验命中 25%" : "探索实验未命中") : (comparison?.strict_pass ? "朱雀与质量双闸门通过" : "尚未双达标")}</div>
                    <div className="mt-2 text-sm text-warm-300">首页官方人工特征 {recheck.official_human_percent == null ? "未可靠识别" : `${recheck.official_human_percent.toFixed(2)}%`} · 要求 ≥25%</div>
                    {resultIsProbe && comparison && <div className="mt-1 text-xs text-warm-400">相比基线 {comparison.human_percent_delta >= 0 ? "+" : ""}{comparison.human_percent_delta.toFixed(2)} 个百分点 · 质量验收 {comparison.quality_pass ? "已通过" : "未通过"}</div>}
                    <div className="mt-1 text-xs text-warm-400">报告正文与候选匹配 {Math.round(recheck.source_match_confidence * 100)}% · {recheck.source_match_verified ? "已确认同稿" : "未确认同稿，禁止下载"}</div>
                  </div>}
                  {comparison && !resultIsProbe && !comparison.strict_pass && comparison.recommended_next_strategy && (
                    <button type="button" onClick={() => optimize(comparison.recommended_next_strategy, true)} disabled={optimizing} className="btn-ghost mt-4 inline-flex w-full items-center justify-center gap-2 disabled:opacity-45">
                      {optimizing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                      再优化一轮
                    </button>
                  )}
                  <button type="button" onClick={() => downloadText(result.candidate_text, resultIsProbe ? "朱雀探索实验稿-非终稿.txt" : "朱雀复检通过终稿.txt")} disabled={!resultIsProbe && !finalPassed} className="btn-ghost mt-4 inline-flex w-full items-center justify-center gap-2 disabled:cursor-not-allowed disabled:opacity-35">
                    <Download className="h-4 w-4" />{resultIsProbe ? "下载探索实验稿（非终稿）" : (finalPassed ? "下载双达标终稿" : "复检通过后才能下载终稿")}
                  </button>
                </div>
              </div>
            </>
          )}
        </section>
      )}

    </div>
  );
}

function ScoreCard({ label, value, tone }: { label: string; value: number; tone: "green" | "amber" | "red" }) {
  const colors = { green: "text-emerald-200", amber: "text-amber-200", red: "text-red-200" };
  return <div className="rounded-lg border border-gold-500/10 bg-base-950/40 p-4"><div className="text-xs text-warm-400">{label}</div><div className={`mt-2 text-2xl ${colors[tone]}`}>{value.toFixed(2)}%</div></div>;
}

function AuditCard({ label, value, threshold }: { label: string; value: number; threshold: number }) {
  const passed = value >= threshold;
  return <div className="rounded-lg border border-gold-500/10 bg-base-950/40 p-4"><div className="text-xs text-warm-400">{label} · ≥{threshold}</div><div className={`mt-2 text-xl ${passed ? "text-emerald-200" : "text-red-200"}`}>{value}</div></div>;
}
