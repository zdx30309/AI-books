import { useMemo, useState } from "react";
import {
  AlertTriangle,
  BookOpenText,
  ClipboardCopy,
  Loader2,
  Plus,
  Sparkles,
  Trash2,
  UploadCloud,
} from "lucide-react";
import { api } from "@/api/client";
import type {
  PublicDomainRewriteResponse,
  PublicDomainRewriteSource,
} from "@/api/types";

interface SourceDraft {
  title: string;
  text: string;
  file: File | null;
}

const emptySource = (index: number): SourceDraft => ({
  title: `公版图书 ${index}`,
  text: "",
  file: null,
});

function toPayload(item: SourceDraft): PublicDomainRewriteSource {
  return {
    title: item.title.trim(),
    text: item.text.trim(),
  };
}

function formatChars(value: string) {
  return value.trim().length.toLocaleString("zh-CN");
}

export function PublicDomainRewrite() {
  const [sources, setSources] = useState<SourceDraft[]>([emptySource(1), emptySource(2)]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<PublicDomainRewriteResponse | null>(null);

  const preparedSources = useMemo(
    () => sources.filter((item) => item.title.trim() && (item.text.trim() || item.file)),
    [sources],
  );
  const fileMode = preparedSources.length > 0 && preparedSources.every((item) => item.file);
  const mixedMode = preparedSources.some((item) => item.file) && !fileMode;
  const canSubmit = preparedSources.length >= 2 && preparedSources.length <= 3 && !mixedMode && !loading;

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
          : "请输入 2 到 3 本公版图书。",
      );
      return;
    }
    setLoading(true);
    try {
      const response = fileMode
        ? await api.generatePublicDomainRewriteFromFiles(
            preparedSources.map((item) => item.file).filter(Boolean) as File[],
            {
              public_domain_confirmed: true,
              sources: preparedSources.map((item) => ({ title: item.title.trim() })),
            },
          )
        : await api.generatePublicDomainRewrite({
            public_domain_confirmed: true,
            sources: preparedSources.map(toPayload),
          });
      setResult(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "公版图书洗稿失败");
    } finally {
      setLoading(false);
    }
  };

  const copyResult = async () => {
    if (!result?.manuscript) return;
    await navigator.clipboard.writeText(result.manuscript);
  };

  return (
    <div className="space-y-7">
      <section className="flex flex-col gap-5 border-b border-gold-500/10 pb-6 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-gold-500/20 px-4 py-2 text-xs text-gold-200">
            <BookOpenText className="h-4 w-4" />
            只处理公版 / 公共领域图书
          </div>
          <h1 className="font-serif text-4xl text-warm-50">公版图书洗稿</h1>
          <p className="mt-3 max-w-3xl text-sm leading-7 text-warm-300">
            直接输入 2 到 3 本公版书稿，系统会自动判断融合方向、重构结构和成稿表达。
          </p>
        </div>
        <button
          type="button"
          onClick={submit}
          disabled={!canSubmit}
          className="btn-gold inline-flex min-w-[180px] items-center justify-center gap-2 disabled:cursor-not-allowed disabled:opacity-45"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
          {loading ? "处理中" : "开始洗稿"}
        </button>
      </section>

      <section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(420px,0.75fr)]">
        <div className="space-y-5">
          {sources.map((source, index) => (
            <div key={index} className="card-warm p-6">
              <div className="mb-4 flex items-center justify-between gap-4">
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-gold-500/20 bg-gold-500/10 text-gold-200">
                    <BookOpenText className="h-5 w-5" />
                  </div>
                  <div>
                    <div className="text-base font-medium text-warm-100">公版图书 {index + 1}</div>
                    <div className="text-xs text-warm-500">
                      {source.file ? source.file.name : `${formatChars(source.text)} 字`}
                    </div>
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

              <input
                value={source.title}
                onChange={(event) => updateSource(index, { title: event.target.value })}
                placeholder="书名"
                className="w-full rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-sm text-warm-100 outline-none transition focus:border-gold-300/50"
              />

              <label className="mt-4 flex cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-gold-500/25 bg-base-900/45 px-4 py-5 text-center text-sm text-warm-300 transition hover:border-gold-300/45 hover:text-warm-100">
                <input
                  type="file"
                  accept=".txt,.md,.markdown,.docx,.png,.jpg,.jpeg,.webp,.bmp,image/png,image/jpeg,image/webp,image/bmp"
                  className="hidden"
                  onChange={(event) => updateSource(index, { file: event.target.files?.[0] ?? null })}
                />
                <UploadCloud className="mb-2 h-5 w-5 text-gold-200" />
                <span className="text-gold-200">{source.file ? source.file.name : "可上传 txt / md / docx / 图片"}</span>
                <span className="mt-1 text-xs text-warm-500">图片会先 OCR 成文本；也可以不上传，直接在下方粘贴全文。</span>
              </label>

              <textarea
                value={source.text}
                onChange={(event) => updateSource(index, { text: event.target.value, file: null })}
                placeholder="粘贴这本公版图书的正文"
                className="mt-4 min-h-[300px] w-full resize-y rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-sm leading-7 text-warm-100 outline-none transition focus:border-gold-300/50"
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
            添加第三本公版图书
          </button>
        </div>

        <aside className="space-y-5">
          {error && (
            <div className="rounded-lg border border-red-400/30 bg-red-500/10 p-4 text-sm leading-6 text-red-100">
              <div className="mb-1 flex items-center gap-2 font-medium">
                <AlertTriangle className="h-4 w-4" />
                处理失败
              </div>
              {error}
            </div>
          )}

          <div className="card-warm min-h-[680px] p-6">
            <div className="mb-5 flex items-center justify-between gap-4">
              <div>
                <div className="text-sm text-gold-200">输出</div>
                <h2 className="mt-2 font-serif text-2xl text-warm-50">
                  {result?.title || (loading ? "正在洗稿" : "等待执行")}
                </h2>
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

            <pre className="min-h-[560px] whitespace-pre-wrap break-words rounded-lg border border-gold-500/10 bg-base-950/60 p-5 text-sm leading-7 text-warm-100">
              {loading ? "模型正在自动融合 2-3 本公版图书并生成成稿..." : result?.manuscript || "这里会直接显示最终成稿。"}
            </pre>
          </div>
        </aside>
      </section>
    </div>
  );
}
