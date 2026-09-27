import { clsx } from "clsx";
import type { ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "outline" | "ghost" | "danger" | "ink";
  size?: "sm" | "md";
};

export function Button({ variant = "outline", size = "md", className, ...props }: ButtonProps) {
  return (
    <button
      {...props}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-md font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "px-2.5 py-1 text-xs" : "px-4 py-2 text-sm",
        variant === "primary" && "bg-accent text-paper ring-1 ring-accent hover:bg-accent/90",
        variant === "ink" && "bg-ink text-paper hover:bg-ink/85",
        variant === "outline" && "border border-line bg-card text-ink-soft hover:bg-ink/5",
        variant === "ghost" && "text-ink-soft hover:bg-ink/5",
        variant === "danger" &&
          "bg-violation-soft text-violation ring-1 ring-violation/25 hover:bg-violation/15",
        className,
      )}
    />
  );
}

export function Card({
  title,
  action,
  children,
  className,
  bodyClassName,
}: {
  title?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={cn("rounded-xl bg-card ring-1 ring-black/5", className)}>
      {(title || action) && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          {typeof title === "string" ? (
            <h2 className="font-display text-sm font-semibold tracking-tight">{title}</h2>
          ) : (
            title
          )}
          {action}
        </header>
      )}
      <div className={cn("p-4", bodyClassName)}>{children}</div>
    </section>
  );
}

export function MonoLabel({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "font-mono text-[10px] uppercase tracking-[0.18em] text-ink-soft",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block space-y-1.5">
      <MonoLabel>{label}</MonoLabel>
      {children}
      {hint && <p className="text-[11px] text-ink-soft">{hint}</p>}
    </label>
  );
}

const control =
  "w-full rounded-md border border-line bg-card px-2.5 py-1.5 text-sm text-ink outline-none transition-shadow placeholder:text-ink-soft/60 focus:ring-2 focus:ring-accent/30";

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cn(control, className)} />;
}

export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={cn(control, "min-h-24 font-mono text-xs", className)} />;
}

export function Select({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={cn(control, "font-medium", className)} />;
}

export function StatusPill({ status }: { status?: string | null }) {
  const s = (status || "").toUpperCase();
  const tone =
    s === "PUBLISHED"
      ? "bg-accent-soft text-accent ring-accent/20"
      : s === "DRAFT"
        ? "bg-amber-soft text-amber ring-amber/20"
        : "bg-ink/5 text-ink-soft ring-line";
  return (
    <span
      className={cn(
        "rounded-full px-2.5 py-0.5 font-mono text-[10px] font-medium uppercase tracking-[0.15em] ring-1",
        tone,
      )}
    >
      {s || "—"}
    </span>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-ink-soft">
      <span className="size-3 animate-spin rounded-full border-2 border-line border-t-accent" />
      {label || "Loading…"}
    </div>
  );
}

export function Notice({
  tone = "info",
  children,
}: {
  tone?: "info" | "warn" | "error" | "ok";
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        "rounded-md px-3 py-2 text-xs ring-1",
        tone === "error" && "bg-violation-soft text-violation ring-violation/20",
        tone === "warn" && "bg-amber-soft text-amber ring-amber/20",
        tone === "ok" && "bg-accent-soft text-accent ring-accent/20",
        tone === "info" && "bg-ink/5 text-ink-soft ring-line",
      )}
    >
      {children}
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="rounded-md border border-dashed border-line px-4 py-10 text-center">
      <p className="font-display text-sm font-semibold">{title}</p>
      {hint && <p className="mt-1 text-xs text-ink-soft">{hint}</p>}
    </div>
  );
}

export function errorText(error: unknown) {
  return error instanceof Error ? error.message : "Something went wrong.";
}
