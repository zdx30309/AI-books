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

export interface TaskStatusResponse {
  id: string;
  source_url: string;
  status: TaskStatus;
  progress: number;
  current_step: string;
  cover_url: string | null;
  error_message: string | null;
  created_at: string;
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
