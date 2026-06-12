import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  AlertTriangle,
  BookOpenText,
  CheckCircle2,
  Database,
  Download,
  FileCheck2,
  GitBranch,
  Layers3,
  Loader2,
  Play,
  RefreshCcw,
  ScrollText,
  SearchCheck,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { api, ApiError } from "@/api/client";
import type {
  EpicDashboardResponse,
  EpicNovelCreateRequest,
  EpicNovelProject,
  EpicNovelProjectDetailResponse,
  EpicReferenceExtractionControl,
  EpicReferenceExtractionCostEstimate,
} from "@/api/types";

type TabId = "foundation" | "reference" | "chapters" | "qa" | "logs";

type CreateFormState = {
  title: string;
  premise: string;
  targetWords: string;
  targetVolumes: string;
  targetChapters: string;
  generationMode: "cheap" | "standard" | "high_quality";
};

const tabs: Array<{ id: TabId; label: string }> = [
  { id: "foundation", label: "基建流水线" },
  { id: "reference", label: "参考书库" },
  { id: "chapters", label: "章节生产" },
  { id: "qa", label: "质检验收" },
  { id: "logs", label: "生成日志" },
];

const initialForm: CreateFormState = {
  title: "长篇小说项目",
  premise: "",
  targetWords: "3000000",
  targetVolumes: "12",
  targetChapters: "700",
  generationMode: "standard",
};

const statusLabels: Record<string, string> = {
  accepted: "已验收",
  active: "进行中",
  blocked: "已阻断",
  complete: "已完成",
  completed: "已完成",
  detected: "已检测",
  downloading: "下载中",
  downloaded: "已下载",
  draft: "草稿",
  failed: "失败",
  fallback: "兜底稿",
  found: "已发现",
  generating: "生成中",
  imported: "已导入",
  indexed: "已索引",
  manual_required: "需手动处理",
  missing: "缺失",
  need_repair: "待修复",
  need_review: "待复核",
  paused: "已暂停",
  pending: "等待中",
  planned: "已规划",
  ready: "已就绪",
  repaired: "已修复",
  running: "运行中",
  skipped: "已跳过",
  stopped: "已停止",
  true: "已导入",
  false: "未导入",
};

const actionLabels: Record<string, string> = {
  bible_coverage_ci: "圣经覆盖检查",
  bible_export: "导出设定圣经",
  bible_manifest: "生成圣经目录",
  bible_section: "生成圣经分节",
  bible_section_generate: "生成圣经分节",
  build_reference_profile: "构建参考机制画像",
  chapter_acceptance: "章节验收",
  chapter_draft_writer: "章节草稿写作",
  compile_chapter_contracts: "编译章节合同",
  create_project: "创建项目",
  execute_chapter: "执行章节",
  forge_power_system: "铸造战力体系",
  generate_arc_ledgers: "生成弧线台账",
  generate_batch: "批量生成章节",
  generate_ensemble_cast: "生成群像角色",
  generate_global_spine: "生成全局主线",
  generate_volume_contracts: "生成分卷合同",
  generate_world_atlas: "生成世界图谱",
  generate_world_bible: "生成世界圣经",
  novel_export: "导出正文",
  outline_ci: "大纲一致性检查",
  outline_repair: "修复大纲",
  reference_import: "导入参考书",
  reference_scan: "扫描下载目录",
  retrieve_context: "编译上下文包",
  run_worldview_tournament: "世界观竞选",
  state_extract: "抽取状态",
  world_bible: "生成世界圣经",
  world_atlas: "生成世界图谱",
  worldview_tournament: "世界观竞选",
};

const generationModeLabels: Record<CreateFormState["generationMode"], string> = {
  cheap: "省成本模式",
  standard: "标准模式",
  high_quality: "高质量模式",
};

function statusLabel(status: string | null | undefined) {
  if (!status) return "-";
  return statusLabels[String(status).toLowerCase()] ?? "未知状态";
}

function actionLabel(action: string | null | undefined) {
  if (!action) return "-";
  return actionLabels[String(action).toLowerCase()] ?? "未登记动作";
}

