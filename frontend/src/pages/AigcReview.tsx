import { useEffect, useMemo, useState } from "react";
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
  AigcReviewCalibrationSample,
  AigcReviewComparison,
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

const styleFeatureText: Record<string, string> = {
  mean_sentence_chars: "平均句长",
  sentence_length_cv: "句长波动",
  short_sentence_ratio: "短句占比",
  long_sentence_ratio: "长句占比",
  paragraph_length_cv: "段长波动",
  comma_per_sentence_end: "逗号/句末比",
  semicolon_per_1000_chars: "分号密度",
  colon_per_1000_chars: "冒号密度",
  discourse_markers_per_1000_chars: "模板连接词密度",
  punctuation_entropy: "标点分布熵",
  repeated_four_gram_ratio: "四字重复率",
  unique_cjk_ratio: "汉字多样度",
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
  const [budgetCny, setBudgetCny] = useState(0.8);
  const [calibrationSamples, setCalibrationSamples] = useState<AigcReviewCalibrationSample[]>([]);
  const [humanEditedText, setHumanEditedText] = useState("");
  const [calibrationValues, setCalibrationValues] = useState<Record<string, string>>({});
  const [calibrationFiles, setCalibrationFiles] = useState<Record<string, File | null>>({});
  const [calibrationBusy, setCalibrationBusy] = useState(false);
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
  const finalPassed = Boolean(
    result?.status === "candidate_ready" && recheck?.strict_pass && recheck?.source_match_verified,
  );
  const rewriteProviderOptions = report?.model_strategy.rewrite_providers ?? [];
  const selectedRewriteProvider = rewriteProviderOptions.find((item) => item.id === rewriteProvider);
  const rewriteProviderReady = Boolean(selectedRewriteProvider?.configured);
  const maxCalibrationSegments = report?.model_strategy.recommended_max_calibration_segments ?? 3;
  const calibrationUnit = useMemo(() => {
    for (const segment of result?.segment_results ?? []) {
      for (const unit of segment.unit_results ?? []) {
        if (
          unit.status === "ready"
          && unit.original_chars >= 350
          && unit.original_chars <= 2000
          && unit.candidate_chars >= 350
          && unit.candidate_chars <= 2000
        ) {
          return { segment, unit };
        }
      }
    }
    return null;
  }, [result]);

  useEffect(() => {
    api.listAigcReviewCalibrations(50).then(setCalibrationSamples).catch(() => undefined);
  }, []);

  useEffect(() => {
    setHumanEditedText(calibrationUnit?.unit.candidate_text ?? "");
  }, [calibrationUnit]);

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
    if (
      selectedIds.size > maxCalibrationSegments
      && !window.confirm(
        `你选择了 ${selectedIds.size} 个片段，将按整篇模式逐段调用模型，费用会明显高于 ${maxCalibrationSegments} 段校准。继续处理全部选中片段吗？`,
      )
    ) {
      return;
    }
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

  const refreshCalibrations = async () => {
    setCalibrationSamples(await api.listAigcReviewCalibrations(50));
  };

  const registerCalibrationPair = async () => {
    if (!calibrationUnit || !result) return;
    setCalibrationBusy(true);
    setError("");
    try {
      const sourceGroup = `${result.request_id}-${calibrationUnit.segment.segment_id}-${calibrationUnit.unit.unit_id}`;
      const originalSample = await api.createAigcReviewCalibration({
        text: calibrationUnit.unit.original_text,
        content_type: contentType,
        source_group: sourceGroup,
        variant_type: "original",
        strategy_id: calibrationUnit.segment.strategy_id,
      });
      await api.createAigcReviewCalibration({
        text: calibrationUnit.unit.candidate_text,
        content_type: contentType,
        source_group: sourceGroup,
        variant_type: "fact_reauthor",
        parent_sha256: originalSample.text_sha256,
        strategy_id: calibrationUnit.segment.strategy_id,
      });
      await refreshCalibrations();
    } catch (err) {
      setError(err instanceof Error ? err.message : "登记隔离校准样本失败");
    } finally {
      setCalibrationBusy(false);
    }
  };

  const registerHumanEdit = async () => {
    if (!calibrationUnit || !result) return;
    const normalized = normalizeManuscriptText(humanEditedText);
    if (normalized.length < 350 || normalized.length > 2000) {
      setError("人工修订校准样本必须保持在 350–2000 字。 ");
      return;
    }
    setCalibrationBusy(true);
    setError("");
    try {
      const sourceGroup = `${result.request_id}-${calibrationUnit.segment.segment_id}-${calibrationUnit.unit.unit_id}`;
      const originalSample = calibrationSamples.find(
        (item) => item.source_group === sourceGroup && item.variant_type === "original",
      );
      await api.createAigcReviewCalibration({
        text: normalized,
        content_type: contentType,
        source_group: sourceGroup,
        variant_type: "human_edited",
        parent_sha256: originalSample?.text_sha256 ?? null,
        strategy_id: calibrationUnit.segment.strategy_id,
      });
      await refreshCalibrations();
    } catch (err) {
      setError(err instanceof Error ? err.message : "登记人工修订样本失败");
    } finally {
      setCalibrationBusy(false);
    }
  };

  const saveCalibrationValue = async (sample: AigcReviewCalibrationSample) => {
    const value = Number(calibrationValues[sample.sample_id]);
    if (!Number.isFinite(value) || value < 0 || value > 1) {
      setError("请输入朱雀报告中的 AIGC 值，范围 0–1，例如 0.3472。 ");
      return;
    }
    setCalibrationBusy(true);
    setError("");
    try {
      await api.saveAigcReviewCalibrationResult(sample.sample_id, {
        text_sha256: sample.text_sha256,
        aigc_value: value,
      });
      await refreshCalibrations();
    } catch (err) {
      setError(err instanceof Error ? err.message : "登记真实朱雀值失败");
    } finally {
      setCalibrationBusy(false);
    }
  };

  const uploadCalibrationReport = async (sample: AigcReviewCalibrationSample) => {
    const file = calibrationFiles[sample.sample_id];
    if (!file) {
      setError("请先选择该隔离样本单独送检后打印的朱雀 PDF。 ");
      return;
    }
    setCalibrationBusy(true);
    setError("");
    try {
      await api.uploadAigcReviewCalibrationReport(sample.sample_id, file);
      await refreshCalibrations();
    } catch (err) {
      setError(err instanceof Error ? err.message : "隔离校准报告解析失败");
    } finally {
      setCalibrationBusy(false);
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
            真实朱雀报告驱动 · 质量优先 · 小说暂不处理
          </div>
          <h1 className="font-serif text-4xl text-warm-50">AI 审稿降率</h1>
          <p className="mt-3 max-w-4xl text-sm leading-7 text-warm-300">
            上传朱雀打印 PDF，并粘贴送检时的同一份原稿。系统只改报告指出的风险片段，先守住事实与文稿质量，再等待真实朱雀复检；人工特征严格大于 50% 才算通过。
          </p>
        </div>
        <div className="flex flex-col items-stretch gap-3">
          <div className="rounded-lg border border-emerald-400/20 bg-emerald-500/8 px-4 py-3 text-xs leading-6 text-emerald-100">
            本地 OCR：0 元<br />DeepSeek 默认重著 · Grok 仅手动可选<br />事实账本缓存 · 350–900 字分批终审
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
            <p>绿色人工特征必须 <span className="text-warm-50">严格 &gt; 50%</span>；橙色和红色都不算人工。</p>
            <p>工程目标默认做到约 60%，给下一次检测波动留余量，但这里只是片段规划，不冒充朱雀结果。</p>
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
                <label htmlFor="aigc-rewrite-provider" className="text-xs text-warm-400">改写模型</label>
                <select
                  id="aigc-rewrite-provider"
                  value={rewriteProvider}
                  onChange={(event) => setRewriteProvider(event.target.value as AigcReviewRewriteProvider)}
                  disabled={optimizing}
                  className="rounded-lg border border-gold-500/15 bg-base-900 px-3 py-2 text-sm text-warm-100"
                >
                  {rewriteProviderOptions.map((provider) => (
                    <option key={provider.id} value={provider.id}>
                      {provider.label}{provider.configured ? "" : "（未配置）"}
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
                  min="0.1"
                  max="20"
                  step="0.1"
                  value={budgetCny}
                  onChange={(event) => setBudgetCny(Math.max(0.1, Number(event.target.value) || 0.8))}
                  disabled={optimizing}
                  className="w-24 rounded-lg border border-gold-500/15 bg-base-900 px-3 py-2 text-sm text-warm-100"
                />
              </div>
              <button type="button" onClick={() => optimize()} disabled={optimizing || selectedIds.size === 0 || report.strict_pass || !rewriteProviderReady} className="btn-gold inline-flex min-w-[230px] items-center justify-center gap-2 disabled:opacity-45">
                {optimizing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                {optimizing ? "改写并独立终审中" : `用 ${selectedRewriteProvider?.label ?? "所选模型"} 校准 ${selectedIds.size} 段`}
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
            <ScoreCard label="人工特征" value={report.human_percent} tone="green" />
            <ScoreCard label="疑似 AI" value={report.suspected_percent} tone="amber" />
            <ScoreCard label="AI 特征" value={report.ai_percent} tone="red" />
          </div>
          <div className="mt-4 rounded-lg border border-gold-500/15 bg-base-950/40 px-4 py-3 text-sm text-warm-300">
            {selectedIds.size <= maxCalibrationSegments
              ? `当前是低成本候选轮：选中片段占全文 ${selectedShare.toFixed(2)}%。`
              : `当前是整篇处理模式：已选 ${selectedIds.size} 段、占全文 ${selectedShare.toFixed(2)}%。`}
            系统先抽事实账本，再按 350–900 字从空白重著；字面相似度高于 90% 或超过 25% 可编辑段落基本未改会被硬拦截。本轮最多支出 ¥{budgetCny.toFixed(2)}。
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
              <h2 className="mt-1 font-serif text-2xl text-warm-50">质量闸门与真实复检</h2>
            </div>
            <div className="text-right text-xs leading-6 text-warm-400">
              本轮估算 ¥{result.cost.estimated_cny.toFixed(4)} / 上限 ¥{result.budget?.limit_cny.toFixed(2) ?? budgetCny.toFixed(2)}<br />
              {result.editorial_unit_count ?? 0} 个编辑单元 · {result.audit_batch_count ?? 0} 批终审<br />
              事实 {result.models.fact} → 重著 {result.models.rewrite_provider} / {result.models.rewrite}（{result.models.rewrite_thinking}）→ 终审 {result.models.audit}
            </div>
          </div>

          {result.archive_task_id && (
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-emerald-400/20 bg-emerald-500/8 px-4 py-3 text-sm text-emerald-100">
              <span>本轮原稿、审稿候选、质量结果和朱雀报告已自动归档。</span>
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
                    <AuditCard label="语义忠实" value={result.quality_audit.semantic_fidelity} threshold={95} />
                    <AuditCard label="信息完整" value={result.quality_audit.information_completeness} threshold={98} />
                    <AuditCard label="流畅度" value={result.quality_audit.fluency} threshold={90} />
                    <AuditCard label="声音连续" value={result.quality_audit.voice_continuity} threshold={90} />
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
              <div className="mt-5 grid gap-3 md:grid-cols-4">
                <AuditCard label="语义忠实" value={result.quality_audit?.semantic_fidelity ?? 0} threshold={95} />
                <AuditCard label="信息完整" value={result.quality_audit?.information_completeness ?? 0} threshold={98} />
                <AuditCard label="流畅度" value={result.quality_audit?.fluency ?? 0} threshold={90} />
                <AuditCard label="声音连续" value={result.quality_audit?.voice_continuity ?? 0} threshold={90} />
              </div>
              <div className="mt-4 grid gap-3 md:grid-cols-3">
                {result.segment_results.map((item) => (
                  <div key={item.segment_id} className="rounded-lg border border-gold-500/10 bg-base-950/40 p-4 text-xs leading-6 text-warm-300">
                    <div className="font-medium text-warm-50">片段 #{item.segment_id} · {strategyText[item.strategy_id as RewriteStrategy] || item.strategy_id}</div>
                    <div>字面相似度 {Math.round(item.deterministic_review.literal_similarity * 100)}%（硬门槛 ≤90%）</div>
                    <div>编辑单元 {item.editorial_unit_count} 个 · 共尝试 {item.rewrite_attempts} 次</div>
                    <div className={item.substantive_rewrite_passed ? "text-emerald-200" : "text-red-200"}>
                      {item.substantive_rewrite_passed ? "实质重著硬门槛通过" : "实质重著不足，禁止复检"}
                    </div>
                    {item.style_diagnosis && (
                      <div className="mt-2 border-t border-gold-500/10 pt-2 text-warm-400">
                        局部诊断：{item.style_diagnosis.replace(/\s+/g, " ")}
                      </div>
                    )}
                  </div>
                ))}
              </div>
              <div className="mt-5 grid gap-5 xl:grid-cols-2">
                <div>
                  <div className="mb-2 text-sm text-warm-300">待复检候选（尚不是终稿）</div>
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
                  {recheck && <div className={`mt-4 rounded-lg border p-5 ${recheck.strict_pass ? "border-emerald-400/30 bg-emerald-500/10" : "border-amber-400/30 bg-amber-500/10"}`}>
                    <div className="flex items-center gap-2 text-lg text-warm-50">{recheck.strict_pass ? <CheckCircle2 className="h-5 w-5 text-emerald-300" /> : <AlertTriangle className="h-5 w-5 text-amber-300" />}{recheck.strict_pass ? "双闸门已通过" : "朱雀仍未通过"}</div>
                    <div className="mt-2 text-sm text-warm-300">人工特征 {recheck.human_percent.toFixed(2)}% · 必须严格大于 50%</div>
                    <div className="mt-1 text-xs text-warm-400">报告正文与候选匹配 {Math.round(recheck.source_match_confidence * 100)}% · {recheck.source_match_verified ? "已确认同稿" : "未确认同稿，禁止下载"}</div>
                  </div>}
                  {comparison && (
                    <div className="mt-4 rounded-lg border border-gold-500/15 bg-base-950/45 p-5 text-sm leading-6 text-warm-300">
                      <div className="font-medium text-warm-50">整稿复检观察（不用于策略学习）</div>
                      <div className="mt-2">本轮近似对齐后转绿：{comparison.selected_humanized_share_percent.toFixed(2)}% 全文占比；加权 AIGC 值变化 {comparison.selected_weighted_delta > 0 ? "+" : ""}{comparison.selected_weighted_delta.toFixed(4)} 只用于展示。局部因果必须在下方精确哈希隔离送检台验证。</div>
                      <div className="mt-3 space-y-2">
                        {comparison.strategy_effectiveness.map((item) => (
                          <div key={item.strategy_id} className="flex items-center justify-between gap-3 border-t border-gold-500/10 pt-2">
                            <span>{strategyText[item.strategy_id as RewriteStrategy] || item.strategy_id} · 片段 {item.segment_ids.map((id) => `#${id}`).join("、")}</span>
                            <span className={item.green_conversion_rate > 0 ? "text-emerald-200" : "text-amber-200"}>转绿 {(item.green_conversion_rate * 100).toFixed(1)}% · Δ {item.weighted_delta > 0 ? "+" : ""}{item.weighted_delta.toFixed(4)}</span>
                          </div>
                        ))}
                      </div>
                      <div className="mt-3 text-xs text-warm-400">{comparison.recommendation_reason}</div>
                      {comparison.historical_strategy_summary.length > 0 && (
                        <div className="mt-3 rounded-md border border-gold-500/10 px-3 py-2 text-xs text-warm-400">
                          累计真实复检：{comparison.historical_strategy_summary.map((item) => `${strategyText[item.strategy_id as RewriteStrategy] || item.strategy_id} ${item.experiment_count} 次 / 平均转绿 ${(item.average_green_conversion_rate * 100).toFixed(1)}% / AIGC Δ ${item.average_weighted_delta > 0 ? "+" : ""}${item.average_weighted_delta.toFixed(4)}`).join("；")}
                        </div>
                      )}
                      {comparison.historical_feature_summary.length > 0 ? (
                        <div className="mt-3 rounded-md border border-emerald-400/15 bg-emerald-500/5 px-3 py-2 text-xs text-warm-300">
                          <div className="mb-1 text-emerald-100">黑盒特征证据（相关性不冒充因果）</div>
                          {comparison.historical_feature_summary.slice(0, 4).map((item) => (
                            <div key={item.feature}>{styleFeatureText[item.feature] || item.feature}：样本 {item.sample_count} · 相关系数 {item.pearson_correlation.toFixed(3)} · {item.evidence_grade}</div>
                          ))}
                        </div>
                      ) : (
                        <div className="mt-3 text-xs text-warm-400">真实配对片段不足 5 个，暂不输出所谓“朱雀规律”；继续收集复检后才建立局部替代模型。</div>
                      )}
                      {!comparison.strict_pass && comparison.recommended_next_strategy && (
                        <button type="button" onClick={() => optimize(comparison.recommended_next_strategy, true)} disabled={optimizing} className="btn-ghost mt-3 inline-flex w-full items-center justify-center gap-2 disabled:opacity-45">
                          {optimizing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                          按真实反馈采用“{strategyText[comparison.recommended_next_strategy]}”再生成一轮
                        </button>
                      )}
                      {comparison.stop_automatic_spend && <div className="mt-3 text-amber-200">整稿近似映射不能指导继续烧 API；系统只接受隔离样本的精确哈希结果。</div>}
                    </div>
                  )}
                  <button type="button" onClick={() => downloadText(result.candidate_text, "朱雀复检通过终稿.txt")} disabled={!finalPassed} className="btn-ghost mt-4 inline-flex w-full items-center justify-center gap-2 disabled:cursor-not-allowed disabled:opacity-35">
                    <Download className="h-4 w-4" />{finalPassed ? "下载双达标终稿" : "复检通过后才能下载终稿"}
                  </button>
                </div>
              </div>
            </>
          )}
        </section>
      )}

      {result && (
        <section className="card-warm p-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="text-sm text-gold-200">第四步 · 黑盒校准</div>
              <h2 className="mt-1 font-serif text-2xl text-warm-50">精确哈希隔离送检台</h2>
              <p className="mt-2 max-w-4xl text-sm leading-7 text-warm-300">
                整篇 PDF 的片段边界只能近似对齐，不能证明某条策略有效。这里固定同一个 350–2000 字编辑单元，分别登记原文、事实重著稿和你的人工修订稿；复制哪一版单独送朱雀，就给那一版上传打印 PDF。只有报告正文匹配且哈希完全一致的结果才能学习；手填数值只作观察。
              </p>
            </div>
            <button type="button" onClick={refreshCalibrations} disabled={calibrationBusy} className="btn-ghost inline-flex items-center gap-2 disabled:opacity-45">
              <RefreshCw className={`h-4 w-4 ${calibrationBusy ? "animate-spin" : ""}`} />刷新样本
            </button>
          </div>

          {calibrationUnit ? (
            <div className="mt-5 grid gap-5 xl:grid-cols-2">
              <div className="rounded-lg border border-gold-500/15 bg-base-950/40 p-5">
                <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                  <span className="font-medium text-warm-50">
                    片段 #{calibrationUnit.segment.segment_id} / {calibrationUnit.unit.unit_id} · {calibrationUnit.unit.original_chars} 字
                  </span>
                  <span className="text-warm-400">事实账本：{calibrationUnit.unit.fact_ledger_mode}</span>
                </div>
                <div className="mt-4 grid gap-3 sm:grid-cols-2">
                  <textarea readOnly value={calibrationUnit.unit.original_text} className="min-h-[260px] resize-y rounded-lg border border-red-400/15 bg-base-950/70 p-3 text-xs leading-6 text-warm-200" />
                  <textarea readOnly value={calibrationUnit.unit.candidate_text} className="min-h-[260px] resize-y rounded-lg border border-amber-400/15 bg-base-950/70 p-3 text-xs leading-6 text-warm-200" />
                </div>
                <button type="button" onClick={registerCalibrationPair} disabled={calibrationBusy} className="btn-gold mt-4 inline-flex w-full items-center justify-center gap-2 disabled:opacity-45">
                  {calibrationBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />}
                  登记原文 + 模型重著两份精确样本
                </button>
              </div>

              <div className="rounded-lg border border-emerald-400/15 bg-emerald-500/5 p-5">
                <div className="text-sm font-medium text-emerald-100">人工定向修订</div>
                <p className="mt-2 text-xs leading-6 text-warm-400">
                  在不增删事实的前提下，改掉你一眼能看出的套话、均匀节奏和总结式收尾。保存后它会成为第三个可归因样本，不会调用任何模型。
                </p>
                <textarea value={humanEditedText} onChange={(event) => setHumanEditedText(event.target.value)} className="mt-3 min-h-[310px] w-full resize-y rounded-lg border border-emerald-400/20 bg-base-950/70 p-3 text-xs leading-6 text-warm-100" />
                <button type="button" onClick={registerHumanEdit} disabled={calibrationBusy} className="btn-ghost mt-3 inline-flex w-full items-center justify-center gap-2 disabled:opacity-45">
                  <ShieldCheck className="h-4 w-4" />登记人工修订精确样本
                </button>
              </div>
            </div>
          ) : (
            <div className="mt-5 rounded-lg border border-amber-400/20 bg-amber-500/8 px-4 py-4 text-sm leading-6 text-amber-100">
              本轮还没有同时满足质量保护、实质重著和 350–2000 字范围的编辑单元，因此不生成不可归因的校准数据。
            </div>
          )}

          <div className="mt-6 overflow-x-auto rounded-lg border border-gold-500/10">
            <table className="w-full min-w-[1050px] text-left text-sm">
              <thead className="bg-base-950/60 text-xs text-warm-400">
                <tr><th className="px-4 py-3">样本</th><th className="px-4 py-3">类型</th><th className="px-4 py-3">字数 / 哈希</th><th className="px-4 py-3">送检正文</th><th className="px-4 py-3">朱雀 PDF / 备用手填</th><th className="px-4 py-3">状态</th></tr>
              </thead>
              <tbody>
                {calibrationSamples.slice(0, 12).map((sample) => (
                  <tr key={sample.sample_id} className="border-t border-gold-500/8 text-warm-200">
                    <td className="px-4 py-3 text-xs">{sample.source_group.slice(0, 20)}…</td>
                    <td className="px-4 py-3">{{ original: "原文", fact_reauthor: "模型重著", human_directed: "人工指令", human_edited: "人工修订", human_control: "真人对照" }[sample.variant_type]}</td>
                    <td className="px-4 py-3 text-xs">{sample.char_count} 字<br /><span className="text-warm-500">{sample.text_sha256.slice(0, 12)}…</span></td>
                    <td className="px-4 py-3">
                      <button type="button" onClick={() => navigator.clipboard.writeText(sample.text)} className="text-gold-200 hover:text-gold-50">复制精确正文</button>
                    </td>
                    <td className="px-4 py-3">
                      {sample.status === "labeled" ? <span>{sample.aigc_value?.toFixed(4)}{sample.human_percent != null ? ` · 人工 ${sample.human_percent.toFixed(2)}%` : ""}</span> : <div className="space-y-2">
                        <div className="flex items-center gap-2">
                          <input type="file" accept=".pdf,application/pdf" onChange={(event) => setCalibrationFiles((current) => ({ ...current, [sample.sample_id]: event.target.files?.[0] ?? null }))} className="max-w-[180px] text-[11px] text-warm-400" />
                          <button type="button" onClick={() => uploadCalibrationReport(sample)} disabled={calibrationBusy || !calibrationFiles[sample.sample_id]} className="text-xs text-emerald-200 hover:text-emerald-50 disabled:opacity-40">验 PDF</button>
                        </div>
                        <div className="flex items-center gap-2 text-[11px] text-warm-500">
                          <input type="number" min="0" max="1" step="0.0001" value={calibrationValues[sample.sample_id] ?? ""} onChange={(event) => setCalibrationValues((current) => ({ ...current, [sample.sample_id]: event.target.value }))} placeholder="备用 0.3472" className="w-24 rounded border border-gold-500/15 bg-base-900 px-2 py-1 text-xs" />
                          <button type="button" onClick={() => saveCalibrationValue(sample)} disabled={calibrationBusy} className="hover:text-gold-50 disabled:opacity-40">仅观察</button>
                        </div>
                      </div>}
                    </td>
                    <td className="px-4 py-3">
                      {sample.label ? <span className={`rounded-full border px-2 py-1 text-xs ${labelClass[sample.label]}`}>{labelText[sample.label]} · {sample.learning_eligible ? "PDF 已验，可学习" : "手填，仅观察"}</span> : <span className="text-xs text-warm-400">等待单独送检</span>}
                    </td>
                  </tr>
                ))}
                {calibrationSamples.length === 0 && <tr><td colSpan={6} className="px-4 py-8 text-center text-sm text-warm-500">尚未登记校准样本</td></tr>}
              </tbody>
            </table>
          </div>
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
