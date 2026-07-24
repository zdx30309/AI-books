import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  BookOpenText,
  CheckCircle2,
  Loader2,
  Plus,
  ShieldCheck,
  Sparkles,
  Trash2,
  UploadCloud,
} from "lucide-react";
import { api } from "@/api/client";
import type { PublicDomainRewriteSource } from "@/api/types";

interface SourceDraft {
  title: string;
  text: string;
  file: File | null;
  author: string;
  publicationYear: string;
  authorDeathYear: string;
  rightsNote: string;
  rightsEvidenceUrl: string;
}

const MIN_SOURCES = 1;
const MAX_SOURCES = 10;

const emptySource = (index: number): SourceDraft => ({
  title: `公版图书 ${index}`,
  text: "",
  file: null,
  author: "",
  publicationYear: "",
  authorDeathYear: "",
  rightsNote: "",
  rightsEvidenceUrl: "",
});

const initialSources = () => Array.from({ length: MIN_SOURCES }, (_, index) => emptySource(index + 1));

function optionalNumber(value: string) {
  const parsed = Number.parseInt(value.trim(), 10);
  return Number.isFinite(parsed) ? parsed : null;
}

function toPayload(item: SourceDraft): PublicDomainRewriteSource {
  return {
    title: item.title.trim(),
    text: item.text.trim(),
    author: item.author.trim() || null,
    publication_year: optionalNumber(item.publicationYear),
    author_death_year: optionalNumber(item.authorDeathYear),
    rights_note: item.rightsNote.trim(),
    rights_evidence_url: item.rightsEvidenceUrl.trim() || null,
  };
}

function formatChars(value: string) {
  return value.replace(/\s+/g, "").length.toLocaleString("zh-CN");
}

