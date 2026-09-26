import { useState, useEffect } from 'react'
import { Plus, Trash2, Loader2, Calendar, Clock, CheckCircle2, AlertTriangle, XCircle, ArrowRight } from 'lucide-react'
import { useWorkspaceStore } from '../../store'
import { getCurrentDraft, getVersion, listVersions, req as request } from '../../api'

interface BusySlot {
  id: number
  teacher_short_name: string
  scope: 'permanent' | 'temporary'
  day_of_week?: string
  specific_date?: string
  reason?: string
}

interface MoveInfo {
  subject_code: string
  subject_name: string
  from_day: string
  from_start: string
  from_end: string
  to_day: string
  to_start: string
  to_end: string
  room: string
}

interface CancelledInfo {
  subject_code: string
  subject_name: string
  day: string
  start: string
  end: string
  reason: string
}

interface CreateResult {
  slot: BusySlot
  auto_rescheduled: MoveInfo[]
  cancelled_classes: CancelledInfo[]
  message: string
}

const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

async function fetchTeachers(): Promise<{ short_name: string }[]> {
  try { return await request('/catalog/teachers') } catch { return [] }
}

export default function BusySlotsManager() {
  const { setCurrentVersion, setVersions } = useWorkspaceStore()
  const [slots, setSlots] = useState<BusySlot[]>([])
  const [teachers, setTeachers] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [form, setForm] = useState({ teacher_short_name: '', scope: 'permanent', day_of_week: 'Monday', specific_date: '', reason: '' })
  const [lastResult, setLastResult] = useState<CreateResult | null>(null)

  const setF = (k: string, v: string) => setForm(f => ({ ...f, [k]: v }))

  async function load() {
    setLoading(true)
    try { setSlots(await request('/busy-slots')) } catch { setSlots([]) }
    setLoading(false)
  }

  useEffect(() => {
    load()
    Promise.all([
      fetchTeachers(),
      request('/timetable/draft').catch(() => ({ entries: [] }))
    ]).then(([catTs, draft]: [any, any]) => {
      const ts = new Set<string>(catTs.map((t: any) => t.short_name))
      if (draft && Array.isArray(draft.entries)) {
        draft.entries.forEach((e: any) => {
          if (e.teacher) ts.add(e.teacher)
        })
      }
      setTeachers(Array.from(ts).sort())
    })
  }, [])

  async function add() {
    setSaving(true)
    setLastResult(null)
    try {
      const body: Record<string, unknown> = {
        teacher_short_name: form.teacher_short_name,
        scope: form.scope,
        reason: form.reason || undefined,
      }
      if (form.scope === 'permanent') body.day_of_week = form.day_of_week
      else body.specific_date = form.specific_date
      const result: CreateResult = await request('/busy-slots', { method: 'POST', body: JSON.stringify(body) })
      setLastResult(result)
      if (result.auto_rescheduled && result.auto_rescheduled.length > 0) {
        try {
          const draft = await getCurrentDraft()
          const detail = await getVersion(draft.id)
          setCurrentVersion(detail.id, detail.entries)
          const vList = await listVersions()
          setVersions(vList)
        } catch (err) {
          console.error("Failed to fetch updated draft after rescheduling", err)
        }
      }
      setForm({ teacher_short_name: '', scope: 'permanent', day_of_week: 'Monday', specific_date: '', reason: '' })
      await load()
    } catch (e: unknown) { alert((e as Error).message) }
    setSaving(false)
  }

  async function remove(id: number) {
    if (!confirm('Remove this busy slot?')) return
    try {
      await request(`/busy-slots/${id}`, { method: 'DELETE' })
      setSlots(s => s.filter(x => x.id !== id))
    } catch (e: any) {
      alert(`Delete failed: ${e.message}`)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-5)' }}>
      {/* Add form */}
      <div style={{ background: 'var(--clr-bg-2)', border: '1px solid var(--clr-border)', borderRadius: 'var(--radius)', padding: 'var(--sp-4)' }}>
        <div style={{ fontWeight: 700, fontSize: 'var(--fs-md)', marginBottom: 12 }}>Add Busy Slot</div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 'var(--sp-3)' }}>
          <div className="form-group">
            <label className="form-label">Teacher</label>
            <select className="form-control" value={form.teacher_short_name} onChange={e => setF('teacher_short_name', e.target.value)}>
              <option value="">Select…</option>
              {teachers.map(t => <option key={t}>{t}</option>)}
            </select>
          </div>
          <div className="form-group">
            <label className="form-label">Type</label>
            <select className="form-control" value={form.scope} onChange={e => setF('scope', e.target.value)}>
              <option value="permanent">Permanent (weekly)</option>
              <option value="temporary">Temporary (specific date)</option>
            </select>
          </div>
          {form.scope === 'permanent'
            ? <div className="form-group"><label className="form-label">Day of Week</label>
                <select className="form-control" value={form.day_of_week} onChange={e => setF('day_of_week', e.target.value)}>
                  {WEEKDAYS.map(d => <option key={d}>{d}</option>)}
                </select>
              </div>
            : <div className="form-group"><label className="form-label">Date</label>
                <input className="form-control" type="date" value={form.specific_date} onChange={e => setF('specific_date', e.target.value)} />
              </div>
          }
          <div className="form-group" style={{ gridColumn: '1 / -1' }}>
            <label className="form-label">Reason <span style={{ color: 'var(--clr-text-3)', fontWeight: 400 }}>(optional)</span></label>
            <input className="form-control" placeholder="e.g. Conference, Leave" value={form.reason} onChange={e => setF('reason', e.target.value)} />
          </div>
        </div>
        <button
          className="btn btn-primary"
          disabled={!form.teacher_short_name || saving || (form.scope === 'temporary' && !form.specific_date)}
          onClick={add}
          style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4 }}
        >
          {saving ? <Loader2 size={14} style={{ animation: 'spin 0.7s linear infinite' }} /> : <Plus size={14} />}
          {saving ? 'Adding & Rescheduling…' : 'Add Busy Slot'}
        </button>
      </div>

      {/* Auto-reschedule result panel */}
      {lastResult && (
        <div style={{
          background: 'var(--clr-bg-2)', border: '1px solid var(--clr-border)',
          borderRadius: 'var(--radius)', padding: 'var(--sp-4)'
        }}>
          <div style={{ fontWeight: 700, fontSize: 'var(--fs-md)', marginBottom: 10, display: 'flex', alignItems: 'center', gap: 8 }}>
            <CheckCircle2 size={16} style={{ color: lastResult.cancelled_classes.length > 0 ? 'var(--clr-warning)' : '#10b981' }} />
            Auto-Reschedule Result
          </div>
          <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--clr-text-3)', marginBottom: 10 }}>{lastResult.message}</div>

          {lastResult.auto_rescheduled.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 'var(--fs-xs)', fontWeight: 700, color: '#10b981', marginBottom: 6 }}>
                ✅ Successfully Rescheduled ({lastResult.auto_rescheduled.length})
              </div>
              {lastResult.auto_rescheduled.map((m, i) => (
                <div key={i} style={{
                  display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px',
                  borderRadius: 8, background: 'rgba(16,185,129,0.08)',
                  border: '1px solid rgba(16,185,129,0.2)', marginBottom: 4, fontSize: 'var(--fs-xs)'
                }}>
                  <span style={{ fontWeight: 700 }}>{m.subject_code.toUpperCase()}</span>
                  <span style={{ color: 'var(--clr-text-3)' }}>{m.subject_name}</span>
                  <span style={{ color: 'var(--clr-text-3)' }}>·</span>
                  <span>{m.from_day} {m.from_start}–{m.from_end}</span>
                  <ArrowRight size={12} style={{ color: '#10b981', flexShrink: 0 }} />
                  <span style={{ fontWeight: 700, color: '#10b981' }}>{m.to_day} {m.to_start}–{m.to_end}</span>
                  {m.room && <span style={{ color: 'var(--clr-text-3)' }}>{m.room}</span>}
                </div>
              ))}
            </div>
          )}

          {lastResult.cancelled_classes.length > 0 && (
            <div>
              <div style={{ fontSize: 'var(--fs-xs)', fontWeight: 700, color: 'var(--clr-error)', marginBottom: 6 }}>
                ⚠️ Could Not Reschedule — Classes Cancelled ({lastResult.cancelled_classes.length})
              </div>
              {lastResult.cancelled_classes.map((c, i) => (
                <div key={i} style={{
                  display: 'flex', alignItems: 'flex-start', gap: 8, padding: '6px 10px',
                  borderRadius: 8, background: 'rgba(239,68,68,0.08)',
                  border: '1px solid rgba(239,68,68,0.25)', marginBottom: 4, fontSize: 'var(--fs-xs)'
                }}>
                  <XCircle size={13} style={{ color: 'var(--clr-error)', flexShrink: 0, marginTop: 1 }} />
                  <div>
                    <div><strong>{c.subject_code.toUpperCase()}</strong> — {c.subject_name}</div>
                    <div style={{ color: 'var(--clr-text-3)' }}>{c.day} {c.start}–{c.end}</div>
                    <div style={{ color: 'var(--clr-text-3)', fontStyle: 'italic' }}>{c.reason}</div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {lastResult.auto_rescheduled.length === 0 && lastResult.cancelled_classes.length === 0 && (
            <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--clr-text-3)', display: 'flex', alignItems: 'center', gap: 6 }}>
              <AlertTriangle size={13} /> No existing classes were affected by this busy slot.
            </div>
          )}
        </div>
      )}

      {/* Table */}
      {loading
        ? <div style={{ textAlign: 'center', padding: 24, color: 'var(--clr-text-3)' }}><Loader2 size={20} style={{ animation: 'spin 0.7s linear infinite' }} /></div>
        : slots.length === 0
          ? <div style={{ textAlign: 'center', padding: 24, color: 'var(--clr-text-3)', fontSize: 'var(--fs-sm)' }}>No busy slots configured.</div>
          : (
            <div style={{ border: '1px solid var(--clr-border)', borderRadius: 'var(--radius)', overflow: 'hidden' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--fs-sm)' }}>
                <thead>
                  <tr style={{ background: 'var(--clr-bg-3)' }}>
                    {['Teacher', 'Type', 'When', 'Reason', ''].map(h => (
                      <th key={h} style={{ padding: '8px 12px', textAlign: 'left', fontWeight: 700, color: 'var(--clr-text-2)', fontSize: '11px', textTransform: 'uppercase' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {slots.map(s => (
                    <tr key={s.id} style={{ borderTop: '1px solid var(--clr-border-soft)' }}>
                      <td style={{ padding: '8px 12px', fontWeight: 700 }}>{s.teacher_short_name}</td>
                      <td style={{ padding: '8px 12px' }}>
                        <span style={{
                          padding: '2px 8px', borderRadius: 20, fontSize: '11px', fontWeight: 700,
                          background: s.scope === 'permanent' ? 'var(--clr-error-bg)' : 'var(--clr-warning-bg)',
                          color: s.scope === 'permanent' ? 'var(--clr-error)' : 'var(--clr-warning)',
                        }}>
                          {s.scope === 'permanent' ? 'Permanent' : 'Temporary'}
                        </span>
                      </td>
                      <td style={{ padding: '8px 12px', display: 'flex', alignItems: 'center', gap: 5 }}>
                        {s.scope === 'permanent'
                          ? <><Clock size={12} style={{ color: 'var(--clr-text-3)' }} />{s.day_of_week} (weekly)</>
                          : <><Calendar size={12} style={{ color: 'var(--clr-text-3)' }} />{s.specific_date}</>}
                      </td>
                      <td style={{ padding: '8px 12px', color: 'var(--clr-text-3)' }}>{s.reason || '—'}</td>
                      <td style={{ padding: '8px 12px' }}>
                        <button className="btn-icon" onClick={() => remove(s.id)} title="Delete">
                          <Trash2 size={14} style={{ color: 'var(--clr-error)' }} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
      }
    </div>
  )
}
