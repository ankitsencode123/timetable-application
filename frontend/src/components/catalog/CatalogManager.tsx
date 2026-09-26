import { useState, useEffect } from 'react'
import { Plus, Trash2, Loader2, Pencil, Check, X } from 'lucide-react'
import { useWorkspaceStore } from '../../store'

const PROGRAMS = ['B.Tech', 'M.Tech', 'M.Sc']
const SEMS = ['1st', '2nd', '3rd', '4th', '5th', '6th', '7th', '8th']
const ENTRY_TYPES = ['Theory', 'Practical', 'Both']

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/)
  const csrf = match ? decodeURIComponent(match[1]) : null
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(csrf ? { 'X-CSRF-Token': csrf } : {}),
      ...(init.headers ?? {}),
    },
    credentials: 'include',
  })
  if (res.status === 204) return undefined as T
  if (!res.ok) { const b = await res.json().catch(() => ({})); throw new Error(b.detail ?? `HTTP ${res.status}`) }
  return res.json()
}

// ── Types ──────────────────────────────────────────
interface Teacher { id: number; short_name: string; full_name: string; subjects_csv: string; is_internal: boolean; user_id: number; auto_email?: string; auto_password?: string }
interface Subject { id: number; code: string; name: string; program: string; semester: string; entry_type: string; weekly_hours: number }
interface Program { id: number; name: string; semesters_count: number; description: string; is_active: boolean }

