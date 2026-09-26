import { useState, useEffect } from 'react'
import { X, Sparkles, Loader2, CheckCircle2, AlertCircle, CalendarClock } from 'lucide-react'
import { useWorkspaceStore } from '../../store'
import { getVersion, req } from '../../api'

const SEMS = ['1st', '2nd', '3rd', '4th', '5th', '6th', '7th', '8th']
const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

interface SmartResult {
  found: boolean
  day?: string
  start?: string
  end?: string
  room?: string
  message: string
  new_version_id?: number
  proposed_action?: object
}

interface Props { onClose: () => void }

export default function SmartScheduleModal({ onClose }: Props) {
  const { currentVersionId, setCurrentVersion } = useWorkspaceStore()
  const [fields, setFields] = useState<Record<string, string>>({
    program: '', semester: '', subject_code: '', subject_name: '',
    teacher: '', custom_teacher: '', entry_type: 'Theory', room: '', preferred_day: '',
  })
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<SmartResult | null>(null)
  const [step, setStep] = useState<'form' | 'preview' | 'done'>('form')
  const [bookingLoading, setBookingLoading] = useState(false)

  const [dynamicPrograms, setDynamicPrograms] = useState<string[]>([])
  const [dynamicTeachers, setDynamicTeachers] = useState<string[]>([])

  useEffect(() => {
    req<{name: string}[]>('/catalog/programs').then(progs => {
      const dbProgs = progs.map(p => (p.name || '').replace(/\.+$/, '').trim()).filter(Boolean)
      setDynamicPrograms(Array.from(new Set([...dbProgs, 'B.Tech', 'M.Tech', 'M.Sc'])))
    }).catch(() => setDynamicPrograms(['B.Tech', 'M.Tech', 'M.Sc']))
    
    req<{short_name: string}[]>('/catalog/teachers').then(techs => {
      const dbTechs = techs.map(t => t.short_name).filter(Boolean)
      const staticTechs = ["AK","SC","RS","DG","DK","SN","NK","VK","HS","RB","SD","SG","SH"]
      setDynamicTeachers(Array.from(new Set([...dbTechs, ...staticTechs])))
    }).catch(() => setDynamicTeachers(["AK","SC","RS","DG","DK","SN","NK","VK","HS","RB","SD","SG","SH"]))
  }, [])

  const set = (k: string, v: string) => setFields(f => ({ ...f, [k]: v }))
  const teacher = fields.teacher === 'Other…' ? fields.custom_teacher : fields.teacher

  async function findSlot() {
    setLoading(true)
    try {
      const res = await fetch('/api/actions/smart-schedule', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          program: fields.program,
          semester: fields.semester,
          subject_code: fields.subject_code || fields.subject_name.toLowerCase().replace(/\s+/g, ''),
          subject_name: fields.subject_name,
          teacher,
          entry_type: fields.entry_type,
          room: fields.room || undefined,
          preferred_day: fields.preferred_day || undefined,
          auto_execute: false,
        }),
      })
      const data = await res.json()
      setResult(data)
      setStep(data.found ? 'preview' : 'done')
    } catch (e: unknown) {
      setResult({ found: false, message: (e as Error).message })
      setStep('done')
    } finally {
      setLoading(false)
    }
  }

  async function bookSlot() {
    if (!result?.proposed_action) return
    setBookingLoading(true)
    try {
      const res = await fetch('/api/actions/execute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ actions: [result.proposed_action], version_id: currentVersionId }),
      })
      const data = await res.json()
      if (data.success && data.new_version_id) {
        const v = await getVersion(data.new_version_id)
        setCurrentVersion(v.id, v.entries)
        setResult(r => ({ ...r!, new_version_id: data.new_version_id, message: `Booked! Class added as Version #${data.new_version_id}.` }))
      } else {
        setResult(r => ({ ...r!, found: false, message: data.results?.[0]?.error || 'Booking failed.' }))
      }
      setStep('done')
    } catch (e: unknown) {
      setResult(r => ({ ...r!, found: false, message: (e as Error).message }))
      setStep('done')
    } finally {
      setBookingLoading(false)
    }
  }

  const canFind = fields.program && fields.semester && fields.subject_name && teacher

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ maxWidth: 560 }}>
        <div className="modal-header">
          <h2 className="modal-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Sparkles size={18} style={{ color: 'var(--clr-primary)' }} /> Smart Auto-Schedule
          </h2>
          <button className="btn-icon" onClick={onClose}><X size={18} /></button>
        </div>

        {step === 'form' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)' }}>
            <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--clr-text-2)', padding: '8px 12px', background: 'var(--clr-primary-10)', borderRadius: 'var(--radius)', border: '1px solid var(--clr-primary-20)' }}>
              Just fill in the class details — the system will automatically find the best conflict-free time slot and room.
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--sp-3)' }}>
              <div className="form-group">
                <label className="form-label">Program</label>
                <select className="form-control" value={fields.program} onChange={e => set('program', e.target.value)}>
                  <option value="">Select…</option>
                  {dynamicPrograms.map(o => <option key={o}>{o}</option>)}
                  {dynamicPrograms.length === 0 && <option value="">Loading…</option>}
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Semester</label>
                <select className="form-control" value={fields.semester} onChange={e => set('semester', e.target.value)}>
                  <option value="">Select…</option>
                  {SEMS.map(o => <option key={o}>{o}</option>)}
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Subject Name</label>
                <input className="form-control" placeholder="e.g. Computer Networks" value={fields.subject_name} onChange={e => set('subject_name', e.target.value)} />
              </div>
              <div className="form-group">
                <label className="form-label">Subject Code <span style={{ color: 'var(--clr-text-3)', fontWeight: 400 }}>(optional)</span></label>
                <input className="form-control" placeholder="e.g. cn (auto-derived)" value={fields.subject_code} onChange={e => set('subject_code', e.target.value)} />
              </div>
              <div className="form-group">
                <label className="form-label">Teacher</label>
                <select className="form-control" value={fields.teacher} onChange={e => set('teacher', e.target.value)}>
                  <option value="">Select…</option>
                  {dynamicTeachers.map(t => <option key={t}>{t}</option>)}
                  <option value="Other…">Other…</option>
                </select>
              </div>
              {fields.teacher === 'Other…' && (
                <div className="form-group">
                  <label className="form-label">New Teacher Short Name</label>
                  <input className="form-control" placeholder="e.g. AK" value={fields.custom_teacher} onChange={e => set('custom_teacher', e.target.value)} />
                </div>
              )}
              <div className="form-group">
                <label className="form-label">Class Type</label>
                <select className="form-control" value={fields.entry_type} onChange={e => set('entry_type', e.target.value)}>
                  <option>Theory</option>
                  <option>Practical</option>
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Preferred Day <span style={{ color: 'var(--clr-text-3)', fontWeight: 400 }}>(optional)</span></label>
                <select className="form-control" value={fields.preferred_day} onChange={e => set('preferred_day', e.target.value)}>
                  <option value="">Any Day</option>
                  {DAYS.map(d => <option key={d}>{d}</option>)}
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Room <span style={{ color: 'var(--clr-text-3)', fontWeight: 400 }}>(optional)</span></label>
                <input className="form-control" placeholder="Leave blank to auto-assign" value={fields.room} onChange={e => set('room', e.target.value)} />
              </div>
            </div>
          </div>
        )}

        {step === 'preview' && result?.found && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div style={{ background: 'var(--clr-primary-10)', border: '1px solid var(--clr-primary-20)', borderRadius: 'var(--radius)', padding: '16px 18px' }}>
              <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--clr-primary)', fontWeight: 700, marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
                <CalendarClock size={15} /> Best Slot Found
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
                {[['Day', result.day], ['Time', `${result.start}–${result.end}`], ['Room', result.room]].map(([label, val]) => (
                  <div key={label as string}>
                    <div style={{ fontSize: '10px', color: 'var(--clr-text-3)', textTransform: 'uppercase', fontWeight: 700, marginBottom: 2 }}>{label}</div>
                    <div style={{ fontWeight: 600, fontSize: 'var(--fs-md)' }}>{val}</div>
                  </div>
                ))}
              </div>
              <div style={{ marginTop: 12, paddingTop: 10, borderTop: '1px solid var(--clr-primary-20)', fontSize: 'var(--fs-sm)', color: 'var(--clr-text-2)' }}>
                <strong>{fields.subject_name}</strong> · {teacher} · {fields.entry_type} · {fields.program} {fields.semester}
              </div>
            </div>
            <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--clr-text-3)', display: 'flex', gap: 6, alignItems: 'center' }}>
              <AlertCircle size={13} /> Constraints H1–H11 will be validated before booking.
            </div>
          </div>
        )}

        {step === 'done' && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12, padding: 'var(--sp-5)', textAlign: 'center' }}>
            {result?.new_version_id
              ? <><CheckCircle2 size={40} style={{ color: 'var(--clr-success)' }} /><div style={{ fontWeight: 700, fontSize: 'var(--fs-lg)' }}>Booked!</div></>
              : <><AlertCircle size={40} style={{ color: result?.found ? 'var(--clr-warning)' : 'var(--clr-error)' }} /><div style={{ fontWeight: 700 }}>{result?.found ? 'No slot available' : 'Error'}</div></>
            }
            <div style={{ color: 'var(--clr-text-2)', fontSize: 'var(--fs-sm)' }}>{result?.message}</div>
          </div>
        )}

        <div className="modal-footer">
          {step === 'form' && <>
            <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
            <button className="btn btn-primary" disabled={!canFind || loading} onClick={findSlot}
              style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              {loading ? <><Loader2 size={14} style={{ animation: 'spin 0.7s linear infinite' }} /> Finding…</> : <><Sparkles size={14} /> Find Best Slot</>}
            </button>
          </>}
          {step === 'preview' && <>
            <button className="btn btn-ghost" onClick={() => setStep('form')}>← Change</button>
            <button className="btn btn-primary" disabled={bookingLoading} onClick={bookSlot}
              style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              {bookingLoading ? <><Loader2 size={14} style={{ animation: 'spin 0.7s linear infinite' }} /> Booking…</> : '✓ Book This Slot'}
            </button>
          </>}
          {step === 'done' && <button className="btn btn-primary" onClick={onClose}>Done</button>}
        </div>
      </div>
    </div>
  )
}
