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

const MIN_SOURCES = 5;
const CLASSICAL_MIN_SOURCES = 1;
const MAX_SOURCES = 10;
export type PublicDomainGenerationMode = "full" | "classical_vernacular";

interface PublicDomainRewriteProps {
  fixedMode?: PublicDomainGenerationMode;
}

const emptySource = (index: number, mode: PublicDomainGenerationMode = "full"): SourceDraft => ({
  title: `${mode === "classical_vernacular" ? "公版古典原文" : "公版图书"} ${index}`,
  text: "",
  file: null,
  author: "",
  publicationYear: "",
  authorDeathYear: "",
  rightsNote: "",
  rightsEvidenceUrl: "",
});

const initialSources = (mode: PublicDomainGenerationMode) =>
  Array.from(
    { length: mode === "classical_vernacular" ? CLASSICAL_MIN_SOURCES : MIN_SOURCES },
    (_, index) => emptySource(index + 1, mode),
  );

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

export function PublicDomainRewrite({ fixedMode }: PublicDomainRewriteProps) {
  const navigate = useNavigate();
  const initialMode = fixedMode ?? "full";
  const [sources, setSources] = useState<SourceDraft[]>(() => initialSources(initialMode));
  const [selectedMode, setSelectedMode] = useState<PublicDomainGenerationMode>(initialMode);
  const generationMode = fixedMode ?? selectedMode;
  const [targetTitle, setTargetTitle] = useState("");
  const [targetDirection, setTargetDirection] = useState("");
  const [targetLength, setTargetLength] = useState("");
  const [targetChapters, setTargetChapters] = useState("");
  const [workType, setWorkType] = useState("古典小说");
  const [eraBackground, setEraBackground] = useState("保持原文时代背景，不擅自现代化");
  const [narrativePerspective, setNarrativePerspective] = useState("保持原文叙事视角");
  const [mustPreserve, setMustPreserve] = useState("全部主要人物、人物关系、时代背景、关键事件、因果、线索、结果与原有章节顺序");
  const [specialRequirements, setSpecialRequirements] = useState("不得提前续写后续剧情；不得新增重要人物、事件、感情线、反派或支线");
  const [confirmed, setConfirmed] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const preparedSources = useMemo(
    () => sources.filter((item) => item.title.trim() && (item.text.trim() || item.file)),
    [sources],
  );
  const fileMode = preparedSources.length > 0 && preparedSources.every((item) => item.file);
  const mixedMode = preparedSources.some((item) => item.file) && !fileMode;
  const classical = generationMode === "classical_vernacular";
  const minSources = classical ? CLASSICAL_MIN_SOURCES : MIN_SOURCES;
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
    setSources((items) => (items.length >= maxSources ? items : [...items, emptySource(items.length + 1, generationMode)]));
  };

  const removeSource = (index: number) => {
    setSources((items) => (items.length <= minSources ? items : items.filter((_, idx) => idx !== index)));
  };

  const selectMode = (mode: PublicDomainGenerationMode) => {
    if (fixedMode) return;
    setSelectedMode(mode);
    setError("");
    setConfirmed(false);
    setSources((items) => {
      const hasEnteredContent = items.some((item) => item.text.trim() || item.file);
      if (mode === "classical_vernacular" && !hasEnteredContent) {
        return [emptySource(1, mode)];
      }
      if (mode === "full" && items.length < MIN_SOURCES) {
        return [
          ...items,
          ...Array.from(
            { length: MIN_SOURCES - items.length },
            (_, index) => emptySource(items.length + index + 1, mode),
          ),
        ];
      }
      return items;
    });
  };

  const submit = async () => {
    setError("");
    if (!canSubmit) {
      setError(
        mixedMode
          ? "请统一使用全文粘贴或文件上传，不要在同一任务中混用。"
          : classical
            ? `必须完整提供 ${minSources}–${maxSources} 份按阅读顺序排列的公版古典原文或分卷、逐份公版依据，并勾选权利确认。`
            : `必须完整提供 ${minSources}–${maxSources} 本来源、逐本公版依据，并勾选权利确认。`,
      );
      return;
    }
    setLoading(true);
    const payloadFor = (item: SourceDraft) => toPayload({
      ...item,
      rightsNote: item.rightsNote,
    });
    const common = {
      public_domain_confirmed: true,
      generation_mode: generationMode,
      research_use_confirmed: false,
      target_title: targetTitle.trim() || null,
      target_direction: targetDirection.trim() || null,
      target_length: classical ? undefined : optionalNumber(targetLength) ?? undefined,
      target_chapters: classical ? undefined : optionalNumber(targetChapters) ?? undefined,
      output_style: classical
        ? "严格执行古典小说现代白话两轮改编模板"
        : "通俗、叙事有起伏；不模仿任何单一来源，不写论文或经典导读",
      adaptation_profile: classical
        ? {
            work_type: workType.trim() || "古典小说",
            era_background: eraBackground.trim() || "保持原文时代背景，不擅自现代化",
            narrative_perspective: narrativePerspective.trim() || "保持原文叙事视角",
            target_audience: "普通大众读者、手机阅读用户",
            vernacular_level: "通俗易读，但不过度口语化",
            adaptation_intensity: "中度改编",
            web_fiction_ratio: "约15%",
            language_tendency: "通俗自然，兼具叙事感和适度文学质感",
            must_preserve: mustPreserve.trim(),
            focus_issues: "翻译腔、对白生硬、叙述拖沓、人物扁平、场景不足、AI腔、说明腔",
            special_requirements: specialRequirements.trim(),
          }
        : undefined,
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
      setError(err instanceof Error ? err.message : classical ? "公版古文白话任务创建失败" : "公版融合长篇任务创建失败");
      setLoading(false);
    }
  };

  return (
    <div className="space-y-7">
      <section className="flex flex-col gap-5 border-b border-gold-500/10 pb-6 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-emerald-400/25 px-4 py-2 text-xs text-emerald-100">
            <ShieldCheck className="h-4 w-4" />
            {classical ? "1–10 份公版古典原文 · 原章节顺序 · 两轮白话改编" : "5–10 本来源 · 逐章串行长篇"}
          </div>
          <h1 className="font-serif text-4xl text-warm-50">{classical ? "公版古文白话" : "公版融合长篇"}</h1>
          <p className="mt-3 max-w-4xl text-sm leading-7 text-warm-300">
            {classical
              ? "按你上传的公版古典原文和分卷顺序识别原有章节。每一章先执行完整的现代白话改编 Prompt，再执行完整的深度编辑 Prompt；保留人物、关系、时代、关键事件、因果、线索、结果和原有章节顺序，不做多书故事融合，也不提前续写。"
              : "系统按来源中位篇幅规划十几万字长篇，只做一次整书规划，随后逐章串行创作并立即落盘。每章执行免费的字数、重复、现代材料和破折号检查，不启动反复付费审稿循环。"}
          </p>
        </div>
        <button
          type="button"
          onClick={submit}
          disabled={!canSubmit}
          className="btn-gold inline-flex min-w-[210px] items-center justify-center gap-2 disabled:cursor-not-allowed disabled:opacity-45"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
          {loading ? "正在创建任务" : classical ? "开始古文白话改编" : "开始长篇融合"}
        </button>
      </section>

      {!fixedMode && (
        <section className="card-warm flex flex-wrap gap-3 p-4">
          <button type="button" onClick={() => selectMode("full")} className={classical ? "btn-ghost" : "btn-gold"}>
            正式长书（5–10 本）
          </button>
          <button type="button" onClick={() => selectMode("classical_vernacular")} className={classical ? "btn-gold" : "btn-ghost"}>
            古文白话专用（1–10 份原文/分卷）
          </button>
        </section>
      )}

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
          {classical ? "作品名称（可空，默认沿用第一份书名）" : "目标书名（可空）"}
          <input value={targetTitle} onChange={(event) => setTargetTitle(event.target.value)} className="mt-2 w-full rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-warm-100 outline-none" />
        </label>
        {classical ? (
          <>
            <label className="text-sm text-warm-300">
              作品类型
              <input value={workType} onChange={(event) => setWorkType(event.target.value)} className="mt-2 w-full rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-warm-100 outline-none" />
            </label>
            <label className="text-sm text-warm-300">
              时代背景
              <input value={eraBackground} onChange={(event) => setEraBackground(event.target.value)} className="mt-2 w-full rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-warm-100 outline-none" />
            </label>
            <label className="text-sm text-warm-300">
              叙事视角
              <input value={narrativePerspective} onChange={(event) => setNarrativePerspective(event.target.value)} className="mt-2 w-full rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-warm-100 outline-none" />
            </label>
            <label className="text-sm text-warm-300 md:col-span-2">
              必须保留的内容
              <textarea value={mustPreserve} onChange={(event) => setMustPreserve(event.target.value)} className="mt-2 min-h-[96px] w-full rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-warm-100 outline-none" />
            </label>
            <label className="text-sm text-warm-300 md:col-span-2">
              特殊要求
              <textarea value={specialRequirements} onChange={(event) => setSpecialRequirements(event.target.value)} className="mt-2 min-h-[96px] w-full rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-warm-100 outline-none" />
            </label>
          </>
        ) : (
          <>
            <label className="text-sm text-warm-300">
              目标字数（可空，自动取来源中位数）
              <input type="number" min={80000} max={220000} value={targetLength} onChange={(event) => setTargetLength(event.target.value)} placeholder="例如 145000" className="mt-2 w-full rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-warm-100 outline-none" />
            </label>
            <label className="text-sm text-warm-300">
              目标章节（可空，自动匹配来源）
              <input type="number" min={12} max={36} value={targetChapters} onChange={(event) => setTargetChapters(event.target.value)} placeholder="例如 26" className="mt-2 w-full rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-warm-100 outline-none" />
            </label>
            <label className="text-sm text-warm-300 md:col-span-2 xl:col-span-1">
              融合方向（可空）
              <input value={targetDirection} onChange={(event) => setTargetDirection(event.target.value)} className="mt-2 w-full rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-warm-100 outline-none" />
            </label>
          </>
        )}
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
                  <div className="font-medium text-warm-100">{source.title || `${classical ? "公版古典原文" : "公版图书"} ${index + 1}`}</div>
                  <div className="text-xs text-warm-500">
                    {source.file ? source.file.name : `${formatChars(source.text)} 字`} · {source.rightsNote ? "已填权利依据" : "待填权利依据"}
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
              <input value={source.rightsNote} onChange={(event) => updateSource(index, { rightsNote: event.target.value })} placeholder="必填：公版/公共领域依据与核对理由" className="rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-sm text-warm-100 outline-none md:col-span-2" />
              <input value={source.rightsEvidenceUrl} onChange={(event) => updateSource(index, { rightsEvidenceUrl: event.target.value })} placeholder="权利依据链接（建议填写）" className="rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-sm text-warm-100 outline-none md:col-span-2" />
            </div>

            <label className="mt-4 flex cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-gold-500/25 bg-base-900/45 px-4 py-5 text-center text-sm text-warm-300 transition hover:border-gold-300/45">
              <input type="file" accept=".txt,.md,.markdown,.docx,.pdf,.epub,application/epub+zip,.png,.jpg,.jpeg,.webp,.bmp" className="hidden" onChange={(event) => updateSource(index, { file: event.target.files?.[0] ?? null, text: "" })} />
              <UploadCloud className="mb-2 h-5 w-5 text-gold-200" />
              <span className="text-gold-200">{source.file ? source.file.name : "上传 txt / md / docx / PDF / EPUB / 图片"}</span>
            </label>
            <textarea value={source.text} onChange={(event) => updateSource(index, { text: event.target.value, file: null })} placeholder={classical ? "也可直接粘贴完整公版古典原文；每份至少 500 字，多个分卷请按阅读顺序添加" : "也可直接粘贴完整正文；每本至少 5,000 字"} className="mt-4 min-h-[240px] w-full resize-y rounded-lg border border-gold-500/15 bg-base-900/70 px-4 py-3 text-sm leading-7 text-warm-100 outline-none" />
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
            {classical
              ? "我已逐份核对权利依据，确认 1–10 份输入均为可用于改编的公版古典原文或同一作品的分卷，并已按原作阅读顺序排列。系统保留原有章节顺序，只处理已提供正文，不续写后文。"
              : "我已逐本核对权利依据，确认 5–10 个输入均可用于本次公版融合。系统会保存来源哈希和权利清单；正文带版权限制标记时，不会调用模型。"}
          </span>
        </label>
        <div className="mt-5 grid gap-3 text-xs leading-6 text-warm-400 md:grid-cols-3">
          {classical ? (
            <>
              <div className="flex gap-2"><CheckCircle2 className="mt-1 h-4 w-4 shrink-0 text-emerald-300" />不重新设计整书结构，不合并或改排原文章节，不提前续写后续剧情。</div>
              <div className="flex gap-2"><CheckCircle2 className="mt-1 h-4 w-4 shrink-0 text-emerald-300" />每个处理段严格执行第一轮完整改编 Prompt，再执行第二轮完整编辑 Prompt。</div>
              <div className="flex gap-2"><CheckCircle2 className="mt-1 h-4 w-4 shrink-0 text-emerald-300" />每章和每轮即时落盘；本地检查不通过即停，不启动第三轮或自动付费重试。</div>
            </>
          ) : (
            <>
              <div className="flex gap-2"><CheckCircle2 className="mt-1 h-4 w-4 shrink-0 text-emerald-300" />现代整理内容只作题材线索，正文不得复制现代例子、评论或独特表达。</div>
              <div className="flex gap-2"><CheckCircle2 className="mt-1 h-4 w-4 shrink-0 text-emerald-300" />每章完成即落盘，逐章检查字数、长段摘要、跨章重复、现代材料与破折号。</div>
              <div className="flex gap-2"><CheckCircle2 className="mt-1 h-4 w-4 shrink-0 text-emerald-300" />失败候选保留在断点，不自动进入反复付费改写或模型审稿循环。</div>
            </>
          )}
        </div>
      </section>
    </div>
  );
}
