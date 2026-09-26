// Strongly typed API client — all calls go through here
// Auth tokens are transmitted via HttpOnly cookies (set by server).
// We include credentials: 'include' and pass the CSRF token header on mutations.

import type {
  TimetableEntry, TimetableVersion, VersionDetail,
  ValidationResult, ActionExecuteResponse, ActionChatResponse,
} from './types';

const BASE = '/api';

// ── CSRF helper ──────────────────────────────────────────────────────────────
// The server sets a readable `csrf_token` cookie (NOT httponly).
// We read it and send it as the X-CSRF-Token header on mutations.

function getCsrfToken(): string | null {
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
    ...(init.body ? csrfHeaders() : {}),
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
  return res.json();
}

export async function logout(): Promise<void> {
  await request('/auth/logout', { method: 'POST' });
}

export async function refreshSession(): Promise<AuthResponse> {
  return request<AuthResponse>('/auth/refresh', { method: 'POST' });
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
  actions: unknown[], version_id?: number | null, partial_ok = false,
): Promise<ActionExecuteResponse> {
  // Independent actions should not be lost because an unrelated action fails.
  // The backend still validates the combined schedule before saving.
  partial_ok = partial_ok || actions.length > 1
  return request<ActionExecuteResponse>('/actions/execute', {
    method: 'POST',
    body: JSON.stringify({ actions, version_id, partial_ok }),
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

export async function createCatalogBundle(payload: unknown): Promise<{ status: string; message: string }> {
  return request('/catalog/bundle', { method: 'POST', body: JSON.stringify(payload) });
}

export { request as req };
