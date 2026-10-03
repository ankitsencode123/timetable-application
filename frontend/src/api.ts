// Strongly typed API client — all calls go through here
// Auth tokens are transmitted via HttpOnly cookies (set by server).
// We include credentials: 'include' and pass the CSRF token header on mutations.

import type {
  TimetableEntry, TimetableVersion, VersionDetail,
  ValidationResult, ActionExecuteResponse, ActionChatResponse,
  CatalogTeacher, CatalogSubject, CatalogProgram,
} from './types';

const envBaseUrl = (import.meta as any).env?.VITE_API_BASE_URL;
const BASE = envBaseUrl ? `${envBaseUrl.replace(/\/+$/, '')}/api` : '/api';

// ── CSRF helper ──────────────────────────────────────────────────────────────
// The server sets a readable `csrf_token` cookie (NOT httponly).
// We read it and send it as the X-CSRF-Token header on mutations.

function getCsrfToken(): string | null {
  const localCsrf = localStorage.getItem('csrf_token');
  if (localCsrf) return localCsrf;
  
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

function csrfHeaders(): HeadersInit {
  const csrf = getCsrfToken();
  return csrf ? { 'X-CSRF-Token': csrf } : {};
}

// ── Base request ─────────────────────────────────────────────────────────────

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const isJson = !init.headers?.toString().includes('urlencoded');
  const headers: HeadersInit = {
    ...(isJson ? { 'Content-Type': 'application/json' } : {}),
    ...(init.method && ['POST', 'PUT', 'PATCH', 'DELETE'].includes(init.method.toUpperCase()) ? csrfHeaders() : {}),
    ...(init.headers ?? {}),
  };

  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers,
    credentials: 'include', // Send cookies automatically
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const err = new Error(body.detail ?? `HTTP ${res.status}`) as Error & { status: number };
    err.status = res.status;
    throw err;
  }
  // 204 No Content
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ── Auth ─────────────────────────────────────────────────────────────────────

export interface LoginForm { email: string; password: string }
export interface AuthResponse {
  access_token: string;
  refresh_token: string;
  csrf_token: string;
  token_type: string;
  user: { id: number; email: string; full_name: string; role: string; is_active: boolean; last_login: string | null };
}

