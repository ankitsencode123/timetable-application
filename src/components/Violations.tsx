import { cn } from "./ui-kit";

function textOf(v: Record<string, unknown>): { title: string; detail: string; hard: boolean } {
  const pick = (...keys: string[]) => {
    for (const k of keys) {
      const val = v[k];
      if (typeof val === "string" && val.trim()) return val;
      if (typeof val === "number") return String(val);
    }
    return "";
  };
  const title = pick("rule", "code", "id", "type", "title", "constraint") || "Violation";
  const detail =
    pick("message", "detail", "description", "reason", "text") ||
    JSON.stringify(v, null, 0).slice(0, 400);
  const sev = pick("severity", "level", "kind").toLowerCase();
  const hard = sev ? sev.includes("hard") || sev.includes("error") || sev.includes("critical") : true;
  return { title, detail, hard };
}

export function ViolationList({
  items,
  emptyLabel = "No violations reported.",
}: {
  items?: Record<string, unknown>[] | null;
  emptyLabel?: string;
}) {
  if (!items || items.length === 0) {
    return (
      <div className="rounded-md bg-accent-soft px-3 py-2 text-xs text-accent ring-1 ring-accent/20">
        {emptyLabel}
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {items.map((raw, i) => {
        const { title, detail, hard } = textOf(raw);
        return (
          <div
            key={i}
            className={cn(
              "rounded-md p-2.5 ring-1",
              hard ? "bg-violation-soft ring-violation/20" : "bg-amber-soft ring-amber/20",
            )}
          >
            <div className="flex items-center gap-1.5">
              <span
                className={cn("size-1.5 rounded-full", hard ? "bg-violation" : "bg-amber")}
              />
              <span
                className={cn(
                  "text-xs font-semibold",
                  hard ? "text-violation" : "text-amber",
                )}
              >
                {title}
              </span>
            </div>
            <p className="mt-1 break-words text-xs text-ink-soft">{detail}</p>
          </div>
        );
      })}
    </div>
  );
}
