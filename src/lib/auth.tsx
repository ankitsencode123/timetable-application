import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { getSession, login as loginRequest, logoutRequest, storeSession } from "./api";
import type { Session, User } from "./api";

type AuthValue = {
  ready: boolean;
  user: User | null;
  role: string;
  isAdmin: boolean;
  signIn: (email: string, password: string) => Promise<Session>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const sync = () => setSession(getSession());
    sync();
    setReady(true);
    window.addEventListener("tt:session", sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener("tt:session", sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    const data = await loginRequest(email, password);
    storeSession(data);
    setSession(data);
    return data;
  }, []);

  const signOut = useCallback(async () => {
    try {
      await logoutRequest();
    } catch {
      /* clear locally regardless */
    }
    storeSession(null);
    setSession(null);
  }, []);

  const value = useMemo<AuthValue>(() => {
    const role = (session?.user?.role || "").toUpperCase();
    return {
      ready,
      user: session?.user ?? null,
      role,
      isAdmin: role === "ADMIN",
      signIn,
      signOut,
    };
  }, [ready, session, signIn, signOut]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
