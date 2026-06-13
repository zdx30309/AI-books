import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent, ReactNode } from "react";
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
  EpicExportMetadata,
  EpicNovelCreateRequest,
  EpicNovelProject,
  EpicNovelProjectDetailResponse,
  EpicReferenceBooklistItem,
  EpicReferenceDownloadTask,
  EpicReferenceExtractionControl,
  EpicReferenceExtractionCostEstimate,
  EpicReferenceMechanismProfile,
  EpicReferencePatternCard,
} from "@/api/types";

type TabId = "foundation" | "reference" | "bible" | "planning" | "chapters" | "context" | "qa" | "dashboard" | "export" | "logs";

type CreateFormState = {
  title: string;
  premise: string;
  targetWords: string;
  targetVolumes: string;
  targetChapters: string;
  generationMode: "cheap" | "standard" | "high_quality";
};

const tabs: Array<{ id: TabId; label: string }> = [
  { id: "foundation", label: "基建总览" },
  { id: "reference", label: "参考语料" },
  { id: "bible", label: "圣经世界" },
  { id: "planning", label: "规划合同" },
  { id: "chapters", label: "章节生产" },
  { id: "context", label: "上下文检索" },
  { id: "qa", label: "质检验收" },
  { id: "dashboard", label: "生产仪表盘" },
  { id: "export", label: "文件导出" },
  { id: "logs", label: "调用日志" },
];

const bibleSectionOptions = [
  ["project_overview", "项目总览"],
  ["genre_promise", "类型与爽点承诺"],
  ["world_law", "世界底层法则"],
  ["power_system", "战力体系"],
  ["nation_and_court", "国家与朝堂"],
  ["bureaucracy", "官僚体系"],
  ["map_geography", "地图与地理"],
  ["factions", "势力系统"],
  ["characters", "人物系统"],
  ["heroine_arcs", "多女主独立线"],
  ["relationship_graph", "关系图谱"],
  ["history_timeline", "历史年表"],
  ["global_spine", "全书大主线"],
  ["volume_contracts", "分卷合同"],
  ["foreshadow_ledger", "伏笔账本"],
  ["reader_question_ledger", "读者疑问账本"],
  ["pleasure_curve", "爽点曲线"],
  ["villain_plan", "反派计划"],
  ["style_guide", "风格指南"],
  ["anti_cliche_rules", "禁忌与反俗套"],
  ["ending_plan", "结局规划"],
  ["glossary", "术语表"],
  ["reference_profile_usage", "参考机制画像接入说明"],
  ["production_control_rules", "生产控制规则"],
] as const;

type EpicArtifactType = "volume_markdown" | "chapter_bundle" | "qa_reports" | "retrieval_traces" | "acceptance_reports";

