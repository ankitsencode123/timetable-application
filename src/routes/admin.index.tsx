import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { generateTimetable } from "@/lib/api";
import type { GenerateResult } from "@/lib/api";
import { TimetableGrid } from "@/components/TimetableGrid";
import { ViolationList } from "@/components/Violations";
import {
  Button,
  Card,
  Field,
  Input,
  MonoLabel,
  Notice,
  Textarea,
  errorText,
} from "@/components/ui-kit";

export const Route = createFileRoute("/admin/")({
  component: GeneratePage,
});

const MARKDOWN_SECTIONS: { key: keyof GenerateResult; label: string }[] = [
  { key: "data_validation_markdown", label: "Data validation" },
  { key: "assumptions_markdown", label: "Assumptions" },
  { key: "btech_semester_markdown", label: "B.Tech semesters" },
  { key: "msc_semester_markdown", label: "M.Sc semesters" },
  { key: "mtech_semester_markdown", label: "M.Tech semesters" },
  { key: "faculty_wise_markdown", label: "Faculty-wise" },
  { key: "room_wise_markdown", label: "Room-wise" },
  { key: "workload_markdown", label: "Workload" },
  { key: "preference_markdown", label: "Preferences" },
  { key: "free_day_markdown", label: "Free days" },
  { key: "validation_report_markdown", label: "Validation report" },
  { key: "change_log_markdown", label: "Change log" },
  { key: "infeasibility_markdown", label: "Infeasibility" },
  { key: "quality_score_markdown", label: "Quality score" },
];

function GeneratePage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [form, setForm] = useState({
    subject_teacher_allocation: "",
    teacher_preferences: "",
    room_information: "",
    extra_notes: "",
    change_summary: "LLM-generated schedule",
  });
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<GenerateResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const set = (k: keyof typeof form) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  async function run(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const data = await generateTimetable(form);
      setResult(data);
      qc.invalidateQueries({ queryKey: ["draft"] });
      qc.invalidateQueries({ queryKey: ["versions"] });
      toast.success(`Draft version ${data.version_id} created`);
    } catch (err) {
      setError(errorText(err));
      toast.error("Generation failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <MonoLabel>Generate</MonoLabel>
          <h1 className="mt-1 font-display text-2xl font-semibold tracking-tight sm:text-3xl">
            New timetable draft
          </h1>
        </div>
        <Button variant="outline" onClick={() => navigate({ to: "/admin/draft" })}>
          Open current draft
        </Button>
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1fr_360px]">
        <Card title="Inputs">
          <form onSubmit={run} className="space-y-4">
            <Field
              label="Subject / teacher allocation"
              hint="One line per subject: code, name, programme, semester, hours, teacher."
            >
              <Textarea
                value={form.subject_teacher_allocation}
                onChange={set("subject_teacher_allocation")}
                rows={8}
              />
            </Field>
            <Field label="Teacher preferences">
              <Textarea
                value={form.teacher_preferences}
                onChange={set("teacher_preferences")}
                rows={5}
              />
            </Field>
            <Field label="Room information">
              <Textarea value={form.room_information} onChange={set("room_information")} rows={4} />
            </Field>
            <Field label="Extra notes">
              <Textarea value={form.extra_notes} onChange={set("extra_notes")} rows={3} />
            </Field>
            <Field label="Change summary">
              <Input value={form.change_summary} onChange={set("change_summary")} />
            </Field>
            <Button variant="primary" type="submit" disabled={busy}>
              {busy ? "Generating… this can take a minute" : "Generate draft"}
            </Button>
            {error && <Notice tone="error">{error}</Notice>}
          </form>
        </Card>

        <div className="space-y-4">
          <Card title="Run summary">
            {!result ? (
              <p className="text-xs text-ink-soft">
                Results appear here after a run: version id, model used, validator attempts and
                violations.
              </p>
            ) : (
              <div className="space-y-2 text-xs">
                <Row k="Version" v={`#${result.version_id}`} />
                <Row k="Status" v={result.status} />
                <Row k="Model" v={result.model_used || "—"} />
                <Row k="Validator attempts" v={String(result.validator_attempts ?? 0)} />
                <Row k="Classes" v={String(result.schedule?.length ?? 0)} />
                {result.warning && <Notice tone="warn">{result.warning}</Notice>}
              </div>
            )}
          </Card>
          {result && (
            <Card title={`Violations (${result.violations?.length ?? 0})`}>
              <ViolationList items={result.violations} />
            </Card>
          )}
          {result && result.schema_errors?.length > 0 && (
            <Card title="Schema errors">
              <ViolationList items={result.schema_errors} />
            </Card>
          )}
        </div>
      </div>

      {result && (
        <Card title="Generated schedule" bodyClassName="p-0">
          <TimetableGrid entries={result.schedule || []} />
        </Card>
      )}

      {result && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {MARKDOWN_SECTIONS.filter((s) => typeof result[s.key] === "string" && result[s.key]).map(
            (s) => (
              <Card key={String(s.key)} title={s.label}>
                <pre className="max-h-80 overflow-auto whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-ink-soft">
                  {String(result[s.key])}
                </pre>
              </Card>
            ),
          )}
        </div>
      )}
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-center justify-between border-b border-line pb-1.5 last:border-0">
      <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-soft">{k}</span>
      <span className="font-medium">{v}</span>
    </div>
  );
}
