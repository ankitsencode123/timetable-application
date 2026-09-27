import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { User } from './types';

// ── Auth Store ────────────────────────────────────────────────────────────────
// NOTE: We do NOT store the access token in state — it lives in an HttpOnly
// cookie managed by the server. We only store non-sensitive user info.

interface AuthState {
  user: User | null;
  isLoggedIn: boolean;
  csrfToken: string | null;
  setCsrfToken: (t: string) => void;
  login: (user: User) => void;
  updateUser: (partial: Partial<User>) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      isLoggedIn: false,
      csrfToken: null,
      setCsrfToken: (csrfToken) => set({ csrfToken }),
      login: (user) => {
        set({ user, isLoggedIn: true });
      },
      updateUser: (partial) => {
        set(s => ({ user: s.user ? { ...s.user, ...partial } : s.user }));
      },
      logout: () => {
        set({ user: null, isLoggedIn: false, csrfToken: null });
      },
    }),
    {
      name: 'auth-user',
      // Only persist user info and non-sensitive CSRF (which isn't a direct sec threat on client)
      partialize: (s) => ({ user: s.user, isLoggedIn: s.isLoggedIn, csrfToken: s.csrfToken }),
    }
  )
);

// ── Timetable workspace state ─────────────────────────────────────────────────

import type { TimetableVersion, TimetableEntry, TimetableFilters, ParsedActionItem, CatalogTeacher, CatalogProgram, CatalogSubject } from './types';
import { getCatalogTeachers, getCatalogPrograms, getCatalogSubjects } from './api';

interface WorkspaceState {
  currentVersionId: number | null;
  entries: TimetableEntry[];
  versions: TimetableVersion[];
  catalogTeachers: CatalogTeacher[];
  catalogPrograms: CatalogProgram[];
  catalogSubjects: CatalogSubject[];
  filters: TimetableFilters;
  density: 'comfortable' | 'compact';
  sidebarTab: 'dashboard' | 'versions' | 'validation' | 'publish';
  pendingChatQuery: string | null;
  pendingDeterminateActions: ParsedActionItem[] | null;
  setCurrentVersion: (id: number, entries: TimetableEntry[]) => void;
  setVersions: (versions: TimetableVersion[]) => void;
  updateEntries: (entries: TimetableEntry[]) => void;
  setFilters: (f: Partial<TimetableFilters>) => void;
  setDensity: (d: 'comfortable' | 'compact') => void;
  setSidebarTab: (t: WorkspaceState['sidebarTab']) => void;
  setPendingChatQuery: (q: string | null) => void;
  setPendingDeterminateActions: (actions: ParsedActionItem[] | null) => void;
  loadCatalog: () => Promise<void>;
}

export const useWorkspaceStore = create<WorkspaceState>()((set) => ({
  currentVersionId: null,
  entries: [],
  versions: [],
  catalogTeachers: [],
  catalogPrograms: [],
  catalogSubjects: [],
  filters: { program: 'All', semester: 'All', teacher: '', subject: '', room: '', day: '', search: '' },
  density: 'comfortable',
  sidebarTab: 'dashboard',
  pendingChatQuery: null,
  pendingDeterminateActions: null,
  setCurrentVersion: (id, entries) => set({ currentVersionId: id, entries }),
  setVersions: (versions) => set({ versions }),
  updateEntries: (entries) => set({ entries }),
  setFilters: (f) => set((s) => ({ filters: { ...s.filters, ...f } })),
  setDensity: (density) => set({ density }),
  setSidebarTab: (sidebarTab) => set({ sidebarTab }),
  setPendingChatQuery: (q) => set({ pendingChatQuery: q }),
  setPendingDeterminateActions: (actions) => set({ pendingDeterminateActions: actions }),
  loadCatalog: async () => {
    try {
      const [teachers, programs, subjects] = await Promise.all([
        getCatalogTeachers(),
        getCatalogPrograms(),
        getCatalogSubjects()
      ]);
      set({ catalogTeachers: teachers, catalogPrograms: programs, catalogSubjects: subjects });
    } catch (e) {
      console.error('Failed to load catalog', e);
    }
  },
}));
