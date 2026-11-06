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

export interface CatalogTeacher {
  id: number;
  short_name: string;
  full_name: string;
  subjects_csv: string;
  is_internal: boolean;
  designation?: string;
}

export interface CatalogSubject {
  id: number;
  code: string;
  name: string;
  program: string;
  semester: string;
  entry_type: string;
  weekly_hours: number;
}

export interface CatalogProgram {
  id: number;
  name: string;
  semesters_count: number;
  description: string;
  is_active: boolean;
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

// ── Automated Timetable Generator Types ───────────────────────
export interface GeneratorConfig {
  working_days: string[];
  allow_extension_slots: boolean;
  default_theory_sessions: number;
  default_practical_sessions: number;
  full_session_minutes?: number;
  short_session_minutes?: number;
  allow_split_sessions?: boolean;
  max_split_rounds?: number;
  allow_short_in_regular_slot?: boolean;
  time_limit_s: number;
  seed: number | null;
  max_restarts?: number;
  sa_start_temp?: number;
  sa_end_temp?: number;
  sa_max_iterations?: number;
}

export interface GeneratorTimeSlot {
  id: number;
  start: string;
  end: string;
  tier: number;
  label?: string;
}

export interface GeneratorSemester {
  id: string;
  programme: string;
  number: number;
  name?: string;
  strength?: number | null;
}

export interface GeneratorRoom {
  number: string;
  facility: string;
  kind: 'classroom' | 'lab';
  capacity?: number | null;
}

export interface GeneratorSlotPreference {
  day: string;
  slot: number;
  facility: string;
}

export interface GeneratorTeacher {
  id: string;
  name: string;
  internal: boolean;
  short_name?: string;
  auto_email?: string;
  auto_password?: string;
  preferences: GeneratorSlotPreference[];
  unavailable: [string, number][];
  unavailable_days: string[];
}

export interface GeneratorSubject {
  code: string;
  name: string;
  semester_id: string;
  class_type: 'theory' | 'practical';
  internal_teacher_id?: string | null;
  external_teacher_id?: string | null;
  sessions_per_week?: number | null;
  external_sessions?: number | null;
  facility?: string;
}

export interface GeneratorBoard {
  config: GeneratorConfig;
  slots: GeneratorTimeSlot[];
  extension_slots: GeneratorTimeSlot[];
  semesters: GeneratorSemester[];
  rooms: GeneratorRoom[];
  teachers: GeneratorTeacher[];
  subjects: GeneratorSubject[];
}

export interface GeneratorScheduledClass {
  semester_id: string;
  programme: string;
  subject_code: string;
  subject_name: string;
  class_type: string;
  teacher_id: string;
  teacher_name: string;
  day: string;
  slot_id: number;
  slot_title: string;
  start: string;
  end: string;
  room: string;
  preference_rank: number | null;
  extension_slot: boolean;
  duration_hours?: number;
  is_half_session?: boolean;
  is_split_piece?: boolean;
}

export interface GeneratorIssue {
  level: 'error' | 'warning';
  message: string;
}

export interface GeneratorUnscheduledSlotConflict {
  day: string;
  slot_id: number;
  slot_label: string;
  start: string;
  end: string;
  time_str: string;
  conflict_type: string;
  detail: string;
}

export interface GeneratorUnscheduledClass {
  session_id?: number;
  subject: string;
  subject_name?: string;
  semester: string;
  semester_name?: string;
  programme?: string;
  semester_number?: number;
  teacher: string;
  teacher_name?: string;
  teacher_label?: string;
  class_type?: string;
  duration_hours?: number;
  is_half_session?: boolean;
  is_split_piece?: boolean;
  reason: string;
  detailed_reasons?: string[];
  slot_conflicts?: GeneratorUnscheduledSlotConflict[];
  category_slots?: {
    semester_busy?: Array<{ slot: string; detail: string; code?: string; teacher?: string }>;
    teacher_busy?: Array<{ slot: string; detail: string; code?: string; cohort?: string }>;
    teacher_daily_limit?: Array<{ day: string; detail: string }>;
    teacher_free_days?: string[];
    teacher_unavailable?: string[];
    subject_repeat_days?: Array<{ day: string; detail: string }>;
    room_unavailable?: string[];
  };
  remedy?: string;
}

export interface GeneratorResult {
  classes: GeneratorScheduledClass[];
  unscheduled: GeneratorUnscheduledClass[];
  teacher_free_days: Record<string, string[]>;
  preferences: {
    _overall: {
      honoured: number;
      achievable: number;
      percent: number;
    };
    [teacherId: string]: any;
  };
  stats: {
    runtime_s: number;
    attempts: number;
    repairs: number;
    sa_iterations: number;
    final_cost: number;
    extension_slot_classes?: number;
    half_session_classes?: number;
    split_piece_classes?: number;
    split_rounds?: number;
    split_sessions?: number;
  };
  violations: string[];
  issues: GeneratorIssue[];
  complete: boolean;
  summary: string;
}

export interface CreatedAccountInfo {
  teacher_id?: string;
  short_name: string;
  full_name: string;
  email: string;
  password?: string;
  is_internal?: boolean;
}

export interface ExtractedFilesData {
  semesters: GeneratorSemester[];
  subjects: GeneratorSubject[];
  teachers: GeneratorTeacher[];
  created_accounts?: CreatedAccountInfo[];
  sync_summary?: {
    synced_teachers_count?: number;
    synced_subjects_count?: number;
    new_accounts_created?: number;
    error?: string;
  };
  summary: {
    semesters_count: number;
    subjects_count: number;
    teachers_count: number;
  };
}

