import { createFileRoute } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { createBusySlot, deleteBusySlot, listBusySlots, listTeachers } from "@/lib/api";
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

export const Route = createFileRoute("/admin/busy-slots")({
  component: BusySlotsPage,
});

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

function BusySlotsPage() {
  const qc = useQueryClient();
  const slots = useQuery({ queryKey: ["busy-slots"], queryFn: listBusySlots });
  const teachers = useQuery({ queryKey: ["teachers"], queryFn: listTeachers });
  const [form, setForm] = useState({
    teacher_short_name: "",
    scope: "WEEKLY",
    day_of_week: "Monday",
    specific_date: "",
    reason: "",
  });
  const [message, setMessage] = useState<string | null>(null);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    try {
      const res = await createBusySlot({
        teacher_short_name: form.teacher_short_name,
        scope: form.scope,
        day_of_week: form.scope === "WEEKLY" ? form.day_of_week : null,
        specific_date: form.scope === "WEEKLY" ? null : form.specific_date || null,
        reason: form.reason || null,
      });
      setMessage(res.message || "Busy slot saved.");
      toast.success("Busy slot saved");
      qc.invalidateQueries({ queryKey: ["busy-slots"] });
      qc.invalidateQueries({ queryKey: ["draft"] });
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <MonoLabel>Busy slots</MonoLabel>
        <h1 className="mt-1 font-display text-2xl font-semibold tracking-tight sm:text-3xl">
          Teacher unavailability
        </h1>
      </div>

      {message && <Notice tone="ok">{message}</Notice>}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1fr_360px]">
        <Card title="Recorded slots" bodyClassName="p-0">
          {slots.isLoading && (
            <div className="p-4">
              <Spinner />
            </div>
          )}
          {slots.error && (
            <div className="p-4">
              <Notice tone="error">{errorText(slots.error)}</Notice>
            </div>
          )}
          <div className="divide-y divide-line">
            {(slots.data ?? []).map((s) => (
              <div key={s.id} className="flex flex-wrap items-center gap-3 px-4 py-3 text-sm">
                <span className="font-mono text-[11px] text-ink-soft">{s.teacher_short_name}</span>
                <span className="rounded-full bg-ink/5 px-2 py-0.5 font-mono text-[10px] text-ink-soft">
                  {s.scope}
                </span>
                <span className="font-medium">{s.day_of_week || s.specific_date}</span>
                {s.reason && <span className="text-xs text-ink-soft">{s.reason}</span>}
                <Button
                  size="sm"
                  variant="danger"
                  className="ml-auto"
                  onClick={async () => {
                    try {
                      await deleteBusySlot(s.id);
                      toast.success("Slot removed");
                      qc.invalidateQueries({ queryKey: ["busy-slots"] });
                    } catch (err) {
                      toast.error(errorText(err));
                    }
                  }}
                >
                  Remove
                </Button>
              </div>
            ))}
            {slots.data?.length === 0 && (
              <p className="px-4 py-8 text-center text-xs text-ink-soft">
                No unavailability recorded.
              </p>
            )}
          </div>
        </Card>

        <Card title="Mark unavailable">
          <form onSubmit={add} className="space-y-3">
            <Field label="Teacher">
              <Select
                required
                value={form.teacher_short_name}
                onChange={(e) => setForm({ ...form, teacher_short_name: e.target.value })}
              >
                <option value="">Select…</option>
                {(teachers.data ?? []).map((t) => (
                  <option key={t.id} value={t.short_name}>
                    {t.short_name} — {t.full_name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Scope">
              <Select value={form.scope} onChange={(e) => setForm({ ...form, scope: e.target.value })}>
                <option value="WEEKLY">Every week</option>
                <option value="DATE">Specific date</option>
              </Select>
            </Field>
            {form.scope === "WEEKLY" ? (
              <Field label="Day">
                <Select
                  value={form.day_of_week}
                  onChange={(e) => setForm({ ...form, day_of_week: e.target.value })}
                >
                  {DAYS.map((d) => (
                    <option key={d}>{d}</option>
                  ))}
                </Select>
              </Field>
            ) : (
              <Field label="Date">
                <Input
                  type="date"
                  value={form.specific_date}
                  onChange={(e) => setForm({ ...form, specific_date: e.target.value })}
                />
              </Field>
            )}
            <Field label="Reason">
              <Input value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} />
            </Field>
            <Button variant="primary" size="sm" type="submit">
              Save slot
            </Button>
          </form>
        </Card>
      </div>
    </div>
  );
}