export async function login(form: LoginForm): Promise<AuthResponse> {
  const res = await fetch(`${BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(form),
    credentials: 'include',
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const err = new Error(body.detail ?? 'Login failed') as Error & { status: number };
    err.status = res.status;
    throw err;
  }
  const data = await res.json();
  if (data.csrf_token) localStorage.setItem('csrf_token', data.csrf_token);
  return data;
}

export async function logout(): Promise<void> {
  await request('/auth/logout', { method: 'POST' });
  localStorage.removeItem('csrf_token');
}

export async function refreshSession(): Promise<AuthResponse> {
  const data = await request<AuthResponse>('/auth/refresh', { method: 'POST' });
  if (data.csrf_token) localStorage.setItem('csrf_token', data.csrf_token);
  return data;
}

export async function getMe() {
  return request<{ id: number; email: string; full_name: string; role: string; last_login: string | null }>('/auth/me');
}

export async function changePassword(oldPassword: string, newPassword: string): Promise<{ message: string }> {
  return request('/auth/change-password', {
    method: 'POST',
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  });
}

// ── Profile (self-service) ────────────────────────────────────────────────────

export async function getMyProfile() {
  return request<{ id: number; email: string; full_name: string; role: string; is_active: boolean; last_login: string | null }>('/profile/me');
}

export async function updateMyProfile(fullName: string) {
  return request('/profile/me', {
    method: 'PATCH',
    body: JSON.stringify({ full_name: fullName }),
  });
}

// ── Admin: User management ────────────────────────────────────────────────────

export interface AdminUser {
  id: number; email: string; full_name: string; role: string;
  is_active: boolean; created_at: string; last_login: string | null;
  failed_attempts: number; locked_until: string | null;
}

export async function adminListUsers(): Promise<AdminUser[]> {
  return request<AdminUser[]>('/users');
}

export async function adminCreateUser(form: {
  email: string; password: string; full_name: string; role: string;
}): Promise<AdminUser> {
  return request<AdminUser>('/users', { method: 'POST', body: JSON.stringify(form) });
}

export async function adminUpdateUser(id: number, data: { full_name?: string; role?: string }): Promise<AdminUser> {
  return request<AdminUser>(`/users/${id}`, { method: 'PATCH', body: JSON.stringify(data) });
}

export async function adminEnableUser(id: number): Promise<AdminUser> {
  return request<AdminUser>(`/users/${id}/enable`, { method: 'POST' });
}

export async function adminDisableUser(id: number): Promise<AdminUser> {
  return request<AdminUser>(`/users/${id}/disable`, { method: 'POST' });
}

export async function adminResetPassword(id: number, newPassword: string): Promise<{ message: string }> {
  return request(`/users/${id}/reset-password`, {
    method: 'POST',
    body: JSON.stringify({ new_password: newPassword }),
  });
}

export async function adminGetUserActivity(id: number): Promise<{ id: number; action: string; details: string; created_at: string }[]> {
  return request(`/users/${id}/activity`);
}

// ── Public Timetable ─────────────────────────────────────────────────────────

export async function getPublicTimetable(): Promise<TimetableEntry[]> {
  return request<TimetableEntry[]>('/public/timetable');
}

export async function publicChat(message: string): Promise<{ message: string; parsed_actions?: unknown[] }> {
  return request('/public/chat', { method: 'POST', body: JSON.stringify({ message }) });
}

export async function getPublicMeta(): Promise<{ version_id: number; published_at: string; change_summary: string } | null> {
  try { return await request('/public/meta'); }
  catch { return null; }
}

// ── Versions (authenticated) ─────────────────────────────────────────────────

export async function listVersions(): Promise<TimetableVersion[]> {
  return request<TimetableVersion[]>('/versions');
}

export async function getVersion(id: number): Promise<VersionDetail> {
  return request<VersionDetail>(`/versions/${id}`);
}

export async function publishVersion(id: number): Promise<TimetableVersion> {
  return request<TimetableVersion>(`/versions/${id}/publish`, { method: 'POST' });
}

// ── Timetable (authenticated) ─────────────────────────────────────────────────

export async function getCurrentDraft(): Promise<TimetableVersion> {
  return request<TimetableVersion>('/timetable/draft');
}

export async function validateVersion(id: number): Promise<ValidationResult & { version_id: number; status: string }> {
  return request(`/timetable/validate/${id}`, { method: 'POST' });
}

// ── Actions (authenticated) ───────────────────────────────────────────────────

export async function executeActions(
  actions: unknown[], version_id?: number | null, partial_ok = false, skip_suggestions = false
): Promise<ActionExecuteResponse> {
  // Independent actions should not be lost because an unrelated action fails.
  // The backend still validates the combined schedule before saving.
  partial_ok = partial_ok || actions.length > 1
  return request<ActionExecuteResponse>('/actions/execute', {
    method: 'POST',
    body: JSON.stringify({ actions, version_id, partial_ok, skip_suggestions }),
  });
}

export async function parseActions(text: string): Promise<{ parsed_actions: unknown[]; action_count: number; interpretation: string }> {
  return request('/actions/parse', { method: 'POST', body: JSON.stringify({ text }) });
}

export async function actionChat(text: string, version_id?: number | null, execute = false): Promise<ActionChatResponse> {
  return request<ActionChatResponse>('/actions/chat', {
    method: 'POST',
    body: JSON.stringify({ text, version_id, execute }),
  });
}

export async function teacherChat(message: string): Promise<{ message: string; schedule_updated: boolean; parsed_actions: unknown[] }> {
  return request('/teacher/chat', { method: 'POST', body: JSON.stringify({ message }) });
}

export async function listTeacherEntries(): Promise<TimetableEntry[]> {
  return request<TimetableEntry[]>('/teachers/schedule');
}

// ── Catalog ──────────────────────────────────────────────────────────────────

export async function getCatalogTeachers(): Promise<CatalogTeacher[]> {
  return request<CatalogTeacher[]>('/catalog/teachers');
}

export async function getCatalogSubjects(): Promise<CatalogSubject[]> {
  return request<CatalogSubject[]>('/catalog/subjects');
}

export async function getCatalogPrograms(): Promise<CatalogProgram[]> {
  return request<CatalogProgram[]>('/catalog/programs');
}

export async function createCatalogBundle(payload: unknown): Promise<{ status: string; message: string }> {
  return request('/catalog/bundle', { method: 'POST', body: JSON.stringify(payload) });
}

// ── Calendar System (public) ──────────────────────────────────────────────────

export interface CalendarDayEntry {
  day: string; program: string; semester: string;
  start: string; end: string;
  subject_code: string; subject_name: string;
  teacher: string; type: string; room: string;
  cal_key?: string; cal_source?: string; cal_version_id?: number | null;
  cal_override_id?: number; cal_note?: string; cal_original?: Record<string, any>;
}

export interface CalendarDayResult {
  date: string; weekday: string; version_id: number | null;
  validity_id: number | null; validity_label: string | null;
  is_holiday: boolean; has_overrides: boolean;
  entries: CalendarDayEntry[]; cancelled: CalendarDayEntry[];
  notices: string[]; warnings?: string[];
  day_offs: { override_id: number; program: string | null; semester: string | null; reason: string }[];
}

export interface CalendarMonthDay {
  date: string; weekday: string; classes: number; cancelled: number;
  is_holiday: boolean; has_changes: boolean;
  validity_id: number | null; validity_label: string | null; version_id: number | null;
}

export interface CalendarOverrideRecord {
  id: number; date: string; weekday: string; action: string; target_key: string;
  entry?: Record<string, any> | null; changes?: Record<string, any> | null;
  program?: string | null; semester?: string | null;
  target?: Record<string, any> | null; reason: string; forced: boolean;
  forced_violations?: any[] | null;
  created_by: number; created_at: string;
}

export interface CalendarValidityRecord {
  id: number; version_id: number; scope: string;
  start_date: string; end_date: string;
  label: string; priority: number;
  created_by: number; created_at: string;
}

export async function getCalendarMonth(year: number, month: number, filters?: { program?: string; semester?: string; teacher?: string }): Promise<{ year: number; month: number; days: CalendarMonthDay[] }> {
  const params = new URLSearchParams()
  if (filters?.program) params.set('program', filters.program)
  if (filters?.semester) params.set('semester', filters.semester)
  if (filters?.teacher) params.set('teacher', filters.teacher)
  const qs = params.toString()
  return request(`/calendar/month/${year}/${month}${qs ? `?${qs}` : ''}`)
}

export async function getCalendarDay(date: string, filters?: { program?: string; semester?: string; teacher?: string }): Promise<CalendarDayResult> {
  const params = new URLSearchParams()
  if (filters?.program) params.set('program', filters.program)
  if (filters?.semester) params.set('semester', filters.semester)
  if (filters?.teacher) params.set('teacher', filters.teacher)
  const qs = params.toString()
  return request(`/calendar/day/${date}${qs ? `?${qs}` : ''}`)
}

export async function getCalendarRange(start: string, end: string, filters?: { program?: string; semester?: string; teacher?: string }): Promise<{ start: string; end: string; days: CalendarDayResult[] }> {
  const params = new URLSearchParams({ start, end })
  if (filters?.program) params.set('program', filters.program)
  if (filters?.semester) params.set('semester', filters.semester)
  if (filters?.teacher) params.set('teacher', filters.teacher)
  return request(`/calendar/range?${params.toString()}`)
}

// ── Calendar System (admin) ────────────────────────────────────────────────────

export async function adminGetCalendarDay(date: string): Promise<CalendarDayResult & { violations: any[]; overrides: CalendarOverrideRecord[] }> {
  return request(`/admin/calendar/day/${date}`)
}

export async function adminListOverrides(start?: string, end?: string): Promise<CalendarOverrideRecord[]> {
  const params = new URLSearchParams()
  if (start) params.set('start', start)
  if (end) params.set('end', end)
  const qs = params.toString()
  return request(`/admin/calendar/overrides${qs ? `?${qs}` : ''}`)
}

export async function adminCreateOverride(payload: {
  date: string; action: 'ADD' | 'CANCEL' | 'MODIFY' | 'DAY_OFF';
  target_key?: string; entry?: Record<string, any>; changes?: Record<string, any>;
  program?: string; semester?: string; reason?: string; force?: boolean;
}): Promise<{ override: CalendarOverrideRecord; forced_violations: any[]; day: CalendarDayResult }> {
  return request('/admin/calendar/overrides', { method: 'POST', body: JSON.stringify(payload) })
}

export async function adminDeleteOverride(id: number, force = false): Promise<{ deleted: number; day: CalendarDayResult }> {
  return request(`/admin/calendar/overrides/${id}?force=${force}`, { method: 'DELETE' })
}

export async function adminListValidity(start?: string, end?: string): Promise<CalendarValidityRecord[]> {
  const params = new URLSearchParams()
  if (start) params.set('start', start)
  if (end) params.set('end', end)
  const qs = params.toString()
  return request(`/admin/calendar/validity${qs ? `?${qs}` : ''}`)
}

export async function adminCreateValidity(payload: {
  version_id: number; scope: 'WEEK' | 'MONTH' | 'RANGE';
  anchor_date?: string; start_date?: string; end_date?: string;
  label?: string; priority?: number; force?: boolean;
}): Promise<{ window: CalendarValidityRecord; overlapping_windows: any[]; warnings: string[]; note: string }> {
  return request('/admin/calendar/validity', { method: 'POST', body: JSON.stringify(payload) })
}

export async function adminDeleteValidity(id: number, force = false): Promise<{ deleted: number; warnings: string[] }> {
  return request(`/admin/calendar/validity/${id}?force=${force}`, { method: 'DELETE' })
}

export async function adminCalendarAudit(limit = 100): Promise<{ id: number; actor_id: number | null; action: string; entity: string; entity_id: number | null; payload: any; created_at: string }[]> {
  return request(`/admin/calendar/audit?limit=${limit}`)
}

export { request as req };

