// 与后端 schemas 对应的 TS 类型

export type TaskStatus =
  | "pending"
  | "processing"
  | "fetching_topics"
  | "filtering_topics"
  | "generating_outline"
  | "writing_chapters"
  | "fact_checking"
  | "completed"
  | "failed";

export interface CreateTaskRequest {
  url: string;
  config?: Record<string, unknown>;
}

export interface CreateTaskResponse {
  task_id: string;
  status: TaskStatus;
}

export interface BatchCreateTaskRequest {
  urls: string[];
  config?: Record<string, unknown>;
}

export interface BatchCreateTaskItem {
  url: string;
  task_id: string | null;
  status: string;
  reason: string | null;
}

export interface BatchCreateTaskResponse {
  created: BatchCreateTaskItem[];
  skipped: BatchCreateTaskItem[];
  total_created: number;
  total_skipped: number;
}

export interface BatchDownloadRequest {
  task_ids: string[];
  fmt: "md" | "docx";
}

export interface TaskCategoryOption {
  id: string;
  label: string;
  core_feature: string;
  directions: string;
  prompt_status: string;
}

export interface TaskCategoryListResponse {
  items: TaskCategoryOption[];
}

export interface TaskStatusResponse {
  id: string;
  source_url: string;
  status: TaskStatus;
  progress: number;
  current_step: string;
  cover_url: string | null;
  title: string | null;
  bookshelf: string | null;
  category: string | null;
  category_label: string | null;
  error_message: string | null;
  created_at: string;
}

export interface DeleteTaskResponse {
  success: boolean;
}

export interface TaskListItem {
  id: string;
  source_url: string;
  status: TaskStatus;
  progress: number;
  current_step: string;
  cover_url: string | null;
  created_at: string;
  title: string | null;
  bookshelf: string | null;
  category: string | null;
  category_label: string | null;
  word_count: number;
  chapter_count: number;
}

export interface TaskListResponse {
  total: number;
  page: number;
  page_size: number;
  items: TaskListItem[];
}

export interface StatsResponse {
  total_books: number;
  total_words: number;
  saved_hours: number;
  in_progress: number;
  failed: number;
}

export interface ChapterResponse {
  id: string;
  task_id: string;
  chapter_num: number;
  title: string;
  content: string;
  word_count: number;
  created_at: string;
}

export interface ChaptersListResponse {
  task_id: string;
  total: number;
  chapters: ChapterResponse[];
}

export interface MarkdownResponse {
  task_id: string;
  markdown: string;
  char_count: number;
}

export type StageName =
  | "writer"
  | "writer_repaired"
  | "writer_audited"
  | "polish"
  | "refine"
  | "format";

export interface StageContentResponse {
  task_id: string;
  stage: StageName;
  content: string;
  char_count: number;
}

export interface ChapterUpdateRequest {
  title?: string | null;
  content: string;
}

export interface TaskMetadataUpdateRequest {
  title?: string | null;
  bookshelf?: string | null;
  category?: string | null;
}

export interface RegenerateSelectionRequest {
  text: string;
}

export interface RegenerateSelectionResponse {
  original: string;
  regenerated: string;
  original_chars: number;
  regenerated_chars: number;
}

// 段落润色

export interface PolishParagraphRequest {
  text: string;
}

export interface PolishParagraphResponse {
  original: string;
  polished: string;
  original_chars: number;
  polished_chars: number;
}

// 鉴权

export interface RegisterRequest {
  email?: string | null;
  phone?: string | null;
  password: string;
  name?: string | null;
}

export interface LoginRequest {
  identifier: string;
  password: string;
}

export interface UserPublic {
  id: string;
  email: string | null;
  phone: string | null;
  name: string;
  avatar_url: string | null;
  is_vip: boolean;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user: UserPublic;
}

// 系统健康

export interface SystemHealth {
  celery_alive: boolean;
  celery_workers: string[];
  celery_error: string | null;
  queue_lengths: Record<string, number> | null;
}
