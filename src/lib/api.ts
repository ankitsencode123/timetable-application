/* Typed client for the Timetable FastAPI backend. */

const ENV_BASE = (import.meta as { env?: Record<string, string | undefined> }).env?.[
  "VITE_API_BASE_URL"
];
export const DEFAULT_API_BASE = ENV_BASE || "http://localhost:8000";

const BASE_KEY = "tt.apiBase";
const SESSION_KEY = "tt.session";

export type User = {
  id: number;
  email: string;
  full_name: string;
  role: string;
  is_active: boolean;
  last_login?: string | null;
};

export type Session = {
  access_token: string;
  refresh_token?: string;
  csrf_token: string;
  user: User;
};

export type Entry = {
  day: string;
  program: string;
  semester: string;
  start: string;
  end: string;
  subject_code: string;
  subject_name: string;
  teacher: string;
  type: string;
  room: string;
};

export type Version = {
  id: number;
  created_by?: number | null;
  parent_version_id?: number | null;
  status: string;
  change_summary?: string | null;
  created_at?: string | null;
  published_at?: string | null;
  archived_at?: string | null;
  violation_count?: number | null;
  entries?: Entry[];
  [key: string]: unknown;
};

export type GenerateResult = {
  version_id: number;
  status: string;
  schedule: Entry[];
  violations: Record<string, unknown>[];
  schema_errors: Record<string, unknown>[];
  model_used?: string;
  validator_attempts?: number;
  warning?: string | null;
  [key: string]: unknown;
};

export type Teacher = {
  id: number;
  short_name: string;
  full_name: string;
  subjects_csv: string;
  is_internal: boolean;
  user_id?: number | null;
  auto_email?: string | null;
  auto_password?: string | null;
};

export type Subject = {
  id: number;
  code: string;
  name: string;
  program: string;
  semester: string;
  entry_type: string;
  weekly_hours: number;
};

export type Program = {
  id: number;
  name: string;
  semesters_count: number;
  description?: string | null;
  is_active: boolean;
};

export type BusySlot = {
  id: number;
  teacher_short_name: string;
  scope: string;
  day_of_week?: string | null;
  specific_date?: string | null;
  reason?: string | null;
};

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export function getApiBase(): string {
  if (typeof window === "undefined") return DEFAULT_API_BASE;
  return window.localStorage.getItem(BASE_KEY) || DEFAULT_API_BASE;
}

export function setApiBase(value: string) {
  window.localStorage.setItem(BASE_KEY, value.trim().replace(/\/+$/, ""));
}

export function getSession(): Session | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(SESSION_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as Session;
  } catch {
    return null;
  }
}

export function storeSession(session: Session | null) {
  if (typeof window === "undefined") return;
  if (session) window.localStorage.setItem(SESSION_KEY, JSON.stringify(session));
  else window.localStorage.removeItem(SESSION_KEY);
  window.dispatchEvent(new Event("tt:session"));
}

function describeError(status: number, payload: unknown): string {
  if (typeof payload === "string" && payload) return payload;
  const detail = (payload as { detail?: unknown })?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) =>
        [Array.isArray(d.loc) ? d.loc.slice(1).join(".") : "", d.msg].filter(Boolean).join(": "),
      )
      .join(" · ");
  }
  if (status === 401) return "Your session has expired. Please sign in again.";
  if (status === 403) return "You do not have permission to do that.";
  if (status === 404) return "Not found.";
  return `Request failed (${status})`;
}

type Options = {
  method?: string;
  body?: unknown;
  retry?: boolean;
};

async function raw(path: string, opts: Options = {}): Promise<Response> {
  const session = getSession();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (session?.access_token) headers["Authorization"] = `Bearer ${session.access_token}`;
  if (session?.csrf_token) headers["x-csrf-token"] = session.csrf_token;

  const init: RequestInit = {
    method: opts.method || "GET",
    headers,
    credentials: "include",
  };
  if (opts.body !== undefined) init.body = JSON.stringify(opts.body);
  return fetch(`${getApiBase()}${path}`, init);
}

export async function api<T>(path: string, opts: Options = {}): Promise<T> {
  let res: Response;
  try {
    res = await raw(path, opts);
  } catch {
    throw new ApiError(
      `Cannot reach the backend at ${getApiBase()}. Check the server URL and that CORS allows this site.`,
      0,
    );
  }

  if (res.status === 401 && opts.retry !== false && path !== "/api/auth/refresh") {
    const refreshed = await tryRefresh();
    if (refreshed) return api<T>(path, { ...opts, retry: false });
  }

  if (res.status === 204) return undefined as T;

  const text = await res.text();
  let payload: unknown = text;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    /* keep text */
  }

  if (!res.ok) throw new ApiError(describeError(res.status, payload), res.status);
  return payload as T;
}

async function tryRefresh(): Promise<boolean> {
  try {
    const res = await raw("/api/auth/refresh", { method: "POST" });
    if (!res.ok) {
      storeSession(null);
      return false;
    }
    const data = (await res.json()) as Session;
    storeSession(data);
    return true;
  } catch {
    return false;
  }
}

