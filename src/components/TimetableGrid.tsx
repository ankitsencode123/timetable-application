import { useMemo } from "react";
import type { Entry } from "@/lib/api";
import { cn, EmptyState } from "./ui-kit";

const DAY_ORDER = [
  "monday",
  "tuesday",
  "wednesday",
  "thursday",
  "friday",
  "saturday",
  "sunday",
];

function dayRank(day: string) {
  const i = DAY_ORDER.findIndex((d) => d.startsWith(day.trim().toLowerCase().slice(0, 3)));
  return i === -1 ? 99 : i;
}

function label(day: string) {
  const d = day.trim();
  return d.length > 3 ? d.slice(0, 3).toUpperCase() : d.toUpperCase();
}

export function TimetableGrid({
  entries,
  conflictKeys,
}: {
  entries: Entry[];
  conflictKeys?: Set<string>;
}) {
  const { days, slots, cells } = useMemo(() => {
    const days = Array.from(new Set(entries.map((e) => e.day).filter(Boolean))).sort(
      (a, b) => dayRank(a) - dayRank(b),
    );
    const slots = Array.from(
      new Set(entries.map((e) => `${e.start}–${e.end}`).filter((s) => s !== "–")),
    ).sort();
    const cells = new Map<string, Entry[]>();
    for (const e of entries) {
      const key = `${e.day}|${e.start}–${e.end}`;
      cells.set(key, [...(cells.get(key) || []), e]);
    }
    return { days, slots, cells };
  }, [entries]);

  if (!entries.length) {
    return <EmptyState title="No classes to show" hint="Nothing matches the current filters." />;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left">
        <thead>
          <tr className="border-b border-line">
            <th className="w-24 bg-paper px-3 py-2 font-mono text-[10px] uppercase tracking-[0.15em] text-ink-soft">
              Time
            </th>
            {days.map((d) => (
              <th
                key={d}
                className="bg-paper px-3 py-2 font-mono text-[10px] uppercase tracking-[0.15em] text-ink-soft"
              >
                {label(d)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {slots.map((slot) => (
            <tr key={slot} className="hover:bg-ink/[0.02]">
              <td className="px-3 py-3 align-top font-mono text-[11px] text-ink-soft">{slot}</td>
              {days.map((d) => {
                const items = cells.get(`${d}|${slot}`) || [];
                return (
                  <td key={d} className="min-w-44 space-y-2 px-2 py-2 align-top">
                    {items.map((e, i) => {
                      const conflict =
                        items.length > 1 ||
                        conflictKeys?.has(`${e.day}|${e.start}|${e.teacher}`) ||
                        conflictKeys?.has(`${e.day}|${e.start}|${e.room}`);
                      return (
                        <div
                          key={`${e.subject_code}-${i}`}
                          className={cn(
                            "rounded-md p-2 ring-1",
                            conflict
                              ? "bg-violation-soft ring-violation/25"
                              : e.type?.toLowerCase().includes("lab")
                                ? "bg-amber-soft ring-amber/20"
                                : "bg-accent-soft ring-accent/20",
                          )}
                        >
                          <div
                            className={cn(
                              "font-display text-sm font-semibold",
                              conflict
                                ? "text-violation"
                                : e.type?.toLowerCase().includes("lab")
                                  ? "text-amber"
                                  : "text-accent",
                            )}
                          >
                            {e.subject_name || e.subject_code}
                          </div>
                          <div className="mt-0.5 text-xs text-ink-soft">
                            {[e.teacher, e.room].filter(Boolean).join(" · ")}
                          </div>
                          <div
                            className={cn(
                              "mt-1 font-mono text-[10px]",
                              conflict ? "text-violation" : "text-ink-soft",
                            )}
                          >
                            {e.start}–{e.end}
                            {e.subject_code ? ` · ${e.subject_code}` : ""}
                            {e.program ? ` · ${e.program} S${e.semester}` : ""}
                            {conflict ? " · CONFLICT" : ""}
                          </div>
                        </div>
                      );
                    })}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
