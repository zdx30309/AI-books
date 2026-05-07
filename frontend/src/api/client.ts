// 后端 API 客户端。dev 时走 vite proxy（/api → :8000），prod 时由部署侧反代。
// 自动从 localStorage 读 token 并加 Authorization 头。

import type {
  BatchCreateTaskRequest,
  BatchCreateTaskResponse,
  BatchDownloadRequest,
  ChapterResponse,
  ChapterUpdateRequest,
  ChaptersListResponse,
  CreateTaskRequest,
  CreateTaskResponse,
  LoginRequest,
  MarkdownResponse,
  PolishParagraphRequest,
  PolishParagraphResponse,
  RegenerateSelectionRequest,
  RegenerateSelectionResponse,
  RegisterRequest,
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

  const res = await fetch(`${BASE}${path}`, { headers });
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

  const res = await fetch(`${BASE}${path}`, { headers });
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

export const api = {
  // 任务
  listTasks: (page = 1, pageSize = 20) =>
    request<TaskListResponse>(`/tasks?page=${page}&page_size=${pageSize}`),
  getTaskCategories: () => request<TaskCategoryListResponse>("/tasks/categories"),
  getTask: (id: string) => request<TaskStatusResponse>(`/tasks/${id}`),
  createTask: (body: CreateTaskRequest) =>
    request<CreateTaskResponse>("/tasks", {
      method: "POST",
      body: JSON.stringify(body),
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
  regenerateCover: (id: string) =>
    request<TaskStatusResponse>(`/tasks/${id}/cover/regenerate`, { method: "POST" }),
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
  getCoverBlob: (id: string) => fetchBlob(`/tasks/${id}/cover`),

  // 段落润色
  polishParagraph: (body: PolishParagraphRequest) =>
    request<PolishParagraphResponse>("/polish/paragraph", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // 系统健康（检测 Celery worker 是否在线）
  systemHealth: () => request<SystemHealth>("/system/health"),

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
