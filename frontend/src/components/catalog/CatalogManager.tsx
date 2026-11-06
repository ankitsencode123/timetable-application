import { useState, useEffect } from 'react'
import { Plus, Trash2, Loader2, Pencil, Check, X, AlertCircle, Upload, Download, Copy, KeyRound, CheckCircle2 } from 'lucide-react'
import { useWorkspaceStore } from '../../store'
import { req, weeklySmartSchedule, createGeneratorSubject, extractGeneratorFiles } from '../../api'
import type { CreatedAccountInfo } from '../../types'

const PROGRAMS = ['B.Tech', 'M.Tech', 'M.Sc']
const SEMS = ['1st', '2nd', '3rd', '4th', '5th', '6th', '7th', '8th']
const ENTRY_TYPES = ['Theory', 'Practical', 'Both']

// ── Types ──────────────────────────────────────────
interface Teacher { id: number; short_name: string; full_name: string; designation?: string; subjects_csv: string; is_internal: boolean; user_id: number; auto_email?: string; auto_password?: string }
interface Subject { id: number; code: string; name: string; program: string; semester: string; entry_type: string; weekly_hours: number }
interface Program { id: number; name: string; semesters_count: number; description: string; is_active: boolean }

// ── Teacher Tab ────────────────────────────────────
function TeachersTab() {
  const [list, setList] = useState<Teacher[]>([])
  const [subjects, setSubjects] = useState<Subject[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [created, setCreated] = useState<{ email: string; password?: string } | null>(null)
  const [editId, setEditId] = useState<number | null>(null)
  const [editSubjects, setEditSubjects] = useState('')
  const [editError, setEditError] = useState<string | null>(null)
  const [form, setForm] = useState({ short_name: '', full_name: '', designation: '', subjects_csv: '', is_internal: true, email: '' })
  const [formError, setFormError] = useState<string | null>(null)
  const setF = (k: string, v: unknown) => setForm(f => ({ ...f, [k]: v }))

  useEffect(() => {
    Promise.all([
      req<Teacher[]>('/catalog/teachers'),
      req<Subject[]>('/catalog/subjects'),
    ]).then(([ts, ss]) => { setList(ts); setSubjects(ss) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  function validateSubjectCodes(csv: string): string | null {
    if (!csv.trim()) return null
    const knownCodes = new Set(subjects.map(s => s.code.toLowerCase().trim()))
    const entered = csv.split(',').map(c => c.trim().toLowerCase()).filter(Boolean)
    const unknown = entered.filter(c => !knownCodes.has(c))
    if (unknown.length > 0) {
      return `Unknown subject code(s): ${unknown.join(', ')}. Only existing subject codes may be assigned.`
    }
    return null
  }

  async function add() {
    setFormError(null)
    const err = validateSubjectCodes(form.subjects_csv)
    if (err) { setFormError(err); return }
    setSaving(true)
    try {
      const r = await req<Teacher>('/catalog/teachers', { method: 'POST', body: JSON.stringify(form) })
      setList(l => [...l, r])
      if (r.auto_email) setCreated({ email: r.auto_email, password: r.auto_password })
      setForm({ short_name: '', full_name: '', designation: '', subjects_csv: '', is_internal: true, email: '' })
      useWorkspaceStore.getState().loadCatalog()
    } catch (e: unknown) { alert((e as Error).message) }
    setSaving(false)
  }

  async function saveEdit(id: number) {
    setEditError(null)
    const err = validateSubjectCodes(editSubjects)
    if (err) { setEditError(err); return }
    try {
      const r = await req<Teacher>(`/catalog/teachers/${id}`, { method: 'PATCH', body: JSON.stringify({ subjects_csv: editSubjects }) })
      setList(l => l.map(t => t.id === id ? r : t))
      setEditId(null)
      useWorkspaceStore.getState().loadCatalog()
    } catch (e: unknown) { alert((e as Error).message) }
  }

  const subjectCodes = subjects.map(s => s.code)

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
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 'var(--sp-3)' }}>
          {[
            ['short_name', 'Short Name', 'e.g. AK'],
            ['full_name', 'Full Name', 'e.g. Dr. Ankit Kumar'],
            ['designation', 'Designation', 'e.g. Associate Professor'],
            ['email', 'Email (auto if blank)', '']
          ].map(([k, label, ph]) => (
            <div className="form-group" key={k}>
              <label className="form-label">{label}</label>
              <input className="form-control" placeholder={ph} value={(form as Record<string, unknown>)[k] as string} onChange={e => setF(k, e.target.value)} />
            </div>
          ))}
          {/* Subject CSV with validation */}
          <div className="form-group">
            <label className="form-label">Subjects (CSV codes)</label>
            <input
              className="form-control"
              list="tt-subjects-csv"
              placeholder="e.g. cn,dbms"
              value={form.subjects_csv}
              onChange={e => { setF('subjects_csv', e.target.value); setFormError(null) }}
            />
            <datalist id="tt-subjects-csv">
              {subjectCodes.map(code => <option key={code} value={code} />)}
            </datalist>
          </div>
          <div className="form-group">
            <label className="form-label">Type</label>
            <select className="form-control" value={form.is_internal ? 'internal' : 'external'} onChange={e => setF('is_internal', e.target.value === 'internal')}>
              <option value="internal">Internal (permanent faculty)</option>
              <option value="external">External / Visiting Guest Faculty</option>
            </select>
          </div>
        </div>
        {formError && (
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6, color: 'var(--clr-error)', fontSize: 'var(--fs-xs)', marginTop: 8, padding: '8px 10px', background: 'var(--clr-error-bg)', borderRadius: 'var(--radius)', border: '1px solid color-mix(in oklab, var(--clr-error) 25%, transparent)' }}>
            <AlertCircle size={13} style={{ flexShrink: 0, marginTop: 1 }} /> {formError}
          </div>
        )}
        <button className="btn btn-primary" disabled={!form.short_name || !form.full_name || saving} onClick={add} style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 10 }}>
          {saving ? <Loader2 size={14} style={{ animation: 'spin 0.7s linear infinite' }} /> : <Plus size={14} />} Add Teacher
        </button>
      </div>

      {loading ? <div style={{ textAlign: 'center', padding: 24 }}><Loader2 size={20} style={{ animation: 'spin 0.7s linear infinite', color: 'var(--clr-text-3)' }} /></div> : (
        <div style={{ border: '1px solid var(--clr-border)', borderRadius: 'var(--radius)', overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--fs-sm)', minWidth: 540 }}>
            <thead><tr style={{ background: 'var(--clr-bg-3)' }}>
              {['Short Name', 'Full Name', 'Designation', 'Type', 'Subjects', ''].map(h => (
                <th key={h} style={{ padding: '8px 12px', textAlign: 'left', fontWeight: 700, color: 'var(--clr-text-2)', fontSize: '11px', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{h}</th>
              ))}
            </tr></thead>
            <tbody>{list.map(t => (
              <tr key={t.id} style={{ borderTop: '1px solid var(--clr-border-soft)' }}>
                <td style={{ padding: '8px 12px', fontWeight: 700, whiteSpace: 'nowrap' }}>{t.short_name}</td>
                <td style={{ padding: '8px 12px', whiteSpace: 'nowrap', fontWeight: 500 }}>{t.full_name}</td>
                <td style={{ padding: '8px 12px', fontSize: '11px', color: 'var(--clr-text-2)', whiteSpace: 'nowrap' }}>
                  {t.designation || (t.is_internal ? 'Faculty' : 'Guest Faculty')}
                </td>
                <td style={{ padding: '8px 12px' }}>
                  <span style={{
                    padding: '2px 8px',
                    borderRadius: 20,
                    fontSize: '11px',
                    fontWeight: 700,
                    background: t.is_internal ? 'var(--clr-primary-10)' : 'var(--clr-bg-3)',
                    color: t.is_internal ? 'var(--clr-primary)' : 'var(--clr-text-3)',
                    border: t.is_internal ? '1px solid var(--clr-primary-20, rgba(0,0,0,0.1))' : '1px solid var(--clr-border)',
                    whiteSpace: 'nowrap'
                  }}>
                    {t.is_internal ? 'Internal' : 'Guest Faculty'}
                  </span>
                </td>
                <td style={{ padding: '8px 12px', color: 'var(--clr-text-2)', fontFamily: 'monospace', fontSize: '11px', minWidth: 140 }}>
                  {editId === t.id ? (
                    <div>
                      <input
                        list="edit-subjects-datalist"
                        style={{ background: 'var(--clr-bg)', border: '1px solid var(--clr-border)', borderRadius: 4, padding: '2px 6px', fontSize: '11px', width: '100%' }}
                        value={editSubjects}
                        onChange={e => { setEditSubjects(e.target.value); setEditError(null) }}
                      />
                      <datalist id="edit-subjects-datalist">
                        {subjectCodes.map(code => <option key={code} value={code} />)}
                      </datalist>
                      {editError && (
                        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 4, color: 'var(--clr-error)', fontSize: '10px', marginTop: 4 }}>
                          <AlertCircle size={10} style={{ flexShrink: 0, marginTop: 1 }} /> {editError}
                        </div>
                      )}
                    </div>
                  ) : (t.subjects_csv || '—')}
                </td>
                <td style={{ padding: '8px 12px' }}>
                  <div style={{ display: 'flex', gap: 4 }}>
                    {editId === t.id
                      ? <><button className="btn-icon" onClick={() => saveEdit(t.id)}><Check size={13} style={{ color: 'var(--clr-success)' }} /></button>
                          <button className="btn-icon" onClick={() => { setEditId(null); setEditError(null) }}><X size={13} /></button></>
                      : <button className="btn-icon" onClick={() => { setEditId(t.id); setEditSubjects(t.subjects_csv); setEditError(null) }}><Pencil size={13} /></button>}
                  </div>
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
  const [teachers, setTeachers] = useState<Teacher[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [form, setForm] = useState({ code: '', name: '', program: '', semester: '1st', entry_type: 'Theory', teacher_short: '' })
  const setF = (k: string, v: string) => setForm(f => ({ ...f, [k]: v }))

  useEffect(() => {
    Promise.all([
      req<Subject[]>('/catalog/subjects'),
      req<Program[]>('/catalog/programs'),
      req<Teacher[]>('/catalog/teachers'),
    ])
      .then(([subs, progs, ts]) => {
        setList(subs)
        setPrograms(progs)
        setTeachers(ts)
        if (progs.length > 0 && !form.program) setF('program', progs[0].name)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  async function add() {
    setSaving(true)
    try {
      const semNum = parseInt(form.semester.replace(/\D/g, '')) || 1
      await createGeneratorSubject({
        code: form.code.trim(),
        name: form.name.trim(),
        program: form.program || (programs[0]?.name || 'B.Tech'),
        semester: semNum,
        class_type: form.entry_type.toLowerCase(),
        weekly_hours: 2,
        teacher_short_name: form.teacher_short || undefined,
      })
      const [subs, ts] = await Promise.all([
        req<Subject[]>('/catalog/subjects'),
        req<Teacher[]>('/catalog/teachers'),
      ])
      setList(subs)
      setTeachers(ts)
      setForm({ code: '', name: '', program: form.program || 'B.Tech', semester: '1st', entry_type: 'Theory', teacher_short: '' })
      useWorkspaceStore.getState().loadCatalog()
    } catch (e: unknown) { alert((e as Error).message) }
    setSaving(false)
  }

  async function remove(id: number) {
    if (!confirm('Remove this subject?')) return
    await req(`/catalog/subjects/${id}`, { method: 'DELETE' })
    setList(l => l.filter(s => s.id !== id))
    useWorkspaceStore.getState().loadCatalog()
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
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 'var(--sp-3)' }}>
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
            <label className="form-label">Assign Faculty</label>
            <select className="form-control" value={form.teacher_short} onChange={e => setF('teacher_short', e.target.value)}>
              <option value="">None / Unassigned</option>
              {teachers.map(t => (
                <option key={t.id} value={t.short_name}>
                  {t.short_name} — {t.full_name}
                </option>
              ))}
            </select>
          </div>
        </div>
        <button className="btn btn-primary" disabled={!form.code || !form.name || saving} onClick={add} style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 10 }}>
          {saving ? <Loader2 size={14} style={{ animation: 'spin 0.7s linear infinite' }} /> : <Plus size={14} />} Add Subject
        </button>
      </div>

      {loading ? <div style={{ textAlign: 'center', padding: 24 }}><Loader2 size={20} style={{ animation: 'spin 0.7s linear infinite', color: 'var(--clr-text-3)' }} /></div>
        : Object.entries(grouped).map(([group, subjects]) => (
          <div key={group}>
            <div style={{ fontSize: '11px', fontWeight: 700, color: 'var(--clr-text-3)', textTransform: 'uppercase', marginBottom: 6 }}>{group}</div>
            <div style={{ border: '1px solid var(--clr-border)', borderRadius: 'var(--radius)', overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--fs-sm)', minWidth: 400 }}>
                <tbody>{subjects.map(s => (
                  <tr key={s.id} style={{ borderTop: '1px solid var(--clr-border-soft)' }}>
                    <td style={{ padding: '7px 12px', fontFamily: 'monospace', fontWeight: 700, width: 80, color: 'var(--clr-primary)', whiteSpace: 'nowrap' }}>{s.code}</td>
                    <td style={{ padding: '7px 12px' }}>{s.name}</td>
                    <td style={{ padding: '7px 12px', color: 'var(--clr-text-3)', whiteSpace: 'nowrap' }}>{s.entry_type}</td>
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
      useWorkspaceStore.getState().loadCatalog()
    } catch (e: unknown) { alert((e as Error).message) }
    setSaving(false)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>
      <div style={{ background: 'var(--clr-bg-2)', border: '1px solid var(--clr-border)', borderRadius: 'var(--radius)', padding: 'var(--sp-4)' }}>
        <div style={{ fontWeight: 700, marginBottom: 12 }}>+ New Program</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 'var(--sp-3)' }}>
          <div className="form-group" style={{ gridColumn: 'span 2' }}>
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
        <button className="btn btn-primary" disabled={!form.name || saving} onClick={add} style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 10 }}>
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
              <div key={p.id} style={{ background: 'var(--clr-bg-2)', border: '1px solid var(--clr-border)', borderRadius: 'var(--radius)', padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                <div style={{ flex: 1, minWidth: 120 }}>
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
  const [applying, setApplying] = useState(false)
  const [pendingActions, setPendingActions] = useState<any[]>([])
  const [scheduleLog, setScheduleLog] = useState<{ name: string; status: 'ok' | 'fail'; detail: string }[]>([])

  const submit = async () => {
    setSaving(true)
    try {
      const cleanSubjects = subjects.map(s => {
        const nameClean = (s.name as string || '').trim()
        const codeClean = (s.code as string || '').trim() || nameClean.replace(/\s+/g, '').slice(0, 6).toUpperCase()
        return {
          ...s,
          name: nameClean,
          code: codeClean,
          teacher_short_name: (s.teacher_short_name as string || '').trim(),
          teacher_full_name: (s.teacher_full_name as string || '').trim(),
          semester: (s.semester as string || '1st').trim(),
          weekly_hours: parseFloat(s.weekly_hours as string) || 2,
          entry_type: (s.entry_type as string || 'Theory'),
        }
      })

      const payload = {
        program_name: programName.trim(),
        semesters: parseInt(semesters) || 8,
        description: description.trim(),
        subjects: cleanSubjects
      }
      await req('/catalog/bundle', { method: 'POST', body: JSON.stringify(payload) })
      useWorkspaceStore.getState().loadCatalog()

      setStep('preview')
      setScheduling(true)
      setScheduleLog([])
      setPendingActions([])

      const store = useWorkspaceStore.getState()
      let currentSchedule = [...(store.entries || [])]
      const allActionsToApply: any[] = []
      const log: { name: string; status: 'ok' | 'fail'; detail: string }[] = []

      for (const sub of cleanSubjects) {
        const teacher = sub.teacher_short_name || sub.teacher_full_name
        try {
          const res = await weeklySmartSchedule({
            program: programName.trim(),
            semester: sub.semester,
            subject_code: sub.code,
            subject_name: sub.name,
            teacher: teacher,
            entry_type: sub.entry_type as 'Theory' | 'Practical',
            weekly_hours: sub.weekly_hours,
            selected_days: ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'],
            day_rule: 'at_least',
            day_count: 1,
            outside_days: 'allow',
            include_advanced: true,
            allow_cancel: false,
            entries: currentSchedule,
            version_id: store.currentVersionId ?? undefined,
          })

          if (res.found && res.proposals && res.proposals.length > 0) {
            const proposal = res.proposals[0]
            allActionsToApply.push(...proposal.actions_to_apply)

            for (const s of proposal.sessions) {
              currentSchedule.push({
                program: programName.trim(),
                semester: sub.semester,
                day: s.day,
                start: s.start,
                end: s.end,
                subject_code: sub.code,
                subject_name: sub.name,
                teacher: teacher,
                type: (sub.entry_type === 'Practical' ? 'Practical' : 'Theory') as 'Theory' | 'Practical',
                room: s.room || '',
              })
            }

            const sessionSummary = proposal.sessions.map(s => `${s.day} ${s.start}–${s.end}`).join(', ')
            const hoursLabel = proposal.total_minutes ? `${proposal.total_minutes / 60}h` : `${sub.weekly_hours}h`
            log.push({
              name: sub.name,
              status: 'ok',
              detail: `Scheduled: ${sessionSummary} (${hoursLabel})`,
            })
          } else {
            log.push({
              name: sub.name,
              status: 'fail',
              detail: res.message || 'Could not find conflict-free weekly slots — add manually',
            })
          }
        } catch (err: any) {
          log.push({
            name: sub.name,
            status: 'fail',
            detail: err?.message || 'Failed to auto-schedule',
          })
        }
        setScheduleLog([...log])
      }

      setPendingActions(allActionsToApply)
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
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 'var(--sp-3)' }}>
          <div className="form-group" style={{ gridColumn: 'span 2' }}>
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
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 'var(--sp-2)' }}>
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
              <div className="form-group" style={{ gridColumn: 'span 2' }}>
                <label className="form-label">Subject Name</label>
                <input className="form-control" list="existing-subjects-name" placeholder="Name" value={sub.name as string} onChange={e => {
                  const val = e.target.value;
                  updateSub(i, 'name', val);
                  const matched = catalogSubjects.find(s => s.name.toLowerCase() === val.toLowerCase());
                  if (matched) {
                    if (!sub.code) updateSub(i, 'code', matched.code);
                    if (!sub.semester || sub.semester === '1st') updateSub(i, 'semester', matched.semester);
                  }
                }} />
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
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 'var(--sp-2)', marginTop: 4 }}>
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
         if (!programName.trim()) { alert("Please provide a Program Name in Step 1."); return; }
         if (subjects.length === 0) { alert("Please add at least one subject to the bundle in Step 2."); return; }
         for (let idx = 0; idx < subjects.length; idx++) {
           const s = subjects[idx];
           const num = idx + 1;
           const sName = (s.name as string || '').trim();
           const sCode = (s.code as string || '').trim();
           const sTeacher = (s.teacher_short_name as string || s.teacher_full_name as string || '').trim();
           const sHours = parseFloat(s.weekly_hours as string) || 0;
           if (!sName && !sCode) {
             alert(`Subject #${num} is empty. Please enter a Subject Name and Code, or click the trash icon to remove it.`);
             return;
           }
           if (!sName) {
             alert(`Subject #${num} (Code: ${sCode}) is missing a Subject Name. Ghost subjects without a name cannot be scheduled.`);
             return;
           }
           if (!sCode) {
             alert(`Subject #${num} ("${sName}") is missing a Subject Code. Please provide a short code (e.g. ${sName.slice(0, 4).toUpperCase()}).`);
             return;
           }
           if (!sTeacher) {
             alert(`Subject #${num} ("${sName}") has no assigned Teacher. Please select or enter a Teacher.`);
             return;
           }
           if (sHours <= 0) {
             alert(`Subject #${num} ("${sName}") must have at least 1 weekly hour.`);
             return;
           }
         }
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
            <><Loader2 size={14} style={{ animation: 'spin 0.7s linear infinite', color: 'var(--clr-primary)' }} /> Auto-scheduling via Weekly Planner…</>
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
          <div style={{ marginTop: 24, display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <button
              className="btn btn-primary"
              style={{ fontSize: 'var(--fs-sm)' }}
              disabled={applying || pendingActions.length === 0}
              onClick={async () => {
                if (pendingActions.length > 0) {
                  setApplying(true)
                  try {
                    const store = useWorkspaceStore.getState()
                    const data = await req<any>('/actions/execute', {
                      method: 'POST',
                      body: JSON.stringify({ actions: pendingActions, version_id: store.currentVersionId }),
                    })
                    if (data.success && data.new_version_id) {
                      const vdata = await req<any>(`/versions/${data.new_version_id}`)
                      store.setCurrentVersion(vdata.id, vdata.entries)
                    }
                  } catch (e: any) {
                    alert(e.message || 'Failed to apply schedule')
                  } finally {
                    setApplying(false)
                  }
                }
                setProgramName('')
                setSemesters('8')
                setDescription('')
                setSubjects([])
                setStep('form')
                onClose?.()
              }}
            >
              {applying ? <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} /> : '✓ Yes, Confirm & Apply'}
            </button>
            <button
              className="btn btn-ghost"
              style={{ fontSize: 'var(--fs-sm)' }}
              disabled={applying}
              onClick={() => {
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

// ── Import CSV Tab ─────────────────────────────────
const DEFAULT_SUBJECTS_TEMPLATE = `Subject Code,Subject Name,Semester,Internal Teacher ID,External Teacher ID,Class Type,Sessions Per Week,Facility
CS301,Data Structures,B.Tech 3rd,I01,E01,Theory,2.5,none
CS302,Database Systems,B.Tech 3rd,I02,E02,Theory,3,none
MA301,Discrete Mathematics,B.Tech 3rd,I03,,Theory,2.5,blackboard
CS351,Data Structures Lab,B.Tech 3rd,I01,,Practical,1,none
CS601,Advanced Algorithms,M.Tech 1st,I01,E01,Theory,2.5,none
CS651,Algorithms Lab,M.Tech 1st,I03,,Practical,2,none
`

const DEFAULT_TEACHERS_TEMPLATE = `Teacher ID,Name,Pref 1 Day,Pref 1 Time,Pref 1 Facility,Pref 2 Day,Pref 2 Time,Pref 2 Facility,Pref 3 Day,Pref 3 Time,Pref 3 Facility
I01,Dr. A. Rao,Mon,10-12:00,blackboard and projector,Wed,12-2,projector,Fri,10-12:00,black board
I02,Dr. B. Iyer,Tue,10-12:00,black board,Thu,2:30-4:30,blackboard and projector,Mon,12-2,projector
I03,Prof. C. Das,Mon,10-12:00,black board,Tue,12-2,black board,Thu,10-12:00,black board
`

function ImportCSVTab() {
  const [subjectsCsv, setSubjectsCsv] = useState('')
  const [teachersCsv, setTeachersCsv] = useState('')
  const [subjectsFileName, setSubjectsFileName] = useState('')
  const [teachersFileName, setTeachersFileName] = useState('')
  const [importing, setImporting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [accounts, setAccounts] = useState<CreatedAccountInfo[]>([])
  const [copied, setCopied] = useState(false)

  function handleFileRead(file: File, type: 'subjects' | 'teachers') {
    const reader = new FileReader()
    reader.onload = e => {
      const text = (e.target?.result as string) || ''
      if (type === 'subjects') {
        setSubjectsCsv(text)
        setSubjectsFileName(file.name)
      } else {
        setTeachersCsv(text)
        setTeachersFileName(file.name)
      }
    }
    reader.readAsText(file)
  }

  function downloadTemplate(filename: string, content: string) {
    const blob = new Blob([content], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
  }

  async function handleImport() {
    if (!subjectsCsv.trim() && !teachersCsv.trim()) {
      setError('Please provide at least one CSV file (Subjects or Teachers).')
      return
    }
    setImporting(true)
    setError(null)
    setSuccess(null)
    try {
      const res = await extractGeneratorFiles({
        subjects_file_content: subjectsCsv || undefined,
        teachers_file_content: teachersCsv || undefined,
      })
      if (res.created_accounts && res.created_accounts.length > 0) {
        setAccounts(res.created_accounts)
      }
      setSuccess(
        `Import Successful! Added ${res.summary.semesters_count} semesters, ${res.summary.subjects_count} subjects, and ${res.summary.teachers_count} teachers. Generated ${res.created_accounts?.length || 0} teacher user accounts.`
      )
      useWorkspaceStore.getState().loadCatalog()
    } catch (err: any) {
      setError(err.message || String(err))
    } finally {
      setImporting(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>
      {/* Informational Header */}
      <div style={{ background: 'var(--clr-bg-2)', border: '1px solid var(--clr-border)', borderRadius: 'var(--radius)', padding: 'var(--sp-4)' }}>
        <div style={{ fontWeight: 700, fontSize: 'var(--fs-md)', marginBottom: 4 }}>
          CSV Curriculum & Faculty Ingestion
        </div>
        <p style={{ margin: 0, fontSize: 'var(--fs-sm)', color: 'var(--clr-text-2)' }}>
          Upload or paste CSV files containing subject allocations and teacher preferences. Subjects will automatically be linked to their semesters, teachers will be added to the catalog, and <strong>institutional login accounts (email & password)</strong> will automatically be created.
        </p>
      </div>

      {error && (
        <div style={{ background: 'rgba(239, 68, 68, 0.1)', border: '1px solid var(--clr-error)', borderRadius: 'var(--radius)', padding: '10px 14px', color: 'var(--clr-error)', fontSize: 'var(--fs-sm)', display: 'flex', alignItems: 'center', gap: 8 }}>
          <AlertCircle size={16} />
          <div style={{ flex: 1 }}>{error}</div>
          <button className="btn-icon" onClick={() => setError(null)}><X size={12} /></button>
        </div>
      )}

      {success && (
        <div style={{ background: 'rgba(16, 185, 129, 0.1)', border: '1px solid #10b981', borderRadius: 'var(--radius)', padding: '10px 14px', color: '#065f46', fontSize: 'var(--fs-sm)', display: 'flex', alignItems: 'center', gap: 8 }}>
          <CheckCircle2 size={16} style={{ color: '#10b981' }} />
          <div style={{ flex: 1 }}>{success}</div>
        </div>
      )}

      {/* Inputs Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 'var(--sp-4)' }}>
        {/* Subjects CSV */}
        <div style={{ background: 'var(--clr-bg-2)', border: '1px solid var(--clr-border)', borderRadius: 'var(--radius)', padding: 'var(--sp-3)', display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontWeight: 600, fontSize: 'var(--fs-sm)' }}>1. Subjects & Allocations CSV</span>
            <button
              className="btn btn-ghost btn-sm"
              style={{ fontSize: 11 }}
              onClick={() => downloadTemplate('subjects_template.csv', DEFAULT_SUBJECTS_TEMPLATE)}
            >
              <Download size={11} style={{ marginRight: 4 }} /> Sample CSV
            </button>
          </div>
          <label className="btn btn-sm btn-ghost" style={{ border: '1px dashed var(--clr-border)', textAlign: 'center', cursor: 'pointer', padding: 8 }}>
            <Upload size={13} style={{ marginRight: 6 }} />
            {subjectsFileName || 'Choose or Drag CSV File'}
            <input type="file" accept=".csv,.txt" style={{ display: 'none' }} onChange={e => { const f = e.target.files?.[0]; if (f) handleFileRead(f, 'subjects') }} />
          </label>
          <textarea
            className="form-control"
            rows={7}
            style={{ fontFamily: 'monospace', fontSize: 11, width: '100%' }}
            placeholder={DEFAULT_SUBJECTS_TEMPLATE}
            value={subjectsCsv}
            onChange={e => setSubjectsCsv(e.target.value)}
          />
        </div>

        {/* Teachers CSV */}
        <div style={{ background: 'var(--clr-bg-2)', border: '1px solid var(--clr-border)', borderRadius: 'var(--radius)', padding: 'var(--sp-3)', display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontWeight: 600, fontSize: 'var(--fs-sm)' }}>2. Teachers & Preferences CSV</span>
            <button
              className="btn btn-ghost btn-sm"
              style={{ fontSize: 11 }}
              onClick={() => downloadTemplate('teachers_template.csv', DEFAULT_TEACHERS_TEMPLATE)}
            >
              <Download size={11} style={{ marginRight: 4 }} /> Sample CSV
            </button>
          </div>
          <label className="btn btn-sm btn-ghost" style={{ border: '1px dashed var(--clr-border)', textAlign: 'center', cursor: 'pointer', padding: 8 }}>
            <Upload size={13} style={{ marginRight: 6 }} />
            {teachersFileName || 'Choose or Drag CSV File'}
            <input type="file" accept=".csv,.txt" style={{ display: 'none' }} onChange={e => { const f = e.target.files?.[0]; if (f) handleFileRead(f, 'teachers') }} />
          </label>
          <textarea
            className="form-control"
            rows={7}
            style={{ fontFamily: 'monospace', fontSize: 11, width: '100%' }}
            placeholder={DEFAULT_TEACHERS_TEMPLATE}
            value={teachersCsv}
            onChange={e => setTeachersCsv(e.target.value)}
          />
        </div>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
        <button
          className="btn btn-primary"
          disabled={importing || (!subjectsCsv.trim() && !teachersCsv.trim())}
          onClick={handleImport}
          style={{ display: 'flex', alignItems: 'center', gap: 6 }}
        >
          {importing ? <Loader2 size={14} style={{ animation: 'spin 0.7s linear infinite' }} /> : <Upload size={14} />}
          {importing ? 'Processing & Registering…' : 'Import to Catalog & Generate Credentials'}
        </button>
      </div>

      {/* Generated Accounts Table */}
      {accounts.length > 0 && (
        <div style={{ background: 'var(--clr-bg-2)', border: '1px solid var(--clr-border)', borderRadius: 'var(--radius)', padding: 'var(--sp-4)', display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <KeyRound size={18} style={{ color: 'var(--clr-primary)' }} />
              <strong style={{ fontSize: 'var(--fs-md)' }}>Generated Faculty Credentials ({accounts.length})</strong>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => {
                  const csv = ['Teacher Name,Short Name,Email,Password', ...accounts.map(a => `"${a.full_name}","${a.short_name}","${a.email}","${a.password || ''}"`)].join('\n')
                  downloadTemplate(`teacher_accounts_${Date.now()}.csv`, csv)
                }}
              >
                <Download size={12} style={{ marginRight: 4 }} /> Export CSV
              </button>
              <button
                className="btn btn-primary btn-sm"
                onClick={() => {
                  const text = accounts.map(a => `Teacher: ${a.full_name} (${a.short_name})\nEmail: ${a.email}\nPassword: ${a.password || ''}\nRole: TEACHER\n`).join('\n')
                  navigator.clipboard.writeText(text)
                  setCopied(true)
                  setTimeout(() => setCopied(false), 2000)
                }}
              >
                <Copy size={12} style={{ marginRight: 4 }} /> {copied ? 'Copied!' : 'Copy All'}
              </button>
            </div>
          </div>

          <div style={{ border: '1px solid var(--clr-border)', borderRadius: 'var(--radius)', overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--fs-sm)' }}>
              <thead>
                <tr style={{ background: 'var(--clr-bg-3)', borderBottom: '1px solid var(--clr-border)', textAlign: 'left' }}>
                  <th style={{ padding: '8px 12px' }}>Faculty Name</th>
                  <th style={{ padding: '8px 12px' }}>Short Form</th>
                  <th style={{ padding: '8px 12px' }}>Institutional Email</th>
                  <th style={{ padding: '8px 12px' }}>Login Password</th>
                </tr>
              </thead>
              <tbody>
                {accounts.map((acc, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid var(--clr-border-soft)' }}>
                    <td style={{ padding: '8px 12px', fontWeight: 600 }}>{acc.full_name}</td>
                    <td style={{ padding: '8px 12px', fontFamily: 'monospace', color: 'var(--clr-primary)', fontWeight: 700 }}>{acc.short_name}</td>
                    <td style={{ padding: '8px 12px', fontFamily: 'monospace' }}>{acc.email}</td>
                    <td style={{ padding: '8px 12px', fontFamily: 'monospace', fontWeight: 700 }}>
                      <code style={{ background: 'rgba(0,0,0,0.06)', padding: '2px 6px', borderRadius: 4 }}>{acc.password || '••••••••'}</code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Main CatalogManager ────────────────────────────
const TABS = ['Bundle Setup', 'Teachers', 'Subjects', 'Programs', 'Import CSV'] as const
type Tab = typeof TABS[number]

export default function CatalogManager({ initialTab = 'Bundle Setup', onClose }: { initialTab?: string, onClose?: () => void }) {
  const [tab, setTab] = useState<Tab>(initialTab as Tab)
  return (
    <div>
      <div style={{ display: 'flex', gap: 4, marginBottom: 'var(--sp-4)', borderBottom: '1px solid var(--clr-border)', paddingBottom: 8, overflowX: 'auto', WebkitOverflowScrolling: 'touch', flexWrap: 'nowrap' }}>
        {TABS.map(t => (
          <button key={t} onClick={() => setTab(t)} style={{
            padding: '6px 14px', borderRadius: 'var(--radius)', border: 'none', cursor: 'pointer', fontWeight: tab === t ? 700 : 400, fontSize: 'var(--fs-sm)',
            background: tab === t ? 'var(--clr-primary)' : 'transparent',
            color: tab === t ? '#fff' : 'var(--clr-text-2)',
            whiteSpace: 'nowrap', flexShrink: 0,
          }}>{t}</button>
        ))}
      </div>
      {tab === 'Bundle Setup' && <BundleSetupTab onClose={onClose} />}
      {tab === 'Teachers' && <TeachersTab />}
      {tab === 'Subjects' && <SubjectsTab />}
      {tab === 'Programs' && <ProgramsTab />}
      {tab === 'Import CSV' && <ImportCSVTab />}
    </div>
  )
}
