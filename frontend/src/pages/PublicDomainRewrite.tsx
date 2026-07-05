import { useMemo, useState } from "react";
import {
  AlertTriangle,
  BookOpenText,
  ClipboardCopy,
  FileText,
  Loader2,
  Plus,
  ShieldCheck,
  Sparkles,
  Trash2,
} from "lucide-react";
import { api } from "@/api/client";
import type {
  PublicDomainRewriteResponse,
  PublicDomainRewriteSource,
} from "@/api/types";

interface SourceDraft {
  title: string;
  author: string;
  publicationYear: string;
  rightsNote: string;
  text: string;
  file: File | null;
}

const emptySource = (index: number): SourceDraft => ({
  title: index === 1 ? "来源一" : index === 2 ? "来源二" : "来源三",
  author: "",
  publicationYear: "",
  rightsNote: "",
  text: "",
  file: null,
});

const tabs = [
  { key: "manuscript", label: "成稿" },
  { key: "blueprint", label: "融合蓝图" },
  { key: "quality", label: "质检报告" },
] as const;

type TabKey = (typeof tabs)[number]["key"];

function parseYear(value: string): number | null {
  const clean = value.trim();
  if (!clean) return null;
  const parsed = Number(clean);
  if (!Number.isInteger(parsed) || parsed < 0 || parsed > 2100) return null;
  return parsed;
}

function toPayload(item: SourceDraft): PublicDomainRewriteSource {
  return {
    title: item.title.trim(),
    author: item.author.trim() || null,
    publication_year: parseYear(item.publicationYear),
    rights_note: item.rightsNote.trim() || null,
    text: item.text.trim(),
  };
}

function formatChars(value: string) {
  return value.trim().length.toLocaleString("zh-CN");
}

