import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  AlertTriangle,
  ArrowUpRight,
  BookOpenText,
  Download,
  FileText,
  GitBranch,
  Loader2,
  Pause,
  Play,
  RefreshCcw,
  ScrollText,
  Users,
} from "lucide-react";
import { api, ApiError } from "@/api/client";
import { CreateTaskModal } from "@/components/CreateTaskModal";
import type {
  LongNovelChapter,
  LongNovelChapterPlan,
  LongNovelGenerationLog,
  LongNovelProject,
  LongNovelProjectDetailResponse,
} from "@/api/types";

type TabId =
  | "bible"
  | "family"
  | "outline"
  | "chapters"
  | "preview"
  | "conflicts"
  | "issues"
  | "logs";

const tabs: Array<{ id: TabId; label: string }> = [
  { id: "bible", label: "作品圣经" },
  { id: "family", label: "家庭关系图" },
  { id: "outline", label: "分卷大纲" },
  { id: "chapters", label: "章节列表" },
  { id: "preview", label: "正文预览" },
  { id: "conflicts", label: "矛盾线追踪" },
  { id: "issues", label: "连续性问题" },
  { id: "logs", label: "生成日志" },
];

const busyLabels: Record<string, { title: string; detail: string }> = {
  bible: {
    title: "作品圣经生成中",
    detail: "正在搭建人物、家庭关系、矛盾线和写作规则",
  },
  outline: {
    title: "分卷大纲生成中",
    detail: "正在按目标章节数拆分大纲，并均匀规划每章字数",
  },
  chapter: {
    title: "章节正文生成中",
    detail: "正在按章节计划写正文，并保持人物关系和矛盾线连续",
  },
  batch: {
    title: "批量章节生成中",
    detail: "正在逐章生成正文，期间会自动检查连续性问题",
  },
  pause: {
    title: "正在暂停",
    detail: "正在更新项目状态",
  },
  resume: {
    title: "正在继续",
    detail: "正在恢复项目状态",
  },
  extend: {
    title: "扩容计划生成中",
    detail: "只重建未完成章节计划，不改已完成正文和状态账本",
  },
};

const serverBusyActionMap: Record<string, string> = {
  generate_bible: "bible",
  generate_outline: "outline",
  generate_chapter_plan: "plan",
  generate_chapter: "chapter",
  extract_chapter_state: "chapter",
  check_continuity: "check",
  repair_chapter: "repair",
};

const serverBusyLogTtlMs = 6 * 60 * 60 * 1000;

function asText(value: unknown) {
  if (value == null) return "";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function actionError(error: unknown) {
  if (error instanceof ApiError) return `[${error.status}] ${error.detail}`;
  if (error instanceof Error) return error.message;
  return "操作失败";
}

function nextChapterNumber(detail: LongNovelProjectDetailResponse) {
  const done = new Set(detail.chapters.filter((item) => item.status === "completed").map((item) => item.chapter_number));
  for (let index = 1; index <= detail.project.target_chapters; index += 1) {
    if (!done.has(index)) return index;
  }
  return Math.min(detail.project.target_chapters, (detail.project.current_chapter || 0) + 1);
}

function formatDuration(ms: number) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  if (minutes <= 0) return `${seconds} 秒`;
  return `${minutes} 分 ${String(seconds).padStart(2, "0")} 秒`;
}

function busyInfoFor(key: string) {
  if (busyLabels[key]) return busyLabels[key];
  if (key.startsWith("plan")) {
    return {
      title: "章节细纲生成中",
      detail: "正在拆分本章场次、冲突推进和必写事件",
    };
  }
  if (key.startsWith("check")) {
    return {
      title: "连续性检查中",
      detail: "正在检查人物状态、矛盾线和情节前后的一致性",
    };
  }
  if (key.startsWith("repair")) {
    return {
      title: "章节修复中",
      detail: "正在根据连续性问题修正文稿，保持原有质量和风格",
    };
  }
  if (key.startsWith("chapter")) return busyLabels.chapter;
  return { title: `${key} 执行中`, detail: "正在等待后端返回结果" };
}

function logStartedAt(log: LongNovelGenerationLog | undefined) {
  if (!log) return null;
  const startedAt = Date.parse(log.created_at);
  return Number.isFinite(startedAt) ? startedAt : null;
}

