// 后端 API 客户端。dev 时走 vite proxy（/api → :8000），prod 时由部署侧反代。
// 自动从 localStorage 读 token 并加 Authorization 头。

import type {
  BatchCreateTaskRequest,
  BatchCreateTaskResponse,
  BatchDownloadRequest,
  BatchRegenerateTaskRequest,
  BatchRegenerateTaskResponse,
  BatchRetryTaskRequest,
  BatchRetryTaskResponse,
  AigcQualityReport,
  AigcReviewContentType,
  AigcReviewCalibrationSample,
  AigcReviewCalibrationVariant,
  AigcReviewComparison,
  AigcReviewPairedAnalysis,
  AigcReviewOptimizeResult,
  AigcReviewReport,
  AigcReviewStrategy,
  ExternalGreenRepairRequest,
  ExternalGreenRepairResult,
  OptimizeAigcReviewRequest,
  ChapterResponse,
  ChapterUpdateRequest,
  ClassicalReferenceTemplateResponse,
  ChaptersListResponse,
  CreateTaskRequest,
  CreateTaskResponse,
  CoverJobListResponse,
  CoverJobBatchRequest,
  CoverJobBatchResponse,
  CoverJobItemBatchDeleteRequest,
  CoverJobItemBatchRetryRequest,
  CoverJobResponse,
  DataAnalysisResponse,
  EpicBatchGenerationRequest,
  EpicBatchGenerationResponse,
  EpicChapter,
  EpicChapterContract,
  EpicDashboardResponse,
  EpicExportMetadata,
  EpicNovelCreateRequest,
  EpicNovelProject,
  EpicNovelProjectDetailResponse,
  EpicNovelProjectListResponse,
  EpicProjectStatusSummary,
  EpicReferenceBook,
  EpicReferenceBooklistItem,
  EpicReferenceDownloadTask,
  EpicReferenceExtractionControl,
  EpicReferenceExtractionCostEstimate,
  EpicReferenceMechanismProfile,
  EpicReferencePatternCard,
  EpicReferenceReadiness,
  LoginRequest,
  FamilyLongNovelBatchRequest,
  FamilyLongNovelCreateRequest,
  FamilyLongNovelExtendRequest,
  LongNovelAssistRequest,
  LongNovelAssistResponse,
  LongNovelBatchResponse,
  LongNovelChapter,
  LongNovelChapterPlan,
  LongNovelConflictLine,
  LongNovelContinuityIssue,
  LongNovelProject,
  LongNovelProjectDetailResponse,
  LongNovelProjectListResponse,
  MarkdownResponse,
  PolishParagraphRequest,
  PolishParagraphResponse,
  PublicDomainRewriteRequest,
  ClassicalSourceResponse,
  ClassicalTranslationSettingsResponse,
  CoverRegenerateRequest,
  RegenerateSelectionRequest,
  RegenerateSelectionResponse,
  RegisterRequest,
  ReferenceArticleBatchTaskRequest,
  ReferenceArticleTaskRequest,
  StageContentResponse,
  StageName,
  StatsResponse,
  SystemHealth,
  TaskMetadataUpdateRequest,
  TaskCategoryListResponse,
  TaskListResponse,
  TaskStatusResponse,
  TestingAnalysisResponse,
  TestingArticleResponse,
  TestingGenerateArticleRequest,
  TestingRewriteCompletedArticleRequest,
  TestingRewriteCompletedArticleResponse,
  TestingTaskResponse,
  TestingZhuqueFeedbackResponse,
  TokenResponse,
  UserPublic,
  WeReadAnalyzeRequest,
  WeReadAnalyzeResponse,
  WeReadBookRow,
  WeReadCompanyRequest,
  WeReadExcelImportResponse,
} from "./types";

const BASE = "/api/v1";
const TOKEN_KEY = "scriptbook.token";

class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(`[${status}] ${detail}`);
  }
}

function errorMessageFromBody(body: unknown, fallback: string) {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && "message" in detail) {
      const message = (detail as { message?: unknown }).message;
      if (typeof message === "string" && message) return message;
    }
    try {
      return JSON.stringify(detail);
    } catch {
      return fallback;
    }
  }
  return fallback;
}

export const tokenStore = {
  get(): string | null {
    return localStorage.getItem(TOKEN_KEY);
  },
  set(token: string) {
    localStorage.setItem(TOKEN_KEY, token);
  },
  clear() {
    localStorage.removeItem(TOKEN_KEY);
  },
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((init?.headers as Record<string, string>) ?? {}),
  };
  const token = tokenStore.get();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = errorMessageFromBody(body, detail);
    } catch {
      /* ignore */
    }
    if (res.status === 401) {
      tokenStore.clear();
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

async function download(path: string): Promise<{ blob: Blob; filename: string }> {
  const headers: Record<string, string> = {};
  const token = tokenStore.get();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const separator = path.includes("?") ? "&" : "?";
  const res = await fetch(`${BASE}${path}${separator}_=${Date.now()}`, {
    headers,
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = errorMessageFromBody(body, detail);
    } catch {
      /* ignore */
    }
    if (res.status === 401) tokenStore.clear();
    throw new ApiError(res.status, detail);
  }

  const disposition = res.headers.get("Content-Disposition") ?? "";
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  const asciiMatch = disposition.match(/filename="?([^";]+)"?/i);
  const rawName = utf8Match?.[1] ?? asciiMatch?.[1] ?? "book.md";
  const filename = decodeURIComponent(rawName);
  return { blob: await res.blob(), filename };
}

