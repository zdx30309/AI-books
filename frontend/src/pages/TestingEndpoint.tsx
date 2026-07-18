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
import type { TestingGeneratedArticle } from "@/api/types";

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
  const [error, setError] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);
  const resultsRef = useRef<HTMLDivElement | null>(null);
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

  const analyzeBooks = async () => {
    if (!books.length) {
      setError("请至少上传 1 本参考书。");
      return;
    }
    setError("");
    setAnalysisMessage("");
    setArticles([]);
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
            <p className="mt-1 text-sm text-warm-400">OpenAI ChatGPT API 专用测试链路</p>
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

      <section className="rounded-lg border border-warm-100/10 bg-base-800/70 p-6">
        <div className="mb-5">
          <h2 className="text-lg font-semibold text-warm-50">Video Article</h2>
          <p className="mt-1 text-sm text-warm-400">可一次粘贴多个视频链接，系统会逐条识别并分别生成文章。</p>
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
