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
  | "paused"
  | "manual_review";

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

export interface ClassicalReferenceTemplateResponse {
  enabled: boolean;
  name: string;
  source_filename: string;
  chars: number;
  updated_at: string;
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
  aigc_passed: boolean | null;
  aigc_max_score: number | null;
  aigc_required: boolean;
  aigc_status: string;
  aigc_initial_max_score: number | null;
  aigc_final_max_score: number | null;
  aigc_rewrite_rounds: number;
  aigc_quality_passed: boolean | null;
  aigc_report_available: boolean;
  aigc_manual_review_reason: string | null;
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

// Public-domain fusion rewrite

export interface PublicDomainRewriteSource {
  title: string;
  text: string;
  author?: string | null;
  publication_year?: number | null;
  rights_note?: string | null;
}

export interface PublicDomainRewriteRequest {
  sources: PublicDomainRewriteSource[];
  target_title?: string | null;
  target_direction?: string | null;
  target_length?: number;
  output_style?: string | null;
  public_domain_confirmed: boolean;
}

export interface PublicDomainSourceReport {
  title: string;
  chars: number;
  public_domain_basis: string;
  sampled_chars: number;
}

export interface PublicDomainRewriteResponse {
  task_id?: string | null;
  title: string;
  manuscript: string;
  blueprint: string;
  quality_report: string;
  source_reports: PublicDomainSourceReport[];
  warnings: string[];
  status: string;
  source_count: number;
  manuscript_chars: number;
  aigc_required: boolean;
  aigc_status: string;
  aigc_initial_max_score: number | null;
  aigc_final_max_score: number | null;
  aigc_rewrite_rounds: number;
  aigc_quality_passed: boolean | null;
  aigc_report_available: boolean;
}

export interface AigcQualityReport {
  policyVersion?: string;
  promptVersion?: string;
  status?: string;
  passed?: boolean;
  threshold?: number;
  initialMaxScore?: number;
  finalMaxScore?: number;
  rewriteRounds?: number;
  qualityPassed?: boolean;
  manualReviewReason?: string;
  technicalError?: string;
  costSummary?: {
    tencentCalls?: number;
    modelCalls?: number;
    estimatedCny?: number;
    estimatedUsd?: number;
  };
  candidates?: Array<Record<string, unknown>>;
  selectedCandidates?: Array<Record<string, unknown>>;
  bestSafeCandidate?: Record<string, unknown> | null;
  originalAvailable?: boolean;
  [key: string]: unknown;
}

// Report-driven Zhuque review (independent from the automatic task gate)

export type AigcReviewContentType = "article" | "classical" | "public_domain";
export type AigcReviewRewriteProvider = "grok" | "deepseek";
export type AigcReviewExperimentType = "layout_only" | "syntax_constrained" | "combined";

export interface AigcReviewRewriteProviderStatus {
  id: AigcReviewRewriteProvider;
  label: string;
  model: string;
  configured: boolean;
  default: boolean;
  automatic: boolean;
  reasoning: string;
  input_usd_per_m?: number;
  cached_input_usd_per_m?: number;
  output_usd_per_m?: number;
}

export interface AigcReviewSegment {
  segment_id: number;
  share_percent: number;
  char_count: number;
  aigc_value: number;
  label: "human" | "suspected" | "ai";
  start: number;
  end: number;
  mapped_text: string;
  mapping_confidence: number;
  recommended: boolean;
}

export interface AigcReviewStrategy {
  default_rewrite_provider: AigcReviewRewriteProvider;
  rewrite_model: string;
  fact_model: string;
  rewrite_providers: AigcReviewRewriteProviderStatus[];
  audit_model: string;
  rewrite_thinking: string;
  recommended_rewrite_reasoning: "low" | "medium" | "high";
  rewrite_reasoning_options: Array<"low" | "medium" | "high">;
  candidate_count: number;
  max_rewrite_attempts_per_segment: number;
  rewrite_protocol: string;
  experiment_mode: string;
  selection_mode: string;
  recommended_max_calibration_segments: number;
  strategies: string[];
  material_similarity_range: number[];
  material_paragraph_coverage_min: number;
  material_coverage_quantization: string;
  legacy_material_metrics_are_advisory: boolean;
  similarity_is_diagnostic_only: boolean;
  editorial_unit_chars: [number, number];
  macro_block_chars: [number, number];
  experiment_order: AigcReviewExperimentType[];
  audit_batch_units: number;
  default_run_budget_cny: number;
  default_calibration_budget_cny: number;
  proportional_report_learning: boolean;
  style_feature_version: string;
  feature_learning_min_real_samples: number;
  detector_efficacy: {
    status: "unproven" | "directional" | "validated";
    verified_pair_count: number;
    claim: string;
    promotion_rule: string;
    strategies: Array<{
      experiment_type: AigcReviewExperimentType;
      status: "unproven" | "directional" | "validated";
      verified_pair_count: number;
      positive_count: number;
      win_rate: number;
      median_human_percent_delta: number;
      final_pass_count: number;
    }>;
  };
  audit_scope: string;
  ocr: string;
  human_pass_rule: string;
  safety_target_percent: number;
  global_gate_changed: boolean;
  novel_workflows_included: boolean;
}

export interface AigcReviewReport {
  policy_version: string;
  prompt_version: string;
  filename: string;
  report_sha256: string;
  report_storage_id?: string;
  original_sha256: string;
  extraction_method: string;
  page_count: number;
  human_percent: number;
  suspected_percent: number;
  ai_percent: number;
  official_human_percent: number | null;
  official_suspected_percent: number | null;
  official_ai_percent: number | null;
  official_overview_verified: boolean;
  derived_human_share_percent: number;
  derived_suspected_share_percent: number;
  derived_ai_share_percent: number;
  pass_metric_source: "official_overview" | "unverified";
  strict_pass: boolean;
  source_match_verified: boolean;
  source_match_confidence: number;
  source_match_sample_chars: number;
  strict_threshold: number;
  target_human_percent: number;
  projected_human_percent: number;
  recommendation_mode: "calibration";
  calibration_share_percent: number;
  recommended_segment_ids: number[];
  segments: AigcReviewSegment[];
  warnings: string[];
  model_strategy: AigcReviewStrategy;
}

export interface OptimizeAigcReviewRequest {
  original_text: string;
  original_sha256: string;
  content_type: AigcReviewContentType;
  segments: AigcReviewSegment[];
  selected_segment_ids: number[];
  iteration?: number;
  preferred_strategy?: "syntax_rebuild" | "evidence_first" | "rhythm_recompose" | null;
  rewrite_provider?: AigcReviewRewriteProvider;
  rewrite_reasoning_effort?: "low" | "medium" | "high";
  experiment_type?: AigcReviewExperimentType;
  budget_cny?: number;
  title?: string | null;
  baseline_report?: AigcReviewReport;
}

export interface AigcReviewQualityAudit {
  pass: boolean;
  semantic_fidelity: number;
  information_completeness: number;
  fluency: number;
  voice_continuity: number;
  no_added_facts: boolean;
  no_removed_information: boolean;
  no_stance_change: boolean;
  no_plot_or_event_change: boolean;
  structural_rewrite_sufficient: boolean;
  claim_results: Array<{
    audit_id: string;
    claim_id: string;
    status: "preserved" | "altered" | "missing";
    original_evidence?: string;
    candidate_evidence?: string;
    reason?: string;
  }>;
  added_claims: Array<Record<string, unknown> | string>;
  failed_segment_ids: Array<number | string>;
  issues: Array<{
    segment_id: number | string;
    type: string;
    original_claim: string;
    candidate_change: string;
    severity: "high" | "medium" | "low";
  }>;
  reason: string;
}

export interface AigcReviewDeterministicReview {
  passed: boolean;
  length_ratio: number;
  paragraph_count_preserved: boolean;
  headings_preserved: boolean;
  anchors_preserved: boolean;
  protected_text_preserved: boolean;
  model_explanation_absent: boolean;
  hard_error_absent: boolean;
  material_rewrite_passed: boolean;
  literal_similarity: number;
  rewritten_paragraph_ratio: number;
  required_paragraph_coverage: number;
  unchanged_editable_paragraphs: number;
  editable_paragraphs: number;
  missing_anchors: string[];
  extra_anchors: string[];
  reasons: string[];
}

export interface AigcReviewRewriteAttempt {
  attempt: number;
  passed: boolean;
  structured_output: boolean;
  protocol_issues: string[];
  deterministic_review: AigcReviewDeterministicReview;
  input_tokens: number;
  cached_input_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  cost_cny: number;
  strategy_id: string;
  rewrite_provider: AigcReviewRewriteProvider | "local";
  rewrite_model: string;
  hard_safety_passed: boolean;
  substantive_rewrite_passed: boolean;
  substantive_rewrite_reasons?: string[];
  material_rewrite_advisory_passed: boolean;
}

export interface AigcReviewEditorialUnitResult {
  unit_id: string;
  original_chars: number;
  candidate_chars: number;
  original_text: string;
  candidate_text: string;
  status: "ready" | "rejected";
  fact_ledger_mode: "cached" | "model" | "test_fallback" | "classical_legacy" | "not_required_identical_non_whitespace";
  fact_ledger_claim_count: number;
  fact_ledger_cost_cny: number;
  hard_safety_passed: boolean;
  substantive_rewrite_passed: boolean;
  rewrite_attempts: number;
  attempts: AigcReviewRewriteAttempt[];
  deterministic_review: AigcReviewDeterministicReview;
  candidate_preview: string;
  rejected_candidate_preview: string;
}

export interface AigcReviewSegmentResult {
  segment_id: number;
  strategy_id: string;
  rewrite_provider: AigcReviewRewriteProvider | "local";
  rewrite_model: string;
  hard_safety_passed: boolean;
  substantive_rewrite_passed: boolean;
  material_rewrite_advisory_passed: boolean;
  status: "ready" | "rejected";
  original_chars: number;
  candidate_chars: number;
  report_aigc_value?: number;
  report_label?: "human" | "suspected" | "ai";
  style_diagnosis?: string;
  rewrite_attempts: number;
  attempts: AigcReviewRewriteAttempt[];
  editorial_unit_count: number;
  unit_results: AigcReviewEditorialUnitResult[];
  deterministic_review: AigcReviewDeterministicReview;
  candidate_preview: string;
  rejected_candidate_preview: string;
  style_feature_version: string;
  style_feature_delta: Record<string, number>;
}

export interface AigcReviewOptimizeResult {
  request_id: string;
  status: "candidate_ready" | "manual_review";
  deliverable: boolean;
  next_action: string;
  original_sha256: string;
  candidate_sha256: string | null;
  original_text: string;
  candidate_text: string;
  guarded_candidate_preview?: string;
  guarded_candidate_text?: string;
  selected_segment_ids: number[];
  iteration?: number;
  preferred_strategy?: string | null;
  content_type?: AigcReviewContentType;
  experiment_type: AigcReviewExperimentType;
  segment_results: AigcReviewSegmentResult[];
  quality_audit: AigcReviewQualityAudit | null;
  editorial_unit_count?: number;
  audit_batch_count?: number;
  budget?: {
    currency: string;
    limit_cny: number;
    spent_cny: number;
    exhausted: boolean;
  };
  cost: {
    currency: string;
    estimated: boolean;
    input_tokens: number;
    output_tokens: number;
    estimated_cny: number;
  };
  models: {
    rewrite_provider: AigcReviewRewriteProvider | "local";
    rewrite: string;
    fact: string;
    audit: string;
    rewrite_thinking: string;
  };
  prompt_version: string;
  archive_task_id?: string;
}

export interface AigcReviewComparison {
  comparison_id: string;
  request_id: string;
  created_at: string;
  baseline_human_percent: number;
  recheck_human_percent: number;
  baseline_official_human_percent: number | null;
  recheck_official_human_percent: number | null;
  baseline_derived_human_share_percent: number;
  recheck_derived_human_share_percent: number;
  official_overview_verified: boolean;
  human_pass_threshold: number;
  human_percent_delta: number;
  baseline_weighted_aigc: number;
  recheck_weighted_aigc: number;
  selected_weighted_delta: number;
  selected_humanized_share_percent: number;
  strict_pass: boolean;
  detector_pass: boolean;
  quality_pass: boolean;
  segment_deltas: Array<{
    segment_id: number;
    selected: boolean;
    strategy_id: string | null;
    share_percent: number;
    before_aigc_value: number;
    after_aigc_value: number;
    before_label: "human" | "suspected" | "ai";
    after_label: "human" | "suspected" | "ai";
    became_human: boolean;
    delta: number;
    verdict: "strong_improvement" | "improvement" | "neutral" | "worse";
    style_feature_version: string | null;
    style_feature_delta: Record<string, number>;
  }>;
  strategy_effectiveness: Array<{
    strategy_id: string;
    segment_ids: number[];
    weighted_delta: number;
    humanized_share_percent: number;
    green_conversion_rate: number;
    verdict: "strong_improvement" | "improvement" | "neutral" | "worse";
  }>;
  recommended_next_strategy: "syntax_rebuild" | "evidence_first" | "rhythm_recompose" | null;
  recommendation_reason: string;
  stop_automatic_spend: boolean;
  attribution_mode: "proportional_document_overlap";
  learning_eligible: false;
  historical_strategy_summary: Array<{
    strategy_id: string;
    experiment_count: number;
    average_weighted_delta: number;
    average_green_conversion_rate: number;
    green_experiment_count: number;
    improvement_count: number;
    worse_count: number;
  }>;
  historical_feature_summary: Array<{
    feature: string;
    sample_count: number;
    pearson_correlation: number;
    mean_score_delta_when_feature_increased: number;
    mean_score_delta_when_feature_decreased: number;
    evidence_grade: "exploratory" | "directional" | "local_surrogate_ready";
  }>;
  prompt_version: string;
  archive_task_id?: string;
}

export type AigcReviewCalibrationVariant =
  | "original"
  | "fact_reauthor"
  | "human_directed"
  | "human_edited"
  | "human_control"
  | "external_saas"
  | "layout_only"
  | "syntax_constrained"
  | "combined";

export interface AigcReviewCalibrationSample {
  sample_id: string;
  source_group: string;
  variant_type: AigcReviewCalibrationVariant;
  content_type: AigcReviewContentType;
  strategy_id?: string | null;
  parent_sha256?: string | null;
  text: string;
  text_sha256: string;
  char_count: number;
  status: "awaiting_zhuque" | "labeled";
  aigc_value: number | null;
  label: "human" | "suspected" | "ai" | null;
  green: boolean | null;
  learning_eligible: boolean;
  quality_gate_passed?: boolean | null;
  verification_mode?: "manual_value" | "pdf_exact_hash";
  human_percent?: number;
  suspected_percent?: number;
  ai_percent?: number;
  official_overview_verified?: boolean;
  created_at: string;
  updated_at: string;
}

export interface AigcReviewPairedAnalysis {
  analysis_id: string;
  policy_version: "report-driven-v5";
  content_type: AigcReviewContentType;
  baseline_sha256: string;
  modified_sha256: string;
  hashes_confirmed: boolean;
  source_match_verified: boolean;
  baseline_metrics: Record<string, number | boolean | null>;
  modified_metrics: Record<string, number | boolean | null>;
  feature_before: Record<string, number>;
  feature_after: Record<string, number>;
  feature_delta: Record<string, number>;
  non_whitespace_similarity: number;
  length_ratio: number;
  segment_alignment: Array<{
    baseline_segment_id: number;
    modified_segment_id: number;
    position_overlap: number;
    text_similarity: number;
    anchor_overlap: number;
    confidence: number;
  }>;
  sentence_diff: {
    inserted_count: number;
    removed_count: number;
    inserted: string[];
    removed: string[];
  };
  quality_risks: Array<{
    type: string;
    severity: "high" | "medium" | "low";
    evidence: unknown;
    reason: string;
  }>;
  quality_status: "failed_or_unverified" | "unverified";
  strategy_hypotheses: Array<{
    id: string;
    direction: string;
    evidence: string | number;
    causal_status: "unverified";
  }>;
  causal_confidence: "observational_confounded";
  learning_eligible: false;
  next_experiment: "layout_only";
  fallback_workflow: {
    available: boolean;
    mode: "external_saas_minimal_quality_repair";
    repair_scope: string[];
    must_recheck_repaired_text: true;
    reuse_external_report_for_delivery: false;
    reason: string;
  };
  detector_efficacy: AigcReviewStrategy["detector_efficacy"];
  baseline_report: AigcReviewReport;
  modified_report: AigcReviewReport;
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

export interface WeReadCompanyRequest {
  company_name: string;
  max_books?: number | null;
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
  copyright_id: string | null;
  translator: string | null;
  source_url: string | null;
  platform: string | null;
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
  warnings?: string[];
}

export interface WeReadCatalogItem {
  title: string;
  author: string | null;
  source_url: string | null;
}

export interface WeReadExcelImportResponse {
  total: number;
  catalog_text: string;
  items: WeReadCatalogItem[];
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

export interface FamilyLongNovelExtendRequest {
  target_words?: number;
  future_chapter_words?: number;
  expansion_notes?: string | null;
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
  pre_aigc_content: string | null;
  aigc_policy_version: string | null;
  aigc_status: string;
  aigc_report: AigcQualityReport;
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
  cached_prompt_tokens?: number;
  completion_tokens: number;
  reasoning_tokens?: number;
  estimated_cost: number;
  actual_cost?: number | null;
  cost_currency: string;
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
