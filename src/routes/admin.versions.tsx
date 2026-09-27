import { createFileRoute } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import {
  getVersion,
  listVersions,
  publishVersion,
  rollbackVersion,
  unpublishVersion,
} from "@/lib/api";
import { TimetableGrid } from "@/components/TimetableGrid";
import {
  Button,
  Card,
  MonoLabel,
  Notice,
  Spinner,
  StatusPill,
  errorText,
} from "@/components/ui-kit";

export const Route = createFileRoute("/admin/versions")({
  component: VersionsPage,
});

function VersionsPage() {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  const versions = useQuery({ queryKey: ["versions"], queryFn: listVersions });
  const detail = useQuery({
    queryKey: ["version", selected],
    queryFn: () => getVersion(selected as number),
    enabled: typeof selected === "number",
  });

  async function act(label: string, fn: () => Promise<unknown>) {
    setBusy(true);
    try {
      await fn();
      toast.success(`${label} done`);
      qc.invalidateQueries({ queryKey: ["versions"] });
      qc.invalidateQueries({ queryKey: ["draft"] });
      if (selected) qc.invalidateQueries({ queryKey: ["version", selected] });
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <MonoLabel>Versions</MonoLabel>
        <h1 className="mt-1 font-display text-2xl font-semibold tracking-tight sm:text-3xl">
          Timetable history
        </h1>
      </div>

      {versions.isLoading && <Spinner />}
      {versions.error && <Notice tone="error">{errorText(versions.error)}</Notice>}

      <Card title="All versions" bodyClassName="p-0">
        <div className="divide-y divide-line">
          {(versions.data ?? []).map((v) => (
            <div key={v.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
              <span className="font-mono text-[11px] text-ink-soft">v{v.id}</span>
              <span className="text-sm font-medium">{v.change_summary || "No summary"}</span>
              <StatusPill status={v.status} />
              {typeof v.violation_count === "number" && v.violation_count > 0 && (
                <span className="rounded-md bg-violation-soft px-2 py-0.5 font-mono text-[10px] text-violation">
                  {v.violation_count} violations
                </span>
              )}
              <span className="font-mono text-[10px] text-ink-soft">
                {v.created_at ? new Date(v.created_at).toLocaleString() : ""}
              </span>
              <div className="ml-auto flex items-center gap-2">
                <Button size="sm" onClick={() => setSelected(v.id)}>
                  View
                </Button>
                {v.status?.toUpperCase() !== "PUBLISHED" ? (
                  <Button
                    size="sm"
                    variant="primary"
                    disabled={busy}
                    onClick={() => act("Publish", () => publishVersion(v.id))}
                  >
                    Publish
                  </Button>
                ) : (
                  <Button
                    size="sm"
                    disabled={busy}
                    onClick={() => act("Unpublish", () => unpublishVersion(v.id))}
                  >
                    Unpublish
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="danger"
                  disabled={busy}
                  onClick={() => act("Rollback", () => rollbackVersion(v.id))}
                >
                  Rollback
                </Button>
              </div>
            </div>
          ))}
          {versions.data?.length === 0 && (
            <p className="px-4 py-8 text-center text-xs text-ink-soft">No versions yet.</p>
          )}
        </div>
      </Card>

      {selected && (
        <Card
          title={`Version ${selected}`}
          action={
            <Button size="sm" variant="ghost" onClick={() => setSelected(null)}>
              Close
            </Button>
          }
          bodyClassName="p-0"
        >
          {detail.isLoading ? (
            <div className="p-6">
              <Spinner />
            </div>
          ) : (
            <TimetableGrid entries={detail.data?.entries ?? []} />
          )}
        </Card>
      )}
    </div>
  );
}
