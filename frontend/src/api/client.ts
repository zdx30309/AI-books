// 后端 API 客户端。dev 时走 vite proxy（/api → :8000），prod 时由部署侧反代。

import type {
  ChaptersListResponse,
  CreateTaskRequest,
  CreateTaskResponse,
  MarkdownResponse,
  StageContentResponse,
  StageName,
  StatsResponse,
  TaskListResponse,
  TaskStatusResponse,
} from "./types";

const BASE = "/api/v1";

class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(`[${status}] ${detail}`);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body?.detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  listTasks: (page = 1, pageSize = 20) =>
    request<TaskListResponse>(
      `/tasks?page=${page}&page_size=${pageSize}`,
    ),

  getTask: (id: string) => request<TaskStatusResponse>(`/tasks/${id}`),

  createTask: (body: CreateTaskRequest) =>
    request<CreateTaskResponse>("/tasks", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  getChapters: (id: string) =>
    request<ChaptersListResponse>(`/tasks/${id}/chapters`),

  getMarkdown: (id: string) =>
    request<MarkdownResponse>(`/tasks/${id}/markdown`),

  getStage: (id: string, stage: StageName) =>
    request<StageContentResponse>(`/tasks/${id}/stages/${stage}`),

  getStats: () => request<StatsResponse>("/stats"),

  downloadUrl: (id: string, fmt: "md" | "txt" = "md") =>
    `${BASE}/tasks/${id}/download?fmt=${fmt}`,
};

export { ApiError };
