import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, BookOpen, CheckCircle2, Download, FileText, Loader2, Pause, Play, RefreshCcw, Upload } from "lucide-react";
import { api, ApiError } from "@/api/client";
import type { GeneralLongformAction, GeneralLongformState, LongformIssue, LongformManifest, LongformStyleReview, MaterialRole } from "@/api/generalLongformTypes";

const ACTIVE = new Set(["pending", "processing", "fetching_topics", "filtering_topics", "generating_outline", "writing_chapters", "fact_checking"]);
const PHASES: Record<string, string> = {
  needs_outline: "准备策划", awaiting_materials: "等待材料", awaiting_outline_confirmation: "等待确认大纲",
  ready_for_chapter: "等待下一章操作", writing_chapter: "当前章写作中", ready_for_assembly: "等待全书终审",
  needs_review: "有待处理项", paused: "断点已保存", completed: "全书已验收",
};
const STAGES: Record<string, string> = {
  reference_analysis: "参考机制抽象", materials: "材料识别与摘要", import_bindings: "来源绑定",
  research: "联网检索", evidence: "页面证据核验", outline: "包装与大纲", outline_revision: "大纲局部修订",
  draft: "分节起草", continue: "断点续写", polish: "整章一润", refine: "整章二润",
  acceptance: "状态抽取与验收", repair: "局部修复", final: "全书终审", format: "Word 文件装配",
};
const ROLE_LABELS: Record<MaterialRole, string> = {
  auto: "自动识别", source: "视频逐字稿 / 选题素材", outline: "大纲", chapter: "当前书稿正文",
  editorial: "来源与编辑记录", report: "检测 / 编辑反馈", instruction: "指令文件（仅归档）",
};
const TABS = ["规划", "章节", "材料", "文件", "研究", "日志"] as const;
type Tab = typeof TABS[number];
const inputClass = "w-full rounded-lg border border-gold-500/20 bg-base-800/60 px-3 py-2 text-sm text-warm-100 outline-none focus:border-gold-400/60";
const numberText = (n: number | undefined) => (n ?? 0).toLocaleString("zh-CN");
const timeText = (value?: string) => value ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) : "—";
const message = (error: unknown) => error instanceof ApiError ? error.detail : error instanceof Error ? error.message : "操作未完成，请刷新查看状态";
const printable = (value: unknown): string => typeof value === "string" ? value : JSON.stringify(value ?? "");

