export interface VideoScriptRow {
  index: number;
  task_id: string;
  source_url: string;
  source_title: string;
  script_title: string;
  platform: string;
  platform_label: string;
  word_count: number;
  chapter_count: number;
  created_at: string;
  similarity_score: number;
  source_overlap_ratio: number;
  longest_common_chars: number;
  risk_level: string;
  action: string;
  title_source: string;
  similarity_percent: string;
  detail: Record<string, unknown>;
}

export interface VideoScriptRowsResponse {
  total: number;
  columns: string[];
  rows: VideoScriptRow[];
  statuses: string[];
  thresholds: Record<string, number>;
}

export interface VideoScriptSyncRequest {
  statuses?: string[] | null;
  task_ids?: string[] | null;
  limit?: number | null;
  dry_run?: boolean;
  update_existing?: boolean;
  fmt?: "csv" | "tsv" | "xlsx" | "md";
}

export interface VideoScriptSyncResponse {
  status: string;
  provider: string;
  written_rows: number;
  updated_rows: number;
  skipped_rows: number;
  export_path?: string | null;
  message: string;
  errors: string[];
  detail: Record<string, unknown>;
}

export interface KdocsConfigResponse {
  config: Record<string, unknown>;
  detect: {
    provider: string;
    ready: boolean;
    has_token: boolean;
    missing: string[];
    api_base: string;
    export_dir: string;
  };
}

export interface VideoScriptProblemsResponse {
  total: number;
  items: Array<{
    task_id: string;
    source_url: string;
    script_title: string;
    has_source_title: boolean;
    has_source_text: boolean;
    title_source: string;
  }>;
}

export interface VideoScriptLogsResponse {
  total: number;
  items: Array<Record<string, unknown>>;
}
