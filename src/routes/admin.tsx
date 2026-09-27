import { createFileRoute, Link, Outlet, useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { useAuth } from "@/lib/auth";
import { getDraft, health } from "@/lib/api";
import { Button, MonoLabel, Spinner, cn } from "@/components/ui-kit";

export const Route = createFileRoute("/admin")({
  head: () => ({
    meta: [
      { title: "Admin · Courselab Timetable Studio" },
      {
        name: "description",
        content: "Generate, validate, publish and manage academic timetables.",
      },
      { property: "og:title", content: "Admin · Courselab Timetable Studio" },
      { property: "og:description", content: "Timetable generation and publishing workspace." },
    ],
  }),
  component: AdminLayout,
});

const NAV = [
  { to: "/admin", label: "Generate", exact: true },
  { to: "/admin/draft", label: "Draft & Validation" },
  { to: "/admin/versions", label: "Versions" },
  { to: "/admin/catalog", label: "Catalog" },
  { to: "/admin/busy-slots", label: "Busy Slots" },
  { to: "/admin/users", label: "Users" },
  { to: "/admin/chat", label: "Chat console" },
  { to: "/admin/settings", label: "Settings" },
] as const;

function AdminLayout() {
  const { ready, user, role, signOut } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    if (ready && !user) navigate({ to: "/login", replace: true });
  }, [ready, user, navigate]);

  const draft = useQuery({ queryKey: ["draft"], queryFn: getDraft, enabled: !!user, retry: false });
  const status = useQuery({
    queryKey: ["health"],
    queryFn: health,
    enabled: !!user,
    retry: false,
    refetchInterval: 60_000,
  });

  if (!ready || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-paper">
        <Spinner label="Checking your session…" />
      </div>
    );
  }

  const violations = draft.data?.violation_count ?? 0;

  return (
    <div className="min-h-screen bg-paper text-ink">
      <header className="border-b border-line bg-card/80">
        <div className="mx-auto flex h-14 max-w-[1440px] items-center justify-between px-5">
          <div className="flex items-center gap-3">
            <div className="grid size-8 place-items-center rounded-md bg-ink font-display text-sm font-semibold text-paper">
              CT
            </div>
            <div className="leading-none">
              <div className="font-display text-[15px] font-semibold tracking-tight">Courselab</div>
              <MonoLabel>Timetable Studio</MonoLabel>
            </div>
          </div>
          <nav className="hidden items-center gap-1 md:flex">
            <Link
              to="/"
              className="rounded-md px-3 py-1.5 text-sm font-medium text-ink-soft hover:bg-ink/5"
            >
              Public
            </Link>
            <span className="rounded-md bg-accent-soft px-3 py-1.5 text-sm font-medium text-accent">
              Admin
            </span>
          </nav>
          <div className="flex items-center gap-3">
            <div className="hidden items-center gap-2 rounded-full bg-ink/5 px-3 py-1.5 sm:flex">
              <span
                className={cn(
                  "size-1.5 rounded-full",
                  status.isError ? "bg-violation" : "bg-accent",
                )}
              />
              <span className="font-mono text-[11px] text-ink-soft">
                {status.isError ? "backend offline" : "backend online"}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <div className="grid size-8 place-items-center rounded-full bg-accent font-display text-xs font-semibold text-paper">
                {(user.full_name || user.email).slice(0, 2).toUpperCase()}
              </div>
              <span className="hidden text-sm font-medium sm:block">
                {user.full_name || user.email}
              </span>
              <Button
                size="sm"
                variant="ghost"
                onClick={async () => {
                  await signOut();
                  navigate({ to: "/login", replace: true });
                }}
              >
                Sign out
              </Button>
            </div>
          </div>
        </div>
      </header>

      <div className="mx-auto flex max-w-[1440px]">
        <aside className="hidden w-56 shrink-0 border-r border-line bg-card/60 lg:block">
          <div className="sticky top-0 p-4">
            <div className="mb-4 rounded-lg bg-ink p-3 text-paper">
              <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-paper/50">
                Current draft
              </div>
              <div className="mt-1 font-display text-sm font-semibold">
                {draft.data ? `Version ${draft.data.id}` : "None yet"}
              </div>
              <div className="mt-3 flex items-center gap-2">
                <span className="rounded-sm bg-paper/15 px-1.5 py-0.5 font-mono text-[10px]">
                  {draft.data?.status || "—"}
                </span>
                <span className="rounded-sm bg-paper/15 px-1.5 py-0.5 font-mono text-[10px]">
                  {role || "USER"}
                </span>
              </div>
            </div>
            <MonoLabel className="mb-2">Workspace</MonoLabel>
            <nav className="space-y-0.5">
              {NAV.map((item) => (
                <Link
                  key={item.to}
                  to={item.to}
                  activeOptions={{ exact: "exact" in item ? item.exact : false }}
                  className="flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium text-ink-soft hover:bg-ink/5 data-[status=active]:bg-accent-soft data-[status=active]:font-semibold data-[status=active]:text-accent"
                >
                  <span className="size-1.5 rounded-full bg-line" />
                  {item.label}
                </Link>
              ))}
            </nav>
            <div className="mt-5 border-t border-line pt-4">
              <div
                className={cn(
                  "flex items-center gap-2 rounded-md px-3 py-2",
                  violations ? "bg-violation-soft" : "bg-accent-soft",
                )}
              >
                <span
                  className={cn(
                    "size-1.5 rounded-full",
                    violations ? "bg-violation" : "bg-accent",
                  )}
                />
                <span
                  className={cn(
                    "text-xs font-medium",
                    violations ? "text-violation" : "text-accent",
                  )}
                >
                  {violations ? `${violations} open violations` : "No open violations"}
                </span>
              </div>
            </div>
          </div>
        </aside>

        <main className="min-w-0 flex-1">
          <div className="px-5 py-6">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
