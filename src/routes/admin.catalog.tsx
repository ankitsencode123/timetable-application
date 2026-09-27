import { createFileRoute } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import {
  createProgram,
  createSubject,
  createTeacher,
  deleteSubject,
  listPrograms,
  listSubjects,
  listTeachers,
  updateTeacher,
} from "@/lib/api";
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

export const Route = createFileRoute("/admin/catalog")({
  component: CatalogPage,
});

function CatalogPage() {
  const qc = useQueryClient();
  const teachers = useQuery({ queryKey: ["teachers"], queryFn: listTeachers });
  const subjects = useQuery({ queryKey: ["subjects"], queryFn: listSubjects });
  const programs = useQuery({ queryKey: ["programs"], queryFn: listPrograms });

  const [teacherForm, setTeacherForm] = useState({
    short_name: "",
    full_name: "",
    subjects_csv: "",
    is_internal: "true",
    email: "",
    password: "",
  });
  const [subjectForm, setSubjectForm] = useState({
    code: "",
    name: "",
    program: "",
    semester: "",
    entry_type: "Theory",
    weekly_hours: "3",
  });
  const [programForm, setProgramForm] = useState({
    name: "",
    semesters_count: "8",
    description: "",
  });

  async function run(label: string, fn: () => Promise<unknown>, keys: string[]) {
    try {
      await fn();
      toast.success(label);
      keys.forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
    } catch (err) {
      toast.error(errorText(err));
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <MonoLabel>Catalog</MonoLabel>
        <h1 className="mt-1 font-display text-2xl font-semibold tracking-tight sm:text-3xl">
          Teachers, subjects &amp; programmes
        </h1>
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <Card title="Teachers">
          {teachers.isLoading && <Spinner />}
          {teachers.error && <Notice tone="error">{errorText(teachers.error)}</Notice>}
          <div className="divide-y divide-line">
            {(teachers.data ?? []).map((t) => (
              <div key={t.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
                <span className="font-mono text-[11px] text-ink-soft">{t.short_name}</span>
                <span className="font-medium">{t.full_name}</span>
                <span className="text-xs text-ink-soft">{t.subjects_csv}</span>
                <span className="ml-auto flex items-center gap-2">
                  <span className="rounded-full bg-ink/5 px-2 py-0.5 font-mono text-[10px] text-ink-soft">
                    {t.is_internal ? "internal" : "external"}
                  </span>
                  <Button
                    size="sm"
                    onClick={() =>
                      run(
                        "Teacher updated",
                        () => updateTeacher(t.id, { is_internal: !t.is_internal }),
                        ["teachers"],
                      )
                    }
                  >
                    Toggle
                  </Button>
                </span>
              </div>
            ))}
          </div>
          <form
            className="mt-4 space-y-3 border-t border-line pt-4"
            onSubmit={(e) => {
              e.preventDefault();
              run(
                "Teacher created",
                () =>
                  createTeacher({
                    ...teacherForm,
                    is_internal: teacherForm.is_internal === "true",
                    email: teacherForm.email || undefined,
                    password: teacherForm.password || undefined,
                  }),
                ["teachers"],
              );
            }}
          >
            <MonoLabel>Add teacher</MonoLabel>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Short name">
                <Input
                  required
                  value={teacherForm.short_name}
                  onChange={(e) =>
                    setTeacherForm({ ...teacherForm, short_name: e.target.value })
                  }
                />
              </Field>
              <Field label="Full name">
                <Input
                  required
                  value={teacherForm.full_name}
                  onChange={(e) => setTeacherForm({ ...teacherForm, full_name: e.target.value })}
                />
              </Field>
              <Field label="Subjects (csv)">
                <Input
                  value={teacherForm.subjects_csv}
                  onChange={(e) =>
                    setTeacherForm({ ...teacherForm, subjects_csv: e.target.value })
                  }
                />
              </Field>
              <Field label="Internal">
                <Select
                  value={teacherForm.is_internal}
                  onChange={(e) => setTeacherForm({ ...teacherForm, is_internal: e.target.value })}
                >
                  <option value="true">Internal</option>
                  <option value="false">External</option>
                </Select>
              </Field>
              <Field label="Login email (optional)">
                <Input
                  value={teacherForm.email}
                  onChange={(e) => setTeacherForm({ ...teacherForm, email: e.target.value })}
                />
              </Field>
              <Field label="Password (optional)">
                <Input
                  value={teacherForm.password}
                  onChange={(e) => setTeacherForm({ ...teacherForm, password: e.target.value })}
                />
              </Field>
            </div>
            <Button variant="primary" size="sm" type="submit">
              Create teacher
            </Button>
          </form>
        </Card>

        <div className="space-y-5">
          <Card title="Subjects">
            {subjects.isLoading && <Spinner />}
            <div className="divide-y divide-line">
              {(subjects.data ?? []).map((s) => (
                <div key={s.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
                  <span className="font-mono text-[11px] text-ink-soft">{s.code}</span>
                  <span className="font-medium">{s.name}</span>
                  <span className="text-xs text-ink-soft">
                    {s.program} · S{s.semester} · {s.entry_type} · {s.weekly_hours}h
                  </span>
                  <Button
                    size="sm"
                    variant="danger"
                    className="ml-auto"
                    onClick={() =>
                      run("Subject deleted", () => deleteSubject(s.id), ["subjects"])
                    }
                  >
                    Delete
                  </Button>
                </div>
              ))}
            </div>
            <form
              className="mt-4 space-y-3 border-t border-line pt-4"
              onSubmit={(e) => {
                e.preventDefault();
                run(
                  "Subject created",
                  () =>
                    createSubject({
                      ...subjectForm,
                      weekly_hours: Number(subjectForm.weekly_hours) || 0,
                    }),
                  ["subjects"],
                );
              }}
            >
              <MonoLabel>Add subject</MonoLabel>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Code">
                  <Input
                    required
                    value={subjectForm.code}
                    onChange={(e) => setSubjectForm({ ...subjectForm, code: e.target.value })}
                  />
                </Field>
                <Field label="Name">
                  <Input
                    required
                    value={subjectForm.name}
                    onChange={(e) => setSubjectForm({ ...subjectForm, name: e.target.value })}
                  />
                </Field>
                <Field label="Programme">
                  <Input
                    required
                    value={subjectForm.program}
                    onChange={(e) => setSubjectForm({ ...subjectForm, program: e.target.value })}
                  />
                </Field>
                <Field label="Semester">
                  <Input
                    required
                    value={subjectForm.semester}
                    onChange={(e) => setSubjectForm({ ...subjectForm, semester: e.target.value })}
                  />
                </Field>
                <Field label="Type">
                  <Select
                    value={subjectForm.entry_type}
                    onChange={(e) =>
                      setSubjectForm({ ...subjectForm, entry_type: e.target.value })
                    }
                  >
                    <option>Theory</option>
                    <option>Lab</option>
                  </Select>
                </Field>
                <Field label="Weekly hours">
                  <Input
                    type="number"
                    value={subjectForm.weekly_hours}
                    onChange={(e) =>
                      setSubjectForm({ ...subjectForm, weekly_hours: e.target.value })
                    }
                  />
                </Field>
              </div>
              <Button variant="primary" size="sm" type="submit">
                Create subject
              </Button>
            </form>
          </Card>

          <Card title="Programmes">
            {programs.isLoading && <Spinner />}
            <div className="divide-y divide-line">
              {(programs.data ?? []).map((p) => (
                <div key={p.id} className="flex items-center gap-2 py-2 text-sm">
                  <span className="font-medium">{p.name}</span>
                  <span className="text-xs text-ink-soft">{p.semesters_count} semesters</span>
                  <span className="ml-auto font-mono text-[10px] text-ink-soft">
                    {p.is_active ? "active" : "inactive"}
                  </span>
                </div>
              ))}
            </div>
            <form
              className="mt-4 space-y-3 border-t border-line pt-4"
              onSubmit={(e) => {
                e.preventDefault();
                run(
                  "Programme created",
                  () =>
                    createProgram({
                      ...programForm,
                      semesters_count: Number(programForm.semesters_count) || 8,
                    }),
                  ["programs"],
                );
              }}
            >
              <MonoLabel>Add programme</MonoLabel>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Name">
                  <Input
                    required
                    value={programForm.name}
                    onChange={(e) => setProgramForm({ ...programForm, name: e.target.value })}
                  />
                </Field>
                <Field label="Semesters">
                  <Input
                    type="number"
                    value={programForm.semesters_count}
                    onChange={(e) =>
                      setProgramForm({ ...programForm, semesters_count: e.target.value })
                    }
                  />
                </Field>
              </div>
              <Field label="Description">
                <Input
                  value={programForm.description}
                  onChange={(e) => setProgramForm({ ...programForm, description: e.target.value })}
                />
              </Field>
              <Button variant="primary" size="sm" type="submit">
                Create programme
              </Button>
            </form>
          </Card>
        </div>
      </div>
    </div>
  );
}
