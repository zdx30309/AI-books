import type { TaskStatus } from "./types";

export type LongformAction = "plan" | "revise_outline" | "confirm_outline" | "next_chapter" | "revise_chapter" | "assemble" | "import_materials" | "resume";
export type MaterialRole = "auto" | "source" | "outline" | "chapter" | "editorial" | "report" | "instruction";
export interface GeneralLongformAction {
  action: LongformAction;
  feedback?: string;
  chapter_number?: number;
  packaging_index?: number;
  outline_version?: number;
  retry_uncertain?: boolean;
  request_id?: string;
}
export interface LongformSection {
  title: string; task: string; argument: string; material: string; target_words: number; source_ids: string[];
}
export interface LongformContract {
  number: number; title: string; question: string; new_information: string;
  previous_link: string; next_link: string; do_not_repeat: string[];
  target_words: number; sections: LongformSection[]; source_ids: string[];
}
export interface LongformOutline {
  topic: string; domain: string; audience: string; central_question: string; voice: string;
  scope_exclusions: string[]; target_words: number;
  packaging: { direction: string; title: string; description: string }[];
  recommended_index: number; recommendation: string; chapters: LongformContract[];
  research_gaps: string[]; handoff: Record<string, unknown>;
}
export interface LongformIssue { severity: string; type: string; description: string; suggested_fix?: string }
export interface LongformAudit {
  summary: string; endingHook: string; new_information: string[]; used_source_ids: string[];
  planned_vs_actual: { section: string; fulfilled: boolean; evidence: string }[];
  issues: LongformIssue[];
}
export interface LongformChapter {
  number: number; title: string; version: number; status: string; word_count?: number;
  audit?: LongformAudit; issues?: LongformIssue[]; accepted_at?: string;
  content_sha256?: string; state_persisted?: boolean;
  checkpoint?: { section: number; words: number; target: number };
}
export interface LongformArtifact {
  id: string; filename: string; kind: "outline" | "chapter" | "editorial" | "book";
  version: number; number?: number; accepted?: boolean; bytes: number; sha256: string; created_at: string;
}
export interface LongformSource {
  id: string; title?: string; url: string; authors?: string; year?: string;
  bibliography: string; approved_summary: string; supported_claims: string[]; limitations?: string[];
  verification: string; read_scope: string; retrieved_at: string; file_sha256: string;
}
export interface LongformCall {
  action: string; provider: string; model: string; status: string; started_at: string;
  estimated_cost: number | null; cost_status?: string; error?: string | null;
  usage?: Record<string, number | string | boolean>;
}
export interface GeneralLongformState {
  project_id: string; phase: string; task_status: TaskStatus; progress: number; current_step: string;
  task_error?: string | null; last_error?: string | null; export_error?: string | null;
  prompt_version: string; prompt_source_sha256: string; outline_version: number; confirmed: boolean;
  outline: LongformOutline | null; outline_candidate?: Partial<LongformOutline> | null;
  total_words: number; chapters: Record<string, { accepted?: LongformChapter | null; candidate?: LongformChapter | null; history?: LongformChapter[] }>;
  artifacts: LongformArtifact[]; sources: Record<string, LongformSource>;
  materials: { id: string; filename: string; role: MaterialRole; imported: boolean; confirmed: boolean; characters: number; sha256: string; notes?: string; approved_notes?: string }[];
  call_logs: LongformCall[]; can_resume: boolean;
  delivery_holds: { request_id: string; action: string; status: string; response_id?: string | null; error?: string }[];
  final_audit?: { issues: LongformIssue[]; actual_words: number; summary: string };
}
export interface LongformManifest {
  version: string; paragraph_count: number; source_sha256: string;
  sections: { id: string; file: string; paragraph_count: number; sha256: string; role: string; stages: string[] }[];
  stage_sections: Record<string, string[]>;
}
