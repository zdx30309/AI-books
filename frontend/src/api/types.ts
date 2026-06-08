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

export interface ReferenceArticleTaskRequest {
  url: string;
  title?: string | null;
}

export interface ReferenceArticleBatchTaskRequest {
  urls: string[];
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

export interface BatchRetryTaskRequest {
  task_ids?: string[];
}

export interface BatchRetryTaskItem {
  task_id: string;
  status: string;
  reason: string | null;
}

export interface BatchRetryTaskResponse {
  retried: BatchRetryTaskItem[];
  skipped: BatchRetryTaskItem[];
  total_retried: number;
  total_skipped: number;
}

export interface BatchRegenerateTaskRequest {
  task_ids: string[];
}

export interface BatchRegenerateTaskItem {
  task_id: string;
  status: string;
  reason?: string | null;
}

export interface BatchRegenerateTaskResponse {
  regenerated: BatchRegenerateTaskItem[];
  skipped: BatchRegenerateTaskItem[];
  total_regenerated: number;
  total_skipped: number;
}

export interface BatchDownloadRequest {
  task_ids: string[];
  fmt: "md" | "docx";
}

export interface CoverJobItem {
  index: number;
  title: string;
  author: string | null;
  category: string | null;
  category_label: string | null;
  status: string;
  image_url: string | null;
  error: string | null;
  metadata: Record<string, unknown>;
  current_stage?: string | null;
  retry_count?: number | null;
}

export interface CoverJobResponse {
  job_id: string;
  status: string;
  progress: number;
  total: number;
  completed: number;
  failed: number;
  uncertain: number;
  current_step: string;
  source_filename: string | null;
  default_category: string | null;
  default_category_label: string | null;
  created_at: string;
  updated_at: string;
  items: CoverJobItem[];
  engine?: string | null;
}

export interface CoverJobListResponse {
  items: CoverJobResponse[];
}

export interface CoverJobBatchRequest {
  job_ids: string[];
  action: "pause" | "resume" | "delete";
}

export interface CoverJobBatchItem {
  job_id: string;
  status: string;
  reason: string | null;
}

export interface CoverJobBatchResponse {
  items: CoverJobBatchItem[];
  total: number;
}

export interface CoverJobItemBatchDeleteRequest {
  indexes: number[];
}

export interface CoverJobItemBatchRetryRequest {
  indexes: number[];
  reference_mode?: "refresh" | "reuse";
}

export interface CoverRegenerateRequest {
  reference_mode: "refresh" | "reuse";
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
  cover_status: string | null;
  cover_error: string | null;
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

// 微信读书推荐值工具

export interface WeReadAnalyzeRequest {
  catalog_text: string;
  profit_text?: string | null;
}

export interface WeReadBookRow {
  query_title: string;
  query_author: string | null;
  matched_title: string | null;
  matched_author: string | null;
  recommendation: number | null;
  rating_count: string | null;
  today_reading: string | null;
  total_reading: string | null;
  word_count: string | null;
  source_url: string | null;
  profit: number | null;
  status: string;
  error: string | null;
}

export interface WeReadAnalyzeResponse {
  total: number;
  matched: number;
  items: WeReadBookRow[];
}


// 数据分析工具

export interface DataAnalysisSummary {
  source_filename: string;
  source_filenames: string[];
  report_date: string;
  book_count: number;
  matched_book_count: number;
  company_field_detected: string | null;
  file_count: number;
  notes: string[];
}

export interface DataAnalysisRow {
  date_key: string;
  weekday: string;
  ai_duration: number | null;
  ai_mom: number | null;
  public_duration: number | null;
  public_mom: number | null;
  total_duration: number | null;
  total_mom: number | null;
  huian_ai: number | null;
  huian_public: number | null;
  wutu_ai: number | null;
  wutu_public: number | null;
  suite_duration: number | null;
}

export interface DataAnalysisResponse {
  summary: DataAnalysisSummary;
  rows: DataAnalysisRow[];
}

export interface FamilyLongNovelCreateRequest {
  premise: string;
  family_setup?: string | null;
  conflict_tags?: string[];
  source_type?: string;
  source_text?: string | null;
  target_words?: number;
  target_chapters?: number;
  generation_mode?: "cheap" | "standard" | "high_quality";
  style?: string;
  forbidden_rules?: string | null;
}

export interface FamilyLongNovelBatchRequest {
  from_chapter: number;
  to_chapter: number;
  retry_failed?: boolean;
}

export interface LongNovelAssistRequest {
  module_type?: string;
  field: "premise" | "family_setup" | "conflict_tags" | "source_text" | "forbidden_rules";
  current_values?: Record<string, unknown>;
}

export interface LongNovelAssistResponse {
  field: string;
  content: string;
  conflict_tags: string[];
}

export interface LongNovelProject {
  id: string;
  owner_id: string | null;
  title: string;
  type: string;
  genre_label: string;
  premise: string;
  family_setup: string | null;
  conflict_tags: string[];
  source_type: string;
  source_text: string | null;
  target_words: number;
  target_chapters: number;
  generation_mode: string;
  style: string;
  forbidden_rules: string | null;
  status: string;
  current_chapter: number;
  total_words: number;
  created_at: string;
  updated_at: string;
}

export interface LongNovelBible {
  id: string;
  project_id: string;
  theme: string | null;
  core_promise: string | null;
  emotional_hook: string | null;
  family_background: string | null;
  world_rules: unknown[];
  style_rules: unknown[];
  forbidden_rules: unknown[];
  ending_direction: string | null;
  main_conflict: string | null;
  raw_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface LongNovelFamilyMember {
  id: string;
  project_id: string;
  name: string;
  family_role: string | null;
  public_image: string | null;
  private_desire: string | null;
  grievance: string | null;
  leverage: string | null;
  weakness: string | null;
  secret: string | null;
  relationship_to_heroine: string | null;
  relationship_map: Record<string, unknown>;
  voice_rules: Record<string, unknown>;
  arc_start: string | null;
  arc_end: string | null;
  ending: string | null;
  raw_json: Record<string, unknown>;
}

export interface LongNovelFamilyRelation {
  id: string;
  project_id: string;
  from_member: string;
  to_member: string;
  relation_type: string | null;
  surface_relation: string | null;
  hidden_conflict: string | null;
  interest_conflict: string | null;
  emotional_debt: string | null;
  current_tension_level: number;
  raw_json: Record<string, unknown>;
}

export interface LongNovelConflictLine {
  id: string;
  project_id: string;
  conflict_key: string;
  name: string;
  conflict_type: string | null;
  description: string | null;
  introduced_chapter: number | null;
  last_escalated_chapter: number | null;
  planned_explosion_chapter: number | null;
  planned_resolution_chapter: number | null;
  status: string;
  importance: string;
}

export interface LongNovelChapterPlan {
  id: string;
  project_id: string;
  chapter_number: number;
  volume_number: number | null;
  title: string;
  target_words: number;
  chapter_goal: string | null;
  domestic_conflict: string | null;
  emotional_hook: string | null;
  required_events: unknown[];
  character_arc_move: unknown[];
  conflict_lines_to_advance: unknown[];
  forbidden_reveals: unknown[];
  ending_hook: string | null;
  scenes: unknown[];
  status: string;
  raw_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface LongNovelChapter {
  id: string;
  project_id: string;
  chapter_number: number;
  title: string;
  content: string;
  summary: string | null;
  word_count: number;
  status: string;
  version: number;
  raw_model_output: string | null;
  created_at: string;
  updated_at: string;
}

export interface LongNovelContinuityIssue {
  id: string;
  project_id: string;
  chapter_number: number;
  severity: string;
  issue_type: string | null;
  description: string;
  evidence: string | null;
  suggested_fix: string | null;
  status: string;
  created_at: string;
}

export interface LongNovelGenerationLog {
  id: string;
  project_id: string;
  chapter_number: number | null;
  action: string;
  provider: string | null;
  model: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  estimated_cost: number;
  status: string;
  error: string | null;
  created_at: string;
}

export interface LongNovelProjectDetailResponse {
  project: LongNovelProject;
  bible: LongNovelBible | null;
  members: LongNovelFamilyMember[];
  relations: LongNovelFamilyRelation[];
  conflict_lines: LongNovelConflictLine[];
  chapter_plans: LongNovelChapterPlan[];
  chapters: LongNovelChapter[];
  issues: LongNovelContinuityIssue[];
  logs: LongNovelGenerationLog[];
}

export interface LongNovelProjectListResponse {
  total: number;
  items: LongNovelProject[];
}

export interface LongNovelBatchResponse {
  project_id: string;
  status: string;
  generated: number[];
  failed: Array<{ chapter_number: number; error: string }>;
  skipped: number[];
}