function readyLabel(value: boolean) {
  return value ? "已就绪" : "缺失";
}

function actionError(error: unknown) {
  if (error instanceof ApiError) return `操作失败（${error.status}），请查看后端日志。`;
  if (error instanceof Error) return "操作失败，请查看浏览器控制台或后端日志。";
  return "操作失败，请稍后重试。";
}

function formatNumber(value: number | null | undefined) {
  return new Intl.NumberFormat("zh-CN").format(value ?? 0);
}

function pct(value: number | null | undefined) {
  return `${Math.round((value ?? 0) * 100)}%`;
}

function asDate(value: string | null | undefined) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN");
}

function nextChapterNumber(detail: EpicNovelProjectDetailResponse) {
  const accepted = new Set(
    detail.chapters
      .filter((chapter) => chapter.status === "accepted")
      .map((chapter) => chapter.chapter_number),
  );
  for (let index = 1; index <= detail.project.target_chapters; index += 1) {
    if (!accepted.has(index)) return index;
  }
  return detail.project.target_chapters;
}

function modelSummaryLabel(project: EpicNovelProject) {
  const configured = [project.editor_model, project.author_model, project.cheap_model].filter(Boolean).length;
  if (!configured) return "";
  if (configured >= 3) return "编辑模型、作者模型、成本控制模型已配置";
  return `${configured} 路模型已配置`;
}

function modelDisplayName(raw: string | null | undefined) {
  const value = String(raw ?? "").toLowerCase();
  if (!value) return "-";
  if (value.includes("deepseek-v4-pro")) return "深度求索四代专业版";
  if (value.includes("deepseek")) return "深度求索模型";
  if (value.includes("gpt-5.5")) return "第五代增强模型";
  if (value.includes("openai")) return "开放智能模型";
  return "已配置模型";
}

function countRows(detail: EpicNovelProjectDetailResponse | null, key: keyof EpicNovelProjectDetailResponse) {
  const value = detail?.[key];
  return Array.isArray(value) ? value.length : value ? 1 : 0;
}