const artifactOptions: Array<{ type: EpicArtifactType; label: string; needsVolume?: boolean }> = [
  { type: "volume_markdown", label: "分卷正文", needsVolume: true },
  { type: "chapter_bundle", label: "章节包" },
  { type: "qa_reports", label: "质检报告" },
  { type: "retrieval_traces", label: "检索轨迹" },
  { type: "acceptance_reports", label: "验收报告" },
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

function normalizeEpicDetail(detail: EpicNovelProjectDetailResponse): EpicNovelProjectDetailResponse {
  return {
    ...detail,
    bureaucracy_system: detail.bureaucracy_system ?? null,
    worldview_candidates: detail.worldview_candidates ?? [],
    nations: detail.nations ?? [],
    map_regions: detail.map_regions ?? [],
    institutions: detail.institutions ?? [],
    factions: detail.factions ?? [],
    characters: detail.characters ?? [],
    character_arcs: detail.character_arcs ?? [],
    global_spine: detail.global_spine ?? null,
    foreshadows: detail.foreshadows ?? [],
    reader_questions: detail.reader_questions ?? [],
    pleasure_beats: detail.pleasure_beats ?? [],
    issues: detail.issues ?? [],
    bible_sections: detail.bible_sections ?? [],
    volume_contracts: detail.volume_contracts ?? [],
    chapter_contracts: detail.chapter_contracts ?? [],
    chapters: detail.chapters ?? [],
    acceptance_reports: detail.acceptance_reports ?? [],
    retrieval_traces: detail.retrieval_traces ?? [],
    logs: detail.logs ?? [],
  };
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function fieldText(value: unknown, fallback = "-") {
  if (value === null || value === undefined || value === "") return fallback;
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "number") return formatNumber(value);
  if (Array.isArray(value)) return value.length ? `${value.length} 项` : fallback;
  if (typeof value === "object") return Object.keys(asRecord(value)).length ? "已记录" : fallback;
  return String(value);
}

function fieldCount(value: unknown) {
  if (Array.isArray(value)) return value.length;
  if (value && typeof value === "object") return Object.keys(asRecord(value)).length;
  return value ? 1 : 0;
}

function jsonPreview(value: unknown, max = 180) {
  if (value === null || value === undefined) return "-";
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return text.length > max ? `${text.slice(0, max)}...` : text;
}

function traceTaskLabel(trace: Record<string, unknown>) {
  const raw = String(trace.task_type ?? trace.action ?? trace.phase ?? "");
  return actionLabel(raw) !== "未登记动作" ? actionLabel(raw) : raw || "未登记任务";
}

function issueSeverityLabel(value: unknown) {
  const raw = String(value ?? "").toLowerCase();
  if (raw === "severe") return "严重";
  if (raw === "high") return "高危";
  if (raw === "medium") return "中等";
  if (raw === "low") return "低";
  return raw || "-";
}

function exportLabel(metadata: EpicExportMetadata | null) {
  if (!metadata) return "尚未生成导出元数据";
  const filename = typeof metadata.filename === "string" && metadata.filename ? metadata.filename : "未返回文件名";
  const bytes = typeof metadata.bytes === "number" ? formatNumber(metadata.bytes) : "0";
  const checksum =
    typeof metadata.sha256 === "string" && metadata.sha256 ? metadata.sha256.slice(0, 12) : "未返回校验和";
  return `${filename} / ${bytes} 字节 / 校验 ${checksum}`;
}

function summarizeActionResult(key: string, result: unknown, fallback: string) {
  if (key === "batch") {
    const row = asRecord(result);
    const generated = Array.isArray(row.generated) ? row.generated.length : 0;
    const failed = Array.isArray(row.failed) ? row.failed.length : 0;
    const skipped = Array.isArray(row.skipped) ? row.skipped.length : 0;
    return `批量请求完成：已生成 ${generated} 章，失败 ${failed} 章，跳过 ${skipped} 章。`;
  }
  if (key === "check" && Array.isArray(result)) return `章节检查完成：发现 ${result.length} 条问题。`;
  if (key === "scan" && Array.isArray(result)) return `下载目录扫描完成：发现 ${result.length} 个文件。`;
  if (key === "booklist-import" && Array.isArray(result)) return `参考书单导入完成：返回 ${result.length} 条记录。`;
  if (key === "booklist-normalize" && Array.isArray(result)) return `参考书单规范化完成：返回 ${result.length} 条记录。`;
  if (key === "tasks-refresh" && Array.isArray(result)) return `下载任务已刷新：${result.length} 条。`;
  if (key === "patterns" && Array.isArray(result)) return `模式卡检索完成：返回 ${result.length} 张卡。`;
  if (key.startsWith("export-meta") && result && typeof result === "object") {
    const metadata = result as EpicExportMetadata;
    const filename = typeof metadata.filename === "string" && metadata.filename ? metadata.filename : "未返回文件名";
    const bytes = typeof metadata.bytes === "number" ? formatNumber(metadata.bytes) : "0";
    return `导出元数据已生成：${filename}，${bytes} 字节。`;
  }
  return fallback;
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

function Panel({
  title,
  icon: Icon,
  children,
  right,
}: {
  title: string;
  icon: typeof BookOpenText;
  children: ReactNode;
  right?: ReactNode;
}) {
  return (
    <section className="card-warm rounded-lg px-5 py-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-warm-100">
          <Icon className="h-4 w-4 text-gold-300" strokeWidth={1.7} />
          <h2 className="font-serif text-xl">{title}</h2>
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

function EmptyLine({ text }: { text: string }) {
  return <div className="rounded-lg border border-gold-500/10 px-4 py-4 text-sm text-warm-500">{text}</div>;
}

function KeyValueGrid({ rows }: { rows: Array<[string, unknown]> }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {rows.map(([label, value]) => (
        <div key={label} className="rounded-lg border border-gold-500/10 px-3 py-3">
          <div className="text-[11px] text-warm-500">{label}</div>
          <div className="mt-1 break-words text-sm text-warm-200">{fieldText(value)}</div>
        </div>
      ))}
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
  const [rawDetail, setDetail] = useState<EpicNovelProjectDetailResponse | null>(null);
  const [dashboard, setDashboard] = useState<EpicDashboardResponse | null>(null);
  const [tab, setTab] = useState<TabId>("foundation");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [auxError, setAuxError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [volumeNumber, setVolumeNumber] = useState("1");
  const [batchFrom, setBatchFrom] = useState("1");
  const [batchTo, setBatchTo] = useState("3");
  const [bibleSectionKey, setBibleSectionKey] = useState<(typeof bibleSectionOptions)[number][0]>("project_overview");
  const [contextChapter, setContextChapter] = useState("1");
  const [patternQuery, setPatternQuery] = useState("");
  const [artifactType, setArtifactType] = useState<EpicArtifactType>("volume_markdown");
  const [scanRows, setScanRows] = useState<Array<Record<string, unknown>>>([]);
  const [booklistRows, setBooklistRows] = useState<EpicReferenceBooklistItem[]>([]);
  const [downloadTasks, setDownloadTasks] = useState<EpicReferenceDownloadTask[]>([]);
  const [patternCards, setPatternCards] = useState<EpicReferencePatternCard[]>([]);
  const [referenceProfile, setReferenceProfile] = useState<EpicReferenceMechanismProfile | null>(null);
  const [referenceCost, setReferenceCost] = useState<EpicReferenceExtractionCostEstimate | null>(null);
  const [referenceControl, setReferenceControl] = useState<EpicReferenceExtractionControl | null>(null);
  const [contextPack, setContextPack] = useState<Record<string, unknown> | null>(null);
  const [exportMetadata, setExportMetadata] = useState<EpicExportMetadata | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setAuxError(null);
    try {
      const projectRes = await api.getEpicNovelProject(id);
      const normalizedProject = normalizeEpicDetail(projectRes);
      setDetail(normalizedProject);
      const next = nextChapterNumber(normalizedProject);
      setBatchFrom(String(next));
      setBatchTo(String(Math.min(normalizedProject.project.target_chapters, next + 2)));
      setContextChapter(String(next));
      void Promise.allSettled([
        api.getEpicNovelDashboard(id),
        api.getEpicReferenceExtractionCostEstimate(),
        api.getEpicReferenceExtractionControl(),
        api.listEpicReferenceDownloadTasks(),
        api.getEpicReferenceProfile(),
        api.searchEpicReferencePatterns({ query: "", limit: 8 }),
      ]).then(([dashboardRes, costRes, controlRes, tasksRes, profileRes, cardsRes]) => {
        if (dashboardRes.status === "fulfilled") setDashboard(dashboardRes.value);
        if (costRes.status === "fulfilled") setReferenceCost(costRes.value);
        if (controlRes.status === "fulfilled") setReferenceControl(controlRes.value);
        if (tasksRes.status === "fulfilled") setDownloadTasks(tasksRes.value);
        if (profileRes.status === "fulfilled") setReferenceProfile(profileRes.value);
        if (cardsRes.status === "fulfilled") setPatternCards(cardsRes.value);
        if ([dashboardRes, costRes, controlRes, tasksRes, profileRes, cardsRes].some((item) => item.status === "rejected")) {
          setAuxError("部分参考语料辅助数据暂时不可用，核心项目数据已加载。");
        }
      });
    } catch (e) {
      setError(actionError(e));
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  const detail = useMemo(() => (rawDetail ? normalizeEpicDetail(rawDetail) : null), [rawDetail]);

  const runAction = async (key: string, fn: () => Promise<unknown>, message: string) => {
    setBusy(key);
    setError(null);
    setNotice(null);
    try {
      const result = await fn();
      if (key === "scan" && Array.isArray(result)) {
        setScanRows(result as Array<Record<string, unknown>>);
      }
      if ((key === "booklist-import" || key === "booklist-normalize") && Array.isArray(result)) {
        setBooklistRows(result as EpicReferenceBooklistItem[]);
      }
      if (key === "tasks-refresh" && Array.isArray(result)) {
        setDownloadTasks(result as EpicReferenceDownloadTask[]);
      }
      if (key === "patterns" && Array.isArray(result)) {
        setPatternCards(result as EpicReferencePatternCard[]);
      }
      if (key === "profile" && result && typeof result === "object") {
        setReferenceProfile(result as EpicReferenceMechanismProfile);
      }
      if (key === "context-pack" && result && typeof result === "object") {
        setContextPack(result as Record<string, unknown>);
      }
      if (key.startsWith("export-meta") && result && typeof result === "object") {
        setExportMetadata(result as EpicExportMetadata);
      }
      setNotice(summarizeActionResult(key, result, message));
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
  const selectedArtifact = artifactOptions.find((item) => item.type === artifactType) ?? artifactOptions[0];
  const currentJob = asRecord(dashboard?.current_job);
  const latestTrace = detail?.retrieval_traces[0] ? asRecord(detail.retrieval_traces[0]) : null;
  const latestIssue = detail?.issues[0] ? asRecord(detail.issues[0]) : null;

  if (loading && !detail) {
    return (
      <div className="flex items-center gap-2 text-warm-400">
        <Loader2 className="h-4 w-4 animate-spin" />
        正在加载长篇项目
      </div>
    );
  }

  if (!detail || !project) {
    return (
      <div className="space-y-4">
        {error && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/8 px-4 py-3 text-sm text-red-300">
            {error}
          </div>
        )}
        <button type="button" className="btn-ghost rounded-lg" onClick={() => void load()}>
          <RefreshCcw className="h-4 w-4" strokeWidth={1.7} />
          重新加载
        </button>
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
      {auxError && (
        <div className="rounded-lg border border-gold-500/25 bg-gold-500/8 px-4 py-3 text-sm text-gold-200">
          {auxError}
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
              <h2 className="font-serif text-xl">基建总览</h2>
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
            <StatCard label="官僚体系" value={readyLabel(Boolean(detail.bureaucracy_system))} icon={ShieldCheck} />
            <StatCard label="战力体系" value={readyLabel(Boolean(detail.power_system))} icon={ShieldCheck} />
            <StatCard label="群像人物" value={String(countRows(detail, "characters"))} icon={Layers3} />
            <StatCard label="角色弧光" value={String(countRows(detail, "character_arcs"))} icon={GitBranch} />
            <StatCard label="关系图谱" value={String(countRows(detail, "institutions") + countRows(detail, "factions"))} icon={GitBranch} />
            <StatCard label="全局主线" value={readyLabel(Boolean(detail.global_spine))} icon={GitBranch} />
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
                label="导入参考书单"
                busy={busy === "booklist-import"}
                icon={BookOpenText}
                onClick={() =>
                  void runAction("booklist-import", () => api.importEpicReferenceBooklist(), "参考书单导入完成。")
                }
              />
              <ActionButton
                label="规范化书单"
                busy={busy === "booklist-normalize"}
                icon={FileCheck2}
                onClick={() =>
                  void runAction("booklist-normalize", () => api.normalizeEpicReferenceBooklist(), "参考书单规范化完成。")
                }
              />
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
              <ActionButton
                label="刷新下载任务"
                busy={busy === "tasks-refresh"}
                icon={RefreshCcw}
                onClick={() => void runAction("tasks-refresh", () => api.listEpicReferenceDownloadTasks(), "下载任务已刷新。")}
              />
            </div>
            <div className="mt-4 flex flex-wrap gap-2">
              <input
                className="auth-input min-w-[220px] flex-1"
                value={patternQuery}
                onChange={(event) => setPatternQuery(event.target.value)}
                placeholder="输入机制关键词"
              />
              <button
                type="button"
                className="btn-ghost rounded-lg"
                disabled={busy === "patterns"}
                onClick={() =>
                  void runAction(
                    "patterns",
                    () => api.searchEpicReferencePatterns({ query: patternQuery, limit: 12 }),
                    "模式卡检索完成。",
                  )
                }
              >
                {busy === "patterns" ? <Loader2 className="h-4 w-4 animate-spin" /> : <SearchCheck className="h-4 w-4" />}
                检索模式卡
              </button>
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
            <div className="mt-5 overflow-hidden rounded-lg border border-gold-500/10">
              <table className="w-full text-left text-sm">
                <thead className="bg-base-800/70 text-xs text-warm-500">
                  <tr>
                    <th className="px-3 py-2">书名</th>
                    <th className="px-3 py-2">层级</th>
                    <th className="px-3 py-2">下载状态</th>
                    <th className="px-3 py-2">用途</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gold-500/8">
                  {booklistRows.slice(0, 12).map((row) => (
                    <tr key={row.id} className="text-warm-300">
                      <td className="px-3 py-2">{row.title}</td>
                      <td className="px-3 py-2">{row.tier}</td>
                      <td className="px-3 py-2">{statusLabel(row.download_status)}</td>
                      <td className="px-3 py-2">{jsonPreview(row.likely_use, 80)}</td>
                    </tr>
                  ))}
                  {booklistRows.length === 0 && (
                    <tr>
                      <td className="px-3 py-4 text-warm-500" colSpan={4}>
                        尚未在本页导入或规范化书单。
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <div className="mt-5 overflow-hidden rounded-lg border border-gold-500/10">
              <table className="w-full text-left text-sm">
                <thead className="bg-base-800/70 text-xs text-warm-500">
                  <tr>
                    <th className="px-3 py-2">下载任务</th>
                    <th className="px-3 py-2">提供方</th>
                    <th className="px-3 py-2">状态</th>
                    <th className="px-3 py-2">模式卡</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gold-500/8">
                  {downloadTasks.slice(0, 12).map((task) => (
                    <tr key={task.id} className="text-warm-300">
                      <td className="px-3 py-2">{task.title}</td>
                      <td className="px-3 py-2">{task.provider}</td>
                      <td className="px-3 py-2">{statusLabel(task.status)}</td>
                      <td className="px-3 py-2">{formatNumber(task.generated_card_count)}</td>
                    </tr>
                  ))}
                  {downloadTasks.length === 0 && (
                    <tr>
                      <td className="px-3 py-4 text-warm-500" colSpan={4}>
                        暂无下载任务。
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
            <StatCard label="机制画像书数" value={String(referenceProfile?.source_book_count ?? 0)} icon={BookOpenText} />
            <StatCard label="机制画像卡数" value={String(referenceProfile?.source_card_count ?? 0)} icon={Database} />
          </div>
          <Panel title="参考模式卡" icon={Database}>
            {patternCards.length === 0 ? (
              <EmptyLine text="暂无模式卡检索结果。" />
            ) : (
              <div className="space-y-3">
                {patternCards.slice(0, 8).map((card) => (
                  <div key={card.id} className="rounded-lg border border-gold-500/10 px-4 py-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="font-medium text-warm-100">{card.name}</div>
                      <span className="text-xs text-warm-500">{card.category}</span>
                    </div>
                    <div className="mt-2 text-sm leading-6 text-warm-400">{card.description}</div>
                    <div className="mt-2 text-xs text-warm-500">
                      来源章节 {card.source_section_ids.length} / 原文进入提示 {card.raw_text_included ? "是" : "否"}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Panel>
        </section>
      )}

      {tab === "bible" && (
        <section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
          <Panel
            title="主圣经与世界资料"
            icon={BookOpenText}
            right={
              <div className="flex flex-wrap gap-2">
                <select
                  className="auth-input w-[220px]"
                  value={bibleSectionKey}
                  onChange={(event) => setBibleSectionKey(event.target.value as (typeof bibleSectionOptions)[number][0])}
                >
                  {bibleSectionOptions.map(([key, label]) => (
                    <option key={key} value={key}>
                      {label}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="btn-ghost rounded-lg"
                  disabled={busy === "bible-section"}
                  onClick={() =>
                    void runAction(
                      "bible-section",
                      () => api.generateEpicBibleSection(project.id, bibleSectionKey),
                      "圣经分节已生成。",
                    )
                  }
                >
                  {busy === "bible-section" ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileCheck2 className="h-4 w-4" />}
                  生成分节
                </button>
              </div>
            }
          >
            <KeyValueGrid
              rows={[
                ["主圣经状态", asRecord(detail.master_bible).status],
                ["目标字数", asRecord(detail.master_bible).target_words],
                ["已完成分节", detail.bible_sections.filter((row) => String(row.status ?? "") === "accepted").length],
                ["覆盖率", pct(dashboard?.bible_coverage_score)],
                ["世界图谱", detail.world_atlas ? "已记录" : ""],
                ["官僚体系", detail.bureaucracy_system ? "已记录" : ""],
                ["战力体系", detail.power_system ? "已记录" : ""],
                ["全局主线", detail.global_spine ? "已记录" : ""],
              ]}
            />
            <div className="mt-5 overflow-hidden rounded-lg border border-gold-500/10">
              <table className="w-full text-left text-sm">
                <thead className="bg-base-800/70 text-xs text-warm-500">
                  <tr>
                    <th className="px-3 py-2">圣经分节</th>
                    <th className="px-3 py-2">状态</th>
                    <th className="px-3 py-2">字数</th>
                    <th className="px-3 py-2">更新时间</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gold-500/8">
                  {detail.bible_sections.map((section) => (
                    <tr key={String(section.id ?? section.section_key)} className="text-warm-300">
                      <td className="px-3 py-2">{fieldText(section.title ?? section.section_key)}</td>
                      <td className="px-3 py-2">{statusLabel(String(section.status ?? ""))}</td>
                      <td className="px-3 py-2">{fieldText(section.word_count)}</td>
                      <td className="px-3 py-2">{asDate(String(section.updated_at ?? ""))}</td>
                    </tr>
                  ))}
                  {detail.bible_sections.length === 0 && (
                    <tr>
                      <td className="px-3 py-4 text-warm-500" colSpan={4}>
                        暂无圣经分节。
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </Panel>
          <div className="space-y-3">
            <StatCard label="世界观候选" value={String(countRows(detail, "worldview_candidates"))} icon={Sparkles} />
            <StatCard label="国家区域" value={String(countRows(detail, "nations") + countRows(detail, "map_regions"))} icon={Database} />
            <StatCard label="制度势力" value={String(countRows(detail, "institutions") + countRows(detail, "factions"))} icon={ShieldCheck} />
            <StatCard label="人物角色" value={String(countRows(detail, "characters"))} icon={Layers3} />
            <StatCard label="角色弧光" value={String(countRows(detail, "character_arcs"))} icon={GitBranch} />
            <StatCard label="关系图谱" value={String(countRows(detail, "factions") + countRows(detail, "institutions"))} icon={GitBranch} />
          </div>
          <Panel title="世界图谱 / 官僚体系 / 战力体系" icon={Database}>
            <KeyValueGrid
              rows={[
                ["世界图谱状态", asRecord(detail.world_atlas).status],
                ["官僚体系状态", asRecord(detail.bureaucracy_system).status],
                ["战力体系状态", asRecord(detail.power_system).status],
                ["国家数量", countRows(detail, "nations")],
                ["地图区域", countRows(detail, "map_regions")],
                ["势力数量", countRows(detail, "factions")],
              ]}
            />
          </Panel>
        </section>
      )}

      {tab === "planning" && (
        <section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
          <Panel title="分卷合同与章节合同" icon={ScrollText}>
            <div className="overflow-hidden rounded-lg border border-gold-500/10">
              <table className="w-full text-left text-sm">
                <thead className="bg-base-800/70 text-xs text-warm-500">
                  <tr>
                    <th className="px-3 py-2">类型</th>
                    <th className="px-3 py-2">编号</th>
                    <th className="px-3 py-2">标题</th>
                    <th className="px-3 py-2">状态</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gold-500/8">
                  {detail.volume_contracts.map((row) => (
                    <tr key={String(row.id)} className="text-warm-300">
                      <td className="px-3 py-2">分卷合同</td>
                      <td className="px-3 py-2">{fieldText(row.volume_number)}</td>
                      <td className="px-3 py-2">{fieldText(row.title)}</td>
                      <td className="px-3 py-2">{statusLabel(String(row.status ?? "planned"))}</td>
                    </tr>
                  ))}
                  {detail.chapter_contracts.slice(0, 12).map((row) => (
                    <tr key={row.id} className="text-warm-300">
                      <td className="px-3 py-2">章节合同</td>
                      <td className="px-3 py-2">{row.chapter_number}</td>
                      <td className="px-3 py-2">{row.title}</td>
                      <td className="px-3 py-2">{statusLabel(row.status)}</td>
                    </tr>
                  ))}
                  {detail.volume_contracts.length === 0 && detail.chapter_contracts.length === 0 && (
                    <tr>
                      <td className="px-3 py-4 text-warm-500" colSpan={4}>
                        暂无规划合同。
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </Panel>
          <div className="space-y-3">
            <StatCard label="伏笔账本" value={String(countRows(detail, "foreshadows"))} icon={GitBranch} />
            <StatCard label="读者疑问" value={String(countRows(detail, "reader_questions"))} icon={SearchCheck} />
            <StatCard label="爽点曲线" value={String(countRows(detail, "pleasure_beats"))} icon={Sparkles} />
            <StatCard label="大纲执行率" value={pct(dashboard?.outline_execution_rate)} icon={ShieldCheck} />
          </div>
          <Panel title="伏笔 / 读者疑问 / 爽点曲线" icon={GitBranch}>
            <KeyValueGrid
              rows={[
                ["未回收伏笔", dashboard?.unpaid_foreshadows ?? 0],
                ["爽点缺口警告", dashboard?.pleasure_gap_warnings ?? 0],
                ["支线漂移警告", dashboard?.side_quest_warnings ?? 0],
                ["连续性问题", dashboard?.continuity_issues ?? 0],
                ["最新伏笔", fieldText(asRecord(detail.foreshadows[0]).description ?? asRecord(detail.foreshadows[0]).setup_text)],
                ["最新读者疑问", fieldText(asRecord(detail.reader_questions[0]).question)],
              ]}
            />
          </Panel>
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

      {tab === "context" && (
        <section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
          <Panel
            title="上下文包"
            icon={SearchCheck}
            right={
              <div className="flex flex-wrap gap-2">
                <input
                  className="auth-input w-28"
                  value={contextChapter}
                  onChange={(event) => setContextChapter(event.target.value)}
                />
                <button
                  type="button"
                  className="btn-ghost rounded-lg"
                  disabled={busy === "context-pack"}
                  onClick={() =>
                    void runAction(
                      "context-pack",
                      () => api.getEpicChapterContextPack(project.id, Number(contextChapter) || nextChapter),
                      "上下文包已读取。",
                    )
                  }
                >
                  {busy === "context-pack" ? <Loader2 className="h-4 w-4 animate-spin" /> : <SearchCheck className="h-4 w-4" />}
                  读取上下文
                </button>
              </div>
            }
          >
            {contextPack ? (
              <KeyValueGrid
                rows={[
                  ["章节", asRecord(contextPack).chapterNumber ?? contextChapter],
                  ["上下文项", fieldCount(asRecord(contextPack).items ?? asRecord(contextPack).sections)],
                  ["溢出项", fieldCount(asRecord(asRecord(contextPack).metadata).overflowSections)],
                  ["排除项", fieldCount(asRecord(contextPack).excludedSections ?? asRecord(asRecord(contextPack).metadata).excludedSections)],
                  ["原始参考文本", fieldText(asRecord(contextPack).rawTextIncluded ?? asRecord(asRecord(contextPack).metadata).rawTextIncluded)],
                  ["摘要", jsonPreview(contextPack, 140)],
                ]}
              />
            ) : (
              <EmptyLine text="尚未读取上下文包。" />
            )}
          </Panel>
          <div className="space-y-3">
            <StatCard label="检索轨迹" value={String(countRows(detail, "retrieval_traces"))} icon={SearchCheck} />
            <StatCard label="模式卡结果" value={String(patternCards.length)} icon={Database} />
            <StatCard label="机制画像卡数" value={String(referenceProfile?.source_card_count ?? 0)} icon={BookOpenText} />
            <StatCard label="引用安全警告" value={String(dashboard?.reference_safety_warnings ?? 0)} icon={AlertTriangle} />
          </div>
          <Panel title="检索轨迹" icon={Database}>
            <div className="overflow-hidden rounded-lg border border-gold-500/10">
              <table className="w-full text-left text-sm">
                <thead className="bg-base-800/70 text-xs text-warm-500">
                  <tr>
                    <th className="px-3 py-2">任务</th>
                    <th className="px-3 py-2">章节</th>
                    <th className="px-3 py-2">用卡</th>
                    <th className="px-3 py-2">原文</th>
                    <th className="px-3 py-2">时间</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gold-500/8">
                  {detail.retrieval_traces.slice(0, 12).map((trace) => {
                    const row = asRecord(trace);
                    return (
                      <tr key={String(row.id)} className="text-warm-300">
                        <td className="px-3 py-2">{traceTaskLabel(row)}</td>
                        <td className="px-3 py-2">{fieldText(row.chapter_number)}</td>
                        <td className="px-3 py-2">{fieldCount(row.used_card_ids)}</td>
                        <td className="px-3 py-2">{fieldText(row.raw_text_included)}</td>
                        <td className="px-3 py-2">{asDate(String(row.created_at ?? ""))}</td>
                      </tr>
                    );
                  })}
                  {detail.retrieval_traces.length === 0 && (
                    <tr>
                      <td className="px-3 py-4 text-warm-500" colSpan={5}>
                        暂无检索轨迹。
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </Panel>
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
            <div className="mt-5 overflow-hidden rounded-lg border border-gold-500/10">
              <table className="w-full text-left text-sm">
                <thead className="bg-base-800/70 text-xs text-warm-500">
                  <tr>
                    <th className="px-3 py-2">章节</th>
                    <th className="px-3 py-2">级别</th>
                    <th className="px-3 py-2">问题</th>
                    <th className="px-3 py-2">状态</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gold-500/8">
                  {detail.issues.slice(0, 12).map((issue) => {
                    const row = asRecord(issue);
                    return (
                      <tr key={String(row.id)} className="text-warm-300">
                        <td className="px-3 py-2">{fieldText(row.chapter_number)}</td>
                        <td className="px-3 py-2">{issueSeverityLabel(row.severity)}</td>
                        <td className="px-3 py-2">{fieldText(row.message ?? row.issue_type)}</td>
                        <td className="px-3 py-2">{statusLabel(String(row.status ?? "pending"))}</td>
                      </tr>
                    );
                  })}
                  {detail.issues.length === 0 && (
                    <tr>
                      <td className="px-3 py-4 text-warm-500" colSpan={4}>
                        暂无问题清单。
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
            <StatCard label="问题清单" value={String(countRows(detail, "issues"))} icon={AlertTriangle} />
          </div>
        </section>
      )}

      {tab === "dashboard" && (
        <section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
          <Panel title="生产仪表盘" icon={Database}>
            <KeyValueGrid
              rows={[
                ["当前字数", dashboard?.current_words ?? 0],
                ["已完成章节", dashboard?.completed_chapters ?? 0],
                ["已完成分卷", dashboard?.completed_volumes ?? 0],
                ["圣经覆盖率", pct(dashboard?.bible_coverage_score)],
                ["大纲执行率", pct(dashboard?.outline_execution_rate)],
                ["世界图谱使用率", pct(dashboard?.world_atlas_usage_rate)],
                ["剩余预估费用", `$${(dashboard?.estimated_remaining_cost ?? 0).toFixed(3)}`],
                ["重试状态", statusLabel(dashboard?.retry_status)],
              ]}
            />
            <div className="mt-5">
              {Object.keys(currentJob).length > 0 ? (
                <KeyValueGrid
                  rows={[
                    ["当前任务", fieldText(currentJob.job_type ?? currentJob.role)],
                    ["任务状态", statusLabel(String(currentJob.status ?? ""))],
                    ["进度", pct(dashboard?.job_progress)],
                    ["已完成步骤", currentJob.completed_steps],
                    ["总步骤", currentJob.total_steps],
                    ["最新错误", jsonPreview(dashboard?.job_errors?.[0], 120)],
                  ]}
                />
              ) : (
                <EmptyLine text="暂无运行中的生产任务。" />
              )}
            </div>
          </Panel>
          <div className="space-y-3">
            <StatCard label="官僚一致性问题" value={String(dashboard?.bureaucracy_consistency_issues ?? 0)} icon={AlertTriangle} />
            <StatCard label="角色弧光缺口" value={String(dashboard?.character_arc_gaps ?? 0)} icon={AlertTriangle} />
            <StatCard label="女主挂件风险" value={String(dashboard?.heroine_appendage_risk ?? 0)} icon={AlertTriangle} />
            <StatCard label="核心角色消失" value={String(dashboard?.a_tier_disappearance_warnings ?? 0)} icon={AlertTriangle} />
            <StatCard label="存储状态错误" value={String(dashboard?.storage_state_errors ?? 0)} icon={AlertTriangle} />
            <StatCard label="过拟合警告" value={String(dashboard?.reference_overfit_warnings ?? 0)} icon={AlertTriangle} />
          </div>
          <Panel title="最新运行证据" icon={ShieldCheck}>
            <KeyValueGrid
              rows={[
                ["最新检索任务", latestTrace ? traceTaskLabel(latestTrace) : ""],
                ["最新检索章节", latestTrace?.chapter_number],
                ["最新问题级别", issueSeverityLabel(latestIssue?.severity)],
                ["最新问题", latestIssue?.message ?? latestIssue?.issue_type],
                ["下载导入状态", jsonPreview(dashboard?.download_import_status, 120)],
                ["模式卡数量", dashboard?.generated_pattern_card_count ?? 0],
              ]}
            />
          </Panel>
        </section>
      )}

      {tab === "export" && (
        <section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
          <Panel title="文件导出" icon={Download}>
            <div className="grid gap-3 sm:grid-cols-2">
              <ActionButton
                label="全书圣经元数据"
                busy={busy === "export-meta-bible"}
                icon={FileCheck2}
                onClick={() =>
                  void runAction(
                    "export-meta-bible",
                    () => api.getEpicBibleExportMetadata(project.id, "markdown"),
                    "全书圣经导出元数据已生成。",
                  )
                }
              />
              <ActionButton
                label="正文元数据"
                busy={busy === "export-meta-novel"}
                icon={ScrollText}
                onClick={() =>
                  void runAction(
                    "export-meta-novel",
                    () => api.getEpicNovelExportMetadata(project.id, "markdown"),
                    "正文导出元数据已生成。",
                  )
                }
              />
              <ActionButton
                label="下载全书圣经"
                busy={busy === "download-bible"}
                icon={Download}
                onClick={() => void runAction("download-bible", () => api.downloadEpicBibleExport(project.id), "全书圣经下载已触发。")}
              />
              <ActionButton
                label="下载正文"
                busy={busy === "download-novel"}
                icon={Download}
                onClick={() => void runAction("download-novel", () => api.downloadEpicNovelExport(project.id), "正文下载已触发。")}
              />
            </div>
            <div className="mt-5 flex flex-wrap gap-2">
              <select
                className="auth-input min-w-[180px]"
                value={artifactType}
                onChange={(event) => setArtifactType(event.target.value as EpicArtifactType)}
              >
                {artifactOptions.map((item) => (
                  <option key={item.type} value={item.type}>
                    {item.label}
                  </option>
                ))}
              </select>
              {selectedArtifact.needsVolume && (
                <input
                  className="auth-input w-24"
                  value={volumeNumber}
                  onChange={(event) => setVolumeNumber(event.target.value)}
                />
              )}
              <button
                type="button"
                className="btn-ghost rounded-lg"
                disabled={busy === "export-meta-artifact"}
                onClick={() =>
                  void runAction(
                    "export-meta-artifact",
                    () => api.getEpicArtifactExportMetadata(project.id, artifactType, selectedArtifact.needsVolume ? volume : undefined),
                    "导出元数据已生成。",
                  )
                }
              >
                {busy === "export-meta-artifact" ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileCheck2 className="h-4 w-4" />}
                生成元数据
              </button>
              <button
                type="button"
                className="btn-gold rounded-lg"
                disabled={busy === "download-artifact"}
                onClick={() =>
                  void runAction(
                    "download-artifact",
                    () => api.downloadEpicArtifactExport(project.id, artifactType, selectedArtifact.needsVolume ? volume : undefined),
                    "文件下载已触发。",
                  )
                }
              >
                {busy === "download-artifact" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                下载文件
              </button>
            </div>
            <div className="mt-5 rounded-lg border border-gold-500/10 px-4 py-4 text-sm leading-7 text-warm-300">
              {exportLabel(exportMetadata)}
            </div>
          </Panel>
          <div className="space-y-3">
            <StatCard label="圣经分节" value={String(countRows(detail, "bible_sections"))} icon={FileCheck2} />
            <StatCard label="已验收章节" value={String(countRows(detail, "chapters"))} icon={ScrollText} />
            <StatCard label="验收报告" value={String(countRows(detail, "acceptance_reports"))} icon={ShieldCheck} />
            <StatCard label="检索轨迹" value={String(countRows(detail, "retrieval_traces"))} icon={SearchCheck} />
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
