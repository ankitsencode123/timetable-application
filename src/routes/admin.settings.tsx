import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { changePassword, getApiBase, health, setApiBase } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import {
  Button,
  Card,
  Field,
  Input,
  MonoLabel,
  Notice,
  errorText,
} from "@/components/ui-kit";

export const Route = createFileRoute("/admin/settings")({
  component: SettingsPage,
});

function SettingsPage() {
  const { user } = useAuth();
  const [base, setBase] = useState(getApiBase());
  const [pw, setPw] = useState({ current_password: "", new_password: "" });
  const status = useQuery({ queryKey: ["health"], queryFn: health, retry: false });

  return (
    <div className="space-y-5">
      <div>
        <MonoLabel>Settings</MonoLabel>
        <h1 className="mt-1 font-display text-2xl font-semibold tracking-tight sm:text-3xl">
          Connection &amp; account
        </h1>
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <Card title="Backend connection">
          <Field label="API base URL">
            <Input value={base} onChange={(e) => setBase(e.target.value)} />
          </Field>
          <div className="mt-3 flex items-center gap-2">
            <Button
              variant="primary"
              size="sm"
              onClick={() => {
                setApiBase(base.trim().replace(/\/$/, ""));
                toast.success("Backend URL saved");
                status.refetch();
              }}
            >
              Save
            </Button>
            <Button size="sm" onClick={() => status.refetch()}>
              Test connection
            </Button>
          </div>
          <div className="mt-3">
            {status.isFetching ? (
              <Notice tone="info">Checking…</Notice>
            ) : status.error ? (
              <Notice tone="error">{errorText(status.error)}</Notice>
            ) : (
              <Notice tone="ok">Backend reachable.</Notice>
            )}
          </div>
        </Card>

        <Card title="Your account">
          <dl className="mb-4 space-y-1 text-sm">
            <div className="flex justify-between">
              <dt className="text-ink-soft">Name</dt>
              <dd className="font-medium">{user?.full_name || "—"}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-ink-soft">Email</dt>
              <dd className="font-mono text-[12px]">{user?.email}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-ink-soft">Role</dt>
              <dd className="font-mono text-[12px]">{user?.role}</dd>
            </div>
          </dl>
          <form
            className="space-y-3 border-t border-line pt-4"
            onSubmit={async (e) => {
              e.preventDefault();
              try {
                await changePassword(pw);
                toast.success("Password changed");
                setPw({ current_password: "", new_password: "" });
              } catch (err) {
                toast.error(errorText(err));
              }
            }}
          >
            <MonoLabel>Change password</MonoLabel>
            <Field label="Current password">
              <Input
                required
                type="password"
                value={pw.current_password}
                onChange={(e) => setPw({ ...pw, current_password: e.target.value })}
              />
            </Field>
            <Field label="New password">
              <Input
                required
                type="password"
                value={pw.new_password}
                onChange={(e) => setPw({ ...pw, new_password: e.target.value })}
              />
            </Field>
            <Button variant="primary" size="sm" type="submit">
              Update password
            </Button>
          </form>
        </Card>
      </div>
    </div>
  );
}
