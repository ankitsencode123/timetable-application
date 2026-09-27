import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import {
  publicTimetable,
  publicByProgram,
  publicByTeacher,
  publicMeta,
  publicChat,
} from "@/lib/api";
import type { Entry } from "@/lib/api";
import { TimetableGrid } from "@/components/TimetableGrid";
import {
  Button,
  Card,
  Input,
  MonoLabel,
  Notice,
  Select,
  Spinner,
  errorText,
} from "@/components/ui-kit";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Published timetable · Courselab" },
      {
        name: "description",
        content:
          "Browse the published class timetable by programme, semester or teacher, and ask questions about it.",
      },
      { property: "og:title", content: "Published timetable · Courselab" },
      {
        property: "og:description",
        content: "Browse the published class timetable by programme, semester or teacher.",
      },
    ],
  }),
  component: PublicTimetablePage,
});

function PublicTimetablePage() {
  const [program, setProgram] = useState("");
  const [semester, setSemester] = useState("");
  const [teacher, setTeacher] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);

  const all = useQuery({ queryKey: ["public-timetable"], queryFn: publicTimetable });
  const meta = useQuery({ queryKey: ["public-meta"], queryFn: publicMeta, retry: false });

  const filtered = useQuery({
    queryKey: ["public-filtered", program, semester, teacher],
    queryFn: () =>
      teacher
        ? publicByTeacher(teacher)
        : program && semester
          ? publicByProgram(program, semester)
          : Promise.resolve(null),
    enabled: Boolean(teacher || (program && semester)),
  });

  const entries: Entry[] = filtered.data ?? all.data ?? [];

  const programs = useMemo(
    () => Array.from(new Set((all.data ?? []).map((e) => e.program).filter(Boolean))).sort(),
    [all.data],
  );
  const semesters = useMemo(
    () =>
      Array.from(
        new Set(
          (all.data ?? [])
            .filter((e) => !program || e.program === program)
            .map((e) => e.semester)
            .filter(Boolean),
        ),
      ).sort(),
    [all.data, program],
  );
  const teachers = useMemo(
    () => Array.from(new Set((all.data ?? []).map((e) => e.teacher).filter(Boolean))).sort(),
    [all.data],
  );

  async function ask(e: React.FormEvent) {
    e.preventDefault();
    if (!question.trim()) return;
    setAsking(true);
    setAnswer(null);
    try {
      const reply = await publicChat({ message: question.trim() });
      setAnswer(reply.message);
    } catch (err) {
      setAnswer(errorText(err));
    } finally {
      setAsking(false);
    }
  }

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
          <nav className="flex items-center gap-1">
            <span className="rounded-md bg-accent-soft px-3 py-1.5 text-sm font-medium text-accent">
              Public
            </span>
            <Link
              to="/admin"
              className="rounded-md px-3 py-1.5 text-sm font-medium text-ink-soft hover:bg-ink/5"
            >
              Admin
            </Link>
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-[1440px] px-5 py-6">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <MonoLabel>Published schedule</MonoLabel>
            <h1 className="mt-1 font-display text-2xl font-semibold tracking-tight sm:text-3xl">
              Class timetable
            </h1>
          </div>
          {typeof meta.data === "string" && meta.data && (
            <span className="rounded-md bg-ink/5 px-2.5 py-1.5 font-mono text-[11px] text-ink-soft">
              {meta.data}
            </span>
          )}
        </div>

        <div className="sticky top-0 z-10 -mx-5 mt-5 border-y border-line bg-paper/90 px-5 py-3 backdrop-blur">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2">
              <MonoLabel>Programme</MonoLabel>
              <Select
                value={program}
                onChange={(e) => {
                  setProgram(e.target.value);
                  setTeacher("");
                }}
              >
                <option value="">All</option>
                {programs.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </Select>
            </div>
            <div className="flex items-center gap-2">
              <MonoLabel>Semester</MonoLabel>
              <Select
                value={semester}
                onChange={(e) => {
                  setSemester(e.target.value);
                  setTeacher("");
                }}
              >
                <option value="">All</option>
                {semesters.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </Select>
            </div>
            <div className="flex items-center gap-2">
              <MonoLabel>Teacher</MonoLabel>
              <Select
                value={teacher}
                onChange={(e) => {
                  setTeacher(e.target.value);
                  setProgram("");
                  setSemester("");
                }}
              >
                <option value="">All</option>
                {teachers.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </Select>
            </div>
            <div className="ml-auto flex items-center gap-2">
              <span className="rounded-md bg-ink/5 px-2.5 py-1.5 font-mono text-[11px] text-ink-soft">
                {entries.length} entries
              </span>
              <Button size="sm" onClick={() => window.print()}>
                Print
              </Button>
            </div>
          </div>
        </div>

        <div className="mt-5 grid grid-cols-1 gap-5 xl:grid-cols-[1fr_320px]">
          <div className="overflow-hidden rounded-xl bg-card ring-1 ring-black/5">
            {all.isLoading || filtered.isLoading ? (
              <div className="p-6">
                <Spinner label="Loading published timetable…" />
              </div>
            ) : all.error ? (
              <div className="p-4">
                <Notice tone="error">{errorText(all.error)}</Notice>
              </div>
            ) : (
              <TimetableGrid entries={entries} />
            )}
          </div>

          <Card title="Ask about the timetable">
            <form onSubmit={ask} className="space-y-2">
              <Input
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="When does Dr. Okafor teach on Monday?"
              />
              <Button variant="primary" size="sm" type="submit" disabled={asking}>
                {asking ? "Asking…" : "Ask"}
              </Button>
            </form>
            {answer && (
              <p className="mt-3 whitespace-pre-wrap rounded-md bg-ink/5 p-2.5 text-xs text-ink-soft">
                {answer}
              </p>
            )}
          </Card>
        </div>
      </main>
    </div>
  );
}