// ── Teacher Tab ────────────────────────────────────
function TeachersTab() {
  const [list, setList] = useState<Teacher[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [created, setCreated] = useState<{ email: string; password?: string } | null>(null)
  const [editId, setEditId] = useState<number | null>(null)
  const [editSubjects, setEditSubjects] = useState('')
  const [form, setForm] = useState({ short_name: '', full_name: '', subjects_csv: '', is_internal: true, email: '' })
  const setF = (k: string, v: unknown) => setForm(f => ({ ...f, [k]: v }))

  useEffect(() => { req<Teacher[]>('/catalog/teachers').then(setList).catch(() => {}).finally(() => setLoading(false)) }, [])

  async function add() {
    setSaving(true)
    try {
      const r = await req<Teacher>('/catalog/teachers', { method: 'POST', body: JSON.stringify(form) })
      setList(l => [...l, r])
      if (r.auto_email) setCreated({ email: r.auto_email, password: r.auto_password })
      setForm({ short_name: '', full_name: '', subjects_csv: '', is_internal: true, email: '' })
    } catch (e: unknown) { alert((e as Error).message) }
    setSaving(false)
  }

  async function saveEdit(id: number) {
    try {
      const r = await req<Teacher>(`/catalog/teachers/${id}`, { method: 'PATCH', body: JSON.stringify({ subjects_csv: editSubjects }) })
      setList(l => l.map(t => t.id === id ? r : t))
      setEditId(null)
    } catch (e: unknown) { alert((e as Error).message) }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>
      {created && (
        <div style={{ background: 'var(--clr-success-bg)', border: '1px solid var(--clr-success)', borderRadius: 'var(--radius)', padding: '10px 14px', fontSize: 'var(--fs-sm)' }}>
          <strong>Teacher account created!</strong> Login: <code>{created.email}</code>{created.password && <> | Password: <code>{created.password}</code> (save this!)</>}
          <button className="btn-icon" style={{ marginLeft: 8 }} onClick={() => setCreated(null)}><X size={12} /></button>
        </div>
      )}
      <div style={{ background: 'var(--clr-bg-2)', border: '1px solid var(--clr-border)', borderRadius: 'var(--radius)', padding: 'var(--sp-4)' }}>
        <div style={{ fontWeight: 700, marginBottom: 12 }}>+ New Teacher</div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--sp-3)' }}>
          {[['short_name', 'Short Name', 'e.g. AK'], ['full_name', 'Full Name', 'e.g. Dr. Ankit Kumar'], ['subjects_csv', 'Subjects (CSV codes)', 'e.g. cn,dbms'], ['email', 'Email (auto if blank)', '']].map(([k, label, ph]) => (
            <div className="form-group" key={k}>
              <label className="form-label">{label}</label>
              <input className="form-control" list={`tt-${k}`} placeholder={ph} value={(form as Record<string, unknown>)[k] as string} onChange={e => setF(k, e.target.value)} />
            </div>
          ))}
          <datalist id="tt-short_name">
            {list.map(t => <option key={t.id} value={t.short_name}>{t.full_name}</option>)}
          </datalist>
          <datalist id="tt-full_name">
            {Array.from(new Set(list.map(t => t.full_name))).map((n, i) => <option key={i} value={n} />)}
          </datalist>
          <div className="form-group">
            <label className="form-label">Type</label>
            <select className="form-control" value={form.is_internal ? 'internal' : 'external'} onChange={e => setF('is_internal', e.target.value === 'internal')}>
              <option value="internal">Internal (permanent faculty)</option>
              <option value="external">External / Visiting</option>
            </select>
          </div>
        </div>
        <button className="btn btn-primary" disabled={!form.short_name || !form.full_name || saving} onClick={add} style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4 }}>
          {saving ? <Loader2 size={14} style={{ animation: 'spin 0.7s linear infinite' }} /> : <Plus size={14} />} Add Teacher
        </button>
      </div>

      {loading ? <div style={{ textAlign: 'center', padding: 24 }}><Loader2 size={20} style={{ animation: 'spin 0.7s linear infinite', color: 'var(--clr-text-3)' }} /></div> : (
        <div style={{ border: '1px solid var(--clr-border)', borderRadius: 'var(--radius)', overflow: 'hidden' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--fs-sm)' }}>
            <thead><tr style={{ background: 'var(--clr-bg-3)' }}>
              {['Short Name', 'Full Name', 'Type', 'Subjects', ''].map(h => (
                <th key={h} style={{ padding: '8px 12px', textAlign: 'left', fontWeight: 700, color: 'var(--clr-text-2)', fontSize: '11px', textTransform: 'uppercase' }}>{h}</th>
              ))}
            </tr></thead>
            <tbody>{list.map(t => (
              <tr key={t.id} style={{ borderTop: '1px solid var(--clr-border-soft)' }}>
                <td style={{ padding: '8px 12px', fontWeight: 700 }}>{t.short_name}</td>
                <td style={{ padding: '8px 12px' }}>{t.full_name}</td>
                <td style={{ padding: '8px 12px' }}>
                  <span style={{ padding: '2px 8px', borderRadius: 20, fontSize: '11px', fontWeight: 700, background: t.is_internal ? 'var(--clr-primary-10)' : 'var(--clr-bg-3)', color: t.is_internal ? 'var(--clr-primary)' : 'var(--clr-text-3)' }}>{t.is_internal ? 'Internal' : 'External'}</span>
                </td>
                <td style={{ padding: '8px 12px', color: 'var(--clr-text-2)', fontFamily: 'monospace', fontSize: '11px' }}>
                  {editId === t.id
                    ? <input style={{ background: 'var(--clr-bg)', border: '1px solid var(--clr-border)', borderRadius: 4, padding: '2px 6px', fontSize: '11px', width: '100%' }} value={editSubjects} onChange={e => setEditSubjects(e.target.value)} />
                    : t.subjects_csv || '—'}
                </td>
                <td style={{ padding: '8px 12px', display: 'flex', gap: 6 }}>
                  {editId === t.id
                    ? <><button className="btn-icon" onClick={() => saveEdit(t.id)}><Check size={13} style={{ color: 'var(--clr-success)' }} /></button>
                        <button className="btn-icon" onClick={() => setEditId(null)}><X size={13} /></button></>
                    : <button className="btn-icon" onClick={() => { setEditId(t.id); setEditSubjects(t.subjects_csv) }}><Pencil size={13} /></button>}
                </td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── Subject Tab ────────────────────────────────────
function SubjectsTab() {
  const [list, setList] = useState<Subject[]>([])
  const [programs, setPrograms] = useState<Program[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [form, setForm] = useState({ code: '', name: '', program: '', semester: '1st', entry_type: 'Theory', weekly_hours: '2' })
  const setF = (k: string, v: string) => setForm(f => ({ ...f, [k]: v }))

  useEffect(() => {
    Promise.all([req<Subject[]>('/catalog/subjects'), req<Program[]>('/catalog/programs')])
      .then(([subs, progs]) => {
        setList(subs)
        setPrograms(progs)
        if (progs.length > 0 && !form.program) setF('program', progs[0].name)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  async function add() {
    setSaving(true)
    try {
      const r = await req<Subject>('/catalog/subjects', { method: 'POST', body: JSON.stringify({ ...form, weekly_hours: parseInt(form.weekly_hours) || 2 }) })
      setList(l => [...l, r])
      setForm({ code: '', name: '', program: 'B.Tech', semester: '1st', entry_type: 'Theory', weekly_hours: '2' })
    } catch (e: unknown) { alert((e as Error).message) }
    setSaving(false)
  }

  async function remove(id: number) {
    if (!confirm('Remove this subject?')) return
    await req(`/catalog/subjects/${id}`, { method: 'DELETE' })
    setList(l => l.filter(s => s.id !== id))
  }

  const grouped = list.reduce<Record<string, Subject[]>>((acc, s) => {
    const k = `${s.program} — ${s.semester}`
    acc[k] = [...(acc[k] || []), s]
    return acc
  }, {})

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>
      <div style={{ background: 'var(--clr-bg-2)', border: '1px solid var(--clr-border)', borderRadius: 'var(--radius)', padding: 'var(--sp-4)' }}>
        <div style={{ fontWeight: 700, marginBottom: 12 }}>+ New Subject</div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 'var(--sp-3)' }}>
          <div className="form-group">
            <label className="form-label">Code</label>
            <input className="form-control" list="st-code" placeholder="e.g. qc" value={form.code} onChange={e => {
                const val = e.target.value;
                setF('code', val)
                if(!form.name) {
                  const exist = list.find(s => s.code === val)
                  if(exist) setF('name', exist.name)
                }
            }} />
          </div>
          <div className="form-group" style={{ gridColumn: 'span 2' }}>
            <label className="form-label">Subject Name</label>
            <input className="form-control" list="st-name" placeholder="e.g. Quantum Computing" value={form.name} onChange={e => setF('name', e.target.value)} />
          </div>
          <datalist id="st-code">
            {list.map(s => <option key={s.id} value={s.code}>{s.name} ({s.program})</option>)}
          </datalist>
          <datalist id="st-name">
            {Array.from(new Set(list.map(s => s.name))).map((n, i) => <option key={i} value={n} />)}
          </datalist>
          <div className="form-group">
            <label className="form-label">Program</label>
            <select className="form-control" value={form.program} onChange={e => setF('program', e.target.value)}>
              {PROGRAMS.map(p => <option key={p}>{p}</option>)}
              {programs.map(p => <option key={p.name}>{p.name}</option>)}
              {programs.length === 0 && PROGRAMS.length === 0 && <option>No programs found</option>}
            </select>
          </div>
          <div className="form-group">
            <label className="form-label">Semester</label>
            <select className="form-control" value={form.semester} onChange={e => setF('semester', e.target.value)}>
              {SEMS.map(s => <option key={s}>{s}</option>)}
            </select>
          </div>
          <div className="form-group">
            <label className="form-label">Type</label>
            <select className="form-control" value={form.entry_type} onChange={e => setF('entry_type', e.target.value)}>
              {ENTRY_TYPES.map(t => <option key={t}>{t}</option>)}
            </select>
          </div>
          <div className="form-group">
            <label className="form-label">Weekly Hours</label>
            <input className="form-control" type="number" min={1} max={10} value={form.weekly_hours} onChange={e => setF('weekly_hours', e.target.value)} />
          </div>
        </div>
        <button className="btn btn-primary" disabled={!form.code || !form.name || saving} onClick={add} style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4 }}>
          {saving ? <Loader2 size={14} style={{ animation: 'spin 0.7s linear infinite' }} /> : <Plus size={14} />} Add Subject
        </button>
      </div>

      {loading ? <div style={{ textAlign: 'center', padding: 24 }}><Loader2 size={20} style={{ animation: 'spin 0.7s linear infinite', color: 'var(--clr-text-3)' }} /></div>
        : Object.entries(grouped).map(([group, subjects]) => (
          <div key={group}>
            <div style={{ fontSize: '11px', fontWeight: 700, color: 'var(--clr-text-3)', textTransform: 'uppercase', marginBottom: 6 }}>{group}</div>
            <div style={{ border: '1px solid var(--clr-border)', borderRadius: 'var(--radius)', overflow: 'hidden' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--fs-sm)' }}>
                <tbody>{subjects.map(s => (
                  <tr key={s.id} style={{ borderTop: '1px solid var(--clr-border-soft)' }}>
                    <td style={{ padding: '7px 12px', fontFamily: 'monospace', fontWeight: 700, width: 80, color: 'var(--clr-primary)' }}>{s.code}</td>
                    <td style={{ padding: '7px 12px' }}>{s.name}</td>
                    <td style={{ padding: '7px 12px', color: 'var(--clr-text-3)' }}>{s.entry_type} · {s.weekly_hours}h/wk</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right' }}>
                      <button className="btn-icon" onClick={() => remove(s.id)}><Trash2 size={13} style={{ color: 'var(--clr-error)' }} /></button>
                    </td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </div>
        ))
      }
    </div>
  )
}

// ── Program Tab ────────────────────────────────────
function ProgramsTab() {
  const [list, setList] = useState<Program[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [form, setForm] = useState({ name: '', semesters_count: '8', description: '' })
  const setF = (k: string, v: string) => setForm(f => ({ ...f, [k]: v }))

  useEffect(() => { req<Program[]>('/catalog/programs').then(setList).catch(() => {}).finally(() => setLoading(false)) }, [])

  async function add() {
    setSaving(true)
    try {
      const r = await req<Program>('/catalog/programs', { method: 'POST', body: JSON.stringify({ ...form, semesters_count: parseInt(form.semesters_count) || 8 }) })
      setList(l => [...l, r])
      setForm({ name: '', semesters_count: '8', description: '' })
    } catch (e: unknown) { alert((e as Error).message) }
    setSaving(false)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>
      <div style={{ background: 'var(--clr-bg-2)', border: '1px solid var(--clr-border)', borderRadius: 'var(--radius)', padding: 'var(--sp-4)' }}>
        <div style={{ fontWeight: 700, marginBottom: 12 }}>+ New Program</div>
        <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 'var(--sp-3)' }}>
          <div className="form-group">
            <label className="form-label">Program Name</label>
            <input className="form-control" list="pt-name" placeholder="e.g. M.Tech Data Science" value={form.name} onChange={e => setF('name', e.target.value)} />
          </div>
          <datalist id="pt-name">
            {list.map(p => <option key={p.id} value={p.name} />)}
            {PROGRAMS.map(p => <option key={p} value={p} />)}
          </datalist>
          <div className="form-group">
            <label className="form-label">Semesters</label>
            <input className="form-control" type="number" min={1} max={12} value={form.semesters_count} onChange={e => setF('semesters_count', e.target.value)} />
          </div>
          <div className="form-group" style={{ gridColumn: '1 / -1' }}>
            <label className="form-label">Description <span style={{ color: 'var(--clr-text-3)', fontWeight: 400 }}>(optional)</span></label>
            <input className="form-control" placeholder="Short description…" value={form.description} onChange={e => setF('description', e.target.value)} />
          </div>
        </div>
        <button className="btn btn-primary" disabled={!form.name || saving} onClick={add} style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4 }}>
          {saving ? <Loader2 size={14} style={{ animation: 'spin 0.7s linear infinite' }} /> : <Plus size={14} />} Add Program
        </button>
      </div>
      {loading
        ? <div style={{ textAlign: 'center', padding: 24 }}><Loader2 size={20} style={{ animation: 'spin 0.7s linear infinite', color: 'var(--clr-text-3)' }} /></div>
        : (
          <>
            {list.length === 0 && (
              <div style={{ textAlign: 'center', padding: 24, color: 'var(--clr-text-3)', fontSize: 'var(--fs-sm)' }}>No custom programs added yet. The built-in programs (B.Tech, M.Tech, M.Sc) are always available.</div>
            )}
            {list.length > 0 && (
              <div style={{ padding: '12px 16px', background: 'var(--clr-bg-3)', borderRadius: 'var(--radius)', color: 'var(--clr-text-2)', fontSize: 'var(--fs-sm)', border: '1px dashed var(--clr-border-soft)' }}>
                The built-in programs (B.Tech, M.Tech, M.Sc) are always available.
              </div>
            )}
            {list.map(p => (
              <div key={p.id} style={{ background: 'var(--clr-bg-2)', border: '1px solid var(--clr-border)', borderRadius: 'var(--radius)', padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 12 }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 700 }}>{p.name}</div>
                  {p.description && <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--clr-text-3)', marginTop: 2 }}>{p.description}</div>}
                </div>
                <span style={{ fontSize: '11px', color: 'var(--clr-text-3)' }}>{p.semesters_count} semesters</span>
                <span style={{ padding: '2px 8px', borderRadius: 20, fontSize: '11px', fontWeight: 700, background: p.is_active ? 'var(--clr-success-bg)' : 'var(--clr-bg-3)', color: p.is_active ? 'var(--clr-success)' : 'var(--clr-text-3)' }}>
                  {p.is_active ? 'Active' : 'Inactive'}
                </span>
              </div>
            ))}
          </>
        )
      }
    </div>
  )
}

// ── Bundle Setup Tab ─────────────────────────────────
function BundleSetupTab({ onClose }: { onClose?: () => void }) {
  const [programName, setProgramName] = useState('')
  const [semesters, setSemesters] = useState('8')
  const [description, setDescription] = useState('')
  const [subjects, setSubjects] = useState<Record<string, unknown>[]>([])
  const [saving, setSaving] = useState(false)

  const [catalogPrograms, setCatalogPrograms] = useState<Program[]>([])
  const [catalogTeachers, setCatalogTeachers] = useState<Teacher[]>([])
  const [catalogSubjects, setCatalogSubjects] = useState<Subject[]>([])

  useEffect(() => {
    Promise.all([
      req<Program[]>('/catalog/programs'),
      req<Teacher[]>('/catalog/teachers'),
      req<Subject[]>('/catalog/subjects')
    ])
      .then(([progs, tchs, subs]) => {
        setCatalogPrograms(progs)
        setCatalogTeachers(tchs)
        setCatalogSubjects(subs)
      })
      .catch(console.error)
  }, [])

  const addSub = () => {
    setSubjects(s => [...s, { id: Date.now(), code: '', name: '', semester: '1st', entry_type: 'Theory', weekly_hours: '2', teacher_short_name: '', teacher_full_name: '' }])
  }

  const updateSub = (i: number, k: string, v: string) => {
    setSubjects(list => {
      const nw = [...list]
      nw[i] = { ...nw[i], [k]: v }
      return nw
    })
  }

  const removeSub = (i: number) => {
    setSubjects(list => list.filter((_, idx) => idx !== i))
  }

  const [step, setStep] = useState<'form' | 'preview'>('form')
  const [scheduling, setScheduling] = useState(false)
  const [scheduleLog, setScheduleLog] = useState<{ name: string; status: 'ok' | 'fail'; detail: string }[]>([])
  const [initialVersion, setInitialVersion] = useState<{id: number, entries: any[]} | null>(null)

  // Slot candidates tried in order (theory = 2h, practical = 3h)
  const SLOT_CANDIDATES = [
    { day: 'Monday',    start: '10:00', end: '12:00' },
    { day: 'Tuesday',   start: '10:00', end: '12:00' },
    { day: 'Wednesday', start: '10:00', end: '12:00' },
    { day: 'Thursday',  start: '10:00', end: '12:00' },
    { day: 'Friday',    start: '10:00', end: '12:00' },
    { day: 'Monday',    start: '12:00', end: '14:00' },
    { day: 'Tuesday',   start: '12:00', end: '14:00' },
    { day: 'Wednesday', start: '12:00', end: '14:00' },
    { day: 'Thursday',  start: '12:00', end: '14:00' },
    { day: 'Friday',    start: '12:00', end: '14:00' },
    { day: 'Monday',    start: '14:30', end: '16:30' },
    { day: 'Tuesday',   start: '14:30', end: '16:30' },
    { day: 'Wednesday', start: '14:30', end: '16:30' },
    { day: 'Thursday',  start: '14:30', end: '16:30' },
    { day: 'Friday',    start: '14:30', end: '16:30' },
    { day: 'Saturday',  start: '10:00', end: '12:00' },
  ]

  const PRAC_SLOT_CANDIDATES = [
    { day: 'Monday',    start: '14:30', end: '17:30' },
    { day: 'Tuesday',   start: '14:30', end: '17:30' },
    { day: 'Wednesday', start: '14:30', end: '17:30' },
    { day: 'Thursday',  start: '14:30', end: '17:30' },
    { day: 'Friday',    start: '14:30', end: '17:30' },
    { day: 'Monday',    start: '10:00', end: '13:00' },
    { day: 'Tuesday',   start: '10:00', end: '13:00' },
  ]

  const submit = async () => {
    setSaving(true)
    try {
      const payload = {
        program_name: programName,
        semesters: parseInt(semesters) || 8,
        description,
        subjects
      }
      await req('/catalog/bundle', { method: 'POST', body: JSON.stringify(payload) })

      setStep('preview')
      setScheduling(true)
      setScheduleLog([])

      const store = useWorkspaceStore.getState()
      setInitialVersion({ id: store.currentVersionId!, entries: store.entries })
      let versionId = store.currentVersionId

      const log: { name: string; status: 'ok' | 'fail'; detail: string }[] = []

      for (const sub of subjects as any[]) {
        const isPrac = (sub.entry_type || 'Theory') === 'Practical'
        const slots = isPrac ? PRAC_SLOT_CANDIDATES : SLOT_CANDIDATES
        let placed = false

        for (const slot of slots) {
          const action = {
            action: 'ADD_CLASS',
              spec: {
                program: programName,
                semester: sub.semester || '1st',
                day: slot.day,
                start_time: slot.start,
                end_time: slot.end,
                subject_code: sub.code || '',
                subject_name: sub.name || '',
                teacher: sub.teacher_short_name || sub.teacher_full_name || '',
                entry_type: sub.entry_type || 'Theory',
                room: '',
              }
            }
            try {
              const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/)
              const csrf = match ? decodeURIComponent(match[1]) : null
              const res = await fetch('/api/actions/execute', {
                method: 'POST',
                headers: {
                  'Content-Type': 'application/json',
                  ...(csrf ? { 'X-CSRF-Token': csrf } : {}),
                },
                credentials: 'include',
                body: JSON.stringify({ actions: [action], version_id: versionId }),
              })
              const data = await res.json()
              if (data.success) {
                versionId = data.new_version_id || versionId
                if (data.new_version_id) {
                  try {
                    const vres = await fetch(`/api/timetable/versions/${data.new_version_id}`, { credentials: 'include' })
                    const vdata = await vres.json()
                    store.setCurrentVersion(vdata.id, vdata.entries)
                  } catch (_) {}
                }
                log.push({ name: sub.name || sub.code, status: 'ok', detail: `Scheduled: ${slot.day} ${slot.start}–${slot.end}` })
                placed = true
                break
              }
            } catch (_) {}
          }

          if (!placed) {
            log.push({ name: sub.name || sub.code, status: 'fail', detail: 'Could not find a conflict-free slot — add manually' })
          }

          setScheduleLog([...log])
        }

        setScheduling(false)
    } catch (e: unknown) {
      alert((e as Error).message)
    }
    setSaving(false)
  }

  const renderForm = () => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>
      <div style={{ background: 'var(--clr-primary-10)', border: `1px solid var(--clr-primary-50)`, padding: 12, borderRadius: 8, fontSize: '13px', color: 'var(--clr-text-1)' }}>
        <strong>Create a Brand New Option</strong> – Design a new line of study and assign brand new subjects and teachers all at once, without relying on older lists.
      </div>

      <div style={{ background: 'var(--clr-bg-2)', border: '1px solid var(--clr-border)', borderRadius: 'var(--radius)', padding: 'var(--sp-4)' }}>
        <div style={{ fontWeight: 700, marginBottom: 12 }}>1. Program Info</div>
        <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 'var(--sp-3)' }}>
          <div className="form-group">
            <label className="form-label">Program Name</label>
            <input className="form-control" list="existing-programs" placeholder="e.g. M.Tech AI" value={programName} onChange={e => {
                const val = e.target.value;
                setProgramName(val);
                const p = catalogPrograms.find(prog => prog.name === val);
                if (p) setSemesters(p.semesters_count.toString());
            }} />
            <datalist id="existing-programs">
              {catalogPrograms.map(p => <option key={p.id} value={p.name} />)}
              {PROGRAMS.map(p => <option key={p} value={p} />)}
            </datalist>
          </div>
          <div className="form-group">
            <label className="form-label">Semesters</label>
            <input className="form-control" type="number" min={1} value={semesters} onChange={e => setSemesters(e.target.value)} />
          </div>
          <div className="form-group" style={{ gridColumn: '1 / -1' }}>
            <label className="form-label">Description</label>
            <input className="form-control" placeholder="Short description…" value={description} onChange={e => setDescription(e.target.value)} />
          </div>
        </div>
      </div>

      <div style={{ background: 'var(--clr-bg-2)', border: '1px solid var(--clr-border)', borderRadius: 'var(--radius)', padding: 'var(--sp-4)' }}>
        <div style={{ fontWeight: 700, marginBottom: 12 }}>2. Subjects & Teachers</div>
        {subjects.map((sub, i) => (
          <div key={sub.id as number} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-2)', padding: 'var(--sp-3)', border: '1px solid var(--clr-border-soft)', borderRadius: 'var(--radius)', marginBottom: 'var(--sp-3)', position: 'relative' }}>
            <button className="btn-icon" onClick={() => removeSub(i)} style={{ position: 'absolute', top: 8, right: 8, color: 'var(--clr-error)' }}><Trash2 size={13} /></button>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr 1fr 1fr 1fr', gap: 'var(--sp-2)' }}>
              <div className="form-group">
                <label className="form-label">Code</label>
                <input className="form-control" list="existing-subjects-code" placeholder="Code" value={sub.code as string} onChange={e => {
                  const val = e.target.value;
                  const subject = catalogSubjects.find(s => s.code === val);
                  if (subject) {
                     updateSub(i, 'code', val);
                     if (!sub.name) updateSub(i, 'name', subject.name);
                     if (!sub.semester || sub.semester === '1st') updateSub(i, 'semester', subject.semester);
                  } else {
                     updateSub(i, 'code', val);
                  }
                }} />
              </div>
              <div className="form-group">
                <label className="form-label">Subject Name</label>
                <input className="form-control" list="existing-subjects-name" placeholder="Name" value={sub.name as string} onChange={e => updateSub(i, 'name', e.target.value)} />
              </div>
              <div className="form-group"><label className="form-label">Sem ({semesters} max)</label><input className="form-control" placeholder="e.g. 1st" value={sub.semester as string} onChange={e => updateSub(i, 'semester', e.target.value)} /></div>
              <div className="form-group">
                <label className="form-label">Type</label>
                <select className="form-control" value={sub.entry_type as string} onChange={e => updateSub(i, 'entry_type', e.target.value)}>
                   {ENTRY_TYPES.map(t => <option key={t}>{t}</option>)}
                </select>
              </div>
              <div className="form-group"><label className="form-label">Hours</label><input type="number" min={1} className="form-control" value={sub.weekly_hours as string} onChange={e => updateSub(i, 'weekly_hours', e.target.value)} /></div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: 'var(--sp-2)', marginTop: 4 }}>
               <div className="form-group">
                 <label className="form-label">Teacher Short Name</label>
                 <input className="form-control" list="existing-teachers-short" placeholder="e.g. JD" value={sub.teacher_short_name as string} onChange={e => {
                  const val = e.target.value;
                  const teacher = catalogTeachers.find(t => t.short_name === val);
                  if (teacher) {
                    updateSub(i, 'teacher_short_name', val);
                    updateSub(i, 'teacher_full_name', teacher.full_name);
                  } else {
                    updateSub(i, 'teacher_short_name', val);
                  }
                 }} />
               </div>
               <div className="form-group">
                 <label className="form-label">Teacher Full Name (will auto-create account)</label>
                 <input className="form-control" list="existing-teachers-full" placeholder="e.g. Dr. John Doe" value={sub.teacher_full_name as string} onChange={e => updateSub(i, 'teacher_full_name', e.target.value)} />
               </div>
            </div>
          </div>
        ))}
        <button className="btn" onClick={addSub} style={{ fontSize: '11px', marginTop: 4 }}>+ Add Subject to Bundle</button>
      </div>

      <datalist id="existing-subjects-code">
        {catalogSubjects.map(s => <option key={s.id} value={s.code}>{s.name} ({s.program})</option>)}
      </datalist>
      <datalist id="existing-subjects-name">
        {Array.from(new Set(catalogSubjects.map(s => s.name))).map((name, idx) => <option key={idx} value={name} />)}
      </datalist>
      <datalist id="existing-teachers-short">
        {catalogTeachers.map(t => <option key={t.id} value={t.short_name}>{t.full_name}</option>)}
      </datalist>
      <datalist id="existing-teachers-full">
        {catalogTeachers.map(t => <option key={t.id} value={t.full_name} />)}
      </datalist>

      <button className="btn btn-primary" style={{ padding: '12px', fontSize: 'var(--fs-sm)' }} disabled={saving} onClick={() => {
         if (!programName) { alert("Please provide a Program Name in Step 1."); return; }
         if (subjects.length === 0) { alert("Please add at least one subject to the bundle in Step 2."); return; }
         submit();
      }}>
         {saving ? <Loader2 size={16} style={{ animation: 'spin 1s linear infinite' }} /> : '✨ Create Entire Program Structure'}
      </button>
    </div>
  )

  const renderPreview = () => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>
      <div style={{ background: 'var(--clr-bg-2)', border: '1px solid var(--clr-border)', borderRadius: 'var(--radius)', padding: 'var(--sp-4)' }}>
        <div style={{ fontWeight: 700, marginBottom: 10, display: 'flex', alignItems: 'center', gap: 8 }}>
          {scheduling ? (
            <><Loader2 size={14} style={{ animation: 'spin 0.7s linear infinite', color: 'var(--clr-primary)' }} /> Auto-scheduling Preview…</>
          ) : (
            <>✅ Suggested Schedule Preview</>
          )}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {scheduleLog.length === 0 && !scheduling && (
            <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--clr-text-2)' }}>No subjects mapped.</div>
          )}
          {scheduleLog.map((item, i) => (
            <div key={i} style={{
              display: 'flex', alignItems: 'flex-start', gap: 8, fontSize: 'var(--fs-sm)',
              padding: '6px 10px', borderRadius: 6,
              background: item.status === 'ok' ? 'var(--clr-success-bg)' : 'var(--clr-error-bg)',
              border: `1px solid ${item.status === 'ok' ? 'var(--clr-success)' : 'var(--clr-error)'}22`,
            }}>
              <span style={{ fontSize: 14 }}>{item.status === 'ok' ? '✓' : '⚠'}</span>
              <div>
                <span style={{ fontWeight: 600 }}>{item.name}</span>
                <span style={{ color: item.status === 'ok' ? 'var(--clr-text-2)' : 'var(--clr-error)', marginLeft: 6 }}>— {item.detail}</span>
              </div>
            </div>
          ))}
        </div>
        
        {!scheduling && (
          <div style={{ marginTop: 24, display: 'flex', gap: 12 }}>
            <button
              className="btn btn-primary"
              style={{ fontSize: 'var(--fs-sm)' }}
              onClick={() => {
                setProgramName('')
                setSemesters('8')
                setDescription('')
                setSubjects([])
                setStep('form')
                onClose?.()
              }}
            >
              ✓ Yes, Confirm & Apply
            </button>
            <button
              className="btn btn-ghost"
              style={{ fontSize: 'var(--fs-sm)' }}
              onClick={() => {
                if (initialVersion) {
                  useWorkspaceStore.getState().setCurrentVersion(initialVersion.id, initialVersion.entries)
                }
                setStep('form')
              }}
            >
              Cancel & Modify
            </button>
          </div>
        )}
      </div>
    </div>
  )

  return step === 'form' ? renderForm() : renderPreview()
}

