import { useState } from 'react'
import { CheckCircle2, XCircle, AlertTriangle, Loader2, RefreshCw } from 'lucide-react'
import type { ValidationResult, Violation } from '../../types'
import { validateVersion } from '../../api'
import { useWorkspaceStore } from '../../store'

function fmtEntry(e: any) {
  if (!e) return ''
  return `${e.program} ${e.semester} - ${e.subject} (${e.day} ${e.start}-${e.end})`
}


const RULES = [
  { key: 'H1', label: 'No semester simultaneous classes', fn: (v: Violation[]) => v.filter(x => x.rule === 'H1_semester_clash') },
  { key: 'H2', label: 'No teacher double-booking', fn: (v: Violation[]) => v.filter(x => x.rule === 'H2_teacher_clash') },
  { key: 'H3', label: 'No room double-booking', fn: (v: Violation[]) => v.filter(x => x.rule === 'H3_room_clash') },
  { key: 'H4', label: 'Room facility check (Labs for practicals)', fn: (v: Violation[]) => v.filter(x => x.rule === 'H4_room_not_lab' || x.rule === 'H4_unknown_room') },
  { key: 'H5', label: 'Max 1 theory/practical per teacher per day', fn: (v: Violation[]) => v.filter(x => x.rule === 'H5_multiple_theory_same_day' || x.rule === 'H5_multiple_practical_same_day') },
  { key: 'H6', label: 'Internal teachers must have ≥ 1 free day', fn: (v: Violation[]) => v.filter(x => x.rule === 'H6_no_free_day') },
  { key: 'H11', label: 'Subjects assigned to correct semester', fn: (v: Violation[]) => v.filter(x => x.rule === 'H11_wrong_semester_subject') },
]

interface Props { versionId?: number | null }

export default function ValidationPanel({ versionId }: Props) {
  const { currentVersionId } = useWorkspaceStore()
  const id = versionId ?? currentVersionId
  const [result, setResult] = useState<ValidationResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function runValidation() {
    if (!id) return
    setLoading(true); setError(null)
    try {
      const r = await validateVersion(id)
      setResult(r)
    } catch (e: unknown) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  const violations = result?.violations ?? []
  const passed = result && violations.length === 0

  return (
    <div style={{ padding: 'var(--sp-4)', display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
        <div>
          <h3 style={{ fontSize: 'var(--fs-md)', fontWeight: 700 }}>Validation Report</h3>
          {id && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--clr-text-3)', marginTop: 2 }}>Version #{id}</div>}
        </div>
        <button className="btn btn-ghost btn-sm" onClick={runValidation} disabled={!id || loading}>
          {loading ? <Loader2 size={13} style={{ animation: 'spin 0.7s linear infinite' }} /> : <RefreshCw size={13} />}
          {loading ? 'Running…' : 'Run Validation'}
        </button>
      </div>

      {error && <div style={{ background: 'var(--clr-error-bg)', border: '1px solid rgba(239,68,68,0.3)', borderRadius: 'var(--radius)', padding: 'var(--sp-3)', fontSize: 'var(--fs-sm)', color: 'var(--clr-error)' }}>{error}</div>}

      {!result && !loading && (
        <div className="empty-state" style={{ minHeight: 200 }}>
          <ShieldCheckIcon />
          <h3>No validation run yet</h3>
          <p>Click "Run Validation" to check H1–H11 hard constraints for this version.</p>
        </div>
      )}

      {passed && (
        <div style={{ background: 'var(--clr-success-bg)', border: '1px solid rgba(16,185,129,0.3)', borderRadius: 'var(--radius-lg)', padding: 'var(--sp-4)', display: 'flex', alignItems: 'center', gap: 12 }}>
          <CheckCircle2 size={32} style={{ color: 'var(--clr-success)', flexShrink: 0 }} />
          <div>
            <div style={{ fontWeight: 700, color: 'var(--clr-success)', fontSize: 'var(--fs-md)' }}>All checks passed!</div>
            <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--clr-text-2)', marginTop: 2 }}>This timetable satisfies all H1–H11 hard constraints and is ready to publish.</div>
          </div>
        </div>
      )}

      {result && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-2)' }}>
          {RULES.map(rule => {
            const rViolations = rule.fn(violations)
            const ok = rViolations.length === 0
            return (
              <div key={rule.key} className={`validation-rule ${ok ? 'pass' : 'fail'}`}>
                <div style={{ flexShrink: 0, marginTop: 1 }}>
                  {ok
                    ? <CheckCircle2 size={16} style={{ color: 'var(--clr-success)' }} />
                    : <XCircle size={16} style={{ color: 'var(--clr-error)' }} />
                  }
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontSize: 'var(--fs-xs)', fontWeight: 700, color: ok ? 'var(--clr-success)' : 'var(--clr-error)' }}>{rule.key}</span>
                    <span style={{ fontSize: 'var(--fs-sm)', fontWeight: 500, color: 'var(--clr-text)' }}>{rule.label}</span>
                  </div>
                  {!ok && rViolations.map((v: any, i) => (
                    <div key={i} style={{ marginTop: 4, fontSize: 'var(--fs-xs)', color: 'var(--clr-text-2)', display: 'flex', alignItems: 'flex-start', gap: 5 }}>
                      <AlertTriangle size={11} style={{ color: 'var(--clr-warning)', flexShrink: 0, marginTop: 2 }} />
                      <div style={{ lineHeight: 1.4, wordBreak: 'break-word' }}>
                        {v.rule === 'H1_semester_clash' && <span>{fmtEntry(v.a)} overlaps with {fmtEntry(v.b)}</span>}
                        {v.rule === 'H2_teacher_clash' && <span>Teacher(s) <strong>{v.teachers?.join(', ')}</strong> double-booked: {fmtEntry(v.a)} and {fmtEntry(v.b)}</span>}
                        {v.rule === 'H3_room_clash' && <span>Room <strong>{v.room}</strong> double-booked: {fmtEntry(v.a)} and {fmtEntry(v.b)}</span>}
                        {(v.rule === 'H4_room_not_lab' || v.rule === 'H4_unknown_room') && <span>Room <strong>{v.room}</strong> issue for {fmtEntry(v.entry)}. {v.note ? `(${v.note})` : 'Practical requires a Lab room.'}</span>}
                        {v.rule.startsWith('H5') && <span>Teacher <strong>{v.teacher}</strong> has multiple {v.rule.includes('theory') ? 'theory' : 'practical'} classes on {v.day}.</span>}
                        {v.rule === 'H6_no_free_day' && <span>Internal teacher <strong>{v.teacher}</strong> has no free working day.</span>}
                        {v.rule === 'H11_wrong_semester_subject' && <span>{v.note} {fmtEntry(v.entry)}</span>}

                        {/* Fallback */}
                        {!['H1_semester_clash', 'H2_teacher_clash', 'H3_room_clash', 'H4_room_not_lab', 'H4_unknown_room', 'H5_multiple_theory_same_day', 'H5_multiple_practical_same_day', 'H6_no_free_day', 'H11_wrong_semester_subject'].includes(v.rule) && (
                          <span>{v.note ?? v.rule} {v.teacher && <span className="badge badge-red">{v.teacher}</span>} {v.day && <span>on {v.day}</span>}</span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
                {!ok && <span className="badge badge-red">{rViolations.length}</span>}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function ShieldCheckIcon() {
  return (
    <svg width={48} height={48} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
      <polyline points="9 12 11 14 15 10"/>
    </svg>
  )
}
