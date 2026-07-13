import { useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  FileSearch,
  Loader2,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  UploadCloud,
} from "lucide-react";
import { api } from "@/api/client";
import type {
  AigcReviewContentType,
  AigcReviewOptimizeResult,
  AigcReviewReport,
} from "@/api/types";

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
    try {
      const parsed = await api.parseAigcReviewReport(
        reportFile,
        normalizeManuscriptText(originalText),
        contentType,
      );
      setReport(parsed);
      setSelectedIds(new Set(parsed.recommended_segment_ids));
    } catch (err) {
      setError(err instanceof Error ? err.message : "朱雀报告解析失败");
    } finally {
      setParsing(false);
    }
  };

  const optimize = async () => {
    if (!report || selectedIds.size === 0) {
      setError("至少选择一个风险片段。");
      return;
    }
    setError("");
    setOptimizing(true);
    setResult(null);
    setRecheck(null);
    try {
      const response = await api.optimizeAigcReview({
        original_text: normalizeManuscriptText(originalText),
        original_sha256: report.original_sha256,
        content_type: contentType,
        segments: report.segments,
        selected_segment_ids: [...selectedIds],
      });
      setResult(response);
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
        <div className="rounded-lg border border-emerald-400/20 bg-emerald-500/8 px-4 py-3 text-xs leading-6 text-emerald-100">
          本地 OCR：0 元<br />DeepSeek V4 Pro 单候选 · 失败块最多一次有界重试<br />Gemini Flash-Lite 一次整稿终审
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
            <button type="button" onClick={optimize} disabled={optimizing || selectedIds.size === 0 || report.strict_pass} className="btn-gold inline-flex min-w-[210px] items-center justify-center gap-2 disabled:opacity-45">
              {optimizing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
              {optimizing ? "改写并独立终审中" : `处理选中 ${selectedIds.size} 段`}
            </button>
          </div>

          <div className="mt-5 grid gap-3 sm:grid-cols-3">
            <ScoreCard label="人工特征" value={report.human_percent} tone="green" />
            <ScoreCard label="疑似 AI" value={report.suspected_percent} tone="amber" />
            <ScoreCard label="AI 特征" value={report.ai_percent} tone="red" />
          </div>
          <div className="mt-4 rounded-lg border border-gold-500/15 bg-base-950/40 px-4 py-3 text-sm text-warm-300">
            已选片段占全文 {selectedShare.toFixed(2)}%；假设这些片段复检全部转绿，规划值约 {Math.min(100, report.human_percent + selectedShare).toFixed(2)}%。实际是否转绿必须重新上传朱雀报告确认。
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
              本轮估算 ¥{result.cost.estimated_cny.toFixed(4)}<br />{result.models.rewrite} → {result.models.audit}
            </div>
          </div>

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

              {result.segment_results.filter((item) => item.status === "rejected").map((item) => {
                const lastAttempt = item.attempts[item.attempts.length - 1];
                return (
                  <div key={item.segment_id} className="rounded-lg border border-gold-500/15 bg-base-950/45 p-5 text-sm">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="font-medium text-warm-50">片段 #{item.segment_id} 机器校验明细</div>
                      <div className="text-xs text-warm-400">已自动尝试 {item.rewrite_attempts} 次 · 长度比例 {item.deterministic_review.length_ratio.toFixed(3)}</div>
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

              <button type="button" onClick={optimize} disabled={optimizing} className="btn-ghost inline-flex items-center justify-center gap-2 disabled:opacity-45">
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
                  <button type="button" onClick={() => downloadText(result.candidate_text, "朱雀复检通过终稿.txt")} disabled={!finalPassed} className="btn-ghost mt-4 inline-flex w-full items-center justify-center gap-2 disabled:cursor-not-allowed disabled:opacity-35">
                    <Download className="h-4 w-4" />{finalPassed ? "下载双达标终稿" : "复检通过后才能下载终稿"}
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
