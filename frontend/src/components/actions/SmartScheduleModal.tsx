import { useState, useEffect } from 'react'
import { X, Sparkles, Loader2, CheckCircle2, AlertCircle, CalendarClock, Zap, ArrowRight, ChevronRight, AlertTriangle } from 'lucide-react'
import { useWorkspaceStore } from '../../store'
import { getVersion, req } from '../../api'

const SEMS = ['1st', '2nd', '3rd', '4th', '5th', '6th', '7th', '8th']
const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

interface NormalProposal {
  day: string
  start: string
  end: string
  room: string
  proposed_action: object
  constraint_issue?: string | null
}

interface SmartResult {
  found: boolean
  is_perfect_match?: boolean
  proposals?: NormalProposal[]
  message: string
  new_version_id?: number
}

interface ProposedModification {
  description: string
  modification_type: string   // "none" | "move" | "cancel"
  affected_class: Record<string, string>
  new_slot?: Record<string, string>
  priority_slot: Record<string, string>
  actions_to_apply: object[]
}

interface AdvancedResult {
  found: boolean
  proposals: ProposedModification[]
  message: string
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
  
  // Normal mode explicit selection
  const [selectedNormalProposal, setSelectedNormalProposal] = useState<NormalProposal | null>(null)

  const [step, setStep] = useState<'form' | 'preview' | 'advanced_loading' | 'advanced_proposals' | 'confirm' | 'done'>('form')
  const [bookingLoading, setBookingLoading] = useState(false)

  // Advanced Smart Schedule state
  const [advancedResult, setAdvancedResult] = useState<AdvancedResult | null>(null)
  const [selectedProposal, setSelectedProposal] = useState<ProposedModification | null>(null)
  const [applyingProposal, setApplyingProposal] = useState(false)
  
  const [doneMessage, setDoneMessage] = useState('')
  const [doneSuccess, setDoneSuccess] = useState(false)

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

  const setF = (k: string, v: string) => setFields(f => ({ ...f, [k]: v }))
  const teacher = fields.teacher === 'Other…' ? fields.custom_teacher : fields.teacher
  const canFind = fields.program && fields.semester && fields.subject_name && teacher