function requestId() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const s = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${s.slice(0, 8)}-${s.slice(8, 12)}-${s.slice(12, 16)}-${s.slice(16, 20)}-${s.slice(20)}`;
}

function Issues({ items }: { items?: LongformIssue[] }) {
  if (!items?.length) return null;
  return <ul className="mt-3 space-y-2 text-sm" aria-label="验收问题">
    {items.map((issue, i) => <li key={i} className="rounded-lg border border-amber-400/20 bg-amber-400/5 p-3 text-warm-200">
      <span className="mr-2 text-xs text-amber-300">{({ low: "轻微", medium: "一般", high: "高", severe: "严重" } as Record<string, string>)[issue?.severity] || "待核查"}</span>
      {printable(issue?.description || issue)}
      {issue?.suggested_fix && <p className="mt-1 text-xs text-warm-400">局部修订：{issue.suggested_fix}</p>}
    </li>)}
  </ul>;
}

function StyleReview({ review, title = "正反方法与语境复核" }: { review?: LongformStyleReview; title?: string }) {
  if (!review || typeof review !== "object") return null;
  const positive = Array.isArray(review.positive_application) ? review.positive_application : [];
  const negative = Array.isArray(review.negative_checks) ? review.negative_checks : [];
  const words = review.ordinary_word_review;
  return <details className="mt-3" data-testid="longform-style-review"><summary className="cursor-pointer text-xs text-gold-300">{title}</summary>
    <div className="mt-3 space-y-3 text-sm leading-7 text-warm-300">
      {positive.map((item, i) => <div key={`p-${i}`}><p className="text-gold-200">正面方法 · {item?.chapter_number ? `第${item.chapter_number}章 · ` : ""}{printable(item?.card_id)}</p><blockquote className="border-l border-gold-400/30 pl-3">{printable(item?.evidence)}</blockquote><p>{printable(item?.reason)}</p></div>)}
      {negative.map((item, i) => <div key={`n-${i}`}><p className="text-amber-200">反面诊断 · {item?.chapter_number ? `第${item.chapter_number}章 · ` : ""}{printable(item?.card_id)} · {item?.finding === "clear" ? "本次未发现该问题" : "待处理"}</p><blockquote className="border-l border-amber-400/30 pl-3">{printable(item?.evidence)}</blockquote><p>{printable(item?.reason)}</p></div>)}
      <p>普通词语境判断：{words?.mechanical_ban_applied === false ? "未采用机械禁词" : "待复核"} · {printable(words?.reason)}</p>
      {Array.isArray(words?.examples) && words.examples.map((item, i) => <p key={`w-${i}`} className="text-xs">第{printable(item?.chapter_number || "—")}章：“{printable(item?.evidence)}” · {printable(item?.reason)}</p>)}
      <p className="text-xs text-warm-400">{review.historical_reports_used_as_current_result === false ? "历史样稿报告未作为当前稿件的检测结果。" : "待核对历史样稿报告与当前稿件的边界。"}</p>
    </div>
  </details>;
}

export function GeneralLongformPanel({ taskId }: { taskId: string }) {
  const [state, setState] = useState<GeneralLongformState | null>(null);
  const [manifest, setManifest] = useState<LongformManifest | null>(null);
  const [manifestError, setManifestError] = useState("");
  const [tab, setTab] = useState<Tab>("规划");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [submitting, setSubmitting] = useState("");
  const [feedback, setFeedback] = useState("");
  const [packageIndex, setPackageIndex] = useState<number | null>(null);
  const [chapterNumber, setChapterNumber] = useState(1);
  const [retryUncertain, setRetryUncertain] = useState(false);
  const [role, setRole] = useState<MaterialRole>("auto");
  const [confirmedMaterial, setConfirmedMaterial] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [preview, setPreview] = useState<{ number: number; kind: string; content: string; version: number } | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const mounted = useRef(true);
  const actionLock = useRef(false);
  const lastRequest = useRef<{ signature: string; id: string } | null>(null);
  const previewSeq = useRef(0);

  const refresh = useCallback(async () => {
    try {
      const next = await api.getGeneralLongform(taskId);
      if (mounted.current) { setState(next); setError(""); }
    } catch (e) { if (mounted.current) setError(message(e)); }
  }, [taskId]);
  useEffect(() => {
    mounted.current = true;
    void refresh();
    const timer = window.setInterval(() => void refresh(), 4000);
    return () => { mounted.current = false; window.clearInterval(timer); };
  }, [refresh]);
  useEffect(() => {
    let cancelled = false;
    setManifest(null); setManifestError("");
    if (state?.prompt_version) {
      void api.getGeneralLongformManifest(state.prompt_version).then((value) => {
        if (value.version !== state.prompt_version || value.source_sha256 !== state.prompt_source_sha256) throw new Error("项目固定提示词版本或原文哈希不一致，请核对后继续");
        if (!cancelled) setManifest(value);
      }).catch((e) => { if (!cancelled) setManifestError(message(e)); });
    }
    return () => { cancelled = true; };
  }, [state?.prompt_version, state?.prompt_source_sha256]);

  const active = !!state && ACTIVE.has(state.task_status);
  const held = !!state?.delivery_holds?.length && state?.phase === "needs_review";
  const busy = !!submitting || (active && !held);
  const plan = state?.outline;
  const acceptedNumbers = Object.entries(state?.chapters || {}).filter(([, slot]) => slot.accepted).map(([n]) => Number(n));
  const nextNumber = (plan?.chapters || []).find((chapter) => !acceptedNumbers.includes(chapter.number))?.number;
  const title = plan?.packaging?.[plan.recommended_index]?.title || "通用长篇工作区";

  async function act(body: GeneralLongformAction) {
    if (actionLock.current || !state) return;
    actionLock.current = true;
    setSubmitting(body.action); setError(""); setNotice("");
    const payload = { outline_version: state.outline_version, ...body };
    const signature = JSON.stringify(payload);
    if (lastRequest.current?.signature !== signature) lastRequest.current = { signature, id: requestId() };
    try {
      await api.runGeneralLongform(taskId, { ...payload, request_id: lastRequest.current.id });
      lastRequest.current = null;
      setFeedback(""); setRetryUncertain(false); setPreview(null);
      setNotice("指定操作已入队；已完成节点复用断点，不自动启动后续章节。");
      await refresh();
    } catch (e) { setError(message(e)); }
    finally { actionLock.current = false; setSubmitting(""); }
  }
  async function pause() {
    setSubmitting("pause");
    try { await api.pauseGeneralLongform(taskId); setNotice("已请求暂停；当前模型调用结束后保留断点。"); await refresh(); }
    catch (e) { setError(message(e)); }
    finally { setSubmitting(""); }
  }
  async function upload() {
    if (!files.length || busy || actionLock.current) return;
    actionLock.current = true; setSubmitting("upload"); setError("");
    try {
      for (const file of files) await api.uploadGeneralLongform(taskId, file, role, confirmedMaterial);
      setFiles([]); setNotice("材料已上传并去重。点击识别材料后，系统按内容恢复进度。"); await refresh();
    } catch (e) { setError(message(e)); }
    finally { actionLock.current = false; setSubmitting(""); }
  }
  async function showChapter(number: number, kind: "accepted" | "draft") {
    const seq = ++previewSeq.current;
    setPreviewLoading(true); setChapterNumber(number);
    try {
      const content = await api.getGeneralLongformChapter(taskId, number, kind);
      if (seq === previewSeq.current) setPreview({ number, ...content });
    } catch (e) { setError(message(e)); }
    finally { if (seq === previewSeq.current) setPreviewLoading(false); }
  }
  async function download(id: string) {
    try { await api.downloadGeneralLongform(taskId, id); }
    catch (e) { setError(message(e)); }
  }

  if (!state) return <div className="card-warm p-8">
    <Link to="/bookshelf" className="btn-ghost mb-4 inline-flex"><ArrowLeft className="h-4 w-4" />返回书架</Link>
    {error ? <div role="alert"><p className="text-amber-300">{error}</p><button className="btn-ghost mt-3" onClick={() => void refresh()}>重新加载</button></div>
      : <p className="flex items-center gap-2 text-warm-300"><Loader2 className="h-4 w-4 animate-spin" />读取通用长篇工作区</p>}
  </div>;

  return <div className="space-y-5 pb-10" data-testid="general-longform-workspace">
    <div className="flex items-center justify-between gap-3">
      <Link to="/bookshelf" className="btn-ghost inline-flex items-center gap-2"><ArrowLeft className="h-4 w-4" />书架</Link>
      <button className="btn-ghost inline-flex items-center gap-2 text-xs" onClick={() => void refresh()}><RefreshCcw className="h-3.5 w-3.5" />刷新状态</button>
    </div>
    <header className="card-warm space-y-4 p-6 sm:p-8">
      <div className="flex flex-wrap items-center gap-3 text-xs tracking-widest text-gold-300"><BookOpen className="h-4 w-4" />通用长篇<span className="rounded-full border border-gold-400/25 px-3 py-1 text-warm-300">{PHASES[state.phase] || state.phase}</span></div>
      <h1 className="font-serif text-2xl leading-relaxed text-warm-100 sm:text-3xl">{title}</h1>
      <p className="text-sm leading-relaxed text-warm-400">完整指令 · 先确认大纲 · 每次一章 · 真实来源 · 双 Word 交付</p>
      <div className="grid grid-cols-2 gap-4 border-y border-gold-500/10 py-4 sm:grid-cols-4">
        {[["有效正文", `${numberText(state.total_words)} 字`], ["已验收章节", `${acceptedNumbers.length} / ${plan?.chapters?.length || "12—15"}`], ["正文目标", `${numberText(plan?.target_words || 105000)} 字`], ["大纲版本", `v${state.outline_version} · ${state.confirmed ? "已确认" : "待确认"}`]].map(([label, value]) => <div key={label}><p className="text-xs text-warm-500">{label}</p><p className="mt-1 text-sm text-warm-100">{value}</p></div>)}
      </div>
      <div aria-live="polite" className="text-sm text-warm-300">{submitting ? "正在提交操作…" : state.current_step || PHASES[state.phase]}</div>
      <div className="h-1.5 overflow-hidden rounded-full bg-base-800" role="progressbar" aria-label="工作流进度" aria-valuenow={state.progress} aria-valuemin={0} aria-valuemax={100}><div className="h-full bg-gold-400/70 transition-all" style={{ width: `${Math.min(100, Math.max(0, state.progress))}%` }} /></div>
      <div className="flex flex-wrap gap-3">
        {!plan && !state.outline_candidate && <button className="btn-gold disabled:opacity-40" disabled={busy || held} onClick={() => void act({ action: "plan" })}>研究并生成待确认大纲</button>}
        {plan && !state.confirmed && <button className="btn-gold disabled:opacity-40" disabled={busy || held} onClick={() => void act({ action: "confirm_outline", packaging_index: packageIndex ?? plan.recommended_index })}><CheckCircle2 className="h-4 w-4" />确认选中包装与大纲</button>}
        {state.confirmed && nextNumber && <button className="btn-gold disabled:opacity-40" disabled={busy || held || state.phase === "paused"} onClick={() => void act({ action: "next_chapter" })}><Play className="h-4 w-4" />{state.chapters[String(nextNumber)]?.candidate ? "继续完成" : "研究并写作"}第{nextNumber}章</button>}
        {state.confirmed && !nextNumber && <button className="btn-gold disabled:opacity-40" disabled={busy || held} onClick={() => void act({ action: "assemble" })}>全书终审与合稿 Word</button>}
        {state.can_resume && (!active || held) && <button className="btn-ghost disabled:opacity-40" disabled={busy} onClick={() => void act({ action: "resume", retry_uncertain: retryUncertain })}>恢复上次操作</button>}
        {active && !held && <button className="btn-ghost disabled:opacity-40" disabled={!!submitting} onClick={() => void pause()}><Pause className="h-4 w-4" />在断点暂停</button>}
      </div>
      {held && <label className="block text-xs leading-relaxed text-amber-300"><input className="mr-2" type="checkbox" checked={retryUncertain} onChange={(e) => setRetryUncertain(e.target.checked)} />我已核对交付待定调用，确认必要时重新请求（可能产生新费用）；已有响应 ID 时优先恢复原响应。</label>}
      {plan && !state.confirmed && <p className="text-xs text-warm-500">确认只生成确认版大纲 Word；不会同时开始第一章。</p>}
    </header>
    {(error || state.last_error || state.task_error || state.export_error) && <div role="alert" className="rounded-xl border border-amber-400/25 bg-amber-400/5 p-4 text-sm leading-relaxed text-amber-200">{error || state.last_error || state.task_error || state.export_error}</div>}
    {notice && <p role="status" className="text-sm text-gold-200">{notice}</p>}
    <nav role="tablist" aria-label="通用长篇模块" className="flex flex-wrap gap-2">
      {TABS.map((name) => <button key={name} id={`longform-tab-${name}`} role="tab" aria-selected={tab === name} aria-controls="longform-content" onClick={() => setTab(name)} className={`rounded-full border px-5 py-2 text-sm transition-colors ${tab === name ? "border-gold-400/60 bg-gold-500/10 text-warm-100" : "border-gold-500/15 text-warm-400 hover:text-warm-100"}`}>{name}</button>)}
    </nav>
    <section id="longform-content" role="tabpanel" aria-labelledby={`longform-tab-${tab}`} className="card-warm space-y-5 p-5 sm:p-7">
      {tab === "规划" && <>
        <h2 className="font-serif text-xl text-warm-100">包装与完整大纲</h2>
        {!plan ? <p className="text-sm text-warm-400">{state.outline_candidate ? "大纲候选尚有未通过项，可在下方填写局部补齐要求。" : "研究完成后，在这里查看五组书名简介与全部章节计划。已有确认大纲可从材料模块导入。"}</p> : <>
          <div className="space-y-2 text-sm leading-relaxed text-warm-300"><p>主领域：{plan.domain} · 读者：{plan.audience}</p><p>全书主问题：{plan.central_question}</p><p>叙述方式：{plan.voice}</p></div>
          <div className="grid gap-3 lg:grid-cols-2" role="radiogroup" aria-label="书名与简介方案">
            {plan.packaging.map((p, index) => <label key={index} className={`cursor-pointer rounded-xl border p-4 ${index === (packageIndex ?? plan.recommended_index) ? "border-gold-400/60 bg-gold-500/5" : "border-gold-500/15"}`}>
              <span className="text-xs text-gold-300"><input type="radio" name="longform-package" value={index} checked={index === (packageIndex ?? plan.recommended_index)} disabled={state.confirmed || busy} onChange={() => setPackageIndex(index)} className="mr-2" />方案{index + 1}{index === plan.recommended_index ? " · 推荐" : ""} · {p.direction}</span>
              <h3 className="my-2 font-serif text-lg text-warm-100">{p.title}</h3><p className="whitespace-pre-wrap text-sm leading-7 text-warm-300">{p.description}</p>
              <p className="mt-2 text-xs text-warm-500">书名 {Array.from(p.title.replace(/\s/g, "")).length} 字符 · 简介 {Array.from(p.description.replace(/\s/g, "")).length} 字符</p>
            </label>)}
          </div>
          <p className="text-sm text-warm-400">推荐理由：{plan.recommendation}</p>
          <p className="text-sm text-gold-200">计划合计 {numberText(plan.chapters.reduce((sum, c) => sum + c.target_words, 0))} 字 · {plan.chapters.length} 章</p>
          <div className="space-y-3">{plan.chapters.map((c) => <details key={c.number} className="rounded-xl border border-gold-500/15 p-4">
            <summary className="cursor-pointer text-sm text-warm-100">##第{c.number}章 {c.title}<span className="ml-3 text-xs text-warm-400">{numberText(c.target_words)} 字 · {c.sections.length} 小节 {acceptedNumbers.includes(c.number) ? "· 已验收" : ""}</span></summary>
            <div className="mt-4 space-y-3 text-sm leading-7 text-warm-300"><p>本章问题：{c.question}</p><p>新增信息：{c.new_information}</p><p>前后衔接：{c.previous_link} → {c.next_link}</p><p>不重复讲：{c.do_not_repeat?.join("；")}</p>
              {c.sections.map((s) => <div key={s.title} className="border-l border-gold-400/30 pl-4"><h4 className="text-warm-100">#{s.title} · {numberText(s.target_words)} 字</h4><p>任务：{s.task}</p><p>推导：{s.argument}</p><p>材料：{s.material}</p><p className="text-xs text-warm-500">来源：{s.source_ids?.join("、")}</p></div>)}
            </div>
          </details>)}</div>
          {!!plan.research_gaps?.length && <p className="text-sm text-amber-300">研究缺口：{plan.research_gaps.join("；")}</p>}
          <details><summary className="cursor-pointer text-sm text-warm-400">跨对话执行资料</summary><pre className="mt-3 overflow-auto whitespace-pre-wrap break-words text-xs text-warm-400">{JSON.stringify(plan.handoff, null, 2)}</pre></details>
        </>}
        {(plan || state.outline_candidate) && <div className="space-y-3 border-t border-gold-500/10 pt-4">
          <label htmlFor="longform-outline-feedback" className="block text-sm text-warm-200">局部修订大纲（保留无关章节）</label>
          <textarea id="longform-outline-feedback" className={inputClass} rows={4} value={feedback} onChange={(e) => setFeedback(e.target.value)} placeholder="指出需要修改的章、小节、来源或字数分配；已验收章节保持固定" />
          <button className="btn-ghost disabled:opacity-40" disabled={busy || held || !feedback.trim()} onClick={() => void act({ action: "revise_outline", feedback })}>修订并导出新版大纲</button>
        </div>}
      </>}
      {tab === "章节" && <>
        <h2 className="font-serif text-xl text-warm-100">串行章节与验收</h2>
        <p className="text-xs leading-relaxed text-warm-400">候选稿与验收稿分开保存；只有通过验收的当前版本计入总字数。修订整章后重新抽取状态并核对全部有效前文，不连带重写其他章。</p>
        {!Object.keys(state.chapters).length && <p className="text-sm text-warm-400">确认大纲后点击“研究并写作第1章”。</p>}
        {Object.entries(state.chapters).sort(([a], [b]) => Number(a) - Number(b)).map(([number, slot]) => <div key={number} className="rounded-xl border border-gold-500/15 p-4">
          <div className="flex flex-wrap items-center justify-between gap-3"><h3 className="text-sm text-warm-100">第{number}章 {slot.accepted?.title || slot.candidate?.title}</h3><div className="flex gap-3 text-xs">
            {slot.accepted && <button className="text-gold-300" onClick={() => void showChapter(Number(number), "accepted")}>查看验收稿 v{slot.accepted.version}</button>}
            {slot.candidate && <button className="text-warm-300" onClick={() => void showChapter(Number(number), "draft")}>查看候选稿</button>}
          </div></div>
          <p className="mt-2 text-xs text-warm-400">{slot.accepted ? `已验收 ${numberText(slot.accepted.word_count)} 字 · 状态${slot.accepted.state_persisted ? "已持久化" : "待核对"}` : "尚未验收，不计入累计正文"}{slot.candidate?.checkpoint ? ` · 当前小节 ${slot.candidate.checkpoint.section}，${numberText(slot.candidate.checkpoint.words)} / ${numberText(slot.candidate.checkpoint.target)} 字` : ""}</p>
          <Issues items={slot.candidate?.issues || slot.accepted?.issues} />
          {(slot.candidate?.audit || slot.accepted?.audit) && <details className="mt-3"><summary className="cursor-pointer text-xs text-warm-400">状态抽取 / 计划实绩对比</summary><div className="mt-3 space-y-2 text-sm leading-relaxed text-warm-300">
            <p>{(slot.candidate?.audit || slot.accepted?.audit)?.summary}</p>
            {(slot.candidate?.audit || slot.accepted?.audit)?.planned_vs_actual?.map((diff, i) => <p key={i}>{diff.fulfilled ? "✓" : "待处理"} {diff.section}：{diff.evidence}</p>)}
          </div></details>}
          <StyleReview review={(slot.candidate?.audit || slot.accepted?.audit)?.style_review} />
        </div>)}
        {previewLoading && <p className="text-sm text-warm-400">读取正文…</p>}
        {preview && <article className="rounded-xl border border-gold-400/25 p-5"><div className="mb-4 flex justify-between text-xs text-gold-300"><span>第{preview.number}章 · {preview.kind === "accepted" ? "验收正文" : "候选正文"} v{preview.version}</span><button onClick={() => setPreview(null)}>收起</button></div><div className="max-h-[65vh] overflow-y-auto whitespace-pre-wrap break-words font-serif text-base leading-8 text-warm-200">{preview.content}</div></article>}
        {!!Object.keys(state.chapters).length && <div className="space-y-3 border-t border-gold-500/10 pt-4">
          <label htmlFor="longform-revise-chapter" className="block text-sm text-warm-200">指定章局部修订</label>
          <select id="longform-revise-chapter" className={inputClass} value={chapterNumber} onChange={(e) => setChapterNumber(Number(e.target.value))}>{Object.keys(state.chapters).sort((a, b) => Number(a) - Number(b)).map((n) => <option key={n} value={n}>第{n}章</option>)}</select>
          <textarea aria-label="当前章修订要求" className={inputClass} rows={4} value={feedback} onChange={(e) => setFeedback(e.target.value)} placeholder="说明具体原句、问题和希望修订的范围；检测报告可在材料模块上传" />
          <button className="btn-ghost disabled:opacity-40" disabled={busy || held || !state.confirmed || !feedback.trim()} onClick={() => void act({ action: "revise_chapter", chapter_number: chapterNumber, feedback })}>修订、复验并导出两份 Word</button>
        </div>}
        <Issues items={state.final_audit?.issues} />
        <StyleReview review={state.final_audit?.style_review} title="全书正反方法与语境复核" />
      </>}
      {tab === "材料" && <>
        <h2 className="font-serif text-xl text-warm-100">跨对话材料与进度恢复</h2>
        <p className="text-sm leading-7 text-warm-400">支持 Word、UTF-8 TXT、Markdown，每份最多 10 MB / 25 万字符。上传后识别真实正文与配套记录，不把指令样稿当作进度。确认大纲和连续前文齐备时，识别操作会续完当前章或写下一章；全书齐备时进入终审。</p>
        <div className="grid gap-4 sm:grid-cols-2"><label className="space-y-2 text-sm text-warm-300"><span>材料角色</span><select className={inputClass} value={role} onChange={(e) => setRole(e.target.value as MaterialRole)}>{Object.entries(ROLE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label className="flex items-center gap-2 text-sm text-warm-300"><input type="checkbox" checked={confirmedMaterial} onChange={(e) => setConfirmedMaterial(e.target.checked)} />本次上传的大纲已确认，可用于正文</label></div>
        <input aria-label="选择通用长篇材料" type="file" multiple accept=".docx,.txt,.md" className="block w-full text-sm text-warm-300" disabled={busy} onChange={(e) => setFiles(Array.from(e.target.files || []))} />
        <div className="flex flex-wrap gap-3"><button className="btn-ghost disabled:opacity-40" disabled={busy || !files.length} onClick={() => void upload()}><Upload className="h-4 w-4" />上传 {files.length || ""} 份材料</button>
          <button className="btn-gold disabled:opacity-40" disabled={busy || held || !state.materials.some((m) => !m.imported)} onClick={() => void act({ action: "import_materials" })}>识别材料并继续当前阶段</button></div>
        {state.materials.map((m) => <div key={m.id} className="rounded-lg border border-gold-500/15 p-4"><p className="break-all text-sm text-warm-100">{m.filename}</p><p className="mt-1 text-xs text-warm-400">{ROLE_LABELS[m.role] || m.role} · {numberText(m.characters)} 字符 · {m.imported ? "已处理" : "待识别"}{m.confirmed ? " · 大纲已确认" : ""}</p>{m.notes && <p className="mt-2 text-xs text-warm-400">{m.notes}</p>}<p className="mt-2 break-all font-mono text-[10px] text-warm-500">SHA-256 {m.sha256}</p></div>)}
      </>}
      {tab === "文件" && <>
        <h2 className="font-serif text-xl text-warm-100">Word 交付与历史版本</h2>
        <p className="text-sm leading-7 text-warm-400">单章正文与来源/编辑记录分别交付，共用北京时间秒级时间戳。章为“##”＋标题 1，小节为“#”＋标题 2；来源标题单独为无井号的标题 1。未完成稿有明确标记，旧文件保留。</p>
        {!state.artifacts.length && <p className="text-sm text-warm-500">当前阶段尚无可下载文件。</p>}
        {[...state.artifacts].reverse().map((file) => <div key={file.id} className="flex flex-col justify-between gap-3 rounded-xl border border-gold-500/15 p-4 sm:flex-row sm:items-center"><div className="min-w-0"><p className="break-all text-sm text-warm-100">{file.filename}</p><p className="mt-1 text-xs text-warm-400">{({ outline: "大纲", chapter: "章节正文", editorial: "来源与编辑记录", book: "全书" })[file.kind]} · {(file.bytes / 1024).toFixed(1)} KB · {timeText(file.created_at)}</p><details className="mt-1 text-[10px] text-warm-500"><summary className="cursor-pointer">完整性校验</summary><p className="break-all font-mono">{file.sha256}</p></details></div><button aria-label={`下载 ${file.filename}`} className="btn-ghost shrink-0" onClick={() => void download(file.id)}><Download className="h-4 w-4" />下载</button></div>)}
      </>}
      {tab === "研究" && <>
        <h2 className="font-serif text-xl text-warm-100">真实检索与来源证据</h2>
        <p className="text-sm leading-7 text-warm-400">这里仅展示实际搜索、读取页面后形成的摘要与适用范围。原始参考文本只在摄取/抽象阶段使用，不进入正文生成。</p>
        {!Object.keys(state.sources).length && <p className="text-sm text-warm-500">尚未形成通过核验的来源。搜索或页面读取失败会保留错误并停止推进。</p>}
        {Object.values(state.sources).map((s) => <details key={s.id} className="rounded-xl border border-gold-500/15 p-4"><summary className="cursor-pointer text-sm text-warm-100">{s.title || s.bibliography} <span className="text-xs text-gold-300">· 已读取并抽象</span></summary><div className="mt-3 space-y-3 text-sm leading-7 text-warm-300"><p>{s.bibliography}</p>{/^https?:\/\//i.test(s.url) && <a href={s.url} target="_blank" rel="noopener noreferrer" className="block break-all text-gold-300 underline">打开原始来源</a>}<p>{s.approved_summary}</p><p>支撑观点：{s.supported_claims?.map(printable).join("；")}</p><p>范围：{s.read_scope}</p><p>局限：{s.limitations?.map(printable).join("；") || "以记录的读取范围为准"}</p><p className="text-xs text-warm-500">{s.id} · {timeText(s.retrieved_at)}</p></div></details>)}
      </>}
      {tab === "日志" && <>
        <h2 className="font-serif text-xl text-warm-100">调用记录与原文保真</h2>
        <div className="space-y-2 rounded-xl border border-gold-400/20 p-4 text-sm text-warm-300"><p className="flex items-center gap-2"><FileText className="h-4 w-4 text-gold-300" />指令版本 {state.prompt_version}</p>
          {manifest ? <p>{manifest.paragraph_count} 个原段落 · {manifest.sections.length} 个完整分段 · 五份附录完整保留</p> : <p className="text-amber-200">{manifestError || "正在读取本项目固定版本清单…"}</p>}
          <p className="break-all font-mono text-xs text-warm-500">原 Word SHA-256 {state.prompt_source_sha256}</p>
          {manifest?.default_version && manifest.default_version !== state.prompt_version && <p className="text-xs text-amber-200">本项目继续使用原固定版本；新项目默认 {manifest.default_version}，已有正文与断点未自动迁移。</p>}
          {!!manifest?.reference_examples?.length && <div className="space-y-3 border-t border-gold-400/15 pt-3" data-testid="longform-reference-roles">
            {manifest.reference_examples.map((example) => <div key={example.role}><p className={example.role === "positive" ? "text-gold-200" : "text-amber-200"}>{example.role === "positive" ? "正面样稿 · 学习方法" : "反面样稿 · 仅用于诊断"}：《{example.title}》</p>
              <p className="text-xs">附件历史报告：{numberText(example.historical_report.manuscript_words)} 字 · 风险 {example.historical_report.overall_risk_percent}% · {example.historical_report.problem_categories} 类问题；不是当前书稿的检测结果。</p></div>)}
            <p className="text-xs text-warm-400">原文只进入参考抽象。普通词按句子功能与信息推进判断，不机械禁词；历史检测分数不作为通过率承诺。</p>
          </div>}
          {manifest && <details><summary className="cursor-pointer text-xs text-gold-300">查看全部分段与链路位置</summary><div className="mt-3 space-y-2">{manifest.sections.map((s) => <p className="break-words text-xs" key={s.id}>{s.file} · {s.paragraph_count} 段 · {s.role === "reference_only" ? "仅抽象" : "阶段原文"}：{s.stages.map((stage) => STAGES[stage] || stage).join(" / ")}</p>)}</div></details>}
        </div>
        <p className="text-xs leading-relaxed text-warm-400">最近 200 次调用。费用按已配置模型 token 费率估算，未计搜索工具等附加费用；未配置费率不等同于免费。交付待定调用需核对后恢复，不自动重复提交。</p>
        <div className="overflow-x-auto"><table className="w-full min-w-[640px] text-left text-xs"><thead className="border-b border-gold-500/15 text-warm-400"><tr>{["北京时间", "动作", "模型", "状态", "估算费用", "错误"].map((head) => <th key={head} className="p-2 font-normal">{head}</th>)}</tr></thead><tbody className="text-warm-300">{[...state.call_logs].reverse().map((log, i) => <tr key={`${log.started_at}-${i}`} className="border-b border-gold-500/10"><td className="p-2">{timeText(log.started_at)}</td><td className="p-2">{STAGES[log.action] || log.action}</td><td className="p-2">{log.provider} / {log.model}</td><td className="p-2">{({ completed: "已返回", started: "进行中", failed: "失败", uncertain: "交付待核对" } as Record<string, string>)[log.status] || log.status}</td><td className="p-2">{log.estimated_cost == null ? "未配置费率 / 待核算" : log.estimated_cost.toFixed(6)}</td><td className="max-w-xs break-words p-2 text-amber-200">{log.error || "—"}</td></tr>)}</tbody></table></div>
      </>}
    </section>
  </div>;
}
