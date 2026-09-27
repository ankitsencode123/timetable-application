import { createFileRoute } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { adminChat, executeActions, parseActions, smartSchedule } from "@/lib/api";
import {
  Button,
  Card,
  Field,
  Input,
  MonoLabel,
  Notice,
  Select,
  Spinner,
  Textarea,
  errorText,
} from "@/components/ui-kit";

export const Route = createFileRoute("/admin/chat")({
  component: ChatPage,
});

type ParsedAction = Record<string, unknown>;

function ChatPage() {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [interpretation, setInterpretation] = useState<string | null>(null);
  const [actions, setActions] = useState<ParsedAction[] | null>(null);
  const [reply, setReply] = useState<string | null>(null);

  const [smart, setSmart] = useState({
    program: "",
    semester: "",
    subject_code: "",
    subject_name: "",
    teacher: "",
    entry_type: "Theory",
    room: "",
    preferred_day: "",
  });
  const [smartResult, setSmartResult] = useState<Record<string, unknown> | null>(null);

  function invalidate() {
    qc.invalidateQueries({ queryKey: ["draft"] });
    qc.invalidateQueries({ queryKey: ["versions"] });
  }

  async function doParse() {
    setBusy(true);
    setReply(null);
    try {
      const res = await parseActions(text);
      setActions((res.parsed_actions as ParsedAction[]) ?? []);
      setInterpretation(res.interpretation ?? null);
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function doExecute() {
    if (!actions?.length) return;
    setBusy(true);
    try {
      const res = await executeActions(actions);
      toast.success(
        (res as { message?: string }).message || "Changes applied to a new version",
      );
      setActions(null);
      setInterpretation(null);
      setText("");
      invalidate();
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function doAsk() {
    setBusy(true);
    setActions(null);
    try {
      const res = await adminChat({ message: text });
      setReply(res.message || JSON.stringify(res));
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function doSmart(e: React.FormEvent, auto: boolean) {
    e.preventDefault();
    setBusy(true);
    try {
      const res = await smartSchedule({
        ...smart,
        semester: smart.semester,
        preferred_day: smart.preferred_day || null,
        auto_execute: auto,
      });
      setSmartResult(res as Record<string, unknown>);
      if (auto) invalidate();
      toast.success((res as { message?: string }).message || "Slot proposal ready");
    } catch (err) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <MonoLabel>Chat console</MonoLabel>
        <h1 className="mt-1 font-display text-2xl font-semibold tracking-tight sm:text-3xl">
          Change the timetable in plain English
        </h1>
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1fr_380px]">
        <div className="space-y-5">
          <Card title="Instruction">
            <Textarea
              rows={5}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Move BCT 4th semester Data Structures lab from Tuesday 10:15 to Thursday 13:00 in Lab 2"
            />
            <div className="mt-3 flex flex-wrap gap-2">
              <Button variant="primary" onClick={doParse} disabled={busy || !text.trim()}>
                Preview changes
              </Button>
              <Button onClick={doAsk} disabled={busy || !text.trim()}>
                Just ask a question
              </Button>
            </div>
            {busy && <div className="mt-3"><Spinner /></div>}
          </Card>

          {interpretation && (
            <Notice tone="info">{interpretation}</Notice>
          )}

          {actions && (
            <Card
              title={`Proposed changes (${actions.length})`}
              action={
                <div className="flex gap-2">
                  <Button size="sm" variant="ghost" onClick={() => setActions(null)}>
                    Discard
                  </Button>
                  <Button size="sm" variant="primary" onClick={doExecute} disabled={busy || !actions.length}>
                    Apply
                  </Button>
                </div>
              }
            >
              {actions.length === 0 ? (
                <p className="text-xs text-ink-soft">
                  Nothing actionable was found in that instruction.
                </p>
              ) : (
                <div className="space-y-2">
                  {actions.map((a, i) => (
                    <pre
                      key={i}
                      className="overflow-x-auto rounded-lg bg-ink/[0.04] p-3 font-mono text-[11px] leading-relaxed text-ink"
                    >
                      {JSON.stringify(a, null, 2)}
                    </pre>
                  ))}
                </div>
              )}
            </Card>
          )}

          {reply && (
            <Card title="Answer">
              <p className="whitespace-pre-wrap text-sm leading-relaxed">{reply}</p>
            </Card>
          )}
        </div>

        <Card title="Smart schedule">
          <form className="space-y-3" onSubmit={(e) => doSmart(e, false)}>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Programme">
                <Input
                  required
                  value={smart.program}
                  onChange={(e) => setSmart({ ...smart, program: e.target.value })}
                />
              </Field>
              <Field label="Semester">
                <Input
                  required
                  value={smart.semester}
                  onChange={(e) => setSmart({ ...smart, semester: e.target.value })}
                />
              </Field>
              <Field label="Subject code">
                <Input
                  required
                  value={smart.subject_code}
                  onChange={(e) => setSmart({ ...smart, subject_code: e.target.value })}
                />
              </Field>
              <Field label="Subject name">
                <Input
                  required
                  value={smart.subject_name}
                  onChange={(e) => setSmart({ ...smart, subject_name: e.target.value })}
                />
              </Field>
              <Field label="Teacher">
                <Input
                  required
                  value={smart.teacher}
                  onChange={(e) => setSmart({ ...smart, teacher: e.target.value })}
                />
              </Field>
              <Field label="Type">
                <Select
                  value={smart.entry_type}
                  onChange={(e) => setSmart({ ...smart, entry_type: e.target.value })}
                >
                  <option>Theory</option>
                  <option>Lab</option>
                </Select>
              </Field>
              <Field label="Room">
                <Input
                  value={smart.room}
                  onChange={(e) => setSmart({ ...smart, room: e.target.value })}
                />
              </Field>
              <Field label="Preferred day">
                <Input
                  value={smart.preferred_day}
                  onChange={(e) => setSmart({ ...smart, preferred_day: e.target.value })}
                />
              </Field>
            </div>
            <div className="flex gap-2">
              <Button size="sm" type="submit" disabled={busy}>
                Find a slot
              </Button>
              <Button
                size="sm"
                variant="primary"
                disabled={busy}
                onClick={(e) => doSmart(e, true)}
              >
                Find &amp; apply
              </Button>
            </div>
          </form>
          {smartResult && (
            <pre className="mt-3 overflow-x-auto rounded-lg bg-ink/[0.04] p-3 font-mono text-[11px] leading-relaxed">
              {JSON.stringify(smartResult, null, 2)}
            </pre>
          )}
        </Card>
      </div>
    </div>
  );
}
