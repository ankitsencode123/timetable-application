import { createFileRoute } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import {
  getDraft,
  getVersion,
  publishVersion,
  validateVersion,
} from "@/lib/api";
import { TimetableGrid } from "@/components/TimetableGrid";
import { ViolationList } from "@/components/Violations";
import {
  Button,
  Card,
  MonoLabel,
  Notice,
  Select,
  Spinner,
  StatusPill,
  errorText,
} from "@/components/ui-kit";

export const Route = createFileRoute("/admin/draft")({
  component: DraftPage,
});

function DraftPage() {
  const qc = useQueryClient();
  const [program, setProgram] = useState("");
  const [teacher, setTeacher] = useState("");
  const [violations, setViolations] = useState<Record<string, unknown>[] | null>(null);
  const [busy, setBusy] = useState(false);

  const draft = useQuery({ queryKey: ["draft"], queryFn: getDraft, retry: false });
  const versionId = draft.data?.id;
  const version = useQuery({
    queryKey: ["version", versionId],
    queryFn: () => getVersion(versionId as number),
    enabled: typeof versionId === "number",
  });

  const entries = version.data?.entries ?? [];
  const programs = useMemo(
    () => Array.from(new Set(entries.map((e) => e.program).filter(Boolean))).sort(),
    [entries],
  );
  const teachers = useMemo(
    () => Array.from(new Set(entries.map((e) => e.teacher).filter(Boolean))).sort(),
    [entries],
  );
  const filtered = entries.filter(
    (e) => (!program || e.program === program) && (!teacher || e.teacher === teacher),
  );

  async function runValidation() {
    if (!versionId) return;
    setBusy(true);
    try {
      const res = await validateVersion(versionId);
      setViolations(res.violations || []);
      toast.success(`${res.violation_count ?? res.violations?.length ?? 0} violations found`);
      qc.invalidateQueries({ queryKey: ["draft"] });
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function publish() {
    if (!versionId) return;
    setBusy(true);
    try {
      await publishVersion(versionId);
      toast.success(`Version ${versionId} published`);
      qc.invalidateQueries({ queryKey: ["draft"] });
      qc.invalidateQueries({ queryKey: ["versions"] });
      qc.invalidateQueries({ queryKey: ["version", versionId] });
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  if (draft.isLoading) return <Spinner label="Loading latest version…" />;
  if (draft.error)
    return <Notice tone="error">{errorText(draft.error)}</Notice>;
  if (!draft.data) return <Notice tone="info">No draft yet. Generate one first.</Notice>;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <MonoLabel>Draft &amp; Validation</MonoLabel>
          <h1 className="mt-1 font-display text-2xl font-semibold tracking-tight sm:text-3xl">
            Version {draft.data.id}
            {draft.data.change_summary ? ` — ${draft.data.change_summary}` : ""}
          </h1>
        </div>
        <div className="flex items-center gap-2">
          <StatusPill status={draft.data.status} />
          <Button onClick={runValidation} disabled={busy}>
            Run validation
          </Button>
          <Button variant="primary" onClick={publish} disabled={busy}>
            Publish version
          </Button>
        </div>
      </div>

      <div className="sticky top-0 z-10 -mx-5 border-y border-line bg-paper/90 px-5 py-3 backdrop-blur">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <MonoLabel>Programme</MonoLabel>
            <Select value={program} onChange={(e) => setProgram(e.target.value)}>
              <option value="">All</option>
              {programs.map((p) => (
                <option key={p}>{p}</option>
              ))}
            </Select>
          </div>
          <div className="flex items-center gap-2">
            <MonoLabel>Teacher</MonoLabel>
            <Select value={teacher} onChange={(e) => setTeacher(e.target.value)}>
              <option value="">All teachers</option>
              {teachers.map((t) => (
                <option key={t}>{t}</option>
              ))}
            </Select>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <span className="rounded-md bg-ink/5 px-2.5 py-1.5 font-mono text-[11px] text-ink-soft">
              {filtered.length} entries
            </span>
            <span className="rounded-md bg-violation-soft px-2.5 py-1.5 font-mono text-[11px] text-violation">
              {(violations?.length ?? draft.data.violation_count) || 0} conflicts
            </span>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1fr_320px]">
        <div className="overflow-hidden rounded-xl bg-card ring-1 ring-black/5">
          {version.isLoading ? (
            <div className="p-6">
              <Spinner label="Loading classes…" />
            </div>
          ) : (
            <TimetableGrid entries={filtered} />
          )}
        </div>
        <Card
          title="Validation report"
          action={
            <span className="rounded-full bg-violation-soft px-2 py-0.5 font-mono text-[11px] font-medium text-violation">
              {violations?.length ?? draft.data.violation_count ?? 0}
            </span>
          }
        >
          <ViolationList
            items={violations}
            emptyLabel={
              violations ? "No violations found." : "Run validation to see the H1–H11 report."
            }
          />
        </Card>
      </div>
    </div>
  );
}