async function uploadFile<T>(
  path: string,
  file: File,
  fields?: Record<string, string>,
  extraFiles?: Record<string, File | null | undefined>,
): Promise<T> {
  const headers: Record<string, string> = {};
  const token = tokenStore.get();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const form = new FormData();
  form.append("file", file);
  Object.entries(extraFiles ?? {}).forEach(([key, value]) => {
    if (value) form.append(key, value);
  });
  Object.entries(fields ?? {}).forEach(([key, value]) => {
    form.append(key, value);
  });
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers,
    body: form,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = errorMessageFromBody(body, detail);
    } catch {
      /* ignore */
    }
    if (res.status === 401) tokenStore.clear();
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

async function uploadAigcPairedFiles(
  baselineFile: File,
  modifiedFile: File,
  baselineText: string,
  modifiedText: string,
  contentType: AigcReviewContentType,
  hashesConfirmed: boolean,
): Promise<AigcReviewPairedAnalysis> {
  const headers: Record<string, string> = {};
  const token = tokenStore.get();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const form = new FormData();
  form.append("baseline_file", baselineFile);
  form.append("modified_file", modifiedFile);
  form.append("baseline_text", baselineText);
  form.append("modified_text", modifiedText);
  form.append("content_type", contentType);
  form.append("hashes_confirmed", String(hashesConfirmed));
  const res = await fetch(`${BASE}/aigc-review/paired-analysis`, {
    method: "POST",
    headers,
    body: form,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = errorMessageFromBody(await res.json(), detail);
    } catch {
      /* ignore */
    }
    if (res.status === 401) tokenStore.clear();
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as AigcReviewPairedAnalysis;
}

async function downloadWithBody(
  path: string,
  body: unknown,
): Promise<{ blob: Blob; filename: string }> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const token = tokenStore.get();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const errorBody = await res.json();
      detail = errorMessageFromBody(errorBody, detail);
    } catch {
      /* ignore */
    }
    if (res.status === 401) tokenStore.clear();
    throw new ApiError(res.status, detail);
  }

  const disposition = res.headers.get("Content-Disposition") ?? "";
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  const asciiMatch = disposition.match(/filename="?([^";]+)"?/i);
  const rawName = utf8Match?.[1] ?? asciiMatch?.[1] ?? "books.zip";
  const filename = decodeURIComponent(rawName);
  return { blob: await res.blob(), filename };
}

async function fetchBlob(path: string): Promise<Blob> {
  const headers: Record<string, string> = {};
  const token = tokenStore.get();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const separator = path.includes("?") ? "&" : "?";
  const res = await fetch(`${BASE}${path}${separator}_=${Date.now()}`, {
    headers,
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = errorMessageFromBody(body, detail);
    } catch {
      /* ignore */
    }
    if (res.status === 401) tokenStore.clear();
    throw new ApiError(res.status, detail);
  }
  return res.blob();
}

async function uploadFiles<T>(
  path: string,
  files: File[],
  fieldName = "files",
): Promise<T> {
  const headers: Record<string, string> = {};
  const token = tokenStore.get();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const body = new FormData();
  files.forEach((file) => body.append(fieldName, file));
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers,
    body,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const errorBody = await res.json();
      detail = errorMessageFromBody(errorBody, detail);
    } catch {
      /* ignore */
    }
    if (res.status === 401) tokenStore.clear();
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

async function uploadTestingReferenceBooks(books: File[]): Promise<TestingAnalysisResponse> {
  const headers: Record<string, string> = {};
  const token = tokenStore.get();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const body = new FormData();
  books.forEach((book) => body.append("books", book));
  const res = await fetch(`${BASE}/testing/analyze-reference-books`, {
    method: "POST",
    headers,
    body,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = errorMessageFromBody(await res.json(), detail);
    } catch {
      /* ignore */
    }
    if (res.status === 401) tokenStore.clear();
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as TestingAnalysisResponse;
}

async function uploadFilesForDownload(
  path: string,
  files: File[],
  fieldName = "files",
): Promise<{ blob: Blob; filename: string }> {
  const headers: Record<string, string> = {};
  const token = tokenStore.get();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const body = new FormData();
  files.forEach((file) => body.append(fieldName, file));
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers,
    body,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const errorBody = await res.json();
      detail = errorMessageFromBody(errorBody, detail);
    } catch {
      /* ignore */
    }
    if (res.status === 401) tokenStore.clear();
    throw new ApiError(res.status, detail);
  }

  const disposition = res.headers.get("Content-Disposition") ?? "";
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  const asciiMatch = disposition.match(/filename="?([^";]+)"?/i);
  const rawName = utf8Match?.[1] ?? asciiMatch?.[1] ?? "data-analysis.xlsx";
  return { blob: await res.blob(), filename: decodeURIComponent(rawName) };
}

