import { useState } from 'react'
import { X, Search, CheckCircle2, AlertCircle, Loader2, Lightbulb, RefreshCw } from 'lucide-react'
import { useWorkspaceStore } from '../../store'
import { executeActions, getVersion } from '../../api'
import type { TimetableEntry, ActionExecuteResponse } from '../../types'
import { DAYS } from '../../types'

interface Props {
  actionType: string
  onClose: () => void
}

const ACTION_LABELS: Record<string, string> = {
  ADD_CLASS: 'Add Class', REMOVE_CLASS: 'Remove Class', CANCEL_CLASS: 'Cancel Class',
  EXTEND_CLASS: 'Extend Class', SHORTEN_CLASS: 'Shorten Class', MOVE_CLASS: 'Move Class',
  SWAP_CLASSES: 'Swap Classes', INTERCHANGE_CLASSES: 'Interchange Classes',
  CHANGE_TEACHER: 'Change Teacher', CHANGE_ROOM: 'Change Room', CHANGE_TIME: 'Change Time',
  CHANGE_DAY: 'Change Day', REPLACE_CLASS: 'Replace Class',
  OPTIMIZE_TIMETABLE: 'Optimize Timetable', VALIDATE_TIMETABLE: 'Validate Timetable',
}

// Human-readable messages for each constraint rule code
const VIOLATION_MESSAGES: Record<string, (v: Record<string, string>) => string> = {
  H1_semester_clash:          () => 'Two classes for the same program/semester overlap in time.',
  H2_teacher_clash:           (v) => `Teacher ${v.teachers ?? v.teacher ?? ''} is double-booked at this time.`,
  H3_room_clash:              (v) => `Room ${v.room ?? ''} is already occupied at this time.`,
  H4_room_not_lab:            (v) => `Room ${v.room ?? ''} is not a lab — practical sessions need a lab room.`,
  H4_unknown_room:            (v) => `Room "${v.room ?? ''}" is not in the authorised room list.`,
  H5_multiple_theory_same_day:(v) => `Teacher ${v.teacher ?? ''} has more than one theory class on ${v.day ?? 'the same day'}.`,
  H6_no_free_day:             (v) => `Teacher ${v.teacher ?? ''} has no free day this week.`,
  H7_unallocated_subject:     (v) => `${v.teacher ?? 'Teacher'} is not authorised to teach "${v.subject_code ?? ''}".`,
  H7_teacher_not_authorised:  (v) => `Teacher is not authorised for this subject.`,
  H8_wrong_weekly_hours:      (v) => `Subject "${v.subject_code ?? ''}" has the wrong number of weekly hours scheduled (expected ${Math.round(Number(v.expected_minutes ?? 0)/60)}h, got ${Math.round(Number(v.actual_minutes ?? 0)/60)}h).`,
  H9_wrong_practical_duration:(v) => `Practical "${v.subject_code ?? ''}" has wrong duration (expected ${Math.round(Number(v.expected_minutes ?? 0)/60)}h, got ${Math.round(Number(v.actual_minutes ?? 0)/60)}h).`,
  H9_saturday_practical:      () => 'Practical sessions are not allowed on Saturdays.',
  H10_missing_subject:        (v) => `Required subject "${v.subject_code ?? ''}" is not scheduled.`,
  H11_wrong_semester_subject: (v) => `Subject "${v.subject_code ?? ''}" is not in the authorised subject list for ${v.program ?? ''} ${v.semester ?? ''}.`,
  start_not_before_end:       () => 'A class has a start time that is not before its end time.',
  schema_invalid_day:         (v) => `"${v.day ?? ''}" is not a valid day.`,
  schema_invalid_program:     (v) => `"${v.program ?? ''}" is not a valid program.`,
}

function violationMessage(v: Record<string, string>): string {
  const fn = VIOLATION_MESSAGES[v.rule]
  if (fn) return fn(v)
  return v.note ?? v.rule   // fall back to note or raw code
}

const ROOMS = ['R#205', 'R#207A', 'R#207B', 'R#208', 'R#209', 'R#303', 'R#403']
const SEMS = ['1st', '2nd', '3rd', '4th', '5th', '6th', '7th', '8th']