/* ---- auth ---- */
export const login = (email: string, password: string) =>
  api<Session>("/api/auth/login", { method: "POST", body: { email, password } });
export const logoutRequest = () => api<unknown>("/api/auth/logout", { method: "POST" });
export const me = () => api<User>("/api/auth/me");
export const changePassword = (body: Record<string, string>) =>
  api<unknown>("/api/auth/change-password", { method: "POST", body });

/* ---- users ---- */
export const listUsers = () => api<User[]>("/api/users");
export const createUser = (body: Record<string, unknown>) =>
  api<User>("/api/users", { method: "POST", body });
export const setUserEnabled = (id: number, enabled: boolean) =>
  api<unknown>(`/api/users/${id}/${enabled ? "enable" : "disable"}`, { method: "POST" });
export const resetUserPassword = (id: number, body: Record<string, unknown>) =>
  api<unknown>(`/api/users/${id}/reset-password`, { method: "POST", body });

/* ---- timetable ---- */
export const generateTimetable = (body: Record<string, string>) =>
  api<GenerateResult>("/api/timetable/generate", { method: "POST", body });
export const validateVersion = (id: number) =>
  api<{ version_id: number; status: string; violations: Record<string, unknown>[]; schema_errors: Record<string, unknown>[]; violation_count: number }>(
    `/api/timetable/validate/${id}`,
    { method: "POST" },
  );
export const getDraft = () => api<Version>("/api/timetable/draft");

/* ---- versions ---- */
export const listVersions = () => api<Version[]>("/api/versions");
export const getVersion = (id: number) => api<Version>(`/api/versions/${id}`);
export const publishVersion = (id: number) =>
  api<unknown>(`/api/versions/${id}/publish`, { method: "POST" });
export const unpublishVersion = (id: number) =>
  api<unknown>(`/api/versions/${id}/unpublish`, { method: "POST" });
export const rollbackVersion = (id: number) =>
  api<unknown>(`/api/versions/${id}/rollback`, { method: "POST" });

/* ---- public ---- */
export const publicTimetable = () => api<Entry[]>("/api/public/timetable");
export const publicMeta = () => api<unknown>("/api/public/meta");
export const publicByProgram = (program: string, semester: string) =>
  api<Entry[]>(
    `/api/public/timetable/program/${encodeURIComponent(program)}/${encodeURIComponent(semester)}`,
  );
export const publicByTeacher = (code: string) =>
  api<Entry[]>(`/api/public/timetable/teacher/${encodeURIComponent(code)}`);

export type ChatReply = {
  version_id?: number | null;
  message: string;
  schedule_updated?: boolean;
  response_data?: Record<string, unknown> | null;
  parsed_actions?: unknown[];
};
export const publicChat = (body: Record<string, string>) =>
  api<ChatReply>("/api/public/chat", { method: "POST", body });
export const teacherChat = (body: Record<string, string>) =>
  api<ChatReply>("/api/teacher/chat", { method: "POST", body });
export const adminChat = (body: Record<string, string>) =>
  api<ChatReply>("/api/actions/chat", { method: "POST", body });

/* ---- actions ---- */
export const parseActions = (text: string) =>
  api<{ parsed_actions: Record<string, unknown>[]; action_count: number; interpretation: string }>(
    "/api/actions/parse",
    { method: "POST", body: { text } },
  );
export const executeActions = (actions: unknown[]) =>
  api<Record<string, unknown>>("/api/actions/execute", {
    method: "POST",
    body: { actions },
  });
export const smartSchedule = (body: Record<string, unknown>) =>
  api<Record<string, unknown>>("/api/actions/smart-schedule", { method: "POST", body });

/* ---- catalog ---- */
export const listTeachers = () => api<Teacher[]>("/api/catalog/teachers");
export const createTeacher = (body: Record<string, unknown>) =>
  api<Teacher>("/api/catalog/teachers", { method: "POST", body });
export const updateTeacher = (id: number, body: Record<string, unknown>) =>
  api<Teacher>(`/api/catalog/teachers/${id}`, { method: "PATCH", body });
export const listSubjects = () => api<Subject[]>("/api/catalog/subjects");
export const createSubject = (body: Record<string, unknown>) =>
  api<Subject>("/api/catalog/subjects", { method: "POST", body });
export const deleteSubject = (id: number) =>
  api<unknown>(`/api/catalog/subjects/${id}`, { method: "DELETE" });
export const listPrograms = () => api<Program[]>("/api/catalog/programs");
export const createProgram = (body: Record<string, unknown>) =>
  api<Program>("/api/catalog/programs", { method: "POST", body });

/* ---- busy slots ---- */
export const listBusySlots = () => api<BusySlot[]>("/api/busy-slots");
export const createBusySlot = (body: Record<string, unknown>) =>
  api<{ slot: BusySlot; message?: string }>("/api/busy-slots", { method: "POST", body });
export const deleteBusySlot = (id: number) =>
  api<unknown>(`/api/busy-slots/${id}`, { method: "DELETE" });

export const health = () => api<unknown>("/api/health");