  async function findSlot() {
    setLoading(true)
    try {
      const data = await req<SmartResult>('/actions/smart-schedule', {
        method: 'POST',
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
      setResult(data)
      if (data.found && data.proposals && data.proposals.length > 0) {
        setSelectedNormalProposal(data.proposals[0])
        setStep('preview')
      } else {
        // Did not even find a fallback option (e.g., hard constraints block everything or teacher is fully booked without any possibility to force it)
        setStep('done')
      }
    } catch (e: unknown) {
      setResult({ found: false, message: (e as Error).message })
      setStep('done')
    } finally {
      setLoading(false)
    }
  }

  async function bookSlot() {
    if (!selectedNormalProposal?.proposed_action) return
    setBookingLoading(true)
    try {
      const data = await req<any>('/actions/execute', {
        method: 'POST',
        body: JSON.stringify({ actions: [selectedNormalProposal.proposed_action], version_id: currentVersionId }),
      })
      if (data.success && data.new_version_id) {
        const v = await getVersion(data.new_version_id)
        setCurrentVersion(v.id, v.entries)
        setDoneSuccess(true)
        setDoneMessage(`Booked! Class added as Version #${data.new_version_id}.`)
      } else {
        setDoneSuccess(false)
        setDoneMessage(data.results?.[0]?.error || 'Booking failed. It may violate a hard conflict constraint.')
      }
      setStep('done')
    } catch (e: unknown) {
      setDoneSuccess(false)
      setDoneMessage((e as Error).message)
      setStep('done')
    } finally {
      setBookingLoading(false)
    }
  }

  async function runAdvancedSchedule() {
    setStep('advanced_loading')
    try {
      const data = await req<AdvancedResult>('/actions/smart-schedule/advanced', {
        method: 'POST',
        body: JSON.stringify({
          program: fields.program,
          semester: fields.semester,
          subject_code: fields.subject_code || fields.subject_name.toLowerCase().replace(/\s+/g, ''),
          subject_name: fields.subject_name,
          teacher,
          entry_type: fields.entry_type,
          room: fields.room || undefined,
          preferred_day: fields.preferred_day || undefined,
        }),
      })
      setAdvancedResult(data)
      setStep(data.found && data.proposals?.length > 0 ? 'advanced_proposals' : 'done')
      if (!data.found || !data.proposals || data.proposals.length === 0) {
        setDoneSuccess(false)
        setDoneMessage(data.message || 'No modifications possible. Please schedule manually.')
      }
    } catch (e: unknown) {
      setDoneSuccess(false)
      setDoneMessage((e as Error).message)
      setStep('done')
    }
  }

  function confirmProposal(proposal: ProposedModification) {
    setSelectedProposal(proposal)
    setStep('confirm')
  }

  async function applyProposal() {
    if (!selectedProposal) return
    setApplyingProposal(true)
    try {
      const data = await req<any>('/actions/execute', {
        method: 'POST',
        body: JSON.stringify({
          actions: selectedProposal.actions_to_apply,
          version_id: currentVersionId,
          partial_ok: false,
        }),
      })
      if (data.success && data.new_version_id) {
        const v = await getVersion(data.new_version_id)
        setCurrentVersion(v.id, v.entries)
        setDoneSuccess(true)
        setDoneMessage(`Changes applied! Timetable updated to Version #${data.new_version_id}.`)
      } else {
        setDoneSuccess(false)
        setDoneMessage(data.results?.find((r: any) => !r.success)?.error || 'Application failed.')
      }
    } catch (e: unknown) {
      setDoneSuccess(false)
      setDoneMessage((e as Error).message)
    } finally {
      setApplyingProposal(false)
      setStep('done')
    }
  }

  const modTypeColor = (type: string) => {
    if (type === 'none') return 'var(--clr-success)'
    if (type === 'move') return 'var(--clr-primary)'
    return 'var(--clr-error)'
  }
  const modTypeLabel = (type: string) => {
    if (type === 'none') return '✓ No Change Needed'
    if (type === 'move') return '↔ Move Class'
    return '✕ Cancel Class'
  }

  const isPerfect = result?.is_perfect_match ?? false

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ maxWidth: 620 }}>
        <div className="modal-header">
          <h2 className="modal-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Sparkles size={18} style={{ color: 'var(--clr-primary)' }} /> Smart Auto-Schedule
          </h2>
          <button className="btn-icon" onClick={onClose}><X size={18} /></button>
        </div>