function TargetSelector({ entries, onSelect, label = 'Select Class', selected }: {
  entries: TimetableEntry[], onSelect: (e: TimetableEntry) => void, label?: string, selected?: TimetableEntry | null
}) {
  const [q, setQ] = useState('')
  const filtered = entries.filter(e =>
    !q || e.subject_name.toLowerCase().includes(q) || e.teacher.toLowerCase().includes(q) || e.day.toLowerCase().includes(q)
  )
  return (
    <div>
      <div className="input-group" style={{ marginBottom: 6 }}>
        <Search size={14} className="input-icon" />
        <input className="form-control" placeholder={`Search: day, subject, teacher…`} value={q} onChange={e => setQ(e.target.value.toLowerCase())} />
      </div>
      <div style={{ maxHeight: 180, overflowY: 'auto', border: '1px solid var(--clr-border)', borderRadius: 'var(--radius)', background: 'var(--clr-bg-3)' }}>
        {filtered.slice(0, 20).map((e, i) => (
          <div key={i}
            onClick={() => onSelect(e)}
            style={{
              padding: '8px 12px', cursor: 'pointer', borderBottom: '1px solid var(--clr-border-soft)',
              background: selected === e ? 'var(--clr-primary-10)' : undefined,
              fontSize: 'var(--fs-sm)', display: 'flex', gap: 10, alignItems: 'center',
            }}
          >
            <span style={{ minWidth: 70, fontWeight: 600, color: 'var(--clr-text)' }}>{e.day}</span>
            <span style={{ color: 'var(--clr-text-2)' }}>{e.start}–{e.end}</span>
            <span style={{ fontWeight: 500 }}>{e.subject_name}</span>
            <span style={{ color: 'var(--clr-text-3)', marginLeft: 'auto' }}>{e.teacher} · {e.room}</span>
          </div>
        ))}
        {filtered.length === 0 && <div style={{ padding: '12px', color: 'var(--clr-text-3)', textAlign: 'center', fontSize: 'var(--fs-sm)' }}>No classes match</div>}
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="form-group">{children ? <label className="form-label">{label}</label> : null}{children}</div>
}

export default function ActionForm({ actionType, onClose }: Props) {
  const { currentVersionId, entries, setCurrentVersion, catalogTeachers, catalogPrograms, catalogSubjects } = useWorkspaceStore()
  const TEACHERS = Array.from(new Set([...catalogTeachers.map(t => t.short_name), 'SK', 'SKS', 'SC', 'SCh', 'PB', 'PBn', 'RD', 'RM', 'VP']))
  const PROGRAMS = Array.from(new Set([...catalogPrograms.map(p => p.name), 'B.Tech', 'M.Tech', 'M.Sc']))

  const [target, setTarget] = useState<TimetableEntry | null>(null)
  const [targetB, setTargetB] = useState<TimetableEntry | null>(null)
  const [fields, setFields] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<ActionExecuteResponse | null>(null)
  const [step, setStep] = useState<'form' | 'confirm' | 'alternative' | 'done'>('form')
  const [suggestionLoading, setSuggestionLoading] = useState(false)
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [expanded, setExpanded] = useState(false)

  const set = (k: string, v: string) => setFields(f => ({ ...f, [k]: v }))
  const needsTarget = !['ADD_CLASS', 'VALIDATE_TIMETABLE', 'OPTIMIZE_TIMETABLE'].includes(actionType)
  const needsTargetB = ['SWAP_CLASSES', 'INTERCHANGE_CLASSES'].includes(actionType)

  function buildPayload() {
    const base: Record<string, unknown> = { action: actionType }
    if (needsTarget && target) {
      base.target = {
        day: target.day, start_time: target.start, end_time: target.end,
        program: target.program, semester: target.semester,
        subject_code: target.subject_code, teacher: target.teacher,
      }
    }
    if (needsTargetB && targetB) {
      base.target_b = {
        day: targetB.day, start_time: targetB.start,
        program: targetB.program, semester: targetB.semester,
        subject_code: targetB.subject_code,
      }
      base.target_a = base.target; delete base.target
    }
    // Merge extra fields
    if (fields.new_teacher) base.new_teacher = fields.new_teacher
    if (fields.new_room) base.new_room = fields.new_room
    if (fields.new_day) base.new_day = fields.new_day
    if (fields.new_start) base.new_start_time = fields.new_start
    if (fields.new_end)   base.new_end_time   = fields.new_end

    if (actionType === 'ADD_CLASS' || actionType === 'REPLACE_CLASS') {
      base.spec = {
        program: fields.program, semester: fields.semester, day: fields.day,
        start_time: fields.start_time, end_time: fields.end_time,
        subject_code: fields.subject_code, subject_name: fields.subject_name,
        teacher: fields.teacher, entry_type: fields.entry_type || 'Theory', room: fields.room,
      }
    }
    if (actionType === 'VALIDATE_TIMETABLE') base.version_id = currentVersionId
    return base
  }

  async function execute() {
    setLoading(true)
    try {
      const res = await executeActions([buildPayload()], currentVersionId, false, true)
      setResult(res)
      if (res.success) {
        setStep('done')
        if (res.new_version_id) {
          const v = await getVersion(res.new_version_id)
          setCurrentVersion(v.id, v.entries)
        }
      } else {
        // Always show the alternative step on failure so user sees context + suggestions
        setStep('alternative')
        setSuggestionLoading(true)
        try {
          const resSugg = await executeActions([buildPayload()], currentVersionId, false, false)
          setResult(resSugg)
        } catch (e2) {
          console.error(e2)
        } finally {
          setSuggestionLoading(false)
        }
      }
    } catch (e: unknown) {
      setResult({ success: false, results: [{ action_type: actionType, success: false, error: (e as Error).message, change_log: '' }], new_version_id: null, violations: [], schema_errors: [], change_log: '', partial_applied: false })
      setStep('done')
    } finally {
      setLoading(false)
    }
  }

  async function applyAlternative() {
    const failedActionResult = result?.results.find(r => !r.success)
    const rich = failedActionResult?.suggestions?.rich_suggestions || []
    const proposed = rich[selectedIndex]?.action
    if (!proposed) return
    setLoading(true)
    try {
      const res = await executeActions([proposed], currentVersionId)
      setResult(res)
      if (res.success && res.new_version_id) {
        setStep('done')
        const v = await getVersion(res.new_version_id)
        setCurrentVersion(v.id, v.entries)
      } else {
        // The server returns freshly recalculated suggestions when the
        // timetable changed after the original suggestion was generated.
        setStep('alternative')
      }
    } catch (e: unknown) {
      setResult({ success: false, results: [{ action_type: actionType, success: false, error: (e as Error).message, change_log: '' }], new_version_id: null, violations: [], schema_errors: [], change_log: '', partial_applied: false })
      setStep('done')
    } finally {
      setLoading(false)
    }
  }

  const label = ACTION_LABELS[actionType] ?? actionType

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ maxWidth: 560 }}>
        <div className="modal-header">
          <h2 className="modal-title">{label}</h2>
          <button className="btn-icon" onClick={onClose}><X size={18} /></button>
        </div>

        {step === 'form' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>
            {/* Target selection */}
            {needsTarget && (
              <div>
                <label className="form-label" style={{ marginBottom: 6, display: 'block' }}>Select Class to {label.split(' ')[0]}</label>
                <TargetSelector entries={entries} onSelect={(t) => {
                  setTarget(t)
                  if (t && actionType === 'REPLACE_CLASS') {
                    setFields(f => ({
                      ...f,
                      program: t.program,
                      semester: t.semester,
                      day: t.day,
                      start_time: t.start,
                      end_time: t.end,
                      entry_type: t.type || 'Theory',
                      room: t.room || f.room
                    }))
                  }
                }} selected={target} />
              </div>
            )}

            {needsTargetB && (
              <div>
                <label className="form-label" style={{ marginBottom: 6, display: 'block' }}>Select Second Class (B)</label>
                <TargetSelector entries={entries} onSelect={setTargetB} selected={targetB} />
              </div>
            )}

            {/* ADD_CLASS form */}
            {(actionType === 'ADD_CLASS' || actionType === 'REPLACE_CLASS') && (
              <div className="responsive-grid">
                <Field label="Program">
                  <select className="form-control" value={fields.program ?? ''} onChange={e => set('program', e.target.value)}>
                    <option value="">Select…</option>
                    {PROGRAMS.map(p => <option key={p}>{p}</option>)}
                  </select>
                </Field>
                <Field label="Semester">
                  <select className="form-control" value={fields.semester ?? ''} onChange={e => set('semester', e.target.value)}>
                    <option value="">Select…</option>
                    {SEMS.map(s => <option key={s}>{s}</option>)}
                  </select>
                </Field>
                <Field label="Day">
                  <select className="form-control" value={fields.day ?? ''} onChange={e => set('day', e.target.value)}>
                    <option value="">Select…</option>
                    {DAYS.map(d => <option key={d}>{d}</option>)}
                  </select>
                </Field>
                <Field label="Type">
                  <select className="form-control" value={fields.entry_type ?? 'Theory'} onChange={e => set('entry_type', e.target.value)}>
                    <option>Theory</option>
                    <option>Practical</option>
                  </select>
                </Field>
                <Field label="Start Time">
                  <input className="form-control" type="time" value={fields.start_time ?? ''} onChange={e => set('start_time', e.target.value)} />
                </Field>
                <Field label="End Time">
                  <input className="form-control" type="time" value={fields.end_time ?? ''} onChange={e => set('end_time', e.target.value)} />
                </Field>
                <Field label="Subject Code">
                  <input className="form-control" list="action-subject-codes" placeholder="e.g. cn" value={fields.subject_code ?? ''} onChange={e => {
                    const val = e.target.value
                    set('subject_code', val)
                    if (!fields.subject_name) {
                      const subj = catalogSubjects.find(s => s.code === val)
                      if (subj) set('subject_name', subj.name)
                    }
                  }} />
                  <datalist id="action-subject-codes">
                    {catalogSubjects.map(s => <option key={s.id} value={s.code}>{s.name} ({s.program})</option>)}
                  </datalist>
                </Field>
                <Field label="Subject Name">
                  <input className="form-control" list="action-subject-names" placeholder="e.g. Computer Networks" value={fields.subject_name ?? ''} onChange={e => set('subject_name', e.target.value)} />
                  <datalist id="action-subject-names">
                    {Array.from(new Set(catalogSubjects.map(s => s.name))).map((n, i) => <option key={i} value={n} />)}
                  </datalist>
                </Field>
                <Field label="Teacher">
                  <select className="form-control" value={fields.teacher ?? ''} onChange={e => set('teacher', e.target.value)}>
                    <option value="">Select…</option>
                    {TEACHERS.map(t => <option key={t}>{t}</option>)}
                  </select>
                </Field>
                <Field label="Room">
                  <select className="form-control" value={fields.room ?? ''} onChange={e => set('room', e.target.value)}>
                    <option value="">Select…</option>
                    {ROOMS.map(r => <option key={r}>{r}</option>)}
                  </select>
                </Field>
              </div>
            )}

            {/* MOVE / CHANGE_TIME extra fields */}
            {(actionType === 'MOVE_CLASS' || actionType === 'CHANGE_TIME' || actionType === 'CHANGE_DAY') && (
              <div className="responsive-grid">
                {(actionType === 'MOVE_CLASS' || actionType === 'CHANGE_DAY') && (
                  <Field label="New Day">
                    <select className="form-control" value={fields.new_day ?? ''} onChange={e => set('new_day', e.target.value)}>
                      <option value="">Same day</option>
                      {DAYS.map(d => <option key={d}>{d}</option>)}
                    </select>
                  </Field>
                )}
                {(actionType === 'MOVE_CLASS' || actionType === 'CHANGE_TIME') && (
                  <>
                    <Field label="New Start"><input className="form-control" type="time" value={fields.new_start ?? ''} onChange={e => set('new_start', e.target.value)} /></Field>
                    <Field label="New End"><input className="form-control" type="time" value={fields.new_end ?? ''} onChange={e => set('new_end', e.target.value)} /></Field>
                  </>
                )}
              </div>
            )}

            {actionType === 'CHANGE_TEACHER' && (
              <Field label="New Teacher">
                <select className="form-control" value={fields.new_teacher ?? ''} onChange={e => set('new_teacher', e.target.value)}>
                  <option value="">Select…</option>
                  {TEACHERS.map(t => <option key={t}>{t}</option>)}
                </select>
              </Field>
            )}

            {actionType === 'CHANGE_ROOM' && (
              <Field label="New Room">
                <select className="form-control" value={fields.new_room ?? ''} onChange={e => set('new_room', e.target.value)}>
                  <option value="">Select…</option>
                  {ROOMS.map(r => <option key={r}>{r}</option>)}
                </select>
              </Field>
            )}

            {(actionType === 'EXTEND_CLASS') && (
              <Field label="New End Time">
                <input className="form-control" type="time" value={fields.new_end ?? ''} onChange={e => set('new_end', e.target.value)} />
              </Field>
            )}
          </div>
        )}

        {step === 'confirm' && (
          <div>
            <div style={{ padding: 'var(--sp-4)', background: 'var(--clr-bg-3)', borderRadius: 'var(--radius)', border: '1px solid var(--clr-border)', marginBottom: 'var(--sp-4)' }}>
              <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--clr-text-2)', marginBottom: 4 }}>About to execute:</div>
              <div style={{ fontWeight: 700, fontSize: 'var(--fs-md)', color: 'var(--clr-primary)' }}>{label}</div>
              {target && <div style={{ marginTop: 8, fontSize: 'var(--fs-sm)', color: 'var(--clr-text)' }}>
                Target: <strong>{target.subject_name}</strong> — {target.day} {target.start}–{target.end} ({target.teacher} / {target.room})
              </div>}
            </div>
            <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--clr-warning)', display: 'flex', gap: 6, alignItems: 'center' }}>
              <AlertCircle size={14} /> Hard constraints (H1–H11) will be checked before changes are saved.
            </div>
          </div>
        )}

        {/* ── Alternative step ─────────────────────────────────────────── */}
        {step === 'alternative' && result && (() => {
          const failedActionResult = result.results.find(r => !r.success)
          const s = failedActionResult?.suggestions
          return (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              {/* Error reason */}
              <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
                <AlertCircle size={20} style={{ color: 'var(--clr-error)', flexShrink: 0, marginTop: 2 }} />
                <div>
                  <div style={{ fontWeight: 600, color: 'var(--clr-error)', fontSize: 'var(--fs-sm)' }}>Action blocked by a constraint</div>
                  <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--clr-text-2)', marginTop: 4 }}>
                    {failedActionResult?.error || 'Unknown constraint violation.'}
                  </div>
                </div>
              </div>

              {/* Suggested fix cards */}
              {suggestionLoading ? (
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12, padding: 'var(--sp-5)', background: 'var(--clr-bg-3)', border: '1px solid var(--clr-border)', borderRadius: 'var(--radius)' }}>
                  <Loader2 size={32} style={{ animation: 'spin 1s linear infinite', color: 'var(--clr-primary)' }} />
                  <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--clr-text-2)', fontWeight: 500 }}>Searching for smart alternatives…</div>
                </div>
              ) : (s?.rich_suggestions?.length ?? 0) > 0 ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <div style={{ fontSize: 'var(--fs-xs)', fontWeight: 700, color: 'var(--clr-text-3)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 2 }}>
                    <Lightbulb size={12} style={{ verticalAlign: 'middle', marginRight: 4 }} />Smart Suggestions
                  </div>
                  {(s?.rich_suggestions || []).map((rs: any, i: number) => (
                    <label key={i} style={{
                      background: 'var(--clr-primary-10)',
                      border: `1px solid ${selectedIndex === i ? 'var(--clr-primary)' : 'var(--clr-primary-20)'}`,
                      borderRadius: 'var(--radius)',
                      padding: '10px 14px',
                      display: 'flex',
                      gap: 10,
                      alignItems: 'flex-start',
                      cursor: 'pointer'
                    }}>
                      <input type="radio" name="action-rich-suggestion" checked={selectedIndex === i} onChange={() => setSelectedIndex(i)} style={{ marginTop: 3 }} />
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                        <span style={{ fontWeight: 600, fontSize: 'var(--fs-sm)', color: 'var(--clr-text)' }}>
                          Suggestion {i + 1}: {rs.title}
                        </span>
                        <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--clr-text-2)' }}>
                          — {rs.description}
                        </span>
                        <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--clr-success)', fontWeight: 600 }}>
                          {rs.status || 'Conflict-free and validated'}
                        </span>
                      </div>
                    </label>
                  ))}
                </div>
              ) : (
                <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start', padding: '10px 14px', background: 'var(--clr-bg-3)', border: '1px solid var(--clr-border)', borderRadius: 'var(--radius)' }}>
                  <Lightbulb size={16} style={{ color: 'var(--clr-text-3)', flexShrink: 0, marginTop: 2 }} />
                  <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--clr-text-2)' }}>
                    <span style={{ fontWeight: 600, color: 'var(--clr-text)' }}>Nothing Available.</span>
                    {' '}{s?.note || 'The complete working-day and time-window search found no conflict-free alternative under the current constraints.'}
                  </div>
                </div>
              )}
            </div>
          )
        })()}

        {step === 'done' && result && (
          <div>
            {result.success ? (
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12, padding: 'var(--sp-5)', textAlign: 'center' }}>
                <CheckCircle2 size={40} style={{ color: 'var(--clr-success)' }} />
                <div>
                  <div style={{ fontWeight: 700, fontSize: 'var(--fs-lg)' }}>Success!</div>
                  <div style={{ color: 'var(--clr-text-2)', fontSize: 'var(--fs-sm)', marginTop: 4 }}>
                    New version #{result.new_version_id} saved.
                  </div>
                </div>
                {result.change_log && <pre style={{ fontSize: 'var(--fs-xs)', color: 'var(--clr-text-2)', textAlign: 'left', whiteSpace: 'pre-wrap', background: 'var(--clr-bg-3)', padding: 'var(--sp-3)', borderRadius: 'var(--radius)', width: '100%' }}>{result.change_log}</pre>}
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
                  <AlertCircle size={24} style={{ color: 'var(--clr-error)', flexShrink: 0 }} />
                  <div>
                    <div style={{ fontWeight: 600, color: 'var(--clr-error)' }}>Action failed</div>
                    <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--clr-text-2)', marginTop: 4 }}>
                      {result.results[0]?.error || 'Unknown error'}
                    </div>
                  </div>
                </div>
                {result.violations?.length > 0 && (
                  <div style={{ background: 'var(--clr-error-bg)', borderRadius: 'var(--radius)', padding: 'var(--sp-3)', fontSize: 'var(--fs-xs)', color: 'var(--clr-error)', display: 'flex', flexDirection: 'column', gap: 4 }}>
                    <div style={{ fontWeight: 700, marginBottom: 4, fontSize: 'var(--fs-sm)' }}>Constraint violations ({result.violations.length}):</div>
                    {result.violations.map((v, i) => (
                      <div key={i} style={{ display: 'flex', gap: 6, alignItems: 'flex-start' }}>
                        <span style={{ flexShrink: 0 }}>⚠</span>
                        <span>{violationMessage(v as unknown as Record<string, string>)}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        <div className="modal-footer">
          {step === 'form' && <>
            <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
            <button
              className="btn btn-primary"
              disabled={needsTarget && !target}
              onClick={() => setStep('confirm')}
            >
              Preview →
            </button>
          </>}
          {step === 'confirm' && <>
            <button className="btn btn-ghost" onClick={() => setStep('form')}>← Back</button>
            <button className="btn btn-primary" disabled={loading} onClick={execute}>
              {loading ? <><Loader2 size={14} style={{ animation: 'spin 0.7s linear infinite' }} /> Applying…</> : 'Apply Change'}
            </button>
          </>}
          {step === 'alternative' && (() => {
            const failedAR = result?.results.find(r => !r.success)
            const hasSuggestions = (failedAR?.suggestions?.rich_suggestions?.length ?? 0) > 0
            return (
              <>
                <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
                <button className="btn btn-ghost" onClick={() => setStep('form')}>
                  Try different
                </button>
                {hasSuggestions && (
                  <button
                    className="btn btn-primary"
                    disabled={loading}
                    onClick={applyAlternative}
                    style={{ display: 'flex', alignItems: 'center', gap: 6 }}
                  >
                    {loading
                      ? <><Loader2 size={14} style={{ animation: 'spin 0.7s linear infinite' }} /> Applying…</>
                      : <><RefreshCw size={14} /> Apply Suggestion</>}
                  </button>
                )}
              </>
            )
          })()}
          {step === 'done' && <button className="btn btn-primary" onClick={onClose}>Done</button>}
        </div>
      </div>
    </div>
  )
}