function StatusPill({ status }: { status: string }) {
  const ok = ["accepted", "completed", "active", "indexed"].includes(status);
  const warn = ["need_review", "failed", "manual_required"].includes(status);
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] ${
        ok
          ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-200"
          : warn
            ? "border-red-400/30 bg-red-400/10 text-red-200"
            : "border-gold-500/20 bg-gold-500/10 text-gold-200"
      }`}
    >
      {statusLabel(status)}
    </span>
  );
}

function StatCard({ label, value, icon: Icon }: { label: string; value: string; icon: typeof BookOpenText }) {
  return (
    <div className="card-warm rounded-lg px-4 py-4">
      <div className="mb-3 flex items-center justify-between gap-3 text-warm-400">
        <span className="text-xs uppercase">{label}</span>
        <Icon className="h-4 w-4 text-gold-300" strokeWidth={1.6} />
      </div>
      <div className="font-serif text-2xl text-warm-100">{value}</div>
    </div>
  );
}

function ActionButton({
  label,
  busy,
  onClick,
  icon: Icon = Play,
}: {
  label: string;
  busy: boolean;
  onClick: () => void;
  icon?: typeof Play;
}) {
  return (
    <button
      type="button"
      className="btn-ghost min-h-[42px] rounded-lg px-4 py-2 text-left"
      disabled={busy}
      onClick={onClick}
    >
      {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Icon className="h-4 w-4" strokeWidth={1.7} />}
      <span className="truncate">{label}</span>
    </button>
  );
}

function ProjectList() {
  const navigate = useNavigate();
  const [items, setItems] = useState<EpicNovelProject[]>([]);
  const [form, setForm] = useState<CreateFormState>(initialForm);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.listEpicNovelProjects();
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

  const createProject = async (event: FormEvent) => {
    event.preventDefault();
    if (!form.premise.trim()) {
      setError("请先填写故事前提。");
      return;
    }
    setCreating(true);
    setError(null);
    const body: EpicNovelCreateRequest = {
      title: form.title.trim() || "长篇小说项目",
      premise: form.premise.trim(),
      target_words: Number(form.targetWords) || 3_000_000,
      target_volumes: Number(form.targetVolumes) || 12,
      target_chapters: Number(form.targetChapters) || 700,
      bible_target_words: 100_000,
      generation_mode: form.generationMode,
    };
    try {
      const project = await api.createEpicNovelProject(body);
      navigate(`/epic-novel/${project.id}`);
    } catch (e) {
      setError(actionError(e));
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="space-y-8">
      <section className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="mb-3 inline-flex items-center gap-2 rounded-lg border border-gold-500/20 bg-gold-500/8 px-3 py-1 text-[11px] text-gold-200">
            <BookOpenText className="h-3.5 w-3.5" strokeWidth={1.6} />
            长篇小说工业引擎
          </div>
          <h1 className="font-serif text-3xl text-warm-100">长篇小说生产控制台</h1>
        </div>
        <button type="button" className="btn-ghost rounded-lg" onClick={() => void load()}>
          <RefreshCcw className="h-4 w-4" strokeWidth={1.7} />
          刷新
        </button>
      </section>

      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/8 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_420px]">
        <section className="space-y-3">
          {loading ? (
            <div className="flex items-center gap-2 text-warm-400">
              <Loader2 className="h-4 w-4 animate-spin" />
              正在加载项目
            </div>
          ) : items.length === 0 ? (
            <div className="card-warm rounded-lg px-5 py-6 text-sm text-warm-400">
              还没有长篇项目。
            </div>
          ) : (
            items.map((project) => (
              <button
                key={project.id}
                type="button"
                onClick={() => navigate(`/epic-novel/${project.id}`)}
                className="card-warm block w-full rounded-lg px-5 py-4 text-left transition hover:border-gold-400/30"
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="font-serif text-xl text-warm-100">{project.title}</div>
                    <div className="mt-2 text-xs text-warm-500">
                      {formatNumber(project.total_words)} 字 / 当前第 {project.current_chapter} 章
                    </div>
                  </div>
                  <StatusPill status={project.status} />
                </div>
                <div className="mt-3 line-clamp-2 text-sm leading-6 text-warm-400">{project.premise}</div>
              </button>
            ))
          )}
        </section>

        <form onSubmit={createProject} className="card-warm rounded-lg px-5 py-5">
          <div className="mb-4 flex items-center gap-2 text-warm-100">
            <Sparkles className="h-4 w-4 text-gold-300" strokeWidth={1.7} />
            <h2 className="font-serif text-xl">创建项目</h2>
          </div>
          <label className="mb-3 block text-sm text-warm-300">
            标题
            <input
              className="auth-input mt-2"
              value={form.title}
              onChange={(event) => setForm((prev) => ({ ...prev, title: event.target.value }))}
            />
          </label>
          <label className="mb-3 block text-sm text-warm-300">
            故事前提
            <textarea
              className="auth-input mt-2 min-h-[150px] resize-y"
              value={form.premise}
              onChange={(event) => setForm((prev) => ({ ...prev, premise: event.target.value }))}
            />
          </label>
          <div className="grid gap-3 sm:grid-cols-3">
            <label className="block text-sm text-warm-300">
              目标字数
              <input
                className="auth-input mt-2"
                value={form.targetWords}
                onChange={(event) => setForm((prev) => ({ ...prev, targetWords: event.target.value }))}
              />
            </label>
            <label className="block text-sm text-warm-300">
              分卷数
              <input
                className="auth-input mt-2"
                value={form.targetVolumes}
                onChange={(event) => setForm((prev) => ({ ...prev, targetVolumes: event.target.value }))}
              />
            </label>
            <label className="block text-sm text-warm-300">
              章节数
              <input
                className="auth-input mt-2"
                value={form.targetChapters}
                onChange={(event) => setForm((prev) => ({ ...prev, targetChapters: event.target.value }))}
              />
            </label>
          </div>
          <label className="mt-3 block text-sm text-warm-300">
            生成模式
            <select
              className="auth-input mt-2"
              value={form.generationMode}
              onChange={(event) =>
                setForm((prev) => ({
                  ...prev,
                  generationMode: event.target.value as CreateFormState["generationMode"],
                }))
              }
            >
              <option value="cheap">{generationModeLabels.cheap}</option>
              <option value="standard">{generationModeLabels.standard}</option>
              <option value="high_quality">{generationModeLabels.high_quality}</option>
            </select>
          </label>
          <button type="submit" className="btn-gold mt-5 w-full rounded-lg" disabled={creating}>
            {creating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            创建项目
          </button>
        </form>
      </div>
    </div>
  );
}

function ProjectDetail({ id }: { id: string }) {
  const navigate = useNavigate();
  const [detail, setDetail] = useState<EpicNovelProjectDetailResponse | null>(null);
  const [dashboard, setDashboard] = useState<EpicDashboardResponse | null>(null);
  const [tab, setTab] = useState<TabId>("foundation");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [volumeNumber, setVolumeNumber] = useState("1");
  const [batchFrom, setBatchFrom] = useState("1");
  const [batchTo, setBatchTo] = useState("3");
  const [scanRows, setScanRows] = useState<Array<Record<string, unknown>>>([]);
  const [referenceCost, setReferenceCost] = useState<EpicReferenceExtractionCostEstimate | null>(null);
  const [referenceControl, setReferenceControl] = useState<EpicReferenceExtractionControl | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [projectRes, dashboardRes, costRes, controlRes] = await Promise.all([
        api.getEpicNovelProject(id),
        api.getEpicNovelDashboard(id),
        api.getEpicReferenceExtractionCostEstimate(),
        api.getEpicReferenceExtractionControl(),
      ]);
      setDetail(projectRes);
      setDashboard(dashboardRes);
      setReferenceCost(costRes);
      setReferenceControl(controlRes);
      const next = nextChapterNumber(projectRes);
      setBatchFrom(String(next));
      setBatchTo(String(Math.min(projectRes.project.target_chapters, next + 2)));
    } catch (e) {
      setError(actionError(e));
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  const runAction = async (key: string, fn: () => Promise<unknown>, message: string) => {
    setBusy(key);
    setError(null);
    setNotice(null);
    try {
      const result = await fn();
      if (key === "scan" && Array.isArray(result)) {
        setScanRows(result as Array<Record<string, unknown>>);
      }
      setNotice(message);
      await load();
    } catch (e) {
      setError(actionError(e));
    } finally {
      setBusy(null);
    }
  };

  const project = detail?.project;
  const nextChapter = detail ? nextChapterNumber(detail) : 1;
  const volume = Math.max(1, Number(volumeNumber) || 1);
  const chapterPreview = useMemo(
    () => detail?.chapters.find((chapter) => chapter.status === "accepted") ?? detail?.chapters[0] ?? null,
    [detail],
  );

  if (!detail || !project) {
    return (
      <div className="flex items-center gap-2 text-warm-400">
        <Loader2 className="h-4 w-4 animate-spin" />
        正在加载长篇项目
      </div>
    );
  }

  return (
    <div className="space-y-7">
      <section className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <button
            type="button"
            className="mb-3 text-sm text-warm-400 transition hover:text-warm-100"
            onClick={() => navigate("/epic-novel")}
          >
            返回长篇项目
          </button>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="font-serif text-3xl text-warm-100">{project.title}</h1>
            <StatusPill status={project.status} />
          </div>
          <div className="mt-3 max-w-4xl text-sm leading-7 text-warm-400">{project.premise}</div>
          <div className="mt-3 text-xs text-warm-500">{modelSummaryLabel(project) || "模型尚未配置"}</div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn-ghost rounded-lg" onClick={() => void load()}>
            <RefreshCcw className="h-4 w-4" strokeWidth={1.7} />
            刷新
          </button>
          <button type="button" className="btn-ghost rounded-lg" onClick={() => void api.downloadEpicBibleExport(project.id)}>
            <FileCheck2 className="h-4 w-4" strokeWidth={1.7} />
            导出设定
          </button>
          <button type="button" className="btn-gold rounded-lg" onClick={() => void api.downloadEpicNovelExport(project.id)}>
            <Download className="h-4 w-4" strokeWidth={1.7} />
            导出正文
          </button>
        </div>
      </section>

      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/8 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}
      {notice && (
        <div className="rounded-lg border border-emerald-500/25 bg-emerald-500/8 px-4 py-3 text-sm text-emerald-200">
          {notice}
        </div>
      )}

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard label="已验收字数" value={formatNumber(project.total_words)} icon={BookOpenText} />
        <StatCard label="章节进度" value={`${dashboard?.completed_chapters ?? 0}/${project.target_chapters}`} icon={Layers3} />
        <StatCard label="圣经覆盖率" value={pct(dashboard?.bible_coverage_score)} icon={ShieldCheck} />
        <StatCard label="预估费用" value={`$${(dashboard?.cost_estimate ?? 0).toFixed(3)}`} icon={Database} />
      </section>

      <div className="flex flex-wrap gap-2 border-b border-gold-500/10 pb-3">
        {tabs.map((item) => (
          <button
            key={item.id}
            type="button"
            className={`rounded-lg px-4 py-2 text-sm transition ${
              tab === item.id
                ? "bg-gold-500/15 text-warm-100"
                : "text-warm-400 hover:bg-gold-500/8 hover:text-warm-100"
            }`}
            onClick={() => setTab(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab === "foundation" && (
        <section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
          <div className="card-warm rounded-lg px-5 py-5">
            <div className="mb-4 flex items-center gap-2 text-warm-100">
              <GitBranch className="h-4 w-4 text-gold-300" strokeWidth={1.7} />
              <h2 className="font-serif text-xl">基建流水线</h2>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <ActionButton
                label="世界观竞选"
                busy={busy === "worldview"}
                icon={Sparkles}
                onClick={() =>
                  void runAction("worldview", () => api.runEpicWorldviewTournament(project.id), "世界观候选已生成。")
                }
              />
              <ActionButton
                label="世界圣经"
                busy={busy === "world-bible"}
                icon={BookOpenText}
                onClick={() => void runAction("world-bible", () => api.generateEpicWorldBible(project.id), "世界圣经已生成。")}
              />
              <ActionButton
                label="世界图谱"
                busy={busy === "atlas"}
                icon={Database}
                onClick={() => void runAction("atlas", () => api.generateEpicWorldAtlas(project.id), "世界图谱已生成。")}
              />
              <ActionButton
                label="战力体系"
                busy={busy === "power"}
                icon={ShieldCheck}
                onClick={() => void runAction("power", () => api.forgeEpicPowerSystem(project.id), "战力体系已生成。")}
              />
              <ActionButton
                label="群像角色"
                busy={busy === "ensemble"}
                icon={Layers3}
                onClick={() => void runAction("ensemble", () => api.generateEpicEnsembleCast(project.id), "群像角色已生成。")}
              />
              <ActionButton
                label="全局主线"
                busy={busy === "spine"}
                icon={GitBranch}
                onClick={() => void runAction("spine", () => api.generateEpicGlobalSpine(project.id), "全局主线已生成。")}
              />
              <ActionButton
                label="圣经目录"
                busy={busy === "manifest"}
                icon={FileCheck2}
                onClick={() => void runAction("manifest", () => api.generateEpicBibleManifest(project.id), "圣经目录已生成。")}
              />
              <ActionButton
                label="分卷合同"
                busy={busy === "volumes"}
                icon={ScrollText}
                onClick={() => void runAction("volumes", () => api.generateEpicVolumeContracts(project.id), "分卷合同已生成。")}
              />
              <ActionButton
                label="弧线台账"
                busy={busy === "arc-ledgers"}
                icon={GitBranch}
                onClick={() => void runAction("arc-ledgers", () => api.generateEpicArcLedgers(project.id), "弧线台账已生成。")}
              />
            </div>
          </div>

          <div className="space-y-3">
            <StatCard label="世界圣经" value={readyLabel(Boolean(detail.world_bible))} icon={BookOpenText} />
            <StatCard label="世界图谱" value={readyLabel(Boolean(detail.world_atlas))} icon={Database} />
            <StatCard label="圣经分节" value={String(countRows(detail, "bible_sections"))} icon={FileCheck2} />
            <StatCard label="分卷合同" value={String(countRows(detail, "volume_contracts"))} icon={ScrollText} />
          </div>
        </section>
      )}

      {tab === "reference" && (
        <section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
          <div className="card-warm rounded-lg px-5 py-5">
            <div className="mb-4 flex items-center gap-2 text-warm-100">
              <SearchCheck className="h-4 w-4 text-gold-300" strokeWidth={1.7} />
              <h2 className="font-serif text-xl">参考书库</h2>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <ActionButton
                label="检测下载器"
                busy={busy === "detect"}
                icon={SearchCheck}
                onClick={() => void runAction("detect", () => api.detectEpicReferenceDownloader(), "下载器检测完成。")}
              />
              <ActionButton
                label="扫描下载目录"
                busy={busy === "scan"}
                icon={RefreshCcw}
                onClick={() => void runAction("scan", () => api.scanEpicReferenceDownloads(), "下载目录扫描完成。")}
              />
              <ActionButton
                label="导入已下载文件"
                busy={busy === "import"}
                icon={Download}
                onClick={() => void runAction("import", () => api.importEpicDownloadedReferences(), "已下载文件导入完成。")}
              />
              <ActionButton
                label="构建机制画像"
                busy={busy === "profile"}
                icon={Database}
                onClick={() => void runAction("profile", () => api.buildEpicReferenceProfile(), "参考机制画像已生成。")}
              />
              <ActionButton
                label="暂停抽取"
                busy={busy === "reference-pause"}
                icon={AlertTriangle}
                onClick={() => void runAction("reference-pause", () => api.setEpicReferenceExtractionControl("paused"), "参考抽取已暂停。")}
              />
              <ActionButton
                label="恢复抽取"
                busy={busy === "reference-resume"}
                icon={Play}
                onClick={() => void runAction("reference-resume", () => api.setEpicReferenceExtractionControl("running"), "参考抽取已恢复。")}
              />
              <ActionButton
                label="停止抽取"
                busy={busy === "reference-stop"}
                icon={ShieldCheck}
                onClick={() => void runAction("reference-stop", () => api.setEpicReferenceExtractionControl("stopped"), "参考抽取已停止。")}
              />
            </div>
            <div className="mt-5 overflow-hidden rounded-lg border border-gold-500/10">
              <table className="w-full text-left text-sm">
                <thead className="bg-base-800/70 text-xs text-warm-500">
                  <tr>
                    <th className="px-3 py-2">文件</th>
                    <th className="px-3 py-2">状态</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gold-500/8">
                  {scanRows.map((row, index) => (
                    <tr key={`${row.path ?? index}`} className="text-warm-300">
                      <td className="max-w-[520px] truncate px-3 py-2">{String(row.path ?? row.name ?? "-")}</td>
                      <td className="px-3 py-2">{statusLabel(String(row.status ?? row.imported ?? "found"))}</td>
                    </tr>
                  ))}
                  {scanRows.length === 0 && (
                    <tr>
                      <td className="px-3 py-4 text-warm-500" colSpan={2}>
                        暂无扫描结果。
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <div className="space-y-3">
            <StatCard label="抽象模式卡" value={String(dashboard?.generated_pattern_card_count ?? 0)} icon={Database} />
            <StatCard label="抽取状态" value={statusLabel(referenceControl?.state)} icon={ShieldCheck} />
            <StatCard label="预估批次" value={String(referenceCost?.estimatedBatches ?? 0)} icon={Layers3} />
            <StatCard label="预估令牌" value={formatNumber(referenceCost?.estimatedPromptTokens ?? 0)} icon={ScrollText} />
            <StatCard label="引用安全警告" value={String(dashboard?.reference_safety_warnings ?? 0)} icon={AlertTriangle} />
            <StatCard label="过拟合警告" value={String(dashboard?.reference_overfit_warnings ?? 0)} icon={AlertTriangle} />
          </div>
        </section>
      )}

      {tab === "chapters" && (
        <section className="grid gap-6 xl:grid-cols-[420px_minmax(0,1fr)]">
          <div className="card-warm rounded-lg px-5 py-5">
            <div className="mb-4 flex items-center gap-2 text-warm-100">
              <ScrollText className="h-4 w-4 text-gold-300" strokeWidth={1.7} />
              <h2 className="font-serif text-xl">章节执行</h2>
            </div>
            <div className="grid gap-3 sm:grid-cols-3 xl:grid-cols-1">
              <label className="block text-sm text-warm-300">
                分卷
                <input className="auth-input mt-2" value={volumeNumber} onChange={(event) => setVolumeNumber(event.target.value)} />
              </label>
              <label className="block text-sm text-warm-300">
                批量起始章
                <input className="auth-input mt-2" value={batchFrom} onChange={(event) => setBatchFrom(event.target.value)} />
              </label>
              <label className="block text-sm text-warm-300">
                批量结束章
                <input className="auth-input mt-2" value={batchTo} onChange={(event) => setBatchTo(event.target.value)} />
              </label>
            </div>
            <div className="mt-4 grid gap-3">
              <ActionButton
                label={`编译第 ${volume} 卷`}
                busy={busy === "compile"}
                icon={FileCheck2}
                onClick={() =>
                  void runAction(
                    "compile",
                    () => api.compileEpicChapterContracts(project.id, volume),
                    `第 ${volume} 卷章节合同已生成。`,
                  )
                }
              />
              <ActionButton
                label={`执行第 ${nextChapter} 章`}
                busy={busy === "execute"}
                icon={Play}
                onClick={() =>
                  void runAction(
                    "execute",
                    () => api.executeEpicChapter(project.id, nextChapter),
                    `第 ${nextChapter} 章已验收。`,
                  )
                }
              />
              <ActionButton
                label={`批量执行 ${batchFrom}-${batchTo}`}
                busy={busy === "batch"}
                icon={Layers3}
                onClick={() =>
                  void runAction(
                    "batch",
                    () =>
                      api.generateEpicBatch(project.id, {
                        from_chapter: Number(batchFrom) || nextChapter,
                        to_chapter: Number(batchTo) || nextChapter,
                      }),
                    "批量生成完成。",
                  )
                }
              />
              <ActionButton
                label="大纲一致性检查"
                busy={busy === "outline-ci"}
                icon={ShieldCheck}
                onClick={() => void runAction("outline-ci", () => api.runEpicOutlineCi(project.id), "大纲一致性检查完成。")}
              />
              <ActionButton
                label="修复大纲"
                busy={busy === "outline-repair"}
                icon={AlertTriangle}
                onClick={() => void runAction("outline-repair", () => api.repairEpicOutline(project.id), "大纲修复完成。")}
              />
            </div>
          </div>

          <div className="space-y-4">
            <div className="overflow-hidden rounded-lg border border-gold-500/10">
              <table className="w-full text-left text-sm">
                <thead className="bg-base-800/70 text-xs text-warm-500">
                  <tr>
                    <th className="px-3 py-2">章节</th>
                    <th className="px-3 py-2">标题</th>
                    <th className="px-3 py-2">状态</th>
                    <th className="px-3 py-2">字数</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gold-500/8">
                  {detail.chapters.map((chapter) => (
                    <tr key={chapter.id} className="text-warm-300">
                      <td className="px-3 py-2">{chapter.chapter_number}</td>
                      <td className="px-3 py-2">{chapter.title}</td>
                      <td className="px-3 py-2">
                        <StatusPill status={chapter.status} />
                      </td>
                      <td className="px-3 py-2">{formatNumber(chapter.word_count)}</td>
                    </tr>
                  ))}
                  {detail.chapters.length === 0 && (
                    <tr>
                      <td className="px-3 py-4 text-warm-500" colSpan={4}>
                        暂无已验收章节。
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            {chapterPreview && (
              <div className="card-warm rounded-lg px-5 py-5">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <h3 className="font-serif text-xl text-warm-100">{chapterPreview.title}</h3>
                  <StatusPill status={chapterPreview.status} />
                </div>
                <p className="line-clamp-6 whitespace-pre-line text-sm leading-7 text-warm-400">
                  {chapterPreview.accepted_content ?? chapterPreview.repaired_content ?? chapterPreview.draft_content}
                </p>
              </div>
            )}
          </div>
        </section>
      )}

      {tab === "qa" && (
        <section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
          <div className="card-warm rounded-lg px-5 py-5">
            <div className="mb-4 flex items-center gap-2 text-warm-100">
              <ShieldCheck className="h-4 w-4 text-gold-300" strokeWidth={1.7} />
              <h2 className="font-serif text-xl">验收报告</h2>
            </div>
            <div className="overflow-hidden rounded-lg border border-gold-500/10">
              <table className="w-full text-left text-sm">
                <thead className="bg-base-800/70 text-xs text-warm-500">
                  <tr>
                    <th className="px-3 py-2">章节</th>
                    <th className="px-3 py-2">状态</th>
                    <th className="px-3 py-2">大纲执行</th>
                    <th className="px-3 py-2">连续性</th>
                    <th className="px-3 py-2">反套路</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gold-500/8">
                  {detail.acceptance_reports.map((report) => (
                    <tr key={report.id} className="text-warm-300">
                      <td className="px-3 py-2">{report.chapter_number}</td>
                      <td className="px-3 py-2">
                        <StatusPill status={report.overall_status} />
                      </td>
                      <td className="px-3 py-2">{pct(report.outline_execution_score)}</td>
                      <td className="px-3 py-2">{pct(report.continuity_score)}</td>
                      <td className="px-3 py-2">{pct(report.anti_cliche_score)}</td>
                    </tr>
                  ))}
                  {detail.acceptance_reports.length === 0 && (
                    <tr>
                      <td className="px-3 py-4 text-warm-500" colSpan={5}>
                        暂无验收报告。
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <div className="space-y-3">
            <StatCard label="战力违规" value={String(dashboard?.power_violations ?? 0)} icon={AlertTriangle} />
            <StatCard label="连续性问题" value={String(dashboard?.continuity_issues ?? 0)} icon={AlertTriangle} />
            <StatCard label="未回收伏笔" value={String(dashboard?.unpaid_foreshadows ?? 0)} icon={GitBranch} />
            <StatCard label="修复次数" value={String(dashboard?.repair_count ?? 0)} icon={CheckCircle2} />
          </div>
        </section>
      )}

      {tab === "logs" && (
        <section className="card-warm rounded-lg px-5 py-5">
          <div className="mb-4 flex items-center gap-2 text-warm-100">
            <Database className="h-4 w-4 text-gold-300" strokeWidth={1.7} />
            <h2 className="font-serif text-xl">生成日志</h2>
          </div>
          <div className="overflow-hidden rounded-lg border border-gold-500/10">
            <table className="w-full text-left text-sm">
              <thead className="bg-base-800/70 text-xs text-warm-500">
                <tr>
                  <th className="px-3 py-2">动作</th>
                  <th className="px-3 py-2">模型</th>
                  <th className="px-3 py-2">章节</th>
                  <th className="px-3 py-2">费用</th>
                  <th className="px-3 py-2">时间</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gold-500/8">
                {detail.logs.map((log) => (
                  <tr key={log.id} className="text-warm-300">
                    <td className="px-3 py-2">{actionLabel(log.action)}</td>
                    <td className="px-3 py-2">{modelDisplayName(log.model ?? log.provider)}</td>
                    <td className="px-3 py-2">{log.chapter_number ?? "-"}</td>
                    <td className="px-3 py-2">${log.estimated_cost.toFixed(4)}</td>
                    <td className="px-3 py-2">{asDate(log.created_at)}</td>
                  </tr>
                ))}
                {detail.logs.length === 0 && (
                  <tr>
                    <td className="px-3 py-4 text-warm-500" colSpan={5}>
                      暂无生成日志。
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}

export function EpicNovelWorkspace() {
  const { id } = useParams();
  if (!id) return <ProjectList />;
  return <ProjectDetail id={id} />;
}
