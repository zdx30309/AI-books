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
  | "failed"
  | "paused";

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
  reading_count: string | null;
  comment_count: string | null;
  company_name: string | null;
  source_url: string | null;
  profit: number | null;
  project_editor: string | null;
  project_title: string | null;
  project_word_count_10k: string | null;
  project_finished_at: string | null;
  project_summary: string | null;
  project_pen_name: string | null;
  project_new_title: string | null;
  project_original_author: string | null;
  project_recommendation: number | null;
  project_comment_count: string | null;
  project_tags: string | null;
  project_reading_count: string | null;
  project_weread_url: string | null;
  project_negative_review: string | null;
  project_parent_record: string | null;
  project_status: string | null;
  project_progress: string | null;
  project_update_date: string | null;
  project_start_date: string | null;
  project_time_span: string | null;
  project_note: string | null;
  project_source_sheet: string | null;
  project_match_status: string | null;
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
  actual_cost?: number | null;
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

export interface EpicNovelCreateRequest {
  title: string;
  premise: string;
  genre?: string;
  core_pleasures?: string[];
  target_words?: number;
  target_volumes?: number;
  target_chapters?: number;
  bible_target_words?: number;
  reference_booklist_path?: string | null;
  sonovel_home?: string | null;
  sonovel_download_path?: string | null;
  reference_raw_path?: string | null;
  generation_mode?: "cheap" | "standard" | "high_quality";
  style?: string | null;
  forbidden_rules?: string | null;
}

