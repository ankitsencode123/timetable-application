import { createFileRoute } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { createUser, listUsers, resetUserPassword, setUserEnabled } from "@/lib/api";
import {
  Button,
  Card,
  Field,
  Input,
  MonoLabel,
  Notice,
  Select,
  Spinner,
  errorText,
} from "@/components/ui-kit";

export const Route = createFileRoute("/admin/users")({
  component: UsersPage,
});

function UsersPage() {
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: listUsers });
  const [form, setForm] = useState({
    email: "",
    full_name: "",
    password: "",
    role: "TEACHER",
  });

  async function run(label: string, fn: () => Promise<unknown>) {
    try {
      await fn();
      toast.success(label);
      qc.invalidateQueries({ queryKey: ["users"] });
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <MonoLabel>Users</MonoLabel>
        <h1 className="mt-1 font-display text-2xl font-semibold tracking-tight sm:text-3xl">
          Accounts &amp; access
        </h1>
      </div>

      {users.error && <Notice tone="error">{errorText(users.error)}</Notice>}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1fr_340px]">
        <Card title="All users" bodyClassName="p-0">
          {users.isLoading && (
            <div className="p-4">
              <Spinner />
            </div>
          )}
          <div className="divide-y divide-line">
            {(users.data ?? []).map((u) => (
              <div key={u.id} className="flex flex-wrap items-center gap-3 px-4 py-3 text-sm">
                <span className="font-medium">{u.full_name || u.email}</span>
                <span className="font-mono text-[11px] text-ink-soft">{u.email}</span>
                <span className="rounded-full bg-accent-soft px-2 py-0.5 font-mono text-[10px] text-accent">
                  {u.role}
                </span>
                <span
                  className={
                    u.is_active
                      ? "font-mono text-[10px] text-accent"
                      : "font-mono text-[10px] text-violation"
                  }
                >
                  {u.is_active ? "active" : "disabled"}
                </span>
                <div className="ml-auto flex gap-2">
                  <Button
                    size="sm"
                    onClick={() =>
                      run(
                        u.is_active ? "User disabled" : "User enabled",
                        () => setUserEnabled(u.id, !u.is_active),
                      )
                    }
                  >
                    {u.is_active ? "Disable" : "Enable"}
                  </Button>
                  <Button
                    size="sm"
                    variant="danger"
                    onClick={() => {
                      const pw = window.prompt(`New password for ${u.email}`);
                      if (pw)
                        run("Password reset", () =>
                          resetUserPassword(u.id, { new_password: pw, password: pw }),
                        );
                    }}
                  >
                    Reset password
                  </Button>
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card title="Create user">
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              run("User created", () => createUser(form));
            }}
          >
            <Field label="Email">
              <Input
                required
                type="email"
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
              />
            </Field>
            <Field label="Full name">
              <Input
                required
                value={form.full_name}
                onChange={(e) => setForm({ ...form, full_name: e.target.value })}
              />
            </Field>
            <Field label="Password">
              <Input
                required
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
              />
            </Field>
            <Field label="Role">
              <Select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
                <option value="ADMIN">Admin</option>
                <option value="TEACHER">Teacher</option>
                <option value="VIEWER">Viewer</option>
              </Select>
            </Field>
            <Button variant="primary" size="sm" type="submit">
              Create user
            </Button>
          </form>
        </Card>
      </div>
    </div>
  );
}