export function PublicDomainRewrite() {
  const navigate = useNavigate();
  const [sources, setSources] = useState<SourceDraft[]>(initialSources);
  const generationMode = "legacy_simple" as const;
  const [targetTitle, setTargetTitle] = useState("");
  const [targetDirection, setTargetDirection] = useState("");
  const [targetLength, setTargetLength] = useState("");
  const [targetChapters, setTargetChapters] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const preparedSources = useMemo(
    () => sources.filter((item) => item.title.trim() && (item.text.trim() || item.file)),
    [sources],
  );
  const fileMode = preparedSources.length > 0 && preparedSources.every((item) => item.file);
  const mixedMode = preparedSources.some((item) => item.file) && !fileMode;
  const preview = false;
  const minSources = MIN_SOURCES;
  const maxSources = MAX_SOURCES;
  const metadataComplete = preparedSources.every((item) => item.rightsNote.trim().length >= 4);
  const canSubmit =
    preparedSources.length >= minSources &&
    preparedSources.length <= maxSources &&
    metadataComplete &&
    confirmed &&
    !mixedMode &&
    !loading;

  const updateSource = (index: number, patch: Partial<SourceDraft>) => {
    setSources((items) => items.map((item, idx) => (idx === index ? { ...item, ...patch } : item)));
  };

  const addSource = () => {
    setSources((items) => (items.length >= maxSources ? items : [...items, emptySource(items.length + 1)]));
  };

  const removeSource = (index: number) => {
    setSources((items) => (items.length <= minSources ? items : items.filter((_, idx) => idx !== index)));
  };

  const submit = async () => {
    setError("");
    if (!canSubmit) {
      setError(
        mixedMode
          ? "请统一使用全文粘贴或文件上传，不要在同一任务中混用。"
          : preview
            ? `必须完整提供 ${minSources}–${maxSources} 份研究材料，并确认现代内容隔离与古代原典事实边界。`
            : `必须完整提供 ${minSources}–${maxSources} 本来源、逐本公版依据，并勾选权利确认。`,
      );
      return;
    }
    setLoading(true);
    const payloadFor = (item: SourceDraft) => toPayload({
      ...item,
      rightsNote: preview
        ? "叙事化非虚构预览：仅作事实线索、匿名聚合叙事统计与来源重合排查，未主张现代整理本属于公版"
        : item.rightsNote,
    });
    const common = {
      public_domain_confirmed: !preview,
      generation_mode: generationMode,
      research_use_confirmed: preview,
      target_title: targetTitle.trim() || null,
      target_direction: targetDirection.trim() || null,
      target_length: optionalNumber(targetLength) ?? undefined,
      target_chapters: optionalNumber(targetChapters) ?? undefined,
      output_style: preview
        ? "通俗、有叙事推进和阅读气息；不模仿任何单一来源，不写论文或经典导读；不新增古代原典未支持的人物、事件、对话、心理、场景、因果或评价"
        : "通俗、叙事有起伏；不模仿任何单一来源，不写论文或经典导读",
    };
    try {
      const response = fileMode
        ? await api.generatePublicDomainRewriteFromFiles(
            preparedSources.map((item) => item.file).filter(Boolean) as File[],
            {
              ...common,
              sources: preparedSources.map((item) => {
                const payload = payloadFor(item);
                const { text: _text, ...metadata } = payload;
                return metadata;
              }),
            },
          )
        : await api.generatePublicDomainRewrite({
            ...common,
            sources: preparedSources.map(payloadFor),
          });
      navigate(`/book/${response.task_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "公版融合长篇任务创建失败");
      setLoading(false);
    }
  };

  return (
    <div className="space-y-7">
      <section className="flex flex-col gap-5 border-b border-gold-500/10 pb-6 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-emerald-400/25 px-4 py-2 text-xs text-emerald-100">
            <ShieldCheck className="h-4 w-4" />
            {preview ? "5–7 份材料 · 约一万字叙事化非虚构预览" : "仅处理 8–10 本公版 / 公共领域来源"}
          </div>
          <h1 className="font-serif text-4xl text-warm-50">公版融合长篇</h1>
          <p className="mt-3 max-w-4xl text-sm leading-7 text-warm-300">
            {preview
              ? "先生成 2–4 章、约 8,000–15,000 字的叙事化非虚构新书。现代整理本只用于发现待核验线索、计算匿名节奏参数和检查重合；正文只采用已核验古代原典事实，不新增人物、事件、对话、心理、场景、因果或评价。"
              : "系统会完整分片抽取每本书的事实证据，按参考书中位篇幅规划十几万字长篇，逐章串行写作与复审。任何来源权利不清、事实无证据、连续复述过高或终稿质量不合格，都会阻止下载。"}
          </p>
        </div>
        <button
          type="button"
          onClick={submit}
          disabled={!canSubmit}
          className="btn-gold inline-flex min-w-[210px] items-center justify-center gap-2 disabled:cursor-not-allowed disabled:opacity-45"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
          {loading ? "正在创建任务" : preview ? "生成约一万字叙事新书" : "开始长篇融合"}
        </button>
      </section>

      <section className="card-warm flex flex-wrap gap-3 p-4">
        <button type="button" onClick={() => undefined} className="btn-gold">
          正式长书（8–10 本）
        </button>
        <button type="button" onClick={() => undefined} className="btn-ghost hidden">
          叙事化非虚构预览（5–7 份 / 约一万字）
        </button>
      </section>

      {error && (
        <div className="rounded-lg border border-red-400/30 bg-red-500/10 p-4 text-sm leading-6 text-red-100">
          <div className="mb-1 flex items-center gap-2 font-medium">
            <AlertTriangle className="h-4 w-4" />
            无法创建任务
          </div>
          {error}
        </div>
      )}

      <section className="card-warm grid gap-4 p-6 md:grid-cols-2 xl:grid-cols-4">
        <label className="text-sm text-warm-300">
          目标书名（可空）
          <input value={targetTitle} onChange={(event) => setTargetTitle(event.target.value)} className="mt-2 w-full rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-warm-100 outline-none" />
        </label>
        <label className="text-sm text-warm-300">
          目标字数（可空，自动取来源中位数）
          <input type="number" min={800} max={20000} value={targetLength} onChange={(event) => setTargetLength(event.target.value)} placeholder="例如 10000" className="mt-2 w-full rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-warm-100 outline-none" />
        </label>
        <label className="text-sm text-warm-300">
          目标章节（可空，自动匹配来源）
          <input type="number" min={1} max={36} value={targetChapters} onChange={(event) => setTargetChapters(event.target.value)} placeholder="例如 1" className="mt-2 w-full rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-warm-100 outline-none" />
        </label>
        <label className="text-sm text-warm-300 md:col-span-2 xl:col-span-1">
          融合方向（可空）
          <input value={targetDirection} onChange={(event) => setTargetDirection(event.target.value)} className="mt-2 w-full rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-warm-100 outline-none" />
        </label>
      </section>

      <section className="space-y-5">
        {sources.map((source, index) => (
          <details key={index} className="card-warm group p-6" open={index === 0}>
            <summary className="flex cursor-pointer list-none items-center justify-between gap-4">
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-gold-500/20 bg-gold-500/10 text-gold-200">
                  <BookOpenText className="h-5 w-5" />
                </div>
                <div>
                  <div className="font-medium text-warm-100">{source.title || `公版图书 ${index + 1}`}</div>
                  <div className="text-xs text-warm-500">
                    {source.file ? source.file.name : `${formatChars(source.text)} 字`} · {preview ? "研究线索用途" : source.rightsNote ? "已填权利依据" : "待填权利依据"}
                  </div>
                </div>
              </div>
              {sources.length > minSources && (
                <button type="button" onClick={(event) => { event.preventDefault(); removeSource(index); }} className="btn-ghost inline-flex items-center gap-2 px-3 py-2 text-sm">
                  <Trash2 className="h-4 w-4" />移除
                </button>
              )}
            </summary>

            <div className="mt-5 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <input value={source.title} onChange={(event) => updateSource(index, { title: event.target.value })} placeholder="书名" className="rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-sm text-warm-100 outline-none" />
              <input value={source.author} onChange={(event) => updateSource(index, { author: event.target.value })} placeholder="作者（可填佚名）" className="rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-sm text-warm-100 outline-none" />
              <input type="number" value={source.publicationYear} onChange={(event) => updateSource(index, { publicationYear: event.target.value })} placeholder="首次出版年份" className="rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-sm text-warm-100 outline-none" />
              <input type="number" value={source.authorDeathYear} onChange={(event) => updateSource(index, { authorDeathYear: event.target.value })} placeholder="作者去世年份（如适用）" className="rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-sm text-warm-100 outline-none" />
              <input value={source.rightsNote} onChange={(event) => updateSource(index, { rightsNote: event.target.value })} placeholder={preview ? "试稿模式自动按研究线索用途隔离" : "必填：公版/公共领域依据与核对理由"} disabled={preview} className="rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-sm text-warm-100 outline-none disabled:opacity-55 md:col-span-2" />
              <input value={source.rightsEvidenceUrl} onChange={(event) => updateSource(index, { rightsEvidenceUrl: event.target.value })} placeholder="权利依据链接（建议填写）" className="rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-sm text-warm-100 outline-none md:col-span-2" />
            </div>

            <label className="mt-4 flex cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-gold-500/25 bg-base-900/45 px-4 py-5 text-center text-sm text-warm-300 transition hover:border-gold-300/45">
              <input type="file" accept=".txt,.md,.markdown,.docx,.pdf,.epub,application/epub+zip,.png,.jpg,.jpeg,.webp,.bmp" className="hidden" onChange={(event) => updateSource(index, { file: event.target.files?.[0] ?? null, text: "" })} />
              <UploadCloud className="mb-2 h-5 w-5 text-gold-200" />
              <span className="text-gold-200">{source.file ? source.file.name : "上传 txt / md / docx / PDF / EPUB / 图片"}</span>
            </label>
            <textarea value={source.text} onChange={(event) => updateSource(index, { text: event.target.value, file: null })} placeholder="也可直接粘贴完整正文；每本至少 5,000 字" className="mt-4 min-h-[240px] w-full resize-y rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-sm leading-7 text-warm-100 outline-none" />
          </details>
        ))}

        <button type="button" onClick={addSource} disabled={sources.length >= maxSources} className="btn-ghost inline-flex items-center gap-2 disabled:opacity-40">
          <Plus className="h-4 w-4" />添加第 {sources.length + 1} 份（最多 {maxSources} 份）
        </button>
      </section>

      <section className="card-warm p-6">
        <label className="flex cursor-pointer items-start gap-3 text-sm leading-7 text-warm-200">
          <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} className="mt-1.5 h-4 w-4 accent-amber-500" />
          <span>
            {preview
              ? "我确认现代材料只用于发现待核验线索、匿名聚合叙事统计和来源重合检查；不得复制其原文、故事、人物、桥段、现代评论或来源结构，也不把现代整理本标为公版。正文只能叙述古代原典直接支持的事实，绝不另造人物与情节。"
              : "我已逐本核对权利依据，确认 8–10 个输入均可作为公版/公共领域材料使用。系统会保存来源哈希和权利清单；依据不完整或正文带版权限制标记时，不会调用模型。"}
          </span>
        </label>
        <div className="mt-5 grid gap-3 text-xs leading-6 text-warm-400 md:grid-cols-3">
          <div className="flex gap-2"><CheckCircle2 className="mt-1 h-4 w-4 shrink-0 text-emerald-300" />人物、地点、事件与观点只来自已核验古代原典，禁止任何无证据补写。</div>
          <div className="flex gap-2"><CheckCircle2 className="mt-1 h-4 w-4 shrink-0 text-emerald-300" />逐章拦截论文腔、现代材料泄漏、事实越界、来源重合和语言硬伤。</div>
          <div className="flex gap-2"><CheckCircle2 className="mt-1 h-4 w-4 shrink-0 text-emerald-300" />全书终审未通过时保留断点并禁止下载，不把半成品标为完成。</div>
        </div>
      </section>
    </div>
  );
}