export interface EpicNovelProject {
  id: string;
  owner_id: string | null;
  title: string;
  genre: string;
  premise: string;
  target_words: number;
  target_volumes: number;
  target_chapters: number;
  bible_target_words: number;
  style: string | null;
  forbidden_rules: string | null;
  status: string;
  current_volume: number;
  current_chapter: number;
  total_words: number;
  generation_mode: string;
  editor_model: string | null;
  author_model: string | null;
  cheap_model: string | null;
  reference_profile_id: string | null;
  reference_booklist_path: string | null;
  sonovel_home: string | null;
  sonovel_download_path: string | null;
  reference_raw_path: string | null;
  raw_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface EpicNovelProjectListResponse {
  total: number;
  items: EpicNovelProject[];
}

export interface EpicChapterContract {
  id: string;
  project_id: string;
  volume_number: number;
  chapter_number: number;
  title: string;
  target_words: number;
  chapter_goal: string | null;
  plot_function: string | null;
  global_spine_function: string | null;
  world_atlas_function: string | null;
  bureaucracy_function: string | null;
  local_arc_function: string | null;
  core_pleasure: string | null;
  reference_pattern_cards_used: unknown[];
  must_execute: unknown[];
  must_not_reveal: unknown[];
  ending_hook: string | null;
  status: string;
  raw_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface EpicChapter {
  id: string;
  project_id: string;
  volume_number: number;
  chapter_number: number;
  title: string;
  draft_content: string | null;
  repaired_content: string | null;
  accepted_content: string | null;
  summary: string | null;
  ending_hook: string | null;
  word_count: number;
  status: string;
  version: number;
  repair_count: number;
  raw_model_output: string | null;
  created_at: string;
  updated_at: string;
}

export interface EpicAcceptanceReport {
  id: string;
  project_id: string;
  chapter_number: number;
  outline_execution_score: number;
  character_arc_score: number;
  power_legality_score: number;
  foreshadow_score: number;
  pleasure_score: number;
  continuity_score: number;
  anti_cliche_score: number;
  overall_status: string;
  issues_json: unknown[];
  created_at: string;
}

export interface EpicGenerationLog {
  id: string;
  project_id: string | null;
  action: string;
  provider: string | null;
  model: string | null;
  bible_id: string | null;
  section_key: string | null;
  volume_number: number | null;
  chapter_number: number | null;
  prompt_tokens: number;
  completion_tokens: number;
  estimated_cost: number;
  status: string;
  error: string | null;
  created_at: string;
}

export interface EpicReferenceBook {
  id: string;
  booklist_item_id: string | null;
  download_task_id: string | null;
  title: string;
  author: string | null;
  genre: string | null;
  tier: number;
  category: string;
  source_path: string;
  storage_type: string;
  file_hash: string;
  language: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface EpicReferenceBooklistItem {
  id: string;
  title: string;
  author: string | null;
  tier: number;
  category: string;
  genre: string | null;
  source_type: string;
  source_path: string | null;
  source_url: string | null;
  corpus_role: unknown[];
  priority: number;
  tags: unknown[];
  notes: string | null;
  likely_use: unknown[];
  confidence: number;
  ingest_allowed: boolean;
  needs_verification: boolean;
  download_status: string;
  local_raw_path: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface EpicReferenceDownloadTask {
  id: string;
  booklist_item_id: string | null;
  provider: string;
  title: string;
  author: string | null;
  query: string;
  status: string;
  search_results: unknown[];
  selected_result: Record<string, unknown> | null;
  local_download_path: string | null;
  imported_raw_path: string | null;
  file_hash: string | null;
  generated_card_count: number;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface EpicReferencePatternCard {
  id: string;
  source_book_id: string | null;
  pattern_key: string;
  category: string;
  name: string;
  description: string;
  genre: string | null;
  function: string | null;
  conditions: unknown[];
  beats: unknown[];
  common_pitfalls: unknown[];
  best_use: unknown[];
  required_global_link: string | null;
  originality_notes: string | null;
  tags: unknown[];
  source_section_ids: unknown[];
  source_analysis_ids: unknown[];
  source_book_ids: unknown[];
  source_card_ids: unknown[];
  evidence_count: number;
  raw_text_included: boolean;
  extraction_model_provider: string | null;
  extraction_model_name: string | null;
  output_hash: string | null;
  raw_json: Record<string, unknown>;
  created_at: string;
}

export interface EpicReferenceMechanismProfile {
  id: string;
  project_id: string | null;
  corpus_id: string | null;
  profile_name: string;
  source_book_count: number;
  source_card_count: number;
  genre: string | null;
  tier_weights: Record<string, unknown>;
  core_pleasure_mechanisms: unknown[];
  case_structure_patterns: unknown[];
  court_power_patterns: unknown[];
  bureaucracy_conflict_patterns: unknown[];
  heroine_agency_patterns: unknown[];
  character_arc_patterns: unknown[];
  foreshadow_patterns: unknown[];
  power_progression_patterns: unknown[];
  longform_pacing_patterns: unknown[];
  anti_cliche_rules: unknown[];
  common_pitfalls: unknown[];
  originality_guidance: unknown[];
  raw_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface EpicReferenceExtractionCostEstimate {
  books: number;
  sections: number;
  chars: number;
  estimatedPromptTokens: number;
  batchTokenBudget: number;
  estimatedBatches: number;
  modelProvider: string;
  modelName: string;
  estimatedCost: number;
  perBook: Array<Record<string, unknown>>;
}

export interface EpicReferenceExtractionControl {
  state: "running" | "paused" | "stopped";
}

export interface EpicReferenceReadiness {
  ready: boolean;
  profileReady: boolean;
  profileId: string | null;
  sourceBookCount: number;
  sourceCardCount: number;
  totalBooks: number;
  importedBooks: number;
  totalCards: number;
  productionReadyCards: number;
  downloadStatus: Record<string, number>;
  rawTextIncludedInGeneration: boolean;
  minimumViable: boolean;
}

export interface EpicProjectStatusSummary {
  projectId: string;
  title: string;
  status: string;
  targetWords: number;
  currentWords: number;
  targetChapters: number;
  acceptedChapters: number;
  currentVolume: number;
  currentChapter: number;
  latestTask: Record<string, unknown> | null;
  lastError: string | null;
  reference: Record<string, unknown>;
  bible: Record<string, unknown>;
  qa: Record<string, unknown>;
  exports: Record<string, unknown>;
  updatedAt: string;
}

export interface EpicNovelProjectDetailResponse {
  project: EpicNovelProject;
  world_bible: Record<string, unknown> | null;
  world_atlas: Record<string, unknown> | null;
  bureaucracy_system: Record<string, unknown> | null;
  power_system: Record<string, unknown> | null;
  master_bible: Record<string, unknown> | null;
  worldview_candidates: Array<Record<string, unknown>>;
  nations: Array<Record<string, unknown>>;
  map_regions: Array<Record<string, unknown>>;
  institutions: Array<Record<string, unknown>>;
  factions: Array<Record<string, unknown>>;
  characters: Array<Record<string, unknown>>;
  character_arcs: Array<Record<string, unknown>>;
  global_spine: Record<string, unknown> | null;
  bible_sections: Array<Record<string, unknown>>;
  volume_contracts: Array<Record<string, unknown>>;
  foreshadows: Array<Record<string, unknown>>;
  reader_questions: Array<Record<string, unknown>>;
  pleasure_beats: Array<Record<string, unknown>>;
  chapter_contracts: EpicChapterContract[];
  chapters: EpicChapter[];
  acceptance_reports: EpicAcceptanceReport[];
  issues: Array<Record<string, unknown>>;
  retrieval_traces: Array<Record<string, unknown>>;
  character_knowledge_ledgers: Array<Record<string, unknown>>;
  secret_exposure_policies: Array<Record<string, unknown>>;
  reader_question_ledgers: Array<Record<string, unknown>>;
  character_growth_ledgers: Array<Record<string, unknown>>;
  authority_ledgers: Array<Record<string, unknown>>;
  relationship_ledgers: Array<Record<string, unknown>>;
  secret_ledgers: Array<Record<string, unknown>>;
  artifacts: Array<Record<string, unknown>>;
  logs: EpicGenerationLog[];
}

export interface EpicDashboardResponse {
  project_id: string;
  current_words: number;
  completed_chapters: number;
  completed_volumes: number;
  bible_coverage_score: number;
  outline_execution_rate: number;
  world_atlas_usage_rate: number;
  bureaucracy_consistency_issues: number;
  power_violations: number;
  character_arc_gaps: number;
  heroine_appendage_risk: number;
  a_tier_disappearance_warnings: number;
  unpaid_foreshadows: number;
  pleasure_gap_warnings: number;
  side_quest_warnings: number;
  continuity_issues: number;
  anti_cliche_issues: number;
  storage_state_errors: number;
  reference_safety_warnings: number;
  reference_overfit_warnings: number;
  download_import_status: Record<string, number>;
  generated_pattern_card_count: number;
  repair_count: number;
  cost_estimate: number;
  current_job: Record<string, unknown> | null;
  job_progress: number;
  estimated_remaining_cost: number;
  job_errors: string[];
  retry_status: string | null;
}

export interface EpicBatchGenerationRequest {
  from_chapter: number;
  to_chapter: number;
}

export interface EpicBatchGenerationResponse {
  project_id: string;
  generated: number[];
  failed: Array<{ chapter_number?: number; error: string }>;
  skipped: number[];
}

export interface EpicExportMetadata {
  filename: string;
  mediaType: string;
  path: string;
  objectId: string;
  sha256: string;
  bytes: number;
  createdAt: string;
  downloadRoute: string;
  artifactType: string;
  contentReturned: boolean;
  contentChars: number;
  artifactRecordId?: string;
  status?: string;
}