export function PublicDomainRewrite() {
  const [sources, setSources] = useState<SourceDraft[]>([emptySource(1), emptySource(2)]);
  const [targetTitle, setTargetTitle] = useState("");
  const [targetDirection, setTargetDirection] = useState("");
  const [targetLength, setTargetLength] = useState("4000");
  const [outputStyle, setOutputStyle] = useState("自然、连贯、有知识密度，像一篇新的完整文章");
  const [confirmed, setConfirmed] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<PublicDomainRewriteResponse | null>(null);
  const [activeTab, setActiveTab] = useState<TabKey>("manuscript");

  const preparedSources = useMemo(
    () => sources.filter((item) => item.title.trim() && (item.text.trim() || item.file)),
    [sources],
  );
  const fileMode = preparedSources.length > 0 && preparedSources.every((item) => item.file);
  const mixedMode = preparedSources.some((item) => item.file) && !fileMode;
  const canSubmit =
    preparedSources.length >= 2 && preparedSources.length <= 3 && confirmed && !loading && !mixedMode;

  const updateSource = (index: number, patch: Partial<SourceDraft>) => {
    setSources((items) => items.map((item, idx) => (idx === index ? { ...item, ...patch } : item)));
  };

  const addSource = () => {
    setSources((items) => (items.length >= 3 ? items : [...items, emptySource(items.length + 1)]));
  };

  const removeSource = (index: number) => {
    setSources((items) => (items.length <= 2 ? items : items.filter((_, idx) => idx !== index)));
  };

  const submit = async () => {
    setError("");
    setResult(null);
    if (!canSubmit) {
      setError(
        mixedMode
          ? "请统一使用文本模式或文件模式，不要在同一次生成里混合粘贴文本和上传文件。"
          : "请填写 2 到 3 本来源，并确认它们均为公版或公共领域文本。",
      );
      return;
    }
    setLoading(true);
    try {
      const commonPayload = {
        target_title: targetTitle.trim() || null,
        target_direction: targetDirection.trim() || null,
        target_length: Number(targetLength) || 4000,
        output_style: outputStyle.trim() || null,
        public_domain_confirmed: confirmed,
      };
      const response = fileMode
        ? await api.generatePublicDomainRewriteFromFiles(
            preparedSources.map((item) => item.file).filter(Boolean) as File[],
            {
              ...commonPayload,
              sources: preparedSources.map((item) => {
                const payload = toPayload(item);
                return {
                  title: payload.title,
                  author: payload.author,
                  publication_year: payload.publication_year,
                  rights_note: payload.rights_note,
                };
              }),
            },
          )
        : await api.generatePublicDomainRewrite({
            ...commonPayload,
            sources: preparedSources.map(toPayload),
          });
      setResult(response);
      setActiveTab("manuscript");
    } catch (err) {
      setError(err instanceof Error ? err.message : "公版融合改写失败");
    } finally {
      setLoading(false);
    }
  };

  const copyResult = async () => {
    if (!result?.manuscript) return;
    await navigator.clipboard.writeText(result.manuscript);
  };

  const tabContent =
    activeTab === "blueprint"
      ? result?.blueprint
      : activeTab === "quality"
        ? result?.quality_report
        : result?.manuscript;

  return (
    <div className="space-y-8">
      <section className="flex flex-col gap-5 border-b border-gold-500/10 pb-6 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-gold-500/20 px-4 py-2 text-xs text-gold-200">
            <ShieldCheck className="h-4 w-4" />
            公版图书融合改写
          </div>
          <h1 className="font-serif text-4xl text-warm-50">公版融合改写</h1>
          <p className="mt-3 max-w-3xl text-sm leading-7 text-warm-300">
            输入 2 到 3 本公版或公共领域来源，先抽象融合蓝图，再生成新的完整稿件，最后做本地重叠率和残留硬伤检查。
          </p>
        </div>
        <button
          type="button"
          onClick={submit}
          disabled={!canSubmit}
          className="btn-gold inline-flex min-w-[180px] items-center justify-center gap-2 disabled:cursor-not-allowed disabled:opacity-45"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
          {loading ? "生成中" : "生成融合稿"}
        </button>
      </section>

      <section className="grid gap-6 xl:grid-cols-[minmax(0,0.95fr)_minmax(420px,0.75fr)]">
        <div className="space-y-5">
          <div className="card-warm p-6">
            <div className="grid gap-4 lg:grid-cols-[1fr_180px]">
              <label className="block">
                <span className="mb-2 block text-sm text-warm-200">目标标题</span>
                <input
                  value={targetTitle}
                  onChange={(event) => setTargetTitle(event.target.value)}
                  placeholder="可留空，由模型按成稿拟题"
                  className="w-full rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-sm text-warm-100 outline-none transition focus:border-gold-300/50"
                />
              </label>
              <label className="block">
                <span className="mb-2 block text-sm text-warm-200">目标字数</span>
                <input
                  value={targetLength}
                  onChange={(event) => setTargetLength(event.target.value)}
                  inputMode="numeric"
                  className="w-full rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-sm text-warm-100 outline-none transition focus:border-gold-300/50"
                />
              </label>
            </div>
            <label className="mt-4 block">
              <span className="mb-2 block text-sm text-warm-200">融合方向</span>
              <textarea
                value={targetDirection}
                onChange={(event) => setTargetDirection(event.target.value)}
                placeholder="例如：从三本书中提炼关于人性、秩序、命运选择的共同问题，写成一篇新的思想故事稿"
                className="min-h-[104px] w-full resize-y rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-sm leading-7 text-warm-100 outline-none transition focus:border-gold-300/50"
              />
            </label>
            <label className="mt-4 block">
              <span className="mb-2 block text-sm text-warm-200">文风要求</span>
              <input
                value={outputStyle}
                onChange={(event) => setOutputStyle(event.target.value)}
                className="w-full rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-sm text-warm-100 outline-none transition focus:border-gold-300/50"
              />
            </label>
            <label className="mt-5 flex items-start gap-3 rounded-lg border border-gold-500/15 bg-base-900/50 p-4 text-sm leading-6 text-warm-200">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(event) => setConfirmed(event.target.checked)}
                className="mt-1 h-4 w-4 accent-gold-400"
              />
              <span>我确认输入的 2 到 3 本来源均为公版或公共领域文本，并且不会上传仍受版权限制的现代书稿。</span>
            </label>
          </div>

          {sources.map((source, index) => (
            <div key={index} className="card-warm p-6">
              <div className="mb-4 flex items-center justify-between gap-4">
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-gold-500/20 bg-gold-500/10 text-gold-200">
                    <BookOpenText className="h-5 w-5" />
                  </div>
                  <div>
                    <div className="text-base font-medium text-warm-100">来源 {index + 1}</div>
                    <div className="text-xs text-warm-500">{formatChars(source.text)} 字</div>
                  </div>
                </div>
                {sources.length > 2 && (
                  <button
                    type="button"
                    onClick={() => removeSource(index)}
                    className="btn-ghost inline-flex items-center gap-2 px-3 py-2 text-sm"
                  >
                    <Trash2 className="h-4 w-4" />
                    移除
                  </button>
                )}
              </div>
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                <input
                  value={source.title}
                  onChange={(event) => updateSource(index, { title: event.target.value })}
                  placeholder="书名"
                  className="rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-sm text-warm-100 outline-none transition focus:border-gold-300/50"
                />
                <input
                  value={source.author}
                  onChange={(event) => updateSource(index, { author: event.target.value })}
                  placeholder="作者，可选"
                  className="rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-sm text-warm-100 outline-none transition focus:border-gold-300/50"
                />
                <input
                  value={source.publicationYear}
                  onChange={(event) => updateSource(index, { publicationYear: event.target.value })}
                  placeholder="出版年份，如 1915"
                  inputMode="numeric"
                  className="rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-sm text-warm-100 outline-none transition focus:border-gold-300/50"
                />
                <input
                  value={source.rightsNote}
                  onChange={(event) => updateSource(index, { rightsNote: event.target.value })}
                  placeholder="公版/公共领域依据"
                  className="rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-sm text-warm-100 outline-none transition focus:border-gold-300/50"
                />
              </div>
              <label className="mt-4 flex cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-gold-500/25 bg-base-900/45 px-4 py-5 text-center text-sm text-warm-300 transition hover:border-gold-300/45 hover:text-warm-100">
                <input
                  type="file"
                  accept=".txt,.md,.markdown,.docx"
                  className="hidden"
                  onChange={(event) => updateSource(index, { file: event.target.files?.[0] ?? null })}
                />
                <span className="text-gold-200">{source.file ? source.file.name : "可选上传 txt / md / docx"}</span>
                <span className="mt-1 text-xs text-warm-500">
                  文件模式下请为 2 到 3 个来源都选择文件；文本模式下留空即可。
                </span>
              </label>
              <textarea
                value={source.text}
                onChange={(event) => updateSource(index, { text: event.target.value })}
                placeholder="粘贴该来源的正文或整理后的书稿文本"
                className="mt-4 min-h-[260px] w-full resize-y rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-sm leading-7 text-warm-100 outline-none transition focus:border-gold-300/50"
              />
            </div>
          ))}

          <button
            type="button"
            onClick={addSource}
            disabled={sources.length >= 3}
            className="btn-ghost inline-flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Plus className="h-4 w-4" />
            添加第三本来源
          </button>
        </div>

        <aside className="space-y-5">
          {error && (
            <div className="rounded-lg border border-red-400/30 bg-red-500/10 p-4 text-sm leading-6 text-red-100">
              <div className="mb-1 flex items-center gap-2 font-medium">
                <AlertTriangle className="h-4 w-4" />
                生成失败
              </div>
              {error}
            </div>
          )}

          <div className="card-warm min-h-[640px] p-6">
            <div className="mb-5 flex items-center justify-between gap-4">
              <div>
                <div className="flex items-center gap-2 text-sm text-gold-200">
                  <FileText className="h-4 w-4" />
                  输出
                </div>
                <h2 className="mt-2 font-serif text-2xl text-warm-50">{result?.title || "等待生成"}</h2>
              </div>
              <button
                type="button"
                onClick={copyResult}
                disabled={!result?.manuscript}
                className="btn-ghost inline-flex items-center gap-2 px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-40"
              >
                <ClipboardCopy className="h-4 w-4" />
                复制
              </button>
            </div>

            {result && (
              <div className="mb-4 flex flex-wrap gap-2">
                <span className="badge-gold">状态：{result.status}</span>
                <span className="rounded-full border border-gold-500/15 px-3 py-1 text-xs text-warm-300">
                  {result.source_count} 本来源
                </span>
                <span className="rounded-full border border-gold-500/15 px-3 py-1 text-xs text-warm-300">
                  {result.manuscript_chars.toLocaleString("zh-CN")} 字
                </span>
              </div>
            )}

            <div className="mb-5 flex flex-wrap gap-2">
              {tabs.map((tab) => (
                <button
                  key={tab.key}
                  type="button"
                  onClick={() => setActiveTab(tab.key)}
                  className={`rounded-full border px-4 py-2 text-sm transition ${
                    activeTab === tab.key
                      ? "border-gold-300/70 bg-gold-400/15 text-gold-100"
                      : "border-gold-500/15 text-warm-400 hover:border-gold-400/40 hover:text-warm-100"
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            {result?.warnings?.length ? (
              <div className="mb-5 rounded-lg border border-amber-300/20 bg-amber-400/10 p-4 text-sm leading-6 text-amber-100">
                {result.warnings.map((item) => (
                  <div key={item}>- {item}</div>
                ))}
              </div>
            ) : null}

            <pre className="min-h-[420px] whitespace-pre-wrap break-words rounded-lg border border-gold-500/10 bg-base-950/60 p-5 text-sm leading-7 text-warm-100">
              {loading
                ? "正在生成融合蓝图与成稿..."
                : tabContent || "提交后会在这里显示成稿、融合蓝图和质检报告。"}
            </pre>
          </div>
        </aside>
      </section>
    </div>
  );
}
