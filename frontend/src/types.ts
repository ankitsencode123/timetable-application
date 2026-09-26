// Core domain types matching the backend data model

export interface TimetableEntry {
  day: string;
  program: string;
  semester: string;
  start: string;
  end: string;
  subject_code: string;
  subject_name: string;
  teacher: string;
  type: 'Theory' | 'Practical';
  room: string;
  // UI-only fields
  _id?: string; // synthetic key for React
  _conflict?: boolean;
  _cancelled?: boolean;
  _modified?: boolean;
}

export interface TimetableVersion {
  id: number;
  created_by: number;
  parent_version_id: number | null;
  status: 'DRAFT' | 'VALIDATED' | 'PUBLISHED' | 'ARCHIVED';
  change_summary: string | null;
  created_at: string;
  published_at: string | null;
  archived_at: string | null;
  validation_result?: ValidationResult;
}

export interface VersionDetail extends TimetableVersion {
  entries: TimetableEntry[];
  validation_result: ValidationResult;
  llm_output: Record<string, unknown>;
}

export interface ValidationResult {
  violations: Violation[];
  schema_errors: SchemaError[];
  violation_count: number;
}

export interface Violation {
  rule: string;
  note?: string;
  day?: string;
  teacher?: string;
  teachers?: string[];
  room?: string;
  subject_code?: string;
  count?: number;
  expected_minutes?: number;
  actual_minutes?: number;
  a?: TimetableEntry;
  b?: TimetableEntry;
  entry?: TimetableEntry;
}

export interface SchemaError {
  field: string;
  message: string;
}

export interface User {
  id: number;
  email: string;
  full_name: string;
  role: 'ADMIN' | 'TEACHER';
}

export interface AuthTokens {
  access_token: string;
  token_type: string;
}

// ── Chat / Actions ────────────────────────────────────────
export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: Date;
  parsed_actions?: ParsedActionItem[];
  execution_result?: ActionExecuteResponse;
  loading?: boolean;
  error?: string;
}

export interface ParsedActionItem {
  action: string;
  [key: string]: unknown;
}

export interface ActionResult {
  action_type: string;
  success: boolean;
  change_log: string;
  error: string;
  violated_constraint?: Violation;
  suggestions?: {
    free_rooms?: string[];
    free_slots?: { day: string; start: string; end: string; room?: string }[];
    proposed_action?: Record<string, unknown>;
    proposed_label?: string;
    note?: string;
    rich_suggestions?: { title: string, description: string, status?: string, action: Record<string, unknown> }[];
  };
  before?: unknown;
  after?: unknown;
}

export interface ActionExecuteResponse {
  success: boolean;
  results: ActionResult[];
  new_version_id: number | null;
  violations: Violation[];
  schema_errors: SchemaError[];
  change_log: string;
  partial_applied: boolean;
}

export interface ActionChatResponse {
  parsed_actions: ParsedActionItem[];
  action_count: number;
  interpretation: string;
  executed: boolean;
  execution_result?: ActionExecuteResponse;
}

// ── Filters ─────────────────────────────────────────────
export interface TimetableFilters {
  program: string;
  semester: string;
  teacher: string;
  subject: string;
  room: string;
  day: string;
  search: string;
}

export const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'] as const;
export const PROGRAMS = ['All', 'B.Tech', 'M.Tech', 'M.Sc'] as const;
export const SEMESTERS = ['All', '1st', '2nd', '3rd', '4th', '5th', '6th', '7th', '8th'] as const;

export const TIME_SLOTS = [
  '09:00', '10:00', '11:00', '12:00', '13:00', '14:00',
  '14:30', '15:00', '16:00', '16:30', '17:00', '17:30',
] as const;

export const TYPE_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  Theory:    { bg: '#EFF6FF', text: '#1D4ED8', border: '#BFDBFE' },
  Practical: { bg: '#F5F3FF', text: '#6D28D9', border: '#DDD6FE' },
};