function inferServerBusy(detail: LongNovelProjectDetailResponse) {
  const now = Date.now();
  const latestLog = detail.logs[0];
  const latestLogStartedAt = logStartedAt(latestLog);
  const activeLog =
    latestLog?.status === "started" && (latestLogStartedAt == null || now - latestLogStartedAt < serverBusyLogTtlMs)
      ? latestLog
      : undefined;
  const generatingPlan = detail.chapter_plans.find((plan) => plan.status === "generating");
  const generatingChapter = detail.chapters.find((chapter) => chapter.status === "generating");
  const projectGenerating = detail.project.status === "generating";
  if (!activeLog && !generatingPlan && !generatingChapter && !projectGenerating) return null;
  const mapped = activeLog ? serverBusyActionMap[activeLog.action] : null;
  const chapterNumber = activeLog?.chapter_number ?? generatingChapter?.chapter_number ?? generatingPlan?.chapter_number ?? null;
  const key = mapped
    ? chapterNumber && !["bible", "outline"].includes(mapped)
      ? `${mapped}-${chapterNumber}`
      : mapped
    : generatingChapter
      ? `chapter-${generatingChapter.chapter_number}`
      : generatingPlan
        ? `plan-${generatingPlan.chapter_number}`
        : "batch";
  const fallbackStartedAt = Date.parse(detail.project.updated_at);
  return {
    key,
    log: activeLog,
    startedAt: logStartedAt(activeLog) ?? (Number.isFinite(fallbackStartedAt) ? fallbackStartedAt : null),
  };
}

