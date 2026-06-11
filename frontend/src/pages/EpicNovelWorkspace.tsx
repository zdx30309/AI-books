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
  { id: "foundation", label: "Foundation" },
  { id: "reference", label: "Reference" },
  { id: "chapters", label: "Chapters" },
  { id: "qa", label: "QA" },
  { id: "logs", label: "Logs" },
];

const initialForm: CreateFormState = {
  title: "Epic Novel",
  premise: "",
  targetWords: "3000000",
  targetVolumes: "12",
  targetChapters: "700",
  generationMode: "standard",
};

function actionError(error: unknown) {
  if (error instanceof ApiError) return `[${error.status}] ${error.detail}`;
  if (error instanceof Error) return error.message;
  return "Action failed";
}

function formatNumber(value: number | null | undefined) {
  return new Intl.NumberFormat("en-US").format(value ?? 0);
}

function pct(value: number | null | undefined) {
  return `${Math.round((value ?? 0) * 100)}%`;
}

function asDate(value: string | null | undefined) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
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

function modelLabel(project: EpicNovelProject) {
  return [project.editor_model, project.author_model, project.cheap_model]
    .filter(Boolean)
    .join(" / ");
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
      {status}
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
      setError("Premise is required");
      return;
    }
    setCreating(true);
    setError(null);
    const body: EpicNovelCreateRequest = {
      title: form.title.trim() || "Epic Novel",
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
            Epic Novel Engine
          </div>
          <h1 className="font-serif text-3xl text-warm-100">Long-form control room</h1>
        </div>
        <button type="button" className="btn-ghost rounded-lg" onClick={() => void load()}>
          <RefreshCcw className="h-4 w-4" strokeWidth={1.7} />
          Refresh
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
              Loading projects
            </div>
          ) : items.length === 0 ? (
            <div className="card-warm rounded-lg px-5 py-6 text-sm text-warm-400">
              No Epic projects yet.
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
                      {formatNumber(project.total_words)} words / chapter {project.current_chapter}
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
            <h2 className="font-serif text-xl">Create project</h2>
          </div>
          <label className="mb-3 block text-sm text-warm-300">
            Title
            <input
              className="auth-input mt-2"
              value={form.title}
              onChange={(event) => setForm((prev) => ({ ...prev, title: event.target.value }))}
            />
          </label>
          <label className="mb-3 block text-sm text-warm-300">
            Premise
            <textarea
              className="auth-input mt-2 min-h-[150px] resize-y"
              value={form.premise}
              onChange={(event) => setForm((prev) => ({ ...prev, premise: event.target.value }))}
            />
          </label>
          <div className="grid gap-3 sm:grid-cols-3">
            <label className="block text-sm text-warm-300">
              Words
              <input
                className="auth-input mt-2"
                value={form.targetWords}
                onChange={(event) => setForm((prev) => ({ ...prev, targetWords: event.target.value }))}
              />
            </label>
            <label className="block text-sm text-warm-300">
              Volumes
              <input
                className="auth-input mt-2"
                value={form.targetVolumes}
                onChange={(event) => setForm((prev) => ({ ...prev, targetVolumes: event.target.value }))}
              />
            </label>
            <label className="block text-sm text-warm-300">
              Chapters
              <input
                className="auth-input mt-2"
                value={form.targetChapters}
                onChange={(event) => setForm((prev) => ({ ...prev, targetChapters: event.target.value }))}
              />
            </label>
          </div>
          <label className="mt-3 block text-sm text-warm-300">
            Mode
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
              <option value="cheap">cheap</option>
              <option value="standard">standard</option>
              <option value="high_quality">high_quality</option>
            </select>
          </label>
          <button type="submit" className="btn-gold mt-5 w-full rounded-lg" disabled={creating}>
            {creating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            Create
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

  const load = useCallback(async () => {
    setError(null);
    try {
      const [projectRes, dashboardRes] = await Promise.all([
        api.getEpicNovelProject(id),
        api.getEpicNovelDashboard(id),
      ]);
      setDetail(projectRes);
      setDashboard(dashboardRes);
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
        Loading Epic project
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
            Back to Epic projects
          </button>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="font-serif text-3xl text-warm-100">{project.title}</h1>
            <StatusPill status={project.status} />
          </div>
          <div className="mt-3 max-w-4xl text-sm leading-7 text-warm-400">{project.premise}</div>
          <div className="mt-3 text-xs text-warm-500">{modelLabel(project) || "models pending"}</div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn-ghost rounded-lg" onClick={() => void load()}>
            <RefreshCcw className="h-4 w-4" strokeWidth={1.7} />
            Refresh
          </button>
          <button type="button" className="btn-ghost rounded-lg" onClick={() => void api.downloadEpicBibleExport(project.id)}>
            <FileCheck2 className="h-4 w-4" strokeWidth={1.7} />
            Bible
          </button>
          <button type="button" className="btn-gold rounded-lg" onClick={() => void api.downloadEpicNovelExport(project.id)}>
            <Download className="h-4 w-4" strokeWidth={1.7} />
            Novel
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
        <StatCard label="Words" value={formatNumber(project.total_words)} icon={BookOpenText} />
        <StatCard label="Chapters" value={`${dashboard?.completed_chapters ?? 0}/${project.target_chapters}`} icon={Layers3} />
        <StatCard label="Bible Coverage" value={pct(dashboard?.bible_coverage_score)} icon={ShieldCheck} />
        <StatCard label="Estimated Cost" value={`$${(dashboard?.cost_estimate ?? 0).toFixed(3)}`} icon={Database} />
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
              <h2 className="font-serif text-xl">Foundation pipeline</h2>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <ActionButton
                label="Worldview tournament"
                busy={busy === "worldview"}
                icon={Sparkles}
                onClick={() =>
                  void runAction("worldview", () => api.runEpicWorldviewTournament(project.id), "Worldview candidates ready")
                }
              />
              <ActionButton
                label="World Bible"
                busy={busy === "world-bible"}
                icon={BookOpenText}
                onClick={() => void runAction("world-bible", () => api.generateEpicWorldBible(project.id), "World Bible ready")}
              />
              <ActionButton
                label="World Atlas"
                busy={busy === "atlas"}
                icon={Database}
                onClick={() => void runAction("atlas", () => api.generateEpicWorldAtlas(project.id), "World Atlas ready")}
              />
              <ActionButton
                label="Power system"
                busy={busy === "power"}
                icon={ShieldCheck}
                onClick={() => void runAction("power", () => api.forgeEpicPowerSystem(project.id), "Power system ready")}
              />
              <ActionButton
                label="Ensemble cast"
                busy={busy === "ensemble"}
                icon={Layers3}
                onClick={() => void runAction("ensemble", () => api.generateEpicEnsembleCast(project.id), "Ensemble cast ready")}
              />
              <ActionButton
                label="Global spine"
                busy={busy === "spine"}
                icon={GitBranch}
                onClick={() => void runAction("spine", () => api.generateEpicGlobalSpine(project.id), "Global spine ready")}
              />
              <ActionButton
                label="Bible manifest"
                busy={busy === "manifest"}
                icon={FileCheck2}
                onClick={() => void runAction("manifest", () => api.generateEpicBibleManifest(project.id), "Bible manifest ready")}
              />
              <ActionButton
                label="Volume contracts"
                busy={busy === "volumes"}
                icon={ScrollText}
                onClick={() => void runAction("volumes", () => api.generateEpicVolumeContracts(project.id), "Volume contracts ready")}
              />
              <ActionButton
                label="Arc ledgers"
                busy={busy === "arc-ledgers"}
                icon={GitBranch}
                onClick={() => void runAction("arc-ledgers", () => api.generateEpicArcLedgers(project.id), "Arc ledgers ready")}
              />
            </div>
          </div>

          <div className="space-y-3">
            <StatCard label="World Bible" value={detail.world_bible ? "ready" : "missing"} icon={BookOpenText} />
            <StatCard label="World Atlas" value={detail.world_atlas ? "ready" : "missing"} icon={Database} />
            <StatCard label="Bible Sections" value={String(countRows(detail, "bible_sections"))} icon={FileCheck2} />
            <StatCard label="Volumes" value={String(countRows(detail, "volume_contracts"))} icon={ScrollText} />
          </div>
        </section>
      )}

      {tab === "reference" && (
        <section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
          <div className="card-warm rounded-lg px-5 py-5">
            <div className="mb-4 flex items-center gap-2 text-warm-100">
              <SearchCheck className="h-4 w-4 text-gold-300" strokeWidth={1.7} />
              <h2 className="font-serif text-xl">Reference corpus</h2>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <ActionButton
                label="Detect SoNovel"
                busy={busy === "detect"}
                icon={SearchCheck}
                onClick={() => void runAction("detect", () => api.detectEpicReferenceDownloader(), "Downloader detection complete")}
              />
              <ActionButton
                label="Scan downloads"
                busy={busy === "scan"}
                icon={RefreshCcw}
                onClick={() => void runAction("scan", () => api.scanEpicReferenceDownloads(), "Download scan complete")}
              />
              <ActionButton
                label="Import downloaded"
                busy={busy === "import"}
                icon={Download}
                onClick={() => void runAction("import", () => api.importEpicDownloadedReferences(), "Downloaded files imported")}
              />
              <ActionButton
                label="Build profile"
                busy={busy === "profile"}
                icon={Database}
                onClick={() => void runAction("profile", () => api.buildEpicReferenceProfile(), "Reference profile ready")}
              />
            </div>
            <div className="mt-5 overflow-hidden rounded-lg border border-gold-500/10">
              <table className="w-full text-left text-sm">
                <thead className="bg-base-800/70 text-xs text-warm-500">
                  <tr>
                    <th className="px-3 py-2">File</th>
                    <th className="px-3 py-2">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gold-500/8">
                  {scanRows.map((row, index) => (
                    <tr key={`${row.path ?? index}`} className="text-warm-300">
                      <td className="max-w-[520px] truncate px-3 py-2">{String(row.path ?? row.name ?? "-")}</td>
                      <td className="px-3 py-2">{String(row.status ?? row.imported ?? "found")}</td>
                    </tr>
                  ))}
                  {scanRows.length === 0 && (
                    <tr>
                      <td className="px-3 py-4 text-warm-500" colSpan={2}>
                        No scan rows loaded.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <div className="space-y-3">
            <StatCard label="Pattern Cards" value={String(dashboard?.generated_pattern_card_count ?? 0)} icon={Database} />
            <StatCard label="Reference Warnings" value={String(dashboard?.reference_safety_warnings ?? 0)} icon={AlertTriangle} />
            <StatCard label="Overfit Warnings" value={String(dashboard?.reference_overfit_warnings ?? 0)} icon={AlertTriangle} />
          </div>
        </section>
      )}

      {tab === "chapters" && (
        <section className="grid gap-6 xl:grid-cols-[420px_minmax(0,1fr)]">
          <div className="card-warm rounded-lg px-5 py-5">
            <div className="mb-4 flex items-center gap-2 text-warm-100">
              <ScrollText className="h-4 w-4 text-gold-300" strokeWidth={1.7} />
              <h2 className="font-serif text-xl">Chapter execution</h2>
            </div>
            <div className="grid gap-3 sm:grid-cols-3 xl:grid-cols-1">
              <label className="block text-sm text-warm-300">
                Volume
                <input className="auth-input mt-2" value={volumeNumber} onChange={(event) => setVolumeNumber(event.target.value)} />
              </label>
              <label className="block text-sm text-warm-300">
                Batch from
                <input className="auth-input mt-2" value={batchFrom} onChange={(event) => setBatchFrom(event.target.value)} />
              </label>
              <label className="block text-sm text-warm-300">
                Batch to
                <input className="auth-input mt-2" value={batchTo} onChange={(event) => setBatchTo(event.target.value)} />
              </label>
            </div>
            <div className="mt-4 grid gap-3">
              <ActionButton
                label={`Compile volume ${volume}`}
                busy={busy === "compile"}
                icon={FileCheck2}
                onClick={() =>
                  void runAction(
                    "compile",
                    () => api.compileEpicChapterContracts(project.id, volume),
                    `Volume ${volume} chapter contracts ready`,
                  )
                }
              />
              <ActionButton
                label={`Execute chapter ${nextChapter}`}
                busy={busy === "execute"}
                icon={Play}
                onClick={() =>
                  void runAction(
                    "execute",
                    () => api.executeEpicChapter(project.id, nextChapter),
                    `Chapter ${nextChapter} accepted`,
                  )
                }
              />
              <ActionButton
                label={`Batch ${batchFrom}-${batchTo}`}
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
                    "Batch generation complete",
                  )
                }
              />
              <ActionButton
                label="Outline CI"
                busy={busy === "outline-ci"}
                icon={ShieldCheck}
                onClick={() => void runAction("outline-ci", () => api.runEpicOutlineCi(project.id), "Outline CI complete")}
              />
              <ActionButton
                label="Repair outline"
                busy={busy === "outline-repair"}
                icon={AlertTriangle}
                onClick={() => void runAction("outline-repair", () => api.repairEpicOutline(project.id), "Outline repair complete")}
              />
            </div>
          </div>

          <div className="space-y-4">
            <div className="overflow-hidden rounded-lg border border-gold-500/10">
              <table className="w-full text-left text-sm">
                <thead className="bg-base-800/70 text-xs text-warm-500">
                  <tr>
                    <th className="px-3 py-2">Chapter</th>
                    <th className="px-3 py-2">Title</th>
                    <th className="px-3 py-2">Status</th>
                    <th className="px-3 py-2">Words</th>
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
                        No chapters accepted yet.
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
              <h2 className="font-serif text-xl">Acceptance reports</h2>
            </div>
            <div className="overflow-hidden rounded-lg border border-gold-500/10">
              <table className="w-full text-left text-sm">
                <thead className="bg-base-800/70 text-xs text-warm-500">
                  <tr>
                    <th className="px-3 py-2">Chapter</th>
                    <th className="px-3 py-2">Status</th>
                    <th className="px-3 py-2">Outline</th>
                    <th className="px-3 py-2">Continuity</th>
                    <th className="px-3 py-2">Anti-cliche</th>
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
                        No acceptance reports yet.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <div className="space-y-3">
            <StatCard label="Power Violations" value={String(dashboard?.power_violations ?? 0)} icon={AlertTriangle} />
            <StatCard label="Continuity Issues" value={String(dashboard?.continuity_issues ?? 0)} icon={AlertTriangle} />
            <StatCard label="Unpaid Foreshadows" value={String(dashboard?.unpaid_foreshadows ?? 0)} icon={GitBranch} />
            <StatCard label="Repair Count" value={String(dashboard?.repair_count ?? 0)} icon={CheckCircle2} />
          </div>
        </section>
      )}

      {tab === "logs" && (
        <section className="card-warm rounded-lg px-5 py-5">
          <div className="mb-4 flex items-center gap-2 text-warm-100">
            <Database className="h-4 w-4 text-gold-300" strokeWidth={1.7} />
            <h2 className="font-serif text-xl">Generation logs</h2>
          </div>
          <div className="overflow-hidden rounded-lg border border-gold-500/10">
            <table className="w-full text-left text-sm">
              <thead className="bg-base-800/70 text-xs text-warm-500">
                <tr>
                  <th className="px-3 py-2">Action</th>
                  <th className="px-3 py-2">Model</th>
                  <th className="px-3 py-2">Chapter</th>
                  <th className="px-3 py-2">Cost</th>
                  <th className="px-3 py-2">Time</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gold-500/8">
                {detail.logs.map((log) => (
                  <tr key={log.id} className="text-warm-300">
                    <td className="px-3 py-2">{log.action}</td>
                    <td className="px-3 py-2">{log.model ?? log.provider ?? "-"}</td>
                    <td className="px-3 py-2">{log.chapter_number ?? "-"}</td>
                    <td className="px-3 py-2">${log.estimated_cost.toFixed(4)}</td>
                    <td className="px-3 py-2">{asDate(log.created_at)}</td>
                  </tr>
                ))}
                {detail.logs.length === 0 && (
                  <tr>
                    <td className="px-3 py-4 text-warm-500" colSpan={5}>
                      No logs yet.
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
