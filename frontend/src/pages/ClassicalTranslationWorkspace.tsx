import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  AlertTriangle,
  BookOpenText,
  CheckCircle2,
  ChevronRight,
  Download,
  FileText,
  Loader2,
  Pause,
  Play,
  Plus,
  RotateCcw,
  Sparkles,
  Trash2,
  UploadCloud,
} from "lucide-react";
import { api } from "@/api/client";
import type {
  ChapterResponse,
  ClassicalSourceResponse,
  ClassicalWorkspaceStateResponse,
  PublicDomainRewriteSource,
  TaskStatusResponse,
} from "@/api/types";

type ScopeMode = "characters" | "chapters" | "complete";
type OutputTab = "translation" | "source";

interface SourceDraft {
  title: string;
  text: string;
  file: File | null;
  author: string;
  rightsNote: string;
}

const DEFAULT_RIGHTS_NOTE = "用户确认该原文属于公版/公共领域";

function emptySource(): SourceDraft {
  return { title: "", text: "", file: null, author: "", rightsNote: DEFAULT_RIGHTS_NOTE };
}

function countChars(value: string) {
  return value.replace(/\s+/g, "").length;
}

function toPayload(item: SourceDraft): PublicDomainRewriteSource {
  return {
    title: item.title.trim(),
    text: item.text.trim(),
    author: item.author.trim() || null,
    rights_note: item.rightsNote.trim() || DEFAULT_RIGHTS_NOTE,
  };
}

function isActive(status?: string) {
  return Boolean(status && ["pending", "processing", "writing_chapters"].includes(status));
}