// ── Main CatalogManager ────────────────────────────
const TABS = ['Bundle Setup', 'Teachers', 'Subjects', 'Programs'] as const
type Tab = typeof TABS[number]

export default function CatalogManager({ initialTab = 'Bundle Setup', onClose }: { initialTab?: string, onClose?: () => void }) {
  const [tab, setTab] = useState<Tab>(initialTab as Tab)
  return (
    <div>
      <div style={{ display: 'flex', gap: 4, marginBottom: 'var(--sp-4)', borderBottom: '1px solid var(--clr-border)', paddingBottom: 8 }}>
        {TABS.map(t => (
          <button key={t} onClick={() => setTab(t)} style={{
            padding: '6px 16px', borderRadius: 'var(--radius)', border: 'none', cursor: 'pointer', fontWeight: tab === t ? 700 : 400, fontSize: 'var(--fs-sm)',
            background: tab === t ? 'var(--clr-primary)' : 'transparent',
            color: tab === t ? '#fff' : 'var(--clr-text-2)',
          }}>{t}</button>
        ))}
      </div>
      {tab === 'Bundle Setup' && <BundleSetupTab onClose={onClose} />}
      {tab === 'Teachers' && <TeachersTab />}
      {tab === 'Subjects' && <SubjectsTab />}
      {tab === 'Programs' && <ProgramsTab />}
    </div>
  )
}
