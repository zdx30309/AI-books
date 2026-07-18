import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ClipboardCopy,
  Download,
  FileText,
  Loader2,
  PlayCircle,
  UploadCloud,
} from "lucide-react";
import { api } from "@/api/client";
import type {
  AigcReviewReport,
  TestingGeneratedArticle,
  TestingRewriteCompletedArticleResponse,
  TestingZhuqueFeedbackResponse,
} from "@/api/types";

const STYLE_METRICS = [
  ["mean_sentence_chars", "平均句长"],
  ["sentence_length_cv", "句长变异系数"],
  ["sentence_length_volatility", "相邻句长波动"],
  ["short_sentence_ratio", "短句占比"],
  ["long_sentence_ratio", "长句占比"],
  ["mean_paragraph_chars", "平均段长"],
  ["paragraph_length_cv", "段长变异系数"],
  ["discourse_markers_per_1000_chars", "连接词/千字"],
  ["punctuation_entropy", "标点熵"],
  ["trigram_perplexity_proxy", "三元字符经验困惑度"],
  ["compression_ratio", "压缩率"],
  ["repeated_four_gram_ratio", "重复四元组占比"],
] as const;

function formatMetric(value: number | undefined) {
  return Number.isFinite(value) ? Number(value).toFixed(3) : "—";
}

function parseVideoLinks(input: string) {
  const seen = new Set<string>();
  return input
    .split(/[\s,，]+/)
    .map((item) => item.trim().replace(/[，。；;、！？!?.,)）\]】]+$/g, ""))
    .filter(Boolean)
    .filter((item) => {
      if (seen.has(item)) return false;
      seen.add(item);
      return true;
    });
}

