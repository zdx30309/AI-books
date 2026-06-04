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
  ChapterResponse,
  ChapterUpdateRequest,
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
  LoginRequest,
  MarkdownResponse,
  PolishParagraphRequest,
  PolishParagraphResponse,
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
  TokenResponse,
  UserPublic,
  WeReadAnalyzeRequest,
  WeReadAnalyzeResponse,
  WeReadBookRow,
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
      detail = body?.detail ?? detail;
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
      detail = body?.detail ?? detail;
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
): Promise<T> {
  const headers: Record<string, string> = {};
  const token = tokenStore.get();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const form = new FormData();
  form.append("file", file);
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
      detail = body?.detail ?? detail;
    } catch {
      /* ignore */
    }
    if (res.status === 401) tokenStore.clear();
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
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
      detail = errorBody?.detail ?? detail;
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
      detail = body?.detail ?? detail;
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
      detail = errorBody?.detail ?? detail;
    } catch {
      /* ignore */
    }
    if (res.status === 401) tokenStore.clear();
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
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
      detail = errorBody?.detail ?? detail;
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

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function openHiddenDownload(url: string) {
  const iframe = document.createElement("iframe");
  iframe.src = url;
  iframe.style.display = "none";
  iframe.setAttribute("aria-hidden", "true");
  document.body.appendChild(iframe);
  window.setTimeout(() => iframe.remove(), 60_000);
}

export const api = {
  // 任务
  listTasks: (page = 1, pageSize = 20) =>
    request<TaskListResponse>(`/tasks?page=${page}&page_size=${pageSize}`),
  getTaskCategories: () => request<TaskCategoryListResponse>("/tasks/categories"),
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
  ) =>
    uploadFile<CreateTaskResponse>("/tasks/classical-chinese/docx", file, {
      classical_variant: classicalVariant,
      title,
    }),
  createTaskBatch: (body: BatchCreateTaskRequest) =>
    request<BatchCreateTaskResponse>("/tasks/batch", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteTask: (id: string) =>
    request<void>(`/tasks/${id}`, { method: "DELETE" }),
  retryTask: (id: string) =>
    request<CreateTaskResponse>(`/tasks/${id}/retry`, { method: "POST" }),
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
  downloadUrl: (id: string, fmt: "md" | "docx" = "md") =>
    `${BASE}/tasks/${id}/download?fmt=${fmt}`,
  downloadFile: async (id: string, fmt: "md" | "docx" = "md") => {
    const { blob, filename } = await download(`/tasks/${id}/download?fmt=${fmt}`);
    saveBlob(blob, filename);
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
  downloadCoverFile: async (id: string) => {
    const { blob, filename } = await download(`/tasks/${id}/cover/download`);
    saveBlob(blob, filename);
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

  // 系统健康（检测 Celery worker 是否在线）
  systemHealth: () => request<SystemHealth>("/system/health"),

  // 微信读书推荐值
  analyzeWeReadBooks: (body: WeReadAnalyzeRequest) =>
    request<WeReadAnalyzeResponse>("/weread/analyze", {
      method: "POST",
      body: JSON.stringify(body),
    }),
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
