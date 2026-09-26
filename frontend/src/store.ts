import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { User } from './types';

// ── Auth Store ────────────────────────────────────────────────────────────────
// NOTE: We do NOT store the access token in state — it lives in an HttpOnly
// cookie managed by the server. We only store non-sensitive user info.

interface AuthState {
  user: User | null;
  isLoggedIn: boolean;
  login: (user: User) => void;
  updateUser: (partial: Partial<User>) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      isLoggedIn: false,
      login: (user) => {
        set({ user, isLoggedIn: true });
      },
      updateUser: (partial) => {
        set(s => ({ user: s.user ? { ...s.user, ...partial } : s.user }));
      },
      logout: () => {
        set({ user: null, isLoggedIn: false });
      },
    }),
    {
      name: 'auth-user',
      // Only persist user info, never tokens
      partialize: (s) => ({ user: s.user, isLoggedIn: s.isLoggedIn }),
    }
  )
);

// ── Timetable workspace state ─────────────────────────────────────────────────

import type { TimetableVersion, TimetableEntry, TimetableFilters, ParsedActionItem } from './types';

interface WorkspaceState {
  currentVersionId: number | null;
  entries: TimetableEntry[];
  versions: TimetableVersion[];
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
}

export const useWorkspaceStore = create<WorkspaceState>()((set) => ({
  currentVersionId: null,
  entries: [],
  versions: [],
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
}));