function downloadText(text: string, filename: string) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function safeFilename(input: string, fallback: string) {
  const cleaned = input.replace(/^https?:\/\//, "").replace(/[^\w\u4e00-\u9fa5.-]+/g, "-").slice(0, 80);
  return cleaned || fallback;
}

export function TestingEndpoint() {
  const [books, setBooks] = useState<File[]>([]);
  const [taskId, setTaskId] = useState("");
  const [analysisMessage, setAnalysisMessage] = useState("");
  const [videoLinks, setVideoLinks] = useState("");
  const [rawVideoText, setRawVideoText] = useState("");
  const [articles, setArticles] = useState<TestingGeneratedArticle[]>([]);
  const [completedArticle, setCompletedArticle] = useState("");
  const [transferStrength, setTransferStrength] = useState<"strong" | "maximum">("maximum");
  const [rewriteResult, setRewriteResult] = useState<TestingRewriteCompletedArticleResponse | null>(null);
  const [rewriting, setRewriting] = useState(false);
  const [baselineFile, setBaselineFile] = useState<File | null>(null);
  const [candidateFile, setCandidateFile] = useState<File | null>(null);
  const [baselineReport, setBaselineReport] = useState<AigcReviewReport | null>(null);
  const [candidateReport, setCandidateReport] = useState<AigcReviewReport | null>(null);
  const [zhuqueFeedback, setZhuqueFeedback] = useState<TestingZhuqueFeedbackResponse | null>(null);
  const [parsingReports, setParsingReports] = useState(false);
  const [error, setError] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);
  const resultsRef = useRef<HTMLDivElement | null>(null);
  const rewriteResultsRef = useRef<HTMLDivElement | null>(null);
  const videoCount = parseVideoLinks(videoLinks).length;
  const currentPhase = generating
    ? "generating"
    : articles.length
      ? "complete"
      : analysisMessage
        ? "ready"
        : analyzing
          ? "analyzing"
          : "setup";

  useEffect(() => {
    if (articles.length > 0) {
      resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [articles]);

  useEffect(() => {
    if (rewriteResult) {
      rewriteResultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [rewriteResult]);

  const analyzeBooks = async () => {
    if (!books.length) {
      setError("请至少上传 1 本参考书。");
      return;
    }
    setError("");
    setAnalysisMessage("");
    setArticles([]);
    setRewriteResult(null);
    setBaselineReport(null);
    setCandidateReport(null);
    setZhuqueFeedback(null);
    setAnalyzing(true);
    try {
      const result = await api.analyzeTestingReferenceBooks(books);
      setTaskId(result.task_id);
      setAnalysisMessage(result.message);
    } catch (err) {
      setError(err instanceof Error ? err.message : "参考书分析失败");
    } finally {
      setAnalyzing(false);
    }
  };

  const generateArticle = async () => {
    if (!taskId || !analysisMessage) {
      setError("请先完成参考书分析。");
      return;
    }
    const urls = parseVideoLinks(videoLinks);
    if (!urls.length) {
      setError("请填写至少 1 个视频链接。");
      return;
    }
    if (urls.length > 1 && rawVideoText.trim()) {
      setError("批量视频链接暂不支持共用文字稿；请清空文字稿，或一次只生成 1 个链接。");
      return;
    }
    setError("");
    setArticles([]);
    setGenerating(true);
    try {
      const result = await api.generateTestingArticle(taskId, {
        video_urls: urls,
        raw_video_text: rawVideoText.trim() || null,
      });
      setArticles(result.articles?.length ? result.articles : result.article ? [{
        video_url: urls[0],
        article: result.article,
        status: result.status,
      }] : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "文章生成失败");
    } finally {
      setGenerating(false);
    }
  };

  const rewriteCompletedArticle = async () => {
    if (!taskId || !analysisMessage) {
      setError("请先上传并分析人类参考书。");
      return;
    }
    if (completedArticle.trim().length < 500) {
      setError("已完成文章至少需要 500 字。");
      return;
    }
    setError("");
    setRewriteResult(null);
    setBaselineReport(null);
    setCandidateReport(null);
    setZhuqueFeedback(null);
    setRewriting(true);
    try {
      setRewriteResult(await api.rewriteTestingCompletedArticle(taskId, {
        original_text: completedArticle.trim(),
        transfer_strength: transferStrength,
      }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "全稿实验重写失败");
    } finally {
      setRewriting(false);
    }
  };

  const parseZhuquePair = async () => {
    if (!baselineFile || !candidateFile || !rewriteResult) {
      setError("请同时上传原稿朱雀 PDF 和候选稿朱雀 PDF。");
      return;
    }
    setError("");
    setParsingReports(true);
    try {
      const [before, after] = await Promise.all([
        api.parseAigcReviewReport(baselineFile, completedArticle.trim(), "article"),
        api.parseAigcReviewReport(candidateFile, rewriteResult.candidate_text, "article"),
      ]);
      setBaselineReport(before);
      setCandidateReport(after);
      setZhuqueFeedback(await api.recordTestingZhuqueFeedback(taskId, before, after));
    } catch (err) {
      setError(err instanceof Error ? err.message : "朱雀 A/B 报告解析失败");
    } finally {
      setParsingReports(false);
    }
  };

  const copyArticle = async (article: string, index: number) => {
    await navigator.clipboard.writeText(article);
    setCopiedIndex(index);
    window.setTimeout(() => setCopiedIndex(null), 1600);
  };

  return (
    <div className="space-y-8">
      <header className="space-y-3">
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-lg border border-gold-400/20 bg-gold-500/10">
            <FileText className="h-5 w-5 text-gold-200" />
          </div>
          <div>
            <h1 className="font-serif text-3xl text-warm-50">Testing Endpoint</h1>
            <p className="mt-1 text-sm text-warm-400">双线并行：参考书约束的从头写作 / 原稿保序改写</p>
          </div>
        </div>
      </header>

      {error && (
        <div className="flex items-start gap-3 rounded-lg border border-red-400/25 bg-red-500/10 p-4 text-sm leading-6 text-red-100">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
          <div>{error}</div>
        </div>
      )}

      <section className="rounded-lg border border-warm-100/10 bg-base-800/70 p-5">
        <div className="grid gap-3 md:grid-cols-4">
          {[
            { id: "setup", label: "1. Reference", done: Boolean(analysisMessage), active: currentPhase === "setup" || currentPhase === "analyzing" },
            { id: "ready", label: "2. Video Input", done: articles.length > 0 || currentPhase === "ready" || currentPhase === "generating", active: currentPhase === "ready" },
            { id: "generating", label: "3. OpenAI Run", done: articles.length > 0, active: currentPhase === "generating" },
            { id: "complete", label: "4. Output", done: articles.length > 0, active: currentPhase === "complete" },
          ].map((step) => (
            <div
              key={step.id}
              className={`rounded-lg border px-4 py-3 text-sm ${
                step.done
                  ? "border-emerald-400/25 bg-emerald-500/10 text-emerald-100"
                  : step.active
                    ? "border-gold-400/30 bg-gold-500/10 text-gold-100"
                    : "border-warm-100/10 bg-base-900/40 text-warm-400"
              }`}
            >
              <div className="flex items-center gap-2">
                {step.done ? <CheckCircle2 className="h-4 w-4" /> : <span className="h-2 w-2 rounded-full bg-current opacity-70" />}
                {step.label}
              </div>
            </div>
          ))}
        </div>
        <div className="mt-4 grid gap-3 text-sm md:grid-cols-4">
          <div className="rounded-lg border border-warm-100/10 bg-base-900/40 px-4 py-3 text-warm-300">
            <div className="text-xs text-warm-500">Books</div>
            <div className="mt-1 text-lg text-warm-50">{books.length}</div>
          </div>
          <div className="rounded-lg border border-warm-100/10 bg-base-900/40 px-4 py-3 text-warm-300">
            <div className="text-xs text-warm-500">Videos</div>
            <div className="mt-1 text-lg text-warm-50">{videoCount}</div>
          </div>
          <div className="rounded-lg border border-warm-100/10 bg-base-900/40 px-4 py-3 text-warm-300">
            <div className="text-xs text-warm-500">Task</div>
            <div className="mt-1 truncate text-sm text-warm-50">{taskId || "Not created"}</div>
          </div>
          <div className="rounded-lg border border-warm-100/10 bg-base-900/40 px-4 py-3 text-warm-300">
            <div className="text-xs text-warm-500">Results</div>
            <div className="mt-1 text-lg text-warm-50">{articles.length}</div>
          </div>
        </div>
      </section>

      <section className="rounded-lg border border-warm-100/10 bg-base-800/70 p-6">
        <div className="mb-5 flex items-center justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-warm-50">Reference Books</h2>
            <p className="mt-1 text-sm text-warm-400">自定义上传参考书数量，支持 EPUB、PDF、Word、TXT、Markdown。</p>
          </div>
          {analysisMessage && (
            <div className="flex items-center gap-2 rounded-lg border border-emerald-400/25 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-100">
              <CheckCircle2 className="h-4 w-4" />
              {analysisMessage}
            </div>
          )}
        </div>

        <label className="block rounded-lg border border-dashed border-warm-100/15 bg-base-900/40 p-5">
          <div className="mb-3 flex items-center gap-2 text-sm text-warm-200">
            <UploadCloud className="h-4 w-4" />
            Reference Books
          </div>
          <input
            type="file"
            multiple
            accept=".txt,.md,.markdown,.docx,.pdf,.epub,application/epub+zip"
            onChange={(event) => setBooks(Array.from(event.target.files ?? []))}
            className="block w-full text-sm text-warm-300 file:mr-4 file:rounded-md file:border-0 file:bg-gold-500/15 file:px-3 file:py-2 file:text-gold-100"
          />
          {books.length > 0 && (
            <div className="mt-3 grid gap-1 text-xs text-warm-500 md:grid-cols-2">
              {books.map((book) => (
                <div key={`${book.name}-${book.size}`} className="truncate">{book.name}</div>
              ))}
            </div>
          )}
        </label>

        <button
          type="button"
          onClick={analyzeBooks}
          disabled={analyzing}
          className="mt-5 inline-flex items-center gap-2 rounded-lg bg-gold-400 px-4 py-2 text-sm font-semibold text-base-950 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {analyzing ? <Loader2 className="h-4 w-4 animate-spin" /> : <UploadCloud className="h-4 w-4" />}
          Analyze
        </button>
      </section>

      <section className="rounded-lg border border-gold-400/20 bg-base-800/70 p-6">
        <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-warm-50">原稿保序改写</h2>
            <p className="mt-1 max-w-3xl text-sm leading-6 text-warm-400">
              原稿的信息顺序、段落轨迹、跳跃、回扣、适度重复和完成度优先保留；只在这些边界内，
              按参考书的抽象画像调整句法、节奏和表达。是否提升人工特征，只认真实朱雀复检报告。
            </p>
          </div>
          <div className="rounded-lg border border-warm-100/10 bg-base-900/50 px-4 py-3 text-xs leading-5 text-warm-400">
            本地统计：0 元<br />
            OpenAI：审查 + 全稿重写
          </div>
        </div>

        <label className="block">
          <span className="mb-2 block text-sm text-warm-300">粘贴已完成文章</span>
          <textarea
            value={completedArticle}
            onChange={(event) => {
              setCompletedArticle(event.target.value);
              setRewriteResult(null);
              setBaselineReport(null);
              setCandidateReport(null);
              setZhuqueFeedback(null);
            }}
            rows={14}
            placeholder="粘贴准备做朱雀 A/B 实验的完整文章……"
            className="w-full resize-y rounded-lg border border-warm-100/10 bg-base-900/60 px-4 py-3 text-sm leading-7 text-warm-100 outline-none transition focus:border-gold-400/50"
          />
        </label>

        <div className="mt-4 flex flex-wrap items-end gap-4">
          <label>
            <span className="mb-2 block text-xs text-warm-400">迁移强度</span>
            <select
              value={transferStrength}
              onChange={(event) => setTransferStrength(event.target.value as "strong" | "maximum")}
              className="rounded-lg border border-warm-100/10 bg-base-900 px-3 py-2 text-sm text-warm-100"
            >
              <option value="maximum">最大幅度（首轮建议）</option>
              <option value="strong">强迁移</option>
            </select>
          </label>
          <div className="text-xs text-warm-500">{completedArticle.trim().length.toLocaleString()} 字符</div>
          <button
            type="button"
            onClick={rewriteCompletedArticle}
            disabled={rewriting || !analysisMessage}
            className="inline-flex items-center gap-2 rounded-lg bg-gold-400 px-4 py-2 text-sm font-semibold text-base-950 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {rewriting ? <Loader2 className="h-4 w-4 animate-spin" /> : <PlayCircle className="h-4 w-4" />}
            按原稿轨迹审查并改写
          </button>
        </div>
      </section>

      {rewriteResult && (
        <section ref={rewriteResultsRef} className="space-y-5 rounded-lg border border-emerald-400/20 bg-base-800/70 p-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <h2 className="text-lg font-semibold text-warm-50">全稿实验候选</h2>
              <p className="mt-1 text-sm text-warm-400">
                {rewriteResult.model} · {rewriteResult.style_feature_version} ·
                选中第 {rewriteResult.selected_attempt} 轮 / 共 {rewriteResult.rewrite_attempts.length} 轮 ·
                仅供朱雀黑盒复检
              </p>
            </div>
            <div className="grid grid-cols-2 gap-2 text-center text-xs">
              <div className="rounded-lg border border-warm-100/10 bg-base-900/60 px-4 py-3">
                <div className="text-warm-500">统计距离变化</div>
                <div className="mt-1 text-lg text-emerald-200">
                  {rewriteResult.distance_improvement_percent > 0 ? "↓" : "↑"}
                  {Math.abs(rewriteResult.distance_improvement_percent).toFixed(1)}%
                </div>
              </div>
              <div className="rounded-lg border border-warm-100/10 bg-base-900/60 px-4 py-3">
                <div className="text-warm-500">四元语面改动</div>
                <div className="mt-1 text-lg text-gold-100">{rewriteResult.surface_change_percent.toFixed(1)}%</div>
              </div>
            </div>
          </div>

          <details className="rounded-lg border border-warm-100/10 bg-base-900/50 p-4">
            <summary className="cursor-pointer text-sm font-medium text-warm-100">查看全稿诊断与重写计划</summary>
            <div className="mt-4 whitespace-pre-wrap text-sm leading-7 text-warm-300">{rewriteResult.audit_notes}</div>
          </details>

          <div className="overflow-x-auto rounded-lg border border-warm-100/10">
            <table className="min-w-full text-left text-xs">
              <thead className="bg-base-900/80 text-warm-400">
                <tr>
                  <th className="px-4 py-3">可观察指标</th>
                  <th className="px-4 py-3">人类参考画像</th>
                  <th className="px-4 py-3">原稿</th>
                  <th className="px-4 py-3">候选</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-warm-100/10">
                {STYLE_METRICS.map(([key, label]) => (
                  <tr key={key} className="text-warm-200">
                    <td className="px-4 py-3">{label}</td>
                    <td className="px-4 py-3">{formatMetric(rewriteResult.reference_statistics[key])}</td>
                    <td className="px-4 py-3">{formatMetric(rewriteResult.original_statistics[key])}</td>
                    <td className="px-4 py-3">{formatMetric(rewriteResult.candidate_statistics[key])}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs leading-5 text-warm-500">
            “三元字符经验困惑度”是本地统计代理，不是朱雀内部困惑度；统计距离下降也不等于朱雀必然变绿。
          </p>

          <div className="rounded-lg border border-warm-100/10 bg-base-900/60 p-5">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div className="text-sm font-medium text-warm-100">候选全文</div>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => navigator.clipboard.writeText(rewriteResult.candidate_text)}
                  className="inline-flex items-center gap-2 rounded-lg border border-warm-100/10 px-3 py-2 text-xs text-warm-200 hover:bg-warm-100/5"
                >
                  <ClipboardCopy className="h-4 w-4" />
                  复制
                </button>
                <button
                  type="button"
                  onClick={() => downloadText(rewriteResult.candidate_text, "human-reference-rewrite-candidate.txt")}
                  className="inline-flex items-center gap-2 rounded-lg border border-warm-100/10 px-3 py-2 text-xs text-warm-200 hover:bg-warm-100/5"
                >
                  <Download className="h-4 w-4" />
                  下载
                </button>
              </div>
            </div>
            <article className="max-h-[720px] overflow-y-auto whitespace-pre-wrap font-serif text-[16px] leading-8 text-warm-50">
              {rewriteResult.candidate_text}
            </article>
          </div>

          <div className="rounded-lg border border-gold-400/20 bg-gold-500/5 p-5">
            <h3 className="text-sm font-semibold text-gold-100">真实朱雀 A/B 复检</h3>
            <p className="mt-1 text-xs leading-5 text-warm-400">
              分别上传这次原稿和候选稿的朱雀打印 PDF。系统会校验报告与正文匹配，只展示首页官方人工特征及真实增量。
            </p>
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <label className="rounded-lg border border-dashed border-warm-100/15 p-4 text-xs text-warm-300">
                <span className="mb-2 block">原稿朱雀 PDF</span>
                <input type="file" accept=".pdf,application/pdf" onChange={(event) => setBaselineFile(event.target.files?.[0] ?? null)} />
              </label>
              <label className="rounded-lg border border-dashed border-warm-100/15 p-4 text-xs text-warm-300">
                <span className="mb-2 block">候选稿朱雀 PDF</span>
                <input type="file" accept=".pdf,application/pdf" onChange={(event) => setCandidateFile(event.target.files?.[0] ?? null)} />
              </label>
            </div>
            <button
              type="button"
              onClick={parseZhuquePair}
              disabled={parsingReports}
              className="mt-4 inline-flex items-center gap-2 rounded-lg border border-gold-400/30 px-4 py-2 text-sm text-gold-100 hover:bg-gold-500/10 disabled:opacity-60"
            >
              {parsingReports && <Loader2 className="h-4 w-4 animate-spin" />}
              解析并对比两份报告
            </button>
            {baselineReport && candidateReport && (
              <div className="mt-4 grid gap-3 md:grid-cols-3">
                <div className="rounded-lg bg-base-900/60 p-4">
                  <div className="text-xs text-warm-500">原稿官方人工特征</div>
                  <div className="mt-1 text-xl text-warm-100">
                    {baselineReport.official_overview_verified ? `${baselineReport.official_human_percent?.toFixed(2)}%` : "未可靠识别"}
                  </div>
                </div>
                <div className="rounded-lg bg-base-900/60 p-4">
                  <div className="text-xs text-warm-500">候选官方人工特征</div>
                  <div className="mt-1 text-xl text-warm-100">
                    {candidateReport.official_overview_verified ? `${candidateReport.official_human_percent?.toFixed(2)}%` : "未可靠识别"}
                  </div>
                </div>
                <div className="rounded-lg bg-base-900/60 p-4">
                  <div className="text-xs text-warm-500">真实变化</div>
                  <div className="mt-1 text-xl text-emerald-200">
                    {baselineReport.official_overview_verified && candidateReport.official_overview_verified
                      ? `${((candidateReport.official_human_percent ?? 0) - (baselineReport.official_human_percent ?? 0)) >= 0 ? "+" : ""}${((candidateReport.official_human_percent ?? 0) - (baselineReport.official_human_percent ?? 0)).toFixed(2)} 个百分点`
                      : "需要人工核对首页"}
                  </div>
                </div>
              </div>
            )}
            {zhuqueFeedback && (
              <div className="mt-3 flex items-center gap-2 rounded-lg border border-emerald-400/20 bg-emerald-500/10 px-4 py-3 text-xs text-emerald-100">
                <CheckCircle2 className="h-4 w-4" />
                真实 A/B 证据已回写，本任务累计 {zhuqueFeedback.verified_observation_count} 组有效观测。
              </div>
            )}
          </div>
        </section>
      )}

      <section className="rounded-lg border border-warm-100/10 bg-base-800/70 p-6">
        <div className="mb-5">
          <h2 className="text-lg font-semibold text-warm-50">从头生成</h2>
          <p className="mt-1 text-sm leading-6 text-warm-400">
            输入视频或文字素材，系统结合参考书画像生成自然的信息展开轨迹，再逐条生成文章；不会把参考书原文交给写作模型。
          </p>
        </div>

        <div className="space-y-4">
          <label className="block">
            <span className="mb-2 block text-sm text-warm-300">Video Links</span>
            <textarea
              value={videoLinks}
              onChange={(event) => setVideoLinks(event.target.value)}
              rows={6}
              placeholder="每行一个链接，也可以直接粘贴多条链接"
              className="w-full resize-y rounded-lg border border-warm-100/10 bg-base-900/60 px-4 py-3 text-sm leading-6 text-warm-100 outline-none transition focus:border-gold-400/50"
            />
          </label>

          <label className="block">
            <span className="mb-2 block text-sm text-warm-300">Transcript Override</span>
            <textarea
              value={rawVideoText}
              onChange={(event) => setRawVideoText(event.target.value)}
              rows={5}
              placeholder="可选：当视频访问受限时粘贴文字稿"
              className="w-full resize-y rounded-lg border border-warm-100/10 bg-base-900/60 px-4 py-3 text-sm leading-6 text-warm-100 outline-none transition focus:border-gold-400/50"
            />
          </label>
        </div>

        <button
          type="button"
          onClick={generateArticle}
          disabled={generating || !analysisMessage}
          className="mt-5 inline-flex items-center gap-2 rounded-lg bg-gold-400 px-4 py-2 text-sm font-semibold text-base-950 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {generating ? <Loader2 className="h-4 w-4 animate-spin" /> : <PlayCircle className="h-4 w-4" />}
          Generate Article
        </button>
      </section>

      <section ref={resultsRef} className="rounded-lg border border-warm-100/10 bg-base-800/70 p-6">
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-warm-50">Output</h2>
            <p className="mt-1 text-sm text-warm-400">
              {articles.length > 0 ? `已生成 ${articles.length} 篇文章。` : "生成完成后，文章会固定显示在这里。"}
            </p>
          </div>
          {articles.length > 0 && (
            <button
              type="button"
              onClick={() => downloadText(
                articles.map((item, index) => `# ${index + 1}. ${item.video_url}\n\n${item.article}`).join("\n\n---\n\n"),
                "testing-endpoint-articles.txt",
              )}
              className="inline-flex items-center gap-2 rounded-lg border border-gold-400/25 px-3 py-2 text-sm text-gold-100 hover:bg-gold-500/10"
            >
              <Download className="h-4 w-4" />
              Download All
            </button>
          )}
        </div>

        {generating && (
          <div className="flex items-center gap-3 rounded-lg border border-gold-400/25 bg-gold-500/10 p-4 text-sm text-gold-100">
            <Loader2 className="h-5 w-5 animate-spin" />
            OpenAI 正在按视频顺序生成文章
          </div>
        )}

        {articles.length > 0 ? (
          <div className="space-y-5">
          {articles.map((item, index) => (
            <section key={`${item.video_url}-${index}`} className="rounded-lg border border-warm-100/10 bg-base-900/70 p-7">
              <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="text-sm font-medium text-warm-100">Article {index + 1}</div>
                  <div className="mt-1 break-all text-xs text-warm-500">{item.video_url}</div>
                </div>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => copyArticle(item.article, index)}
                    className="inline-flex items-center gap-2 rounded-lg border border-warm-100/10 px-3 py-2 text-xs text-warm-200 hover:bg-warm-100/5"
                  >
                    <ClipboardCopy className="h-4 w-4" />
                    {copiedIndex === index ? "Copied" : "Copy"}
                  </button>
                  <button
                    type="button"
                    onClick={() => downloadText(item.article, `${safeFilename(item.video_url, `article-${index + 1}`)}.txt`)}
                    className="inline-flex items-center gap-2 rounded-lg border border-warm-100/10 px-3 py-2 text-xs text-warm-200 hover:bg-warm-100/5"
                  >
                    <Download className="h-4 w-4" />
                    Download
                  </button>
                </div>
              </div>
              <article className="whitespace-pre-wrap font-serif text-[17px] leading-8 text-warm-50">
                {item.article}
              </article>
            </section>
          ))}
          </div>
        ) : !generating && (
          <div className="rounded-lg border border-dashed border-warm-100/15 bg-base-900/40 p-8 text-center text-sm text-warm-500">
            No article generated yet
          </div>
        )}
      </section>
    </div>
  );
}