function ProjectList() {
  const navigate = useNavigate();
  const [items, setItems] = useState<LongNovelProject[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.listLongNovelProjects();
      setItems(res.items);
    } catch (e) {
      setError(actionError(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <>
      <section className="mb-8 flex items-start justify-between gap-4">
        <div>
          <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-gold-500/20 bg-gold-500/8 px-3 py-1 text-[11px] tracking-widest text-gold-200">
            <BookOpenText className="h-3.5 w-3.5" strokeWidth={1.6} />
            长篇小说
          </div>
          <h1 className="font-serif text-3xl text-warm-100">长篇小说工作区</h1>
          <p className="mt-3 max-w-2xl text-sm leading-7 text-warm-400">
            独立管理作品圣经、家庭关系图、长篇大纲、章节正文、矛盾线、连续性问题和生成日志。
          </p>
        </div>
        <button type="button" className="btn-gold" onClick={() => setModalOpen(true)}>
          创建长篇项目
        </button>
      </section>

      {error && <div className="mb-4 rounded-lg border border-red-500/30 bg-red-500/8 px-4 py-3 text-sm text-red-300">{error}</div>}

      {loading ? (
        <div className="flex items-center gap-2 text-warm-400">
          <Loader2 className="h-4 w-4 animate-spin" />
          读取长篇项目
        </div>
      ) : items.length === 0 ? (
        <div className="card-warm p-8 text-center">
          <p className="text-warm-300">还没有长篇项目。</p>
          <button type="button" className="btn-gold mt-5" onClick={() => setModalOpen(true)}>
            创建第一个项目
          </button>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {items.map((project) => (
            <button
              key={project.id}
              type="button"
              onClick={() => navigate(`/long-novel/${project.id}`)}
              className="card-warm p-5 text-left transition-colors hover:border-gold-400/30"
            >
              <div className="mb-3 flex items-center justify-between gap-3">
                <h2 className="font-serif text-xl text-warm-100">{project.title}</h2>
                <span className="badge-gold">{project.status}</span>
              </div>
              <p className="line-clamp-3 text-sm leading-6 text-warm-400">{project.premise}</p>
              <div className="mt-4 grid grid-cols-3 gap-3 text-xs text-warm-500">
                <span>{project.total_words} 字</span>
                <span>{project.current_chapter}/{project.target_chapters} 章</span>
                <span>{project.generation_mode}</span>
              </div>
            </button>
          ))}
        </div>
      )}

      <CreateTaskModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onCreated={() => setModalOpen(false)}
        onLongNovelCreated={(projectId) => {
          setModalOpen(false);
          navigate(`/long-novel/${projectId}`);
        }}
      />
    </>
  );
}

export function LongNovelWorkspace() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<LongNovelProjectDetailResponse | null>(null);
  const [tab, setTab] = useState<TabId>("bible");
  const [loading, setLoading] = useState(Boolean(id));
  const [busy, setBusy] = useState<string | null>(null);
  const [busyStartedAt, setBusyStartedAt] = useState<number | null>(null);
  const [busyNow, setBusyNow] = useState(Date.now());
  const [error, setError] = useState<string | null>(null);
  const [batchFrom, setBatchFrom] = useState(1);
  const [batchTo, setBatchTo] = useState(3);
  const [selectedChapter, setSelectedChapter] = useState<number | null>(null);

  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.getLongNovelProject(id);
      setDetail(res);
      setBatchFrom(nextChapterNumber(res));
      setBatchTo(Math.min(res.project.target_chapters, nextChapterNumber(res) + 2));
      setSelectedChapter((prev) => prev ?? res.chapters[0]?.chapter_number ?? null);
    } catch (e) {
      setError(actionError(e));
    } finally {
      setLoading(false);
    }
  }, [id]);

  const serverBusy = useMemo(() => (detail ? inferServerBusy(detail) : null), [detail]);
  const activeBusy = busy ?? serverBusy?.key ?? null;
  const activeBusyStartedAt = busyStartedAt ?? serverBusy?.startedAt ?? null;

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!activeBusyStartedAt) return undefined;
    const timer = window.setInterval(() => setBusyNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [activeBusyStartedAt]);

  useEffect(() => {
    if (!activeBusy || !id) return undefined;
    const timer = window.setInterval(() => {
      api
        .getLongNovelProject(id)
        .then((res) => setDetail(res))
        .catch(() => undefined);
    }, 12000);
    return () => window.clearInterval(timer);
  }, [activeBusy, id]);

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    setBusyStartedAt(Date.now());
    setBusyNow(Date.now());
    setError(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setError(actionError(e));
    } finally {
      setBusy(null);
      setBusyStartedAt(null);
    }
  };

  const selected = useMemo<LongNovelChapter | undefined>(() => {
    if (!detail) return undefined;
    return detail.chapters.find((item) => item.chapter_number === selectedChapter) ?? detail.chapters[0];
  }, [detail, selectedChapter]);

  if (!id) return <ProjectList />;

  if (loading && !detail) {
    return (
      <div className="flex items-center gap-2 text-warm-400">
        <Loader2 className="h-4 w-4 animate-spin" />
        读取长篇项目
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="card-warm p-8">
        <p className="text-red-300">{error || "长篇项目不存在"}</p>
        <button type="button" className="btn-ghost mt-4" onClick={() => navigate("/long-novel")}>
          返回列表
        </button>
      </div>
    );
  }

  const project = detail.project;
  const nextNumber = nextChapterNumber(detail);
  const isBusy = Boolean(activeBusy);
  const busyElapsed = activeBusyStartedAt ? busyNow - activeBusyStartedAt : 0;
  const busyProgress = activeBusy ? Math.min(92, 12 + Math.floor((busyElapsed / 1000) * 1.4)) : 0;
  const busyInfo = activeBusy ? busyInfoFor(activeBusy) : null;
  const latestLog = serverBusy?.log ?? detail.logs[0];
  const expansionRemaining = Math.max(0, 300000 - project.total_words);
  const expansionExtraChapters = Math.max(1, Math.ceil(expansionRemaining / 4000));
  const expansionTargetChapters = Math.min(
    80,
    Math.max(project.target_chapters, project.current_chapter + expansionExtraChapters),
  );
  const canExtendTo300k = project.target_words < 300000 || project.target_chapters < expansionTargetChapters;
  const confirmExtendTo300k = () => {
    const ok = window.confirm(
      `只扩展当前项目的未完成章节计划，不重写已完成正文。\n\n当前：${project.current_chapter} 章 / ${project.total_words} 字\n目标：300000 字，预计调整到 ${expansionTargetChapters} 章\n\n确认扩展到 30 万字？`,
    );
    if (!ok) return;
    void run("extend", () =>
      api.extendLongNovelTarget(project.id, {
        target_words: 300000,
        future_chapter_words: 4000,
        expansion_notes:
          "《嫂子的账本》专项扩容：沿用现有家长里短长篇架构，保留已完成章节，延展中段账本、养老、房产、亲戚舆论和证据链冲突，让结局延后到30万字体量。",
      }),
    );
  };

  return (
    <div className="space-y-6">
      <button type="button" onClick={() => navigate("/long-novel")} className="text-sm text-warm-400 hover:text-warm-100">
        返回长篇列表
      </button>

      <section className="card-warm p-6">
        <div className="flex flex-col gap-6 xl:flex-row xl:items-start xl:justify-between">
          <div className="min-w-0 flex-1">
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <span className="badge-gold">{project.genre_label}</span>
              <span className="rounded-full border border-gold-500/15 px-2 py-0.5 text-[10px] text-warm-400">{project.status}</span>
              {project.conflict_tags.map((tag) => (
                <span key={tag} className="rounded-full bg-base-700/70 px-2 py-0.5 text-[10px] text-warm-400">
                  {tag}
                </span>
              ))}
            </div>
            <h1 className="font-serif text-3xl text-warm-100">{project.title}</h1>
            <p className="mt-3 text-sm leading-7 text-warm-400">{project.premise}</p>
            <div className="mt-5 grid gap-3 text-sm text-warm-300 sm:grid-cols-4">
              <span>目标 {project.target_words} 字</span>
              <span>当前 {project.total_words} 字</span>
              <span>章节 {project.current_chapter}/{project.target_chapters}</span>
              <span>模式 {project.generation_mode}</span>
            </div>
            <div className="progress-track mt-4">
              <div
                className="progress-fill"
                style={{ width: `${Math.min(100, Math.round((project.total_words / Math.max(project.target_words, 1)) * 100))}%` }}
              />
            </div>
          </div>

          <div className="flex w-full flex-col gap-2 xl:w-[360px]">
            <div className="grid grid-cols-2 gap-2">
              <button type="button" className="btn-ghost" disabled={isBusy} onClick={() => run("bible", () => api.generateLongNovelBible(project.id))}>
                <ScrollText className="h-4 w-4" /> 生成圣经
              </button>
              <button type="button" className="btn-ghost" disabled={isBusy} onClick={() => run("outline", () => api.generateLongNovelOutline(project.id))}>
                <GitBranch className="h-4 w-4" /> 生成大纲
              </button>
              <button
                type="button"
                className="btn-ghost"
                disabled={isBusy || !canExtendTo300k}
                onClick={confirmExtendTo300k}
                title="只重建未完成章节计划，不重写已完成正文"
              >
                <ArrowUpRight className="h-4 w-4" /> 扩展到30万
              </button>
              <button
                type="button"
                className="btn-ghost"
                disabled={isBusy}
                onClick={() => run("chapter", () => api.generateLongNovelChapter(project.id, nextNumber))}
              >
                <FileText className="h-4 w-4" /> 下一章
              </button>
              <button
                type="button"
                className="btn-ghost"
                disabled={isBusy}
                onClick={() =>
                  run("batch", () =>
                    api.generateLongNovelBatch(project.id, {
                      from_chapter: batchFrom,
                      to_chapter: batchTo,
                      retry_failed: true,
                    }),
                  )
                }
              >
                <RefreshCcw className="h-4 w-4" /> 批量生成
              </button>
              <button type="button" className="btn-ghost" disabled={isBusy} onClick={() => run("pause", () => api.pauseLongNovelProject(project.id))}>
                <Pause className="h-4 w-4" /> 暂停
              </button>
              <button type="button" className="btn-ghost" disabled={isBusy} onClick={() => run("resume", () => api.resumeLongNovelProject(project.id))}>
                <Play className="h-4 w-4" /> 继续
              </button>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <input
                type="number"
                min={1}
                max={project.target_chapters}
                value={batchFrom}
                onChange={(e) => setBatchFrom(Number(e.target.value))}
                className="rounded-lg border border-gold-500/15 bg-base-700/60 px-3 py-2 text-sm text-warm-100 outline-none"
              />
              <input
                type="number"
                min={1}
                max={project.target_chapters}
                value={batchTo}
                onChange={(e) => setBatchTo(Number(e.target.value))}
                className="rounded-lg border border-gold-500/15 bg-base-700/60 px-3 py-2 text-sm text-warm-100 outline-none"
              />
            </div>
            <div className="grid grid-cols-3 gap-2">
              <button type="button" className="btn-ghost px-3" disabled={isBusy} onClick={() => void api.downloadLongNovelExport(project.id, "markdown")}>
                <Download className="h-4 w-4" /> MD
              </button>
              <button type="button" className="btn-ghost px-3" disabled={isBusy} onClick={() => void api.downloadLongNovelExport(project.id, "txt")}>
                <Download className="h-4 w-4" /> TXT
              </button>
              <button type="button" className="btn-ghost px-3" disabled={isBusy} onClick={() => void api.downloadLongNovelExport(project.id, "json")}>
                <Download className="h-4 w-4" /> JSON
              </button>
            </div>
            {activeBusy && (
              <div className="rounded-lg border border-gold-500/15 bg-base-700/50 px-3 py-3 text-sm text-gold-200">
                <div className="flex items-center gap-2">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  <span>{busyInfo?.title}</span>
                  <span className="ml-auto text-xs text-warm-500">{formatDuration(busyElapsed)}</span>
                </div>
                <p className="mt-2 text-xs leading-relaxed text-warm-500">{busyInfo?.detail}</p>
                <div className="progress-track mt-3">
                  <div className="progress-fill" style={{ width: `${busyProgress}%` }} />
                </div>
                {latestLog && (
                  <div className="mt-2 text-[11px] leading-relaxed text-warm-500">
                    最近记录：{latestLog.action}
                    {latestLog.chapter_number ? ` · 第 ${latestLog.chapter_number} 章` : ""} · {latestLog.status}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </section>

      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/8 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="flex flex-wrap gap-2 border-b border-gold-500/10 pb-2">
        {tabs.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => setTab(item.id)}
            className={`rounded-full px-4 py-2 text-xs tracking-widest transition-colors ${
              tab === item.id ? "bg-gold-500/15 text-warm-100" : "text-warm-500 hover:text-warm-200"
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab === "bible" && (
        <section className="grid gap-4 lg:grid-cols-2">
          <InfoBlock title="主题" value={detail.bible?.theme} />
          <InfoBlock title="核心承诺" value={detail.bible?.core_promise} />
          <InfoBlock title="情绪钩子" value={detail.bible?.emotional_hook} />
          <InfoBlock title="主冲突" value={detail.bible?.main_conflict} />
          <InfoBlock title="家庭背景" value={detail.bible?.family_background} wide />
          <InfoBlock title="结局方向" value={detail.bible?.ending_direction} wide />
        </section>
      )}

      {tab === "family" && (
        <section className="grid gap-4 xl:grid-cols-[1fr_1fr]">
          <div className="card-warm p-5">
            <h2 className="mb-4 flex items-center gap-2 font-serif text-xl text-warm-100">
              <Users className="h-5 w-5 text-gold-300" /> 人物设定
            </h2>
            <div className="space-y-3">
              {detail.members.map((member) => (
                <div key={member.id} className="rounded-lg border border-gold-500/10 bg-base-700/35 p-3">
                  <div className="font-medium text-warm-100">{member.name} · {member.family_role}</div>
                  <p className="mt-2 text-sm leading-6 text-warm-400">{member.public_image}</p>
                  <p className="mt-1 text-xs leading-5 text-warm-500">{member.private_desire}</p>
                </div>
              ))}
            </div>
          </div>
          <div className="card-warm p-5">
            <h2 className="mb-4 font-serif text-xl text-warm-100">关系与利益</h2>
            <div className="space-y-3">
              {detail.relations.map((relation) => (
                <div key={relation.id} className="rounded-lg border border-gold-500/10 bg-base-700/35 p-3 text-sm text-warm-400">
                  <div className="text-warm-100">{relation.from_member} {"->"} {relation.to_member}</div>
                  <p className="mt-1">{relation.relation_type} · {relation.hidden_conflict || relation.interest_conflict || relation.surface_relation}</p>
                </div>
              ))}
            </div>
          </div>
        </section>
      )}

      {tab === "outline" && <OutlineTable plans={detail.chapter_plans} />}

      {tab === "chapters" && (
        <ChapterTable
          plans={detail.chapter_plans}
          chapters={detail.chapters}
          selected={selectedChapter}
          disabled={isBusy}
          onSelect={setSelectedChapter}
          onGenerate={(num) => run(`chapter-${num}`, () => api.generateLongNovelChapter(project.id, num))}
          onPlan={(num) => run(`plan-${num}`, () => api.generateLongNovelChapterPlan(project.id, num))}
          onCheck={(num) => run(`check-${num}`, () => api.checkLongNovelChapter(project.id, num))}
          onRepair={(num) => run(`repair-${num}`, () => api.repairLongNovelChapter(project.id, num))}
        />
      )}

      {tab === "preview" && (
        <section className="card-warm p-6">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <h2 className="font-serif text-xl text-warm-100">{selected ? `第${selected.chapter_number}章 ${selected.title}` : "正文预览"}</h2>
            <select
              value={selected?.chapter_number ?? ""}
              onChange={(e) => setSelectedChapter(Number(e.target.value))}
              className="rounded-lg border border-gold-500/15 bg-base-700/60 px-3 py-2 text-sm text-warm-100 outline-none"
            >
              {detail.chapters.map((chapter) => (
                <option key={chapter.id} value={chapter.chapter_number}>
                  第{chapter.chapter_number}章 {chapter.title}
                </option>
              ))}
            </select>
          </div>
          <article className="whitespace-pre-wrap text-[15px] leading-8 text-warm-200">
            {selected?.content || "还没有生成正文。"}
          </article>
        </section>
      )}

      {tab === "conflicts" && (
        <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {detail.conflict_lines.map((line) => (
            <div key={line.id} className="card-warm p-5">
              <div className="mb-2 flex items-center justify-between gap-3">
                <h3 className="font-serif text-lg text-warm-100">{line.name}</h3>
                <span className="badge-gold">{line.status}</span>
              </div>
              <p className="text-sm leading-6 text-warm-400">{line.description}</p>
              <div className="mt-4 grid grid-cols-3 gap-2 text-xs text-warm-500">
                <span>引入 {line.introduced_chapter ?? "-"}</span>
                <span>爆发 {line.planned_explosion_chapter ?? "-"}</span>
                <span>解决 {line.planned_resolution_chapter ?? "-"}</span>
              </div>
            </div>
          ))}
        </section>
      )}

      {tab === "issues" && (
        <section className="space-y-3">
          {detail.issues.length === 0 ? (
            <div className="card-warm p-6 text-warm-400">暂无连续性问题。</div>
          ) : (
            detail.issues.map((issue) => (
              <div key={issue.id} className="card-warm p-4">
                <div className="mb-2 flex items-center gap-2">
                  <AlertTriangle className="h-4 w-4 text-gold-300" />
                  <span className="text-warm-100">第{issue.chapter_number}章 · {issue.severity} · {issue.issue_type}</span>
                  <span className="badge-gold">{issue.status}</span>
                </div>
                <p className="text-sm leading-6 text-warm-300">{issue.description}</p>
                {issue.suggested_fix && <p className="mt-2 text-xs leading-5 text-warm-500">{issue.suggested_fix}</p>}
              </div>
            ))
          )}
        </section>
      )}

      {tab === "logs" && (
        <section className="card-warm overflow-hidden">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-gold-500/10 text-xs tracking-widest text-warm-500">
              <tr>
                <th className="px-4 py-3">动作</th>
                <th className="px-4 py-3">章节</th>
                <th className="px-4 py-3">模型</th>
                <th className="px-4 py-3">状态</th>
                <th className="px-4 py-3">错误</th>
              </tr>
            </thead>
            <tbody>
              {detail.logs.map((log) => (
                <tr key={log.id} className="border-b border-gold-500/5 text-warm-300">
                  <td className="px-4 py-3">{log.action}</td>
                  <td className="px-4 py-3">{log.chapter_number ?? "-"}</td>
                  <td className="px-4 py-3">{log.provider}/{log.model}</td>
                  <td className="px-4 py-3">{log.status}</td>
                  <td className="px-4 py-3 text-red-300">{log.error}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}

function InfoBlock({ title, value, wide = false }: { title: string; value: unknown; wide?: boolean }) {
  return (
    <div className={`card-warm p-5 ${wide ? "lg:col-span-2" : ""}`}>
      <h2 className="mb-3 font-serif text-lg text-warm-100">{title}</h2>
      <p className="whitespace-pre-wrap text-sm leading-7 text-warm-400">{asText(value) || "待生成"}</p>
    </div>
  );
}

function OutlineTable({ plans }: { plans: LongNovelChapterPlan[] }) {
  return (
    <section className="card-warm overflow-hidden">
      <table className="w-full text-left text-sm">
        <thead className="border-b border-gold-500/10 text-xs tracking-widest text-warm-500">
          <tr>
            <th className="px-4 py-3">章</th>
            <th className="px-4 py-3">标题</th>
            <th className="px-4 py-3">目标</th>
            <th className="px-4 py-3">矛盾</th>
            <th className="px-4 py-3">钩子</th>
          </tr>
        </thead>
        <tbody>
          {plans.map((plan) => (
            <tr key={plan.id} className="border-b border-gold-500/5 text-warm-300">
              <td className="px-4 py-3">第{plan.chapter_number}章</td>
              <td className="px-4 py-3 text-warm-100">{plan.title}</td>
              <td className="px-4 py-3">{plan.target_words}</td>
              <td className="px-4 py-3">{plan.domestic_conflict}</td>
              <td className="px-4 py-3">{plan.ending_hook}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function ChapterTable({
  plans,
  chapters,
  selected,
  disabled,
  onSelect,
  onGenerate,
  onPlan,
  onCheck,
  onRepair,
}: {
  plans: LongNovelChapterPlan[];
  chapters: LongNovelChapter[];
  selected: number | null;
  disabled: boolean;
  onSelect: (num: number) => void;
  onGenerate: (num: number) => void;
  onPlan: (num: number) => void;
  onCheck: (num: number) => void;
  onRepair: (num: number) => void;
}) {
  const chapterMap = new Map(chapters.map((item) => [item.chapter_number, item]));
  const rows = plans.length > 0 ? plans : chapters.map((chapter) => ({
    id: chapter.id,
    chapter_number: chapter.chapter_number,
    title: chapter.title,
    target_words: chapter.word_count,
    domestic_conflict: "",
    emotional_hook: "",
    ending_hook: "",
  } as LongNovelChapterPlan));
  return (
    <section className="card-warm overflow-x-auto">
      <table className="w-full min-w-[980px] text-left text-sm">
        <thead className="border-b border-gold-500/10 text-xs tracking-widest text-warm-500">
          <tr>
            <th className="px-4 py-3">章</th>
            <th className="px-4 py-3">标题</th>
            <th className="px-4 py-3">字数</th>
            <th className="px-4 py-3">状态</th>
            <th className="px-4 py-3">矛盾点</th>
            <th className="px-4 py-3">结尾钩子</th>
            <th className="px-4 py-3">操作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((plan) => {
            const chapter = chapterMap.get(plan.chapter_number);
            return (
              <tr
                key={plan.id}
                className={`border-b border-gold-500/5 text-warm-300 ${selected === plan.chapter_number ? "bg-gold-500/5" : ""}`}
              >
                <td className="px-4 py-3">第{plan.chapter_number}章</td>
                <td className="px-4 py-3 text-warm-100">
                  <button type="button" onClick={() => onSelect(plan.chapter_number)} className="hover:text-gold-200">
                    {plan.title}
                  </button>
                </td>
                <td className="px-4 py-3">{chapter?.word_count ?? plan.target_words}</td>
                <td className="px-4 py-3">{chapter?.status ?? plan.status}</td>
                <td className="px-4 py-3">{plan.domestic_conflict}</td>
                <td className="px-4 py-3">{plan.ending_hook || plan.emotional_hook}</td>
                <td className="px-4 py-3">
                  <div className="flex flex-wrap gap-2">
                    <button type="button" disabled={disabled} className="rounded-full border border-gold-500/20 px-3 py-1 text-xs hover:border-gold-400/50" onClick={() => onPlan(plan.chapter_number)}>
                      计划
                    </button>
                    <button type="button" disabled={disabled} className="rounded-full border border-gold-500/20 px-3 py-1 text-xs hover:border-gold-400/50" onClick={() => onGenerate(plan.chapter_number)}>
                      生成
                    </button>
                    <button type="button" disabled={disabled || !chapter} className="rounded-full border border-gold-500/20 px-3 py-1 text-xs hover:border-gold-400/50 disabled:opacity-40" onClick={() => onCheck(plan.chapter_number)}>
                      检查
                    </button>
                    <button type="button" disabled={disabled || !chapter} className="rounded-full border border-gold-500/20 px-3 py-1 text-xs hover:border-gold-400/50 disabled:opacity-40" onClick={() => onRepair(plan.chapter_number)}>
                      修复
                    </button>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}