async function uploadPublicDomainRewriteFiles<T>(
  files: File[],
  metadata: unknown,
): Promise<T> {
  const headers: Record<string, string> = {};
  const token = tokenStore.get();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const body = new FormData();
  files.forEach((file) => body.append("files", file));
  body.append("metadata_json", JSON.stringify(metadata));

  const res = await fetch(`${BASE}/public-domain-rewrite/generate-from-files`, {
    method: "POST",
    headers,
    body,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const errorBody = await res.json();
      detail = errorMessageFromBody(errorBody, detail);
    } catch {
      /* ignore */
    }
    if (res.status === 401) tokenStore.clear();
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  window.setTimeout(() => {
    a.remove();
    URL.revokeObjectURL(url);
  }, 60_000);
}

function openHiddenDownload(url: string) {
  const a = document.createElement("a");
  a.href = url;
  a.target = "_blank";
  a.rel = "noopener";
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  window.setTimeout(() => a.remove(), 300_000);
}

export const api = {
  // 任务
  listTasks: (page = 1, pageSize = 20) =>
    request<TaskListResponse>(`/tasks?page=${page}&page_size=${pageSize}`),
  getTaskCategories: () => request<TaskCategoryListResponse>("/tasks/categories"),
  getClassicalReferenceTemplate: () =>
    request<ClassicalReferenceTemplateResponse>("/tasks/classical-reference-rewrite/default-reference"),
  analyzeTestingReferenceBooks: (books: File[]) =>
    uploadTestingReferenceBooks(books),
  generateTestingArticle: (taskId: string, body: TestingGenerateArticleRequest) =>
    request<TestingArticleResponse>(`/testing/tasks/${taskId}/generate-article`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  rewriteTestingCompletedArticle: (
    taskId: string,
    body: TestingRewriteCompletedArticleRequest,
  ) =>
    request<TestingRewriteCompletedArticleResponse>(
      `/testing/tasks/${taskId}/rewrite-completed-article`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
    ),
  recordTestingZhuqueFeedback: (
    taskId: string,
    baselineReport: AigcReviewReport,
    candidateReport: AigcReviewReport,
  ) =>
    request<TestingZhuqueFeedbackResponse>(
      `/testing/tasks/${taskId}/zhuque-feedback`,
      {
        method: "POST",
        body: JSON.stringify({
          baseline_report: baselineReport,
          candidate_report: candidateReport,
        }),
      },
    ),
  getTestingTask: (taskId: string) =>
    request<TestingTaskResponse>(`/testing/tasks/${taskId}`),
  getCoverCategories: () => request<TaskCategoryListResponse>("/covers/categories"),
  getTask: (id: string) => request<TaskStatusResponse>(`/tasks/${id}`),
  createTask: (body: CreateTaskRequest) =>
    request<CreateTaskResponse>("/tasks", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  createEmotionReferenceArticleTask: (body: ReferenceArticleTaskRequest) =>
    request<CreateTaskResponse>("/tasks/reference-article/emotion-relationship", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  createEmotionReferenceArticleTaskBatch: (body: ReferenceArticleBatchTaskRequest) =>
    request<BatchCreateTaskResponse>("/tasks/reference-article/emotion-relationship/batch", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  createClassicalChineseTaskFromDocx: (
    file: File,
    classicalVariant = "plain",
    title = "",
    category = "classical_chinese",
    styleReference = "",
    styleNotes = "",
    referenceFile?: File | null,
  ) =>
    uploadFile<CreateTaskResponse>("/tasks/classical-chinese/docx", file, {
      classical_variant: classicalVariant,
      title,
      category,
      style_reference: styleReference,
      style_notes: styleNotes,
    }, {
      reference_file: referenceFile,
    }),
  createTaskBatch: (body: BatchCreateTaskRequest) =>
    request<BatchCreateTaskResponse>("/tasks/batch", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteTask: (id: string) =>
    request<void>(`/tasks/${id}`, { method: "DELETE" }),
  pauseComicTask: (id: string) =>
    request<TaskStatusResponse>(`/tasks/${id}/pause-comic`, { method: "POST" }),
  retryTask: (id: string) =>
    request<CreateTaskResponse>(`/tasks/${id}/retry`, { method: "POST" }),
  retryTaskAigc: (id: string, publicDomain = false) =>
    request<CreateTaskResponse>(
      publicDomain
        ? `/public-domain-rewrite/tasks/${id}/retry-aigc`
        : `/tasks/${id}/retry-aigc`,
      { method: "POST" },
    ),
  getAigcReport: (id: string) =>
    request<AigcQualityReport>(`/tasks/${id}/aigc-report`),
  retryFailedTasks: (body: BatchRetryTaskRequest = {}) =>
    request<BatchRetryTaskResponse>("/tasks/retry-failed", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  regenerateTasks: (body: BatchRegenerateTaskRequest) =>
    request<BatchRegenerateTaskResponse>("/tasks/regenerate", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateTaskMetadata: (id: string, body: TaskMetadataUpdateRequest) =>
    request<TaskStatusResponse>(`/tasks/${id}/metadata`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  regenerateSelection: (id: string, body: RegenerateSelectionRequest) =>
    request<RegenerateSelectionResponse>(`/tasks/${id}/regenerate-selection`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  regenerateCover: (id: string, body: CoverRegenerateRequest = { reference_mode: "refresh" }) =>
    request<TaskStatusResponse>(`/tasks/${id}/cover/regenerate`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getChapters: (id: string) =>
    request<ChaptersListResponse>(`/tasks/${id}/chapters`),
  updateChapter: (
    id: string,
    chapterNum: number,
    body: ChapterUpdateRequest,
  ) =>
    request<ChapterResponse>(`/tasks/${id}/chapters/${chapterNum}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  getMarkdown: (id: string) =>
    request<MarkdownResponse>(`/tasks/${id}/markdown`),
  getStage: (id: string, stage: StageName) =>
    request<StageContentResponse>(`/tasks/${id}/stages/${stage}`),
  getStats: () => request<StatsResponse>("/stats"),
  downloadUrl: (id: string, fmt: "md" | "docx" = "md", preview = false) =>
    `${BASE}/tasks/${id}/download?fmt=${fmt}${preview ? "&preview=true" : ""}`,
  downloadFile: async (id: string, fmt: "md" | "docx" = "md", preview = false) => {
    const token = tokenStore.get();
    if (!token) throw new ApiError(401, "请先登录");
    const params = new URLSearchParams({ fmt, token, _: String(Date.now()) });
    if (preview) params.set("preview", "true");
    openHiddenDownload(`${BASE}/tasks/${id}/download/direct?${params.toString()}`);
  },
  downloadBatchFile: async (body: BatchDownloadRequest) => {
    const { blob, filename } = await downloadWithBody("/tasks/download-batch", body);
    saveBlob(blob, filename);
  },
  downloadBatchFileDirect: (body: BatchDownloadRequest) => {
    const token = tokenStore.get();
    if (!token) throw new ApiError(401, "请先登录");
    const params = new URLSearchParams({
      task_ids: body.task_ids.join(","),
      fmt: body.fmt,
      token,
    });
    openHiddenDownload(`${BASE}/tasks/download-batch/direct?${params.toString()}`);
  },
  getCoverBlob: (id: string) => fetchBlob(`/tasks/${id}/cover`),
  getTaskImageBlob: (id: string, filename: string) =>
    fetchBlob(`/tasks/${id}/images/${encodeURIComponent(filename)}`),
  getTaskImagePreviewBlob: (id: string, filename: string) =>
    fetchBlob(`/tasks/${id}/images/${encodeURIComponent(filename)}/preview`),
  downloadTaskImageFile: async (id: string, filename: string) => {
    const token = tokenStore.get();
    if (!token) throw new ApiError(401, "璇峰厛鐧诲綍");
    const params = new URLSearchParams({ token, _: String(Date.now()) });
    openHiddenDownload(
      `${BASE}/tasks/${id}/images/${encodeURIComponent(filename)}/download/direct?${params.toString()}`,
    );
  },
  downloadTaskImagePreviewFile: async (id: string, filename: string) => {
    const token = tokenStore.get();
    if (!token) throw new ApiError(401, "璇峰厛鐧诲綍");
    const params = new URLSearchParams({ token, _: String(Date.now()) });
    openHiddenDownload(
      `${BASE}/tasks/${id}/images/${encodeURIComponent(filename)}/preview/download/direct?${params.toString()}`,
    );
  },
  downloadCoverFile: async (id: string) => {
    const token = tokenStore.get();
    if (!token) throw new ApiError(401, "请先登录");
    const params = new URLSearchParams({ token, _: String(Date.now()) });
    openHiddenDownload(`${BASE}/tasks/${id}/cover/download/direct?${params.toString()}`);
  },
  createCoverJob: (file: File, category = "social_science") =>
    uploadFile<CoverJobResponse>("/covers/jobs", file, { category }),
  listCoverJobs: (limit = 50) =>
    request<CoverJobListResponse>(`/covers/jobs?limit=${limit}`),
  getCoverJob: (jobId: string) => request<CoverJobResponse>(`/covers/jobs/${jobId}`),
  pauseCoverJob: (jobId: string) =>
    request<CoverJobResponse>(`/covers/jobs/${jobId}/pause`, { method: "POST" }),
  resumeCoverJob: (jobId: string) =>
    request<CoverJobResponse>(`/covers/jobs/${jobId}/resume`, { method: "POST" }),
  deleteCoverJob: (jobId: string) =>
    request<void>(`/covers/jobs/${jobId}`, { method: "DELETE" }),
  batchCoverJobs: (body: CoverJobBatchRequest) =>
    request<CoverJobBatchResponse>("/covers/jobs/batch", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getCoverJobImageBlob: (jobId: string, index: number) =>
    fetchBlob(`/covers/jobs/${jobId}/items/${index}/image`),
  deleteCoverJobItem: (jobId: string, index: number) =>
    request<CoverJobResponse>(`/covers/jobs/${jobId}/items/${index}`, { method: "DELETE" }),
  deleteCoverJobItems: (jobId: string, body: CoverJobItemBatchDeleteRequest) =>
    request<CoverJobResponse>(`/covers/jobs/${jobId}/items/batch-delete`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  retryCoverJob: (jobId: string) =>
    request<CoverJobResponse>(`/covers/jobs/${jobId}/retry`, { method: "POST" }),
  retryCoverJobItem: (jobId: string, index: number) =>
    request<CoverJobResponse>(`/covers/jobs/${jobId}/items/${index}/retry`, { method: "POST" }),
  retryCoverJobItems: (jobId: string, body: CoverJobItemBatchRetryRequest) =>
    request<CoverJobResponse>(`/covers/jobs/${jobId}/items/batch-retry`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  regenerateCoverJob: (jobId: string, body: CoverRegenerateRequest = { reference_mode: "refresh" }) =>
    request<CoverJobResponse>(`/covers/jobs/${jobId}/regenerate`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  regenerateCoverJobItem: (
    jobId: string,
    index: number,
    body: CoverRegenerateRequest = { reference_mode: "refresh" },
  ) =>
    request<CoverJobResponse>(`/covers/jobs/${jobId}/items/${index}/regenerate`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  regenerateCoverJobItems: (jobId: string, body: CoverJobItemBatchRetryRequest) =>
    request<CoverJobResponse>(`/covers/jobs/${jobId}/items/batch-regenerate`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  downloadCoverJob: async (jobId: string) => {
    const { blob, filename } = await download(`/covers/jobs/${jobId}/download`);
    saveBlob(blob, filename);
  },

  // 段落润色
  polishParagraph: (body: PolishParagraphRequest) =>
    request<PolishParagraphResponse>("/polish/paragraph", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  generatePublicDomainRewrite: (body: PublicDomainRewriteRequest) =>
    request<CreateTaskResponse>("/public-domain-rewrite/generate", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getClassicalTranslationSettings: () =>
    request<ClassicalTranslationSettingsResponse>(
      "/public-domain-rewrite/classical-translation/settings",
    ),
  getClassicalTaskSource: (id: string, chapterNum = 1, offset = 0) =>
    request<ClassicalSourceResponse>(
      `/public-domain-rewrite/tasks/${id}/classical-source?chapter_num=${chapterNum}&offset=${offset}`,
    ),
  generatePublicDomainRewriteFromFiles: (
    files: File[],
    metadata: Omit<PublicDomainRewriteRequest, "sources"> & {
      sources: Array<Omit<PublicDomainRewriteRequest["sources"][number], "text">>;
    },
  ) => uploadPublicDomainRewriteFiles<CreateTaskResponse>(files, metadata),

  // 独立朱雀报告审稿台：不修改历史任务，不依赖全局闸门
  getAigcReviewStrategy: () => request<AigcReviewStrategy>("/aigc-review/strategy"),
  parseAigcReviewReport: (
    file: File,
    originalText: string,
    contentType: AigcReviewContentType = "article",
  ) =>
    uploadFile<AigcReviewReport>("/aigc-review/report", file, {
      original_text: originalText,
      content_type: contentType,
    }),
  optimizeAigcReview: (body: OptimizeAigcReviewRequest) =>
    request<AigcReviewOptimizeResult>("/aigc-review/optimize", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  compareAigcReview: (
    baselineReport: AigcReviewReport,
    recheckReport: AigcReviewReport,
    optimizeResult: AigcReviewOptimizeResult,
  ) =>
    request<AigcReviewComparison>("/aigc-review/compare", {
      method: "POST",
      body: JSON.stringify({
        baseline_report: baselineReport,
        recheck_report: recheckReport,
        optimize_result: optimizeResult,
      }),
    }),
  analyzeAigcReviewPair: uploadAigcPairedFiles,
  repairExternalGreenManuscript: (body: ExternalGreenRepairRequest) =>
    request<ExternalGreenRepairResult>("/aigc-review/external-green-repair", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  createAigcReviewCalibration: (body: {
    text: string;
    content_type: AigcReviewContentType;
    source_group?: string;
    variant_type: AigcReviewCalibrationVariant;
    parent_sha256?: string | null;
    strategy_id?: string | null;
    quality_gate_passed?: boolean | null;
  }) =>
    request<AigcReviewCalibrationSample>("/aigc-review/calibrations", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listAigcReviewCalibrations: (limit = 100) =>
    request<AigcReviewCalibrationSample[]>(`/aigc-review/calibrations?limit=${limit}`),
  saveAigcReviewCalibrationResult: (
    sampleId: string,
    body: { text_sha256: string; aigc_value: number; report_sha256?: string | null },
  ) =>
    request<AigcReviewCalibrationSample>(`/aigc-review/calibrations/${sampleId}/result`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  uploadAigcReviewCalibrationReport: (sampleId: string, file: File) =>
    uploadFile<AigcReviewCalibrationSample>(
      `/aigc-review/calibrations/${sampleId}/report`,
      file,
    ),

  // 系统健康（检测 Celery worker 是否在线）
  createLongNovelProject: (body: FamilyLongNovelCreateRequest) =>
    request<LongNovelProject>("/long-novel/family/projects", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  assistLongNovelField: (body: LongNovelAssistRequest) =>
    request<LongNovelAssistResponse>("/long-novel/assist", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listLongNovelProjects: (page = 1, pageSize = 50) =>
    request<LongNovelProjectListResponse>(
      `/long-novel/family/projects?page=${page}&page_size=${pageSize}`,
    ),
  getLongNovelProject: (id: string) =>
    request<LongNovelProjectDetailResponse>(`/long-novel/family/projects/${id}`),
  generateLongNovelBible: (id: string) =>
    request<LongNovelProjectDetailResponse>(`/long-novel/family/projects/${id}/bible`, {
      method: "POST",
    }),
  generateLongNovelOutline: (id: string) =>
    request<LongNovelProjectDetailResponse>(`/long-novel/family/projects/${id}/outline`, {
      method: "POST",
    }),
  extendLongNovelTarget: (id: string, body: FamilyLongNovelExtendRequest) =>
    request<LongNovelProjectDetailResponse>(`/long-novel/family/projects/${id}/extend-target`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  generateLongNovelChapterPlan: (id: string, chapterNumber: number) =>
    request<LongNovelChapterPlan>(
      `/long-novel/family/projects/${id}/chapters/${chapterNumber}/plan`,
      { method: "POST" },
    ),
  generateLongNovelChapter: (id: string, chapterNumber: number) =>
    request<LongNovelChapter>(
      `/long-novel/family/projects/${id}/chapters/${chapterNumber}/generate`,
      { method: "POST" },
    ),
  generateLongNovelBatch: (id: string, body: FamilyLongNovelBatchRequest) =>
    request<LongNovelBatchResponse>(`/long-novel/family/projects/${id}/generate-batch`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  pauseLongNovelProject: (id: string) =>
    request<LongNovelProject>(`/long-novel/family/projects/${id}/pause`, { method: "POST" }),
  resumeLongNovelProject: (id: string) =>
    request<LongNovelProject>(`/long-novel/family/projects/${id}/resume`, { method: "POST" }),
  checkLongNovelChapter: (id: string, chapterNumber: number) =>
    request<LongNovelContinuityIssue[]>(
      `/long-novel/family/projects/${id}/chapters/${chapterNumber}/check`,
      { method: "POST" },
    ),
  repairLongNovelChapter: (id: string, chapterNumber: number) =>
    request<LongNovelChapter>(
      `/long-novel/family/projects/${id}/chapters/${chapterNumber}/repair`,
      { method: "POST" },
    ),
  retryLongNovelChapterAigc: (id: string, chapterNumber: number) =>
    request<LongNovelChapter>(
      `/long-novel/family/projects/${id}/chapters/${chapterNumber}/retry-aigc`,
      { method: "POST" },
    ),
  listLongNovelConflicts: (id: string) =>
    request<LongNovelConflictLine[]>(`/long-novel/family/projects/${id}/conflicts`),
  downloadLongNovelExport: async (id: string, format: "markdown" | "txt" | "json" = "markdown") => {
    const { blob, filename } = await download(
      `/long-novel/family/projects/${id}/export?format=${format}`,
    );
    saveBlob(blob, filename);
  },

  createEpicNovelProject: (body: EpicNovelCreateRequest) =>
    request<EpicNovelProject>("/epic-novel/projects", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listEpicNovelProjects: (page = 1, pageSize = 50) =>
    request<EpicNovelProjectListResponse>(
      `/epic-novel/projects?page=${page}&page_size=${pageSize}`,
    ),
  getEpicNovelProject: (id: string) =>
    request<EpicNovelProjectDetailResponse>(`/epic-novel/projects/${id}`),
  deleteEpicNovelProject: (id: string) =>
    request<Record<string, unknown>>(`/epic-novel/projects/${id}`, { method: "DELETE" }),
  getEpicNovelProjectStatus: (id: string) =>
    request<EpicProjectStatusSummary>(`/epic-novel/projects/${id}/status`),
  listEpicNovelProjectArtifacts: (id: string) =>
    request<Array<Record<string, unknown>>>(`/epic-novel/projects/${id}/artifacts`),
  listEpicNovelProjectLedgers: (id: string) =>
    request<Record<string, unknown>>(`/epic-novel/projects/${id}/ledgers`),
  getEpicNovelDashboard: (id: string) =>
    request<EpicDashboardResponse>(`/epic-novel/projects/${id}/dashboard`),
  detectEpicReferenceDownloader: () =>
    request<Record<string, unknown>>("/epic-novel/reference-downloader/detect", {
      method: "POST",
    }),
  scanEpicReferenceDownloads: () =>
    request<Array<Record<string, unknown>>>("/epic-novel/reference-downloader/scan-downloads", {
      method: "POST",
    }),
  importEpicDownloadedReferences: () =>
    request<EpicReferenceBook[]>("/epic-novel/reference-downloader/import-downloaded", {
      method: "POST",
    }),
  importEpicReferenceBooklist: (body: { seed_path?: string | null; raw_markdown?: string | null } = {}) =>
    request<EpicReferenceBooklistItem[]>("/epic-novel/reference-booklist/import", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  normalizeEpicReferenceBooklist: (body: { seed_path?: string | null; raw_markdown?: string | null } = {}) =>
    request<EpicReferenceBooklistItem[]>("/epic-novel/reference-booklist/normalize", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listEpicReferenceDownloadTasks: () =>
    request<EpicReferenceDownloadTask[]>("/epic-novel/reference-download-tasks"),
  retryEpicReferenceDownloadTask: (id: string) =>
    request<EpicReferenceDownloadTask>(`/epic-novel/reference-download-tasks/${id}/retry`, {
      method: "POST",
    }),
  listEpicReferenceBooks: (limit = 500) =>
    request<EpicReferenceBook[]>(`/epic-novel/reference-books?limit=${limit}`),
  deleteEpicReferenceBook: (id: string) =>
    request<Record<string, unknown>>(`/epic-novel/reference-books/${id}`, { method: "DELETE" }),
  getEpicReferenceReadiness: () =>
    request<EpicReferenceReadiness>("/epic-novel/reference-readiness"),
  ingestEpicReferenceBook: (body: { book_id?: string; source_path?: string }) =>
    request<Record<string, unknown>>("/epic-novel/reference-books/ingest", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  searchEpicReferencePatterns: (body: { query?: string; category?: string | null; limit?: number } = {}) =>
    request<EpicReferencePatternCard[]>("/epic-novel/reference-patterns/search", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  buildEpicReferenceProfile: () =>
    request<EpicReferenceMechanismProfile>("/epic-novel/reference-mechanism-profile/build", {
      method: "POST",
    }),
  getEpicReferenceProfile: () =>
    request<EpicReferenceMechanismProfile | null>("/epic-novel/reference-mechanism-profile"),
  getEpicReferenceExtractionCostEstimate: () =>
    request<EpicReferenceExtractionCostEstimate>("/epic-novel/reference-extraction/cost-estimate"),
  getEpicReferenceExtractionControl: () =>
    request<EpicReferenceExtractionControl>("/epic-novel/reference-extraction/control"),
  setEpicReferenceExtractionControl: (state: EpicReferenceExtractionControl["state"]) =>
    request<EpicReferenceExtractionControl>(`/epic-novel/reference-extraction/control/${state}`, {
      method: "POST",
    }),
  runEpicWorldviewTournament: (id: string) =>
    request<Array<Record<string, unknown>>>(`/epic-novel/projects/${id}/worldview-tournament`, {
      method: "POST",
    }),
  generateEpicWorldBible: (id: string) =>
    request<Record<string, unknown>>(`/epic-novel/projects/${id}/world-bible`, {
      method: "POST",
    }),
  generateEpicWorldAtlas: (id: string) =>
    request<Record<string, unknown>>(`/epic-novel/projects/${id}/world-atlas`, {
      method: "POST",
    }),
  forgeEpicPowerSystem: (id: string) =>
    request<Record<string, unknown>>(`/epic-novel/projects/${id}/power-system`, {
      method: "POST",
    }),
  generateEpicEnsembleCast: (id: string) =>
    request<Array<Record<string, unknown>>>(`/epic-novel/projects/${id}/ensemble-cast`, {
      method: "POST",
    }),
  generateEpicGlobalSpine: (id: string) =>
    request<Record<string, unknown>>(`/epic-novel/projects/${id}/global-spine`, {
      method: "POST",
    }),
  generateEpicBibleManifest: (id: string) =>
    request<Record<string, unknown>>(`/epic-novel/projects/${id}/bible/manifest`, {
      method: "POST",
    }),
  runEpicBibleCoverageCi: (id: string) =>
    request<Array<Record<string, unknown>>>(`/epic-novel/projects/${id}/bible/coverage-ci`, {
      method: "POST",
    }),
  repairEpicBibleCoverage: (id: string) =>
    request<Array<Record<string, unknown>>>(`/epic-novel/projects/${id}/bible/repair`, {
      method: "POST",
    }),
  generateEpicVolumeContracts: (id: string) =>
    request<Array<Record<string, unknown>>>(`/epic-novel/projects/${id}/volume-contracts`, {
      method: "POST",
    }),
  generateEpicArcLedgers: (id: string) =>
    request<Record<string, unknown>>(`/epic-novel/projects/${id}/arc-ledgers`, {
      method: "POST",
    }),
  runEpicOutlineCi: (id: string) =>
    request<Record<string, unknown>>(`/epic-novel/projects/${id}/outline-ci`, {
      method: "POST",
    }),
  repairEpicOutline: (id: string) =>
    request<Record<string, unknown>>(`/epic-novel/projects/${id}/outline-repair`, {
      method: "POST",
    }),
  compileEpicChapterContracts: (id: string, volumeNumber: number) =>
    request<EpicChapterContract[]>(
      `/epic-novel/projects/${id}/volumes/${volumeNumber}/compile-chapters`,
      { method: "POST" },
    ),
  generateEpicBibleSection: (id: string, sectionKey: string) =>
    request<Record<string, unknown>>(`/epic-novel/projects/${id}/bible/sections/${sectionKey}/generate`, {
      method: "POST",
    }),
  getEpicChapterContextPack: (id: string, chapterNumber: number) =>
    request<Record<string, unknown>>(`/epic-novel/projects/${id}/chapters/${chapterNumber}/context-pack`),
  executeEpicChapter: (id: string, chapterNumber: number) =>
    request<EpicChapter>(`/epic-novel/projects/${id}/chapters/${chapterNumber}/execute`, {
      method: "POST",
    }),
  checkEpicChapter: (id: string, chapterNumber: number) =>
    request<Array<Record<string, unknown>>>(
      `/epic-novel/projects/${id}/chapters/${chapterNumber}/check`,
      { method: "POST" },
    ),
  repairEpicChapter: (id: string, chapterNumber: number) =>
    request<EpicChapter>(`/epic-novel/projects/${id}/chapters/${chapterNumber}/repair`, {
      method: "POST",
    }),
  generateEpicVolume: (id: string, volumeNumber: number) =>
    request<EpicChapter[]>(`/epic-novel/projects/${id}/volumes/${volumeNumber}/generate`, {
      method: "POST",
    }),
  generateEpicBatch: (id: string, body: EpicBatchGenerationRequest) =>
    request<EpicBatchGenerationResponse>(`/epic-novel/projects/${id}/generate-batch`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  downloadEpicBibleExport: async (id: string, format: "markdown" | "json" = "markdown") => {
    const { blob, filename } = await download(
      `/epic-novel/projects/${id}/bible/export?format=${format}&mode=download`,
    );
    saveBlob(blob, filename);
  },
  downloadEpicNovelExport: async (id: string, format: "markdown" | "txt" | "json" = "markdown") => {
    const { blob, filename } = await download(
      `/epic-novel/projects/${id}/export?format=${format}&mode=download`,
    );
    saveBlob(blob, filename);
  },
  getEpicBibleExportMetadata: (id: string, format: "markdown" | "json" = "markdown") =>
    request<EpicExportMetadata>(`/epic-novel/projects/${id}/bible/export?format=${format}&mode=metadata`),
  getEpicNovelExportMetadata: (id: string, format: "markdown" | "txt" | "json" = "markdown") =>
    request<EpicExportMetadata>(`/epic-novel/projects/${id}/export?format=${format}&mode=metadata`),
  getEpicArtifactExportMetadata: (
    id: string,
    artifactType: "volume_markdown" | "chapter_bundle" | "qa_reports" | "retrieval_traces" | "acceptance_reports",
    volumeNumber?: number,
  ) => {
    const params = new URLSearchParams({ artifact_type: artifactType, mode: "metadata" });
    if (volumeNumber) params.set("volume_number", String(volumeNumber));
    return request<EpicExportMetadata>(`/epic-novel/projects/${id}/exports/artifact?${params.toString()}`);
  },
  downloadEpicArtifactExport: async (
    id: string,
    artifactType: "volume_markdown" | "chapter_bundle" | "qa_reports" | "retrieval_traces" | "acceptance_reports",
    volumeNumber?: number,
  ) => {
    const params = new URLSearchParams({ artifact_type: artifactType, mode: "download" });
    if (volumeNumber) params.set("volume_number", String(volumeNumber));
    const { blob, filename } = await download(`/epic-novel/projects/${id}/exports/artifact?${params.toString()}`);
    saveBlob(blob, filename);
  },

  systemHealth: () => request<SystemHealth>("/system/health"),

  // 微信读书推荐值
  analyzeWeReadBooks: (body: WeReadAnalyzeRequest) =>
    request<WeReadAnalyzeResponse>("/weread/analyze", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  analyzeWeReadCompanyBooks: (body: WeReadCompanyRequest) =>
    request<WeReadAnalyzeResponse>("/weread/company", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  analyzeWeReadCompanyCapture: (file: File, companyName: string, maxBooks = 1000) =>
    uploadFile<WeReadAnalyzeResponse>("/weread/company-capture", file, {
      company_name: companyName,
      max_books: String(maxBooks),
    }),
  analyzeFanqieCompanyCapture: (file: File, companyName: string, maxBooks = 1000) =>
    uploadFile<WeReadAnalyzeResponse>("/weread/fanqie-capture", file, {
      company_name: companyName,
      max_books: String(maxBooks),
    }),
  importWeReadExcel: (file: File) =>
    uploadFile<WeReadExcelImportResponse>("/weread/import-excel", file),
  analyzeWeReadExcel: (file: File) =>
    uploadFile<WeReadAnalyzeResponse>("/weread/analyze-excel", file),
  downloadWeReadExcel: async (rows: WeReadBookRow[]) => {
    const { blob, filename } = await downloadWithBody("/weread/export", { rows });
    saveBlob(blob, filename);
  },

  // 数据分析
  analyzeDataFiles: (files: File[]) =>
    uploadFiles<DataAnalysisResponse>("/data-analysis/analyze", files),
  downloadDataAnalysisExcelBatch: async (files: File[]) => {
    const { blob, filename } = await uploadFilesForDownload("/data-analysis/export", files);
    saveBlob(blob, filename);
  },

  // 鉴权
  register: (body: RegisterRequest) =>
    request<TokenResponse>("/auth/register", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  login: (body: LoginRequest) =>
    request<TokenResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  me: () => request<UserPublic>("/auth/me"),
};

export { ApiError };