        {/* ── Step 1: Form ── */}
        {step === 'form' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)' }}>
            <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--clr-text-2)', padding: '8px 12px', background: 'var(--clr-primary-10)', borderRadius: 'var(--radius)', border: '1px solid var(--clr-primary-20)' }}>
              Fill in the class details — the system will automatically find the best available time slots.
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--sp-3)' }}>
              <div className="form-group">
                <label className="form-label">Program</label>
                <select className="form-control" value={fields.program} onChange={e => setF('program', e.target.value)}>
                  <option value="">Select…</option>
                  {dynamicPrograms.map(o => <option key={o}>{o}</option>)}
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Semester</label>
                <select className="form-control" value={fields.semester} onChange={e => setF('semester', e.target.value)}>
                  <option value="">Select…</option>
                  {SEMS.map(o => <option key={o}>{o}</option>)}
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Subject Name</label>
                <input className="form-control" placeholder="e.g. Computer Networks" value={fields.subject_name} onChange={e => setF('subject_name', e.target.value)} />
              </div>
              <div className="form-group">
                <label className="form-label">Subject Code <span style={{ color: 'var(--clr-text-3)', fontWeight: 400 }}>(optional)</span></label>
                <input className="form-control" placeholder="e.g. cn (auto-derived)" value={fields.subject_code} onChange={e => setF('subject_code', e.target.value)} />
              </div>
              <div className="form-group">
                <label className="form-label">Teacher</label>
                <select className="form-control" value={fields.teacher} onChange={e => setF('teacher', e.target.value)}>
                  <option value="">Select…</option>
                  {dynamicTeachers.map(t => <option key={t}>{t}</option>)}
                  <option value="Other…">Other…</option>
                </select>
              </div>
              {fields.teacher === 'Other…' && (
                <div className="form-group">
                  <label className="form-label">Teacher Short Name</label>
                  <input className="form-control" placeholder="e.g. AK" value={fields.custom_teacher} onChange={e => setF('custom_teacher', e.target.value)} />
                </div>
              )}
              <div className="form-group">
                <label className="form-label">Class Type</label>
                <select className="form-control" value={fields.entry_type} onChange={e => setF('entry_type', e.target.value)}>
                  <option>Theory</option>
                  <option>Practical</option>
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Preferred Day <span style={{ color: 'var(--clr-text-3)', fontWeight: 400 }}>(optional)</span></label>
                <select className="form-control" value={fields.preferred_day} onChange={e => setF('preferred_day', e.target.value)}>
                  <option value="">Any Day</option>
                  {DAYS.map(d => <option key={d}>{d}</option>)}
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Room <span style={{ color: 'var(--clr-text-3)', fontWeight: 400 }}>(optional)</span></label>
                <input className="form-control" placeholder="Leave blank to auto-assign" value={fields.room} onChange={e => setF('room', e.target.value)} />
              </div>
            </div>
          </div>
        )}

        {/* ── Step 2: Normal preview (multiple options) ── */}
        {step === 'preview' && result?.found && result.proposals && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {isPerfect ? (
              <div style={{ padding: '10px 14px', background: 'rgba(34, 197, 94, 0.1)', border: '1px solid rgba(34, 197, 94, 0.3)', borderRadius: 'var(--radius)', fontSize: 'var(--fs-sm)', color: 'var(--clr-text-1)' }}>
                <CheckCircle2 size={16} style={{ color: 'var(--clr-success)', display: 'inline', verticalAlign: 'text-bottom', marginRight: 6 }} />
                <strong>Perfect combinations found.</strong> These options have no conflicts and obey all rules.
              </div>
            ) : (
              <div style={{ padding: '10px 14px', background: 'rgba(234, 179, 8, 0.1)', border: '1px solid rgba(234, 179, 8, 0.4)', borderRadius: 'var(--radius)', fontSize: 'var(--fs-sm)', color: 'var(--clr-text-1)' }}>
                <AlertTriangle size={16} style={{ color: 'var(--clr-warning)', display: 'inline', verticalAlign: 'text-bottom', marginRight: 6 }} />
                <strong>No conflict-free slots found.</strong> Displaying the closest feasible alternatives (with soft constraint warnings). Consider Advanced Smart Schedule to optimize further.
              </div>
            )}

            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {result.proposals.map((prop, idx) => (
                <div 
                  key={idx}
                  onClick={() => setSelectedNormalProposal(prop)}
                  style={{ 
                    border: '2px solid',
                    borderColor: selectedNormalProposal === prop ? 'var(--clr-primary)' : 'var(--clr-border)',
                    background: selectedNormalProposal === prop ? 'var(--clr-primary-10)' : 'var(--clr-bg)',
                    borderRadius: 'var(--radius)', cursor: 'pointer', padding: '14px',
                    transition: 'border-color 0.15s, background-color 0.15s'
                  }}
                >
                   <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                     <div>
                       <div style={{ fontSize: 'var(--fs-md)', fontWeight: 600 }}>{prop.day}</div>
                       <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--clr-text-2)' }}>{prop.start}–{prop.end} • Room {prop.room || 'TBD'}</div>
                     </div>
                     {selectedNormalProposal === prop && <CheckCircle2 size={24} style={{ color: 'var(--clr-primary)' }} />}
                   </div>
                   {prop.constraint_issue && (
                     <div style={{ marginTop: 10, fontSize: '11px', color: 'var(--clr-warning)', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 4 }}>
                       <AlertTriangle size={12}/> {prop.constraint_issue}
                     </div>
                   )}
                </div>
              ))}
            </div>

            <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--clr-text-3)', display: 'flex', gap: 6, alignItems: 'center', padding: '0 4px' }}>
              <AlertCircle size={13} /> {isPerfect ? "If you prefer a different time, try Advanced Smart Schedule to securely shift other classes." : "Applying these imperfect slots might violate secondary rules like daily-hour limits."}
            </div>
          </div>
        )}

        {/* ── Step 3: Advanced loading ── */}
        {step === 'advanced_loading' && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 16, padding: 'var(--sp-6)', textAlign: 'center' }}>
            <Loader2 size={36} style={{ animation: 'spin 0.8s linear infinite', color: 'var(--clr-primary)' }} />
            <div style={{ fontWeight: 600 }}>Analyzing Advanced Options…</div>
            <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--clr-text-2)' }}>
              Searching within <strong>{fields.program} {fields.semester}</strong> for optimal multi-step arrangements.
            </div>
          </div>
        )}

        {/* ── Step 4: Advanced proposals ── */}
        {step === 'advanced_proposals' && advancedResult?.proposals && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ padding: '10px 14px', background: 'var(--clr-primary-10)', border: '1px solid var(--clr-primary-20)', borderRadius: 'var(--radius)', fontSize: 'var(--fs-sm)', color: 'var(--clr-text-2)' }}>
              <strong>Advanced Mode Options</strong> — Only modifications to classes running for <strong>{fields.program} {fields.semester}</strong> have been considered. Other programs remain unchanged.
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {advancedResult.proposals.map((proposal, i) => (
                <div key={i} style={{
                  border: `1px solid ${proposal.modification_type === 'cancel' ? 'rgba(220,38,38,0.3)' : 'var(--clr-border)'}`,
                  borderRadius: 'var(--radius)',
                  padding: 14,
                  background: proposal.modification_type === 'cancel' ? 'rgba(220,38,38,0.04)' : 'var(--clr-bg-2)',
                  cursor: 'pointer',
                  transition: 'border-color 0.15s',
                }} onClick={() => confirmProposal(proposal)}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
                    <div style={{ flex: 1 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                        <span style={{ fontSize: '11px', fontWeight: 700, padding: '2px 8px', borderRadius: 20, background: proposal.modification_type === 'cancel' ? 'rgba(220,38,38,0.12)' : 'var(--clr-primary-10)', color: modTypeColor(proposal.modification_type) }}>
                          {modTypeLabel(proposal.modification_type)}
                        </span>
                        <span style={{ fontSize: '10px', color: 'var(--clr-text-3)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                          Option {i + 1}
                        </span>
                      </div>
                      <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--clr-text-1)', lineHeight: 1.55 }}>
                        {proposal.description}
                      </div>
                      <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 6, fontSize: '11px', color: 'var(--clr-success)', fontWeight: 600 }}>
                        <ArrowRight size={12} /> Priority class: <strong>{fields.subject_name}</strong> → {proposal.priority_slot.day} {proposal.priority_slot.start}–{proposal.priority_slot.end} in {proposal.priority_slot.room}
                      </div>
                    </div>
                    <ChevronRight size={18} style={{ color: 'var(--clr-text-3)', flexShrink: 0, marginTop: 4 }} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ── Step 5: Confirmation ── */}
        {step === 'confirm' && selectedProposal && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div style={{ padding: '14px', background: 'rgba(255,165,0,0.07)', border: '1px solid rgba(255,165,0,0.35)', borderRadius: 'var(--radius)' }}>
              <div style={{ fontWeight: 700, marginBottom: 8, fontSize: 'var(--fs-sm)', display: 'flex', alignItems: 'center', gap: 6 }}>
                <AlertCircle size={15} style={{ color: 'orange' }} /> Confirm Changes
              </div>
              <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--clr-text-2)', lineHeight: 1.6 }}>
                {selectedProposal.description}
              </div>
            </div>
            <div style={{ padding: '12px 14px', background: 'var(--clr-success-bg)', border: '1px solid var(--clr-success)', borderRadius: 'var(--radius)', fontSize: 'var(--fs-sm)' }}>
              <strong>Priority class will be placed:</strong><br />
              <span style={{ fontFamily: 'monospace' }}>{fields.subject_name} → {selectedProposal.priority_slot.day} {selectedProposal.priority_slot.start}–{selectedProposal.priority_slot.end} in {selectedProposal.priority_slot.room}</span>
            </div>
            <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--clr-text-3)', display: 'flex', gap: 6, alignItems: 'center' }}>
              <AlertCircle size={11} /> Only classes in <strong>{fields.program} {fields.semester}</strong> will be modified. This cannot be undone automatically — use version history to revert.
            </div>
          </div>
        )}

        {/* ── Step 6: Done ── */}
        {step === 'done' && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12, padding: 'var(--sp-5)', textAlign: 'center' }}>
            {doneSuccess
              ? <><CheckCircle2 size={40} style={{ color: 'var(--clr-success)' }} /><div style={{ fontWeight: 700, fontSize: 'var(--fs-lg)' }}>Done!</div></>
              : <><AlertCircle size={40} style={{ color: result?.found ? 'var(--clr-warning)' : 'var(--clr-error)' }} /><div style={{ fontWeight: 700 }}>{result?.found ? 'No slot available' : 'Could not apply'}</div></>
            }
            <div style={{ color: 'var(--clr-text-2)', fontSize: 'var(--fs-sm)', maxWidth: 360 }}>
              {doneMessage || result?.message || advancedResult?.message}
            </div>
            {!doneSuccess && (
              <button 
                  className="btn btn-ghost" 
                  style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 5, color: 'var(--clr-primary)' }} 
                  onClick={runAdvancedSchedule}
              >
                  <Zap size={14} /> Try Advanced Smart Schedule
              </button>
            )}
          </div>
        )}

        {/* ── Footer buttons ── */}
        <div className="modal-footer">
          {step === 'form' && <>
            <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
            <button className="btn btn-primary" disabled={!canFind || loading} onClick={findSlot}
              style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              {loading ? <><Loader2 size={14} style={{ animation: 'spin 0.7s linear infinite' }} />Finding…</> : <><Sparkles size={14} />Find Slots</>}
            </button>
          </>}

          {step === 'preview' && <>
            <div style={{ display: 'flex', borderRight: '1px solid var(--clr-border)', paddingRight: 14, marginRight: 6 }}>
               <button className="btn btn-ghost" onClick={() => setStep('form')}>← Change</button>
            </div>
            
            <button className="btn btn-ghost" style={{ display: 'flex', alignItems: 'center', gap: 5 }} onClick={runAdvancedSchedule}>
              <Zap size={13} /> {isPerfect ? "Optimize with Advanced" : "Not satisfied? Try Advanced"}
            </button>
            
            <button className="btn btn-primary" disabled={bookingLoading || !selectedNormalProposal} onClick={bookSlot}
              style={{ display: 'flex', alignItems: 'center', gap: 6, opacity: selectedNormalProposal ? 1 : 0.5 }}>
              {bookingLoading ? <><Loader2 size={14} style={{ animation: 'spin 0.7s linear infinite' }} />Booking…</> : '✓ Book Selected Slot'}
            </button>
          </>}

          {step === 'advanced_loading' && (
            <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
          )}

          {step === 'advanced_proposals' && <>
            <button className="btn btn-ghost" onClick={() => setStep('preview')}>← Back</button>
            <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--clr-text-3)', alignSelf: 'center' }}>Click an option to confirm</span>
          </>}

          {step === 'confirm' && <>
            <button className="btn btn-ghost" onClick={() => setStep('advanced_proposals')}>← Back</button>
            <button className="btn btn-primary" disabled={applyingProposal} onClick={applyProposal}
              style={{ display: 'flex', alignItems: 'center', gap: 6, background: 'var(--clr-warning, orange)', borderColor: 'var(--clr-warning, orange)', color: 'black' }}>
              {applyingProposal ? <><Loader2 size={14} style={{ animation: 'spin 0.7s linear infinite' }} />Applying…</> : '⚡ Confirm & Apply Changes'}
            </button>
          </>}

          {step === 'done' && <button className="btn btn-primary" onClick={onClose}>Done</button>}
        </div>
      </div>
    </div>
  )
}
