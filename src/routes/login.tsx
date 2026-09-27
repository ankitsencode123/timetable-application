import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { DEFAULT_API_BASE, getApiBase, setApiBase } from "@/lib/api";
import { Button, Field, Input, MonoLabel, Notice, errorText } from "@/components/ui-kit";

export const Route = createFileRoute("/login")({
  head: () => ({
    meta: [
      { title: "Sign in · Courselab Timetable Studio" },
      { name: "description", content: "Sign in to manage timetable generation and publishing." },
      { property: "og:title", content: "Sign in · Courselab Timetable Studio" },
      { property: "og:description", content: "Staff sign-in for the timetable studio." },
    ],
  }),
  component: LoginPage,
});

function LoginPage() {
  const { signIn, user } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [base, setBase] = useState(DEFAULT_API_BASE);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => setBase(getApiBase()), []);
  useEffect(() => {
    if (user) navigate({ to: "/admin", replace: true });
  }, [user, navigate]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      setApiBase(base);
      await signIn(email.trim(), password);
      navigate({ to: "/admin", replace: true });
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-paper px-4">
      <div className="w-full max-w-sm">
        <div className="mb-5 flex items-center gap-3">
          <div className="grid size-8 place-items-center rounded-md bg-ink font-display text-sm font-semibold text-paper">
            CT
          </div>
          <div className="leading-none">
            <div className="font-display text-[15px] font-semibold tracking-tight">Courselab</div>
            <MonoLabel>Timetable Studio</MonoLabel>
          </div>
        </div>

        <form
          onSubmit={submit}
          className="space-y-4 rounded-xl bg-card p-5 ring-1 ring-black/5"
        >
          <h1 className="font-display text-xl font-semibold tracking-tight">Staff sign in</h1>
          {error && <Notice tone="error">{error}</Notice>}
          <Field label="Email">
            <Input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@university.edu"
            />
          </Field>
          <Field label="Password">
            <Input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>
          <Field label="Backend URL" hint="Where your FastAPI server is running.">
            <Input value={base} onChange={(e) => setBase(e.target.value)} />
          </Field>
          <Button variant="primary" type="submit" className="w-full" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </Button>
        </form>

        <Link
          to="/"
          className="mt-4 block text-center text-xs text-ink-soft hover:text-ink"
        >
          View the published timetable
        </Link>
      </div>
    </div>
  );
}