export function ClassicalTranslationWorkspace() {
  const navigate = useNavigate();
  const { taskId: routeTaskId } = useParams<{ taskId: string }>();
  const [sources, setSources] = useState<SourceDraft[]>([emptySource()]);
  const [targetTitle, setTargetTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [defaultPrompt, setDefaultPrompt] = useState("");
  const [scopeMode, setScopeMode] = useState<ScopeMode>("characters");
  const [previewChars, setPreviewChars] = useState("3000");
  const [previewChapters, setPreviewChapters] = useState("1");
  const [confirmed, setConfirmed] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [taskId, setTaskId] = useState<string | null>(routeTaskId ?? null);
  const [workspaceState, setWorkspaceState] = useState<ClassicalWorkspaceStateResponse | null>(null);
  const [restoring, setRestoring] = useState(Boolean(routeTaskId));
  const [continuing, setContinuing] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [pausing, setPausing] = useState(false);
  const [localPrompt, setLocalPrompt] = useState("");
  const [localSegment, setLocalSegment] = useState("all");
  const [localWorking, setLocalWorking] = useState(false);
  const [task, setTask] = useState<TaskStatusResponse | null>(null);
  const [chapters, setChapters] = useState<ChapterResponse[]>([]);
  const [selectedChapter, setSelectedChapter] = useState(1);
  const [sourceView, setSourceView] = useState<ClassicalSourceResponse | null>(null);
  const [sourceText, setSourceText] = useState("");
  const [loadingMoreSource, setLoadingMoreSource] = useState(false);
  const [outputTab, setOutputTab] = useState<OutputTab>("translation");

  useEffect(() => {
    api.getClassicalTranslationSettings()
      .then((result) => {
        setDefaultPrompt(result.default_prompt);
        setPrompt((value) => value || result.default_prompt);
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : "默认翻译 Prompt 加载失败"));
  }, []);

  useEffect(() => {
    if (!routeTaskId) return;
    setRestoring(true);
    setError("");
    api.getClassicalWorkspaceState(routeTaskId)
      .then((state) => {
        setWorkspaceState(state);
        setTaskId(state.task_id);
        setTargetTitle(state.target_title);
        setPrompt(state.custom_prompt);
        setScopeMode(state.scope_mode);
        setPreviewChars(String(state.preview_target_chars || 3000));
        setPreviewChapters(String(state.preview_chapters || 1));
        setConfirmed(state.public_domain_confirmed);
        setSources(state.sources.map((item) => ({
          title: item.title,
          text: "",
          file: null,
          author: item.author || "",
          rightsNote: item.rights_note || DEFAULT_RIGHTS_NOTE,
        })));
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : "工作台状态恢复失败"))
      .finally(() => setRestoring(false));
  }, [routeTaskId]);

  const preparedSources = useMemo(
    () => sources.filter((item) => item.title.trim() && (item.text.trim() || item.file)),
    [sources],
  );
  const fileMode = preparedSources.length > 0 && preparedSources.every((item) => item.file);
  const mixedMode = preparedSources.some((item) => item.file) && !fileMode;
  const scopeValueValid =
    scopeMode === "complete" ||
    (scopeMode === "characters" && Number(previewChars) >= 500 && Number(previewChars) <= 200000) ||
    (scopeMode === "chapters" && Number(previewChapters) >= 1 && Number(previewChapters) <= 5000);
  const canSubmit =
    !routeTaskId &&
    preparedSources.length >= 1 &&
    preparedSources.length <= 10 &&
    !mixedMode &&
    prompt.trim().length > 0 &&
    scopeValueValid &&
    confirmed &&
    !loading;

  const updateSource = (index: number, patch: Partial<SourceDraft>) => {
    setSources((items) => items.map((item, position) => (position === index ? { ...item, ...patch } : item)));
  };

  const loadSourceChapter = async (id: string, chapterNumber: number) => {
    const result = await api.getClassicalTaskSource(id, chapterNumber, 0);
    setSourceView(result);
    setSourceText(result.selected_text);
    setLocalSegment("all");
  };

  useEffect(() => {
    if (!taskId) return;
    let stopped = false;
    let timer: number | undefined;
    const refresh = async () => {
      try {
        const [taskResult, chapterResult, stateResult] = await Promise.all([
          api.getTask(taskId),
          api.getChapters(taskId),
          api.getClassicalWorkspaceState(taskId),
        ]);
        if (stopped) return;
        setTask(taskResult);
        setChapters(chapterResult.chapters);
        setWorkspaceState(stateResult);
        if (chapterResult.chapters.length > 0) {
          setSelectedChapter((current) => (
            chapterResult.chapters.some((item) => item.chapter_num === current)
              ? current
              : chapterResult.chapters[0].chapter_num
          ));
        }
      } catch (reason) {
        if (!stopped) setError(reason instanceof Error ? reason.message : "翻译状态读取失败");
      }
    };
    void refresh();
    timer = window.setInterval(() => {
      if (!stopped) void refresh();
    }, 2500);
    return () => {
      stopped = true;
      if (timer !== undefined) window.clearInterval(timer);
    };
  }, [taskId]);

  useEffect(() => {
    if (!taskId) return;
    void loadSourceChapter(taskId, selectedChapter).catch((reason) => {
      setError(reason instanceof Error ? reason.message : "古文原文读取失败");
    });
  }, [taskId, selectedChapter]);

  const submit = async () => {
    setError("");
    if (!canSubmit) {
      setError(
        mixedMode
          ? "同一个任务请统一使用文件上传或正文粘贴。"
          : "请填写原文、Prompt 和试译范围，并勾选公版确认。",
      );
      return;
    }
    setLoading(true);
    setTask(null);
    setChapters([]);
    setSourceView(null);
    setSourceText("");
    setSelectedChapter(1);
    setOutputTab("translation");
    const common = {
      public_domain_confirmed: true,
      generation_mode: "classical_vernacular" as const,
      research_use_confirmed: false,
      target_title: targetTitle.trim() || null,
      output_style: "古文完整翻译",
      adaptation_profile: {
        custom_prompt: prompt.trim(),
        scope_mode: scopeMode,
        preview_target_chars: scopeMode === "characters" ? Number(previewChars) : 0,
        preview_chapters: scopeMode === "chapters" ? Number(previewChapters) : 0,
      },
    };
    try {
      const response = fileMode
        ? await api.generatePublicDomainRewriteFromFiles(
            preparedSources.map((item) => item.file).filter(Boolean) as File[],
            {
              ...common,
              sources: preparedSources.map((item) => ({
                title: item.title.trim(),
                author: item.author.trim() || null,
                rights_note: item.rightsNote.trim() || DEFAULT_RIGHTS_NOTE,
              })),
            },
          )
        : await api.generatePublicDomainRewrite({
            ...common,
            sources: preparedSources.map(toPayload),
          });
      setTaskId(response.task_id);
      navigate(`/public-domain-classical-vernacular/${response.task_id}`, { replace: true });
      setLoading(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "古文白话任务创建失败");
      setLoading(false);
    }
  };

  const selectedTranslation = chapters.find((item) => item.chapter_num === selectedChapter);
  const localOriginal = preparedSources[0]?.text || "";
  const visibleSource = taskId ? sourceText : localOriginal;

  const loadMoreSource = async () => {
    if (!taskId || !sourceView?.has_more || sourceView.next_offset === null) return;
    setLoadingMoreSource(true);
    try {
      const next = await api.getClassicalTaskSource(taskId, selectedChapter, sourceView.next_offset);
      setSourceText((value) => value + next.selected_text);
      setSourceView(next);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "继续读取原文失败");
    } finally {
      setLoadingMoreSource(false);
    }
  };

  const downloadResult = async (format: "docx" | "txt" | "md") => {
    if (!taskId) return;
    try {
      await api.downloadFile(taskId, format);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "译稿下载失败");
    }
  };

  const refreshWorkspace = async () => {
    if (!taskId) return;
    const [state, taskResult, chapterResult] = await Promise.all([
      api.getClassicalWorkspaceState(taskId),
      api.getTask(taskId),
      api.getChapters(taskId),
    ]);
    setWorkspaceState(state);
    setTask(taskResult);
    setChapters(chapterResult.chapters);
  };

  const scopeRequest = () => ({
    scope_mode: scopeMode,
    preview_target_chars: scopeMode === "characters" ? Number(previewChars) : 0,
    preview_chapters: scopeMode === "chapters" ? Number(previewChapters) : 0,
    custom_prompt: prompt.trim(),
  });

  const continueCurrent = async () => {
    if (!taskId || continuing) return;
    setContinuing(true);
    setError("");
    try {
      await api.continueClassical(taskId, scopeRequest());
      await refreshWorkspace();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "断点续译启动失败");
    } finally {
      setContinuing(false);
    }
  };

  const pauseCurrent = async () => {
    if (!taskId || pausing) return;
    setPausing(true);
    setError("");
    try {
      await api.pauseClassical(taskId);
      await refreshWorkspace();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "暂停失败");
    } finally {
      setPausing(false);
    }
  };

  const resumeCurrent = async () => {
    if (!taskId || continuing) return;
    setContinuing(true);
    setError("");
    try {
      await api.resumeClassical(taskId);
      await refreshWorkspace();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "继续翻译失败");
    } finally {
      setContinuing(false);
    }
  };

  const restartCurrent = async () => {
    if (!taskId || restarting || !window.confirm("确定从原文第一章第一段重新开始吗？旧译文断点会清除，原文和 Prompt 会保留。")) return;
    setRestarting(true);
    setError("");
    try {
      await api.restartClassical(taskId, scopeRequest());
      setChapters([]);
      setSelectedChapter(1);
      await refreshWorkspace();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "重新开始失败");
    } finally {
      setRestarting(false);
    }
  };

  const runLocalRetranslation = async () => {
    if (!taskId || !localPrompt.trim() || localWorking) return;
    setLocalWorking(true);
    setError("");
    try {
      await api.retranslateClassicalSelection(taskId, {
        chapter_num: selectedChapter,
        segment_num: localSegment === "all" ? null : Number(localSegment),
        custom_prompt: localPrompt.trim(),
      });
      await refreshWorkspace();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "局部重译失败");
    } finally {
      setLocalWorking(false);
    }
  };

  return (
    <div className="space-y-5">
      <section className="border-b border-gold-500/10 pb-5">
        <div className="inline-flex items-center gap-2 rounded-full border border-emerald-400/25 px-4 py-2 text-xs text-emerald-100">
          <BookOpenText className="h-4 w-4" />
          先试译看效果，也可直接翻完整本
        </div>
        <h1 className="mt-3 font-serif text-4xl text-warm-50">古文白话翻译台</h1>
        <p className="mt-2 text-sm leading-7 text-warm-300">
          左侧放原文、调整 Prompt 和翻译范围，右侧实时查看白话译文与对应古文。完整翻译仍使用百万字串行分段和断点续跑。
        </p>
      </section>

      {error && (
        <div className="rounded-xl border border-red-400/30 bg-red-500/10 p-4 text-sm text-red-100">
          <div className="mb-1 flex items-center gap-2 font-medium"><AlertTriangle className="h-4 w-4" />处理提示</div>
          {error}
        </div>
      )}

      {restoring && (
        <div className="flex items-center gap-2 rounded-xl border border-gold-500/15 bg-base-900/60 p-4 text-sm text-warm-300">
          <Loader2 className="h-4 w-4 animate-spin text-gold-300" />正在恢复这本书的原文快照、Prompt、翻译范围和断点状态
        </div>
      )}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <section className="space-y-4">
          <div className="card-warm p-5">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h2 className="text-lg font-medium text-warm-100">1. 放入古文原文</h2>
                <p className="mt-1 text-xs text-warm-500">可粘贴，也可上传 TXT、Word、PDF、EPUB 或图片</p>
              </div>
              <span className="text-xs text-warm-500">{preparedSources.length}/10 份</span>
            </div>

            {workspaceState ? (
              <div className="space-y-3">
                {workspaceState.sources.map((source, index) => (
                  <div key={`${source.sha256}-${index}`} className="rounded-xl border border-emerald-400/20 bg-emerald-500/5 p-4">
                    <div className="flex items-start justify-between gap-4">
                      <div>
                        <div className="font-medium text-warm-100">{source.title}</div>
                        <div className="mt-1 text-xs text-warm-500">
                          {source.author || "作者未填"} · {source.char_count.toLocaleString("zh-CN")} 字 · {source.detected_chapters} 个原文章节
                        </div>
                      </div>
                      <CheckCircle2 className="h-5 w-5 shrink-0 text-emerald-300" />
                    </div>
                    <div className="mt-2 text-xs leading-6 text-warm-400">{source.rights_note || DEFAULT_RIGHTS_NOTE}</div>
                  </div>
                ))}
                <div className="rounded-xl border border-gold-500/15 bg-base-900/45 p-4 text-xs leading-6 text-warm-400">
                  原文快照、来源哈希、Prompt、翻译范围和分段断点均已随此任务保存。继续操作仍使用同一任务，不会重新上传或另建书稿。
                </div>
              </div>
            ) : (
            <div className="space-y-4">
              {sources.map((source, index) => (
                <div key={index} className="rounded-xl border border-gold-500/15 bg-base-900/45 p-4">
                  <div className="flex gap-3">
                    <input
                      value={source.title}
                      onChange={(event) => updateSource(index, { title: event.target.value })}
                      placeholder="作品或分卷名称"
                      className="min-w-0 flex-1 rounded-lg border border-gold-500/15 bg-base-900/80 px-3 py-2.5 text-sm text-warm-100 outline-none"
                    />
                    {sources.length > 1 && (
                      <button type="button" onClick={() => setSources((items) => items.filter((_, position) => position !== index))} className="btn-ghost px-3">
                        <Trash2 className="h-4 w-4" />
                      </button>
                    )}
                  </div>
                  <div className="mt-3 grid gap-3 sm:grid-cols-2">
                    <label className="flex cursor-pointer items-center justify-center gap-2 rounded-lg border border-dashed border-gold-500/25 px-3 py-3 text-sm text-gold-200">
                      <UploadCloud className="h-4 w-4" />
                      <span className="truncate">{source.file ? source.file.name : "上传原文文件"}</span>
                      <input
                        type="file"
                        accept=".txt,.md,.markdown,.docx,.pdf,.epub,application/epub+zip,.png,.jpg,.jpeg,.webp,.bmp"
                        className="hidden"
                        onChange={(event) => {
                          const file = event.target.files?.[0] ?? null;
                          updateSource(index, {
                            file,
                            text: "",
                            title: source.title || file?.name.replace(/\.[^.]+$/, "") || "",
                          });
                        }}
                      />
                    </label>
                    <input
                      value={source.author}
                      onChange={(event) => updateSource(index, { author: event.target.value })}
                      placeholder="作者（可空）"
                      className="rounded-lg border border-gold-500/15 bg-base-900/80 px-3 py-2.5 text-sm text-warm-100 outline-none"
                    />
                  </div>
                  <textarea
                    value={source.text}
                    onChange={(event) => updateSource(index, { text: event.target.value, file: null })}
                    placeholder="或直接粘贴古文正文……"
                    className="mt-3 min-h-[220px] w-full resize-y rounded-lg border border-gold-500/15 bg-base-900/80 px-4 py-3 text-sm leading-7 text-warm-100 outline-none"
                  />
                  <div className="mt-2 flex items-center justify-between text-xs text-warm-500">
                    <span>{source.file ? "文件将在服务器解析" : `${countChars(source.text).toLocaleString("zh-CN")} 字`}</span>
                    <details>
                      <summary className="cursor-pointer text-gold-200">公版说明</summary>
                      <input
                        value={source.rightsNote}
                        onChange={(event) => updateSource(index, { rightsNote: event.target.value })}
                        className="mt-2 w-full rounded-lg border border-gold-500/15 bg-base-900 px-3 py-2 text-warm-200 outline-none"
                      />
                    </details>
                  </div>
                </div>
              ))}
            </div>
            )}
            {!workspaceState && (
              <button type="button" onClick={() => setSources((items) => items.length < 10 ? [...items, emptySource()] : items)} disabled={sources.length >= 10} className="btn-ghost mt-3 inline-flex items-center gap-2 text-sm disabled:opacity-40">
                <Plus className="h-4 w-4" />添加分卷
              </button>
            )}
          </div>

          <div className="card-warm p-5">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div>
                <h2 className="text-lg font-medium text-warm-100">2. 调整翻译 Prompt</h2>
                <p className="mt-1 text-xs text-warm-500">修改这里即可对比不同翻译效果；完整性与断点规则由系统固定保护</p>
              </div>
              {!isActive(task?.status) && <button type="button" onClick={() => setPrompt(defaultPrompt)} className="btn-ghost inline-flex items-center gap-2 px-3 py-2 text-xs">
                <RotateCcw className="h-3.5 w-3.5" />恢复默认
              </button>}
            </div>
            <textarea
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              readOnly={isActive(task?.status) || (task?.status === "paused" && !workspaceState?.can_resume)}
              className="min-h-[210px] w-full resize-y rounded-xl border border-gold-500/15 bg-base-900/80 px-4 py-3 text-sm leading-7 text-warm-100 outline-none"
            />
            <div className="mt-2 text-right text-xs text-warm-500">{prompt.length.toLocaleString("zh-CN")} / 30,000 字</div>
          </div>

          <div className="card-warm p-5">
            <h2 className="text-lg font-medium text-warm-100">3. 选择本次翻译范围</h2>
            <div className="mt-4 grid gap-3 sm:grid-cols-3">
              {([
                ["characters", "按字数试译", "付少量费用先看效果"],
                ["chapters", "按章节试译", "按原书章节完整收尾"],
                ["complete", "完整翻译", "百万字自动连续处理"],
              ] as Array<[ScopeMode, string, string]>).map(([value, label, note]) => (
                <button
                  type="button"
                  key={value}
                  onClick={() => setScopeMode(value)}
                  disabled={isActive(task?.status) || (task?.status === "paused" && !workspaceState?.can_resume)}
                  className={`rounded-xl border p-4 text-left transition ${scopeMode === value ? "border-gold-300/50 bg-gold-500/10" : "border-gold-500/15 bg-base-900/45 hover:border-gold-300/30"}`}
                >
                  <div className="font-medium text-warm-100">{label}</div>
                  <div className="mt-1 text-xs leading-5 text-warm-500">{note}</div>
                </button>
              ))}
            </div>
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              <input
                value={targetTitle}
                onChange={(event) => setTargetTitle(event.target.value)}
                 readOnly={Boolean(workspaceState)}
                placeholder="译稿名称（可空）"
                className="rounded-lg border border-gold-500/15 bg-base-900/80 px-4 py-3 text-sm text-warm-100 outline-none"
              />
              {scopeMode === "characters" && (
                <label className="text-xs text-warm-400">
                  期望译文字数（500–200,000）
                  <input type="number" min={500} max={200000} value={previewChars} readOnly={isActive(task?.status) || (task?.status === "paused" && !workspaceState?.can_resume)} onChange={(event) => setPreviewChars(event.target.value)} className="mt-1 w-full rounded-lg border border-gold-500/15 bg-base-900/80 px-4 py-3 text-sm text-warm-100 outline-none" />
                </label>
              )}
              {scopeMode === "chapters" && (
                <label className="text-xs text-warm-400">
                  从开头试译多少章
                  <input type="number" min={1} max={5000} value={previewChapters} readOnly={isActive(task?.status) || (task?.status === "paused" && !workspaceState?.can_resume)} onChange={(event) => setPreviewChapters(event.target.value)} className="mt-1 w-full rounded-lg border border-gold-500/15 bg-base-900/80 px-4 py-3 text-sm text-warm-100 outline-none" />
                </label>
              )}
              {scopeMode === "complete" && (
                <div className="rounded-lg border border-emerald-400/20 bg-emerald-500/5 px-4 py-3 text-xs leading-6 text-emerald-100">
                  完整翻译不设置字数上限；每段完成即保存，可中断续跑。
                </div>
              )}
            </div>
            {scopeMode === "characters" && <p className="mt-2 text-xs text-warm-500">系统按完整语义段收尾，实际译文字数可能略高于选择值。</p>}

            {!workspaceState && <label className="mt-4 flex items-start gap-3 text-xs leading-6 text-warm-300">
              <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} className="mt-1 h-4 w-4 accent-amber-500" />
              我已确认上传内容属于公版或公共领域，可以用于本次翻译。
            </label>}
            {!workspaceState && <button type="button" onClick={submit} disabled={!canSubmit} className="btn-gold mt-4 flex w-full items-center justify-center gap-2 py-3 disabled:cursor-not-allowed disabled:opacity-45">
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
              {loading ? "正在创建任务" : scopeMode === "complete" ? "开始完整翻译" : "开始试译"}
            </button>}
            {workspaceState && (
              <div className="mt-4 rounded-xl border border-gold-500/15 bg-base-900/45 p-4 text-xs leading-6 text-warm-400">
                已恢复：{workspaceState.checkpoint_chapters} 个章节断点，{workspaceState.checkpoint_chars.toLocaleString("zh-CN")} 字译文，{workspaceState.translated_segments} 个已记录分段。
              </div>
            )}
          </div>
        </section>

        <section className="card-warm min-h-[760px] p-5">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gold-500/10 pb-4">
            <div>
              <h2 className="text-lg font-medium text-warm-100">翻译结果</h2>
              <p className="mt-1 text-xs text-warm-500">
                {task ? `${task.progress}% · ${task.current_step}` : "任务开始后，译文会按章节出现在这里"}
              </p>
            </div>
            {taskId && (
              <button type="button" onClick={() => navigate("/bookshelf")} className="btn-ghost inline-flex items-center gap-2 px-3 py-2 text-xs">
                返回书架<ChevronRight className="h-3.5 w-3.5" />
              </button>
            )}
          </div>

          {task?.status === "completed" && (
            <div className="mt-4 rounded-xl border border-emerald-400/20 bg-emerald-500/5 p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-medium text-emerald-100">
                    {task.bookshelf === "公版古文白话"
                      ? "完整译稿已加入“公版古文白话”书架"
                      : "试译结果已保存到“公版古文白话试译”书架"}
                  </div>
                  <div className="mt-1 text-xs text-warm-500">可直接下载 Word、TXT 或 Markdown。</div>
                </div>
                <div className="flex flex-wrap gap-2">
                  {(["docx", "txt", "md"] as const).map((format) => (
                    <button
                      key={format}
                      type="button"
                      onClick={() => void downloadResult(format)}
                      className="btn-ghost inline-flex items-center gap-2 px-3 py-2 text-xs"
                    >
                      <Download className="h-3.5 w-3.5" />
                      {format === "docx" ? "Word" : format === "txt" ? "TXT" : "Markdown"}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}

          {task && (
            <div className="mt-4">
              <div className="h-1.5 overflow-hidden rounded-full bg-base-900">
                <div className="h-full rounded-full bg-gradient-to-r from-amber-500 to-orange-400 transition-all" style={{ width: `${Math.max(2, task.progress)}%` }} />
              </div>
              {task.error_message && <div className="mt-3 rounded-lg border border-red-400/25 bg-red-500/10 p-3 text-xs leading-6 text-red-100">{task.error_message}</div>}
            </div>
          )}

          {taskId && task && (
            <div className="mt-4 rounded-xl border border-gold-400/25 bg-gold-500/5 p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-medium text-warm-100">
                    {isActive(task.status)
                      ? "翻译正在串行运行"
                      : task.status === "paused" && !workspaceState?.can_resume
                        ? "正在保存当前完整分段"
                        : task.status === "paused"
                          ? "任务已暂停，断点已保存"
                          : "可按左侧当前范围继续，或从原文开头重新开始"}
                  </div>
                  <div className="mt-1 text-xs leading-6 text-warm-500">
                    暂停只会在完整分段落盘后生效。重新开始会清除旧译文，从第一章第一段执行，但保留原文与当前 Prompt。
                  </div>
                </div>
                <div className="flex flex-wrap gap-2">
                  {isActive(task.status) && task.current_step.startsWith("正在只重译") ? (
                    <button type="button" disabled className="btn-ghost inline-flex items-center gap-2 px-4 py-2 text-sm opacity-60">
                      <Loader2 className="h-4 w-4 animate-spin" />局部重译处理中
                    </button>
                  ) : isActive(task.status) ? (
                    <button type="button" onClick={() => void pauseCurrent()} disabled={pausing} className="btn-gold inline-flex items-center gap-2 px-4 py-2 text-sm disabled:opacity-50">
                      {pausing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Pause className="h-4 w-4" />}
                      {pausing ? "正在请求暂停" : "暂停翻译"}
                    </button>
                  ) : task.status === "paused" ? (
                    <button type="button" onClick={() => void resumeCurrent()} disabled={continuing || !workspaceState?.can_resume} className="btn-gold inline-flex items-center gap-2 px-4 py-2 text-sm disabled:opacity-50">
                      {continuing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                      {workspaceState?.can_resume ? "从断点继续" : "等待分段保存"}
                    </button>
                  ) : !(workspaceState?.generation_complete && scopeMode === "complete") ? (
                    <button type="button" onClick={() => void continueCurrent()} disabled={continuing || !scopeValueValid || !prompt.trim()} className="btn-gold inline-flex items-center gap-2 px-4 py-2 text-sm disabled:opacity-50">
                      {continuing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                      {continuing ? "正在投递" : scopeMode === "complete" ? "从断点继续全文" : "按当前范围继续"}
                    </button>
                  ) : null}
                  {!isActive(task.status) && (
                    <button type="button" onClick={() => void restartCurrent()} disabled={restarting || (task.status === "paused" && !workspaceState?.can_resume) || !scopeValueValid || !prompt.trim()} className="btn-ghost inline-flex items-center gap-2 px-4 py-2 text-sm disabled:opacity-50">
                      {restarting ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}
                      {restarting ? "正在清除旧断点" : "从头重新开始"}
                    </button>
                  )}
                </div>
              </div>
            </div>
          )}

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <button type="button" onClick={() => setOutputTab("translation")} className={outputTab === "translation" ? "btn-gold px-4 py-2 text-sm" : "btn-ghost px-4 py-2 text-sm"}>白话译文</button>
            <button type="button" onClick={() => setOutputTab("source")} className={outputTab === "source" ? "btn-gold px-4 py-2 text-sm" : "btn-ghost px-4 py-2 text-sm"}>对应古文</button>
            {taskId && (chapters.length > 0 || (sourceView?.sections.length ?? 0) > 0) && (
              <select value={selectedChapter} onChange={(event) => { setSelectedChapter(Number(event.target.value)); setLocalSegment("all"); }} className="ml-auto max-w-[260px] rounded-lg border border-gold-500/15 bg-base-900 px-3 py-2 text-xs text-warm-200 outline-none">
                {(sourceView?.sections ?? chapters.map((item) => ({ number: item.chapter_num, title: item.title, source_title: "", char_count: item.word_count }))).map((item) => (
                  <option key={item.number} value={item.number}>第 {item.number} 章 · {item.title}</option>
                ))}
              </select>
            )}
          </div>

          {taskId && selectedTranslation && !isActive(task?.status) && (task?.status !== "paused" || workspaceState?.can_resume) && (
            <details className="mt-4 rounded-xl border border-gold-500/15 bg-base-900/45 p-4">
              <summary className="cursor-pointer text-sm font-medium text-warm-100">局部 Prompt 调试与重译</summary>
              <p className="mt-2 text-xs leading-6 text-warm-500">只重译当前章节或当前分段，其他章节和分段保持不变。新 Prompt 会完整注入所选付费分段。</p>
              <div className="mt-3 grid gap-3 sm:grid-cols-[180px_minmax(0,1fr)]">
                <select value={localSegment} onChange={(event) => setLocalSegment(event.target.value)} className="rounded-lg border border-gold-500/15 bg-base-900 px-3 py-2.5 text-sm text-warm-200 outline-none">
                  <option value="all">当前章节全部</option>
                  {(sourceView?.segments ?? []).map((item) => (
                    <option key={item.number} value={item.number}>第 {item.number} 段 · {item.char_count} 字</option>
                  ))}
                </select>
                <textarea value={localPrompt} onChange={(event) => setLocalPrompt(event.target.value)} placeholder="输入只作用于当前选择范围的翻译 Prompt" className="min-h-[110px] resize-y rounded-lg border border-gold-500/15 bg-base-900 px-3 py-2.5 text-sm leading-6 text-warm-100 outline-none" />
              </div>
              <button type="button" onClick={() => void runLocalRetranslation()} disabled={localWorking || !localPrompt.trim()} className="btn-gold mt-3 inline-flex items-center gap-2 px-4 py-2 text-sm disabled:opacity-50">
                {localWorking ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                {localWorking ? "正在投递局部重译" : localSegment === "all" ? "重译当前章节" : `重译第 ${localSegment} 段`}
              </button>
            </details>
          )}

          <div className="mt-4 min-h-[560px] max-h-[70vh] overflow-y-auto rounded-xl border border-gold-500/12 bg-base-950/70 p-5">
            {outputTab === "translation" ? (
              selectedTranslation ? (
                <article>
                  <h3 className="mb-5 font-serif text-2xl text-warm-50">{selectedTranslation.title}</h3>
                  <div className="whitespace-pre-wrap text-[15px] leading-8 text-warm-200">{selectedTranslation.content}</div>
                </article>
              ) : isActive(task?.status) ? (
                <div className="flex h-full min-h-[480px] flex-col items-center justify-center text-center text-warm-500">
                  <Loader2 className="mb-4 h-7 w-7 animate-spin text-gold-300" />
                  <div className="text-sm text-warm-300">正在翻译并保存首个结果</div>
                  <div className="mt-2 text-xs">每个章节完成后会自动显示，无需刷新页面。</div>
                </div>
              ) : (
                <div className="flex h-full min-h-[480px] flex-col items-center justify-center text-center text-warm-500">
                  <FileText className="mb-4 h-8 w-8 text-gold-300/70" />
                  <div className="text-sm text-warm-300">右侧将显示白话译文</div>
                  <div className="mt-2 max-w-sm text-xs leading-6">建议先选“按字数试译”，调整满意后再执行完整翻译。</div>
                </div>
              )
            ) : visibleSource ? (
              <article>
                <div className="mb-4 flex items-center justify-between gap-3">
                  <div>
                    <h3 className="font-serif text-xl text-warm-50">{sourceView?.selected_title || preparedSources[0]?.title || "古文原文"}</h3>
                    <div className="mt-1 text-xs text-warm-500">原文共 {sourceView?.selected_char_count.toLocaleString("zh-CN") || countChars(visibleSource).toLocaleString("zh-CN")} 字</div>
                  </div>
                  <CheckCircle2 className="h-5 w-5 text-emerald-300" />
                </div>
                <div className="whitespace-pre-wrap text-[15px] leading-8 text-warm-300">{visibleSource}</div>
                {sourceView?.has_more && (
                  <button type="button" onClick={loadMoreSource} disabled={loadingMoreSource} className="btn-ghost mt-5 flex w-full items-center justify-center gap-2 text-sm disabled:opacity-50">
                    {loadingMoreSource && <Loader2 className="h-4 w-4 animate-spin" />}继续加载本章原文
                  </button>
                )}
              </article>
            ) : (
              <div className="flex h-full min-h-[480px] items-center justify-center text-sm text-warm-500">粘贴或上传古文后，可在这里查看对应原文。</div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
