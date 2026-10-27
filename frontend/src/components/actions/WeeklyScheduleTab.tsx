import { useState, useEffect, useMemo } from 'react'
import {
  Sparkles, Loader2, CheckCircle2, AlertCircle, AlertTriangle,
  ArrowRight, Calendar, Clock, ChevronRight, Layers, Sliders, Check
} from 'lucide-react'
import { useWorkspaceStore } from '../../store'
import SuggestInput, { SuggestOption } from '../common/SuggestInput'
import {
  getVersion, req, weeklySmartSchedule,
  WeeklyScheduleRequest, WeeklyScheduleResponse, WeeklyProposalItem, DayReportItem
} from '../../api'

const SEMS = ['1st', '2nd', '3rd', '4th', '5th', '6th', '7th', '8th']
const ALL_DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
const QUICK_HOURS = [3, 4, 4.5, 6]

interface Props {
  dynamicPrograms: string[]
  dynamicTeachers: string[]
  onClose: () => void
}

export default function WeeklyScheduleTab({ dynamicPrograms, dynamicTeachers, onClose }: Props) {
  const { currentVersionId, setCurrentVersion, catalogSubjects, loadCatalog } = useWorkspaceStore()

  // Form State
  const [program, setProgram] = useState('')
  const [semester, setSemester] = useState('')
  const [subjectName, setSubjectName] = useState('')
  const [subjectCode, setSubjectCode] = useState('')
  const [teacher, setTeacher] = useState('')
  const [customTeacher, setCustomTeacher] = useState('')
  const [entryType, setEntryType] = useState<'Theory' | 'Practical'>('Theory')
  const [room, setRoom] = useState('')
  const [weeklyHours, setWeeklyHours] = useState<number>(6)
  const [selectedDays, setSelectedDays] = useState<string[]>(['Tuesday', 'Wednesday', 'Thursday'])
  const [dayRule, setDayRule] = useState<'exactly' | 'at_least' | 'at_most'>('exactly')
  const [dayCount, setDayCount] = useState<number>(3)
  const [outsideDays, setOutsideDays] = useState<'auto' | 'allow' | 'deny'>('auto')
  const [includeAdvanced, setIncludeAdvanced] = useState(true)
  const [allowCancel, setAllowCancel] = useState(false)
  const [showAdvancedOptions, setShowAdvancedOptions] = useState(false)

  // Execution & View State
  const [step, setStep] = useState<'form' | 'loading' | 'proposals' | 'confirm' | 'done'>('form')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<WeeklyScheduleResponse | null>(null)
  const [selectedProposal, setSelectedProposal] = useState<WeeklyProposalItem | null>(null)
  const [applying, setApplying] = useState(false)
  const [doneMessage, setDoneMessage] = useState('')
  const [doneSuccess, setDoneSuccess] = useState(false)

  useEffect(() => {
    if (!catalogSubjects.length) {
      loadCatalog()
    }
  }, [catalogSubjects.length, loadCatalog])

  const subjectNameOptions = useMemo((): SuggestOption[] => {
    let subs = catalogSubjects
    if (program) subs = subs.filter(s => s.program.toLowerCase() === program.toLowerCase())
    if (semester) subs = subs.filter(s => s.semester.toLowerCase() === semester.toLowerCase())
    const seen = new Set<string>()
    return subs.flatMap(s => {
      const k = s.name.trim().toLowerCase()
      if (seen.has(k)) return []
      seen.add(k)
      return [{ value: s.name, label: s.code }]
    })
  }, [catalogSubjects, program, semester])

  const subjectCodeOptions = useMemo((): SuggestOption[] => {
    let subs = catalogSubjects
    if (program) subs = subs.filter(s => s.program.toLowerCase() === program.toLowerCase())
    if (semester) subs = subs.filter(s => s.semester.toLowerCase() === semester.toLowerCase())
    const seen = new Set<string>()
    return subs.flatMap(s => {
      const k = s.code.trim().toLowerCase()
      if (seen.has(k)) return []
      seen.add(k)
      return [{ value: s.code, label: s.name }]
    })
  }, [catalogSubjects, program, semester])

  const effectiveTeacher = teacher === 'Other…' ? customTeacher : teacher
  const canSubmit = program && semester && subjectName && effectiveTeacher && weeklyHours > 0 && selectedDays.length > 0

  function toggleDay(d: string) {
    setSelectedDays(prev => {
      const next = prev.includes(d) ? prev.filter(x => x !== d) : [...prev, d]
      if (dayRule === 'exactly') {
        setDayCount(next.length || 1)
      }
      return next
    })
  }

  async function handleSearch() {
    if (!canSubmit) return
    setStep('loading')
    setLoading(true)
    try {
      const payload: WeeklyScheduleRequest = {
        program,
        semester,
        subject_name: subjectName,
        subject_code: subjectCode || subjectName.toLowerCase().replace(/\s+/g, ''),
        teacher: effectiveTeacher,
        entry_type: entryType,
        room: room || undefined,
        weekly_hours: weeklyHours,
        selected_days: selectedDays,
        day_rule: dayRule,
        day_count: dayCount,
        outside_days: outsideDays,
        include_advanced: includeAdvanced,
        allow_cancel: allowCancel,
        max_proposals: 6,
      }
      const data = await weeklySmartSchedule(payload)
      setResult(data)
      if (data.proposals && data.proposals.length > 0) {
        setSelectedProposal(data.proposals[0])
        setStep('proposals')
      } else {
        setDoneSuccess(false)
        setDoneMessage(data.message || 'No feasible schedule could be found for this request.')
        setStep('done')
      }
    } catch (e: unknown) {
      setDoneSuccess(false)
      setDoneMessage((e as Error).message || 'Failed to generate weekly schedule')
      setStep('done')
    } finally {
      setLoading(false)
    }
  }

  async function handleApplyProposal() {
    if (!selectedProposal) return
    setApplying(true)
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
        setDoneMessage(`Weekly schedule applied! Routine updated to Version #${data.new_version_id} with ${selectedProposal.sessions.length} class slots.`)
      } else {
        setDoneSuccess(false)
        const errDetail = data.results?.find((r: any) => !r.success)?.error || data.violations?.[0]?.note || 'Application failed.'
        setDoneMessage(errDetail)
      }
    } catch (e: unknown) {
      setDoneSuccess(false)
      setDoneMessage((e as Error).message || 'Execution error')
    } finally {
      setApplying(false)
      setStep('done')
    }
  }

  const categoryBadge = (cat: WeeklyProposalItem['category']) => {
    switch (cat) {
      case 'exact':
        return { label: '✓ Exact Match', bg: 'rgba(34, 197, 94, 0.12)', color: 'var(--clr-success)' }
      case 'substitute':
        return { label: '⇄ Conflict-Free Substitution', bg: 'rgba(59, 130, 246, 0.12)', color: '#3b82f6' }
      case 'advanced':
        return { label: '⚡ Advanced Rearrangement', bg: 'rgba(245, 158, 11, 0.12)', color: '#f59e0b' }
      case 'partial':
        return { label: '⚠ Partial Schedule', bg: 'rgba(168, 85, 247, 0.12)', color: '#a855f7' }
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* ── Form View ── */}
      {step === 'form' && (
        <>
          <div style={{
            fontSize: 'var(--fs-sm)',
            color: 'var(--clr-text-2)',
            padding: '10px 14px',
            background: 'var(--clr-primary-10)',
            borderRadius: 'var(--radius)',
            border: '1px solid var(--clr-primary-20)',
            display: 'flex',
            alignItems: 'center',
            gap: 8
          }}>
            <Calendar size={16} style={{ color: 'var(--clr-primary)', flexShrink: 0 }} />
            <span>
              Plan a full weekly routine by choosing total hours and preferred teaching days. The scheduler resolves teacher constraints, theory limits, and classroom availability.
            </span>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--sp-3)' }}>
            <div className="form-group">
              <label className="form-label">Program *</label>
              <select className="form-control" value={program} onChange={e => setProgram(e.target.value)}>
                <option value="">Select program…</option>
                {dynamicPrograms.map(o => <option key={o}>{o}</option>)}
              </select>
            </div>

            <div className="form-group">
              <label className="form-label">Semester *</label>
              <select className="form-control" value={semester} onChange={e => setSemester(e.target.value)}>
                <option value="">Select semester…</option>
                {SEMS.map(o => <option key={o}>{o}</option>)}
              </select>
            </div>

            <div className="form-group">
              <label className="form-label">Subject Name *</label>
              <SuggestInput
                id="weekly-subject-name"
                placeholder="e.g. Data Structures"
                value={subjectName}
                onChange={setSubjectName}
                options={subjectNameOptions}
                onPick={o => {
                  setSubjectName(o.value)
                  if (o.label) setSubjectCode(o.label)
                  const s = catalogSubjects.find(x => x.name.toLowerCase() === o.value.toLowerCase() || x.code.toLowerCase() === (o.label || '').toLowerCase())
                  if (s) {
                    if (s.entry_type === 'Theory' || s.entry_type === 'Practical') setEntryType(s.entry_type)
                    if (!program && s.program) setProgram(s.program)
                    if (!semester && s.semester) setSemester(s.semester)
                  }
                }}
              />
            </div>

            <div className="form-group">
              <label className="form-label">Subject Code <span style={{ color: 'var(--clr-text-3)', fontWeight: 400 }}>(optional)</span></label>
              <SuggestInput
                id="weekly-subject-code"
                placeholder="e.g. ds (auto-derived)"
                value={subjectCode}
                onChange={setSubjectCode}
                options={subjectCodeOptions}
                onPick={o => {
                  setSubjectCode(o.value)
                  if (o.label) setSubjectName(o.label)
                  const s = catalogSubjects.find(x => x.code.toLowerCase() === o.value.toLowerCase())
                  if (s) {
                    if (s.entry_type === 'Theory' || s.entry_type === 'Practical') setEntryType(s.entry_type)
                    if (!program && s.program) setProgram(s.program)
                    if (!semester && s.semester) setSemester(s.semester)
                  }
                }}
              />
            </div>

            <div className="form-group">
              <label className="form-label">Teacher *</label>
              <select className="form-control" value={teacher} onChange={e => setTeacher(e.target.value)}>
                <option value="">Select teacher…</option>
                {dynamicTeachers.map(t => <option key={t}>{t}</option>)}
                <option value="Other…">Other…</option>
              </select>
            </div>

            {teacher === 'Other…' && (
              <div className="form-group">
                <label className="form-label">Teacher Code</label>
                <input
                  className="form-control"
                  placeholder="e.g. SKS"
                  value={customTeacher}
                  onChange={e => setCustomTeacher(e.target.value)}
                />
              </div>
            )}

            <div className="form-group">
              <label className="form-label">Class Type</label>
              <select className="form-control" value={entryType} onChange={e => setEntryType(e.target.value as any)}>
                <option value="Theory">Theory</option>
                <option value="Practical">Practical</option>
              </select>
            </div>

            <div className="form-group">
              <label className="form-label">Room <span style={{ color: 'var(--clr-text-3)', fontWeight: 400 }}>(optional)</span></label>
              <input
                className="form-control"
                placeholder="Auto-assigned if blank"
                value={room}
                onChange={e => setRoom(e.target.value)}
              />
            </div>
          </div>

          {/* ── Weekly Hours & Quick Pills ── */}
          <div style={{
            padding: 14,
            background: 'var(--clr-bg-2, rgba(0,0,0,0.02))',
            borderRadius: 'var(--radius)',
            border: '1px solid var(--clr-border)'
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <label className="form-label" style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 6 }}>
                <Clock size={14} style={{ color: 'var(--clr-primary)' }} /> Weekly Teaching Hours *
              </label>
              <span style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--clr-primary)' }}>
                {weeklyHours} hrs / week
              </span>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <input
                type="number"
                step="0.5"
                min="0.5"
                max="24"
                className="form-control"
                style={{ width: 100 }}
                value={weeklyHours}
                onChange={e => setWeeklyHours(parseFloat(e.target.value) || 0)}
              />
              <div style={{ display: 'flex', gap: 6 }}>
                {QUICK_HOURS.map(h => (
                  <button
                    key={h}
                    type="button"
                    onClick={() => setWeeklyHours(h)}
                    style={{
                      padding: '4px 10px',
                      fontSize: '12px',
                      fontWeight: 600,
                      borderRadius: 16,
                      border: '1px solid',
                      borderColor: weeklyHours === h ? 'var(--clr-primary)' : 'var(--clr-border)',
                      background: weeklyHours === h ? 'var(--clr-primary-10)' : 'transparent',
                      color: weeklyHours === h ? 'var(--clr-primary)' : 'var(--clr-text-2)',
                      cursor: 'pointer'
                    }}
                  >
                    {h}h
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* ── Preferred Teaching Days Chips ── */}
          <div style={{
            padding: 14,
            background: 'var(--clr-bg-2, rgba(0,0,0,0.02))',
            borderRadius: 'var(--radius)',
            border: '1px solid var(--clr-border)'
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <label className="form-label" style={{ margin: 0 }}>
                Preferred Days * <span style={{ color: 'var(--clr-text-3)', fontWeight: 400 }}>({selectedDays.length} selected)</span>
              </label>
              <button
                type="button"
                className="btn-ghost"
                style={{ fontSize: '11px', padding: '2px 6px' }}
                onClick={() => setSelectedDays(selectedDays.length === ALL_DAYS.length ? ['Tuesday', 'Wednesday', 'Thursday'] : [...ALL_DAYS])}
              >
                {selectedDays.length === ALL_DAYS.length ? 'Reset Default' : 'Select All'}
              </button>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8 }}>
              {ALL_DAYS.map(day => {
                const isSelected = selectedDays.includes(day)
                return (
                  <button
                    key={day}
                    type="button"
                    onClick={() => toggleDay(day)}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      padding: '8px 12px',
                      borderRadius: 'var(--radius)',
                      border: '1px solid',
                      borderColor: isSelected ? 'var(--clr-primary)' : 'var(--clr-border)',
                      background: isSelected ? 'var(--clr-primary-10)' : 'var(--card-bg, #fff)',
                      color: isSelected ? 'var(--clr-primary)' : 'var(--clr-text-1)',
                      fontWeight: isSelected ? 600 : 400,
                      cursor: 'pointer',
                      fontSize: 'var(--fs-sm)',
                      transition: 'all 0.15s ease'
                    }}
                  >
                    <span>{day}</span>
                    {isSelected && <Check size={14} style={{ color: 'var(--clr-primary)' }} />}
                  </button>
                )
              })}
            </div>

            {/* ── Day Rules & Constraints ── */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--clr-border)' }}>
              <div>
                <label className="form-label" style={{ fontSize: '12px' }}>Day Requirement</label>
                <select
                  className="form-control"
                  style={{ fontSize: '13px' }}
                  value={dayRule}
                  onChange={e => setDayRule(e.target.value as any)}
                >
                  <option value="exactly">Exactly N days</option>
                  <option value="at_least">At least N days</option>
                  <option value="at_most">At most N days</option>
                </select>
              </div>

              <div>
                <label className="form-label" style={{ fontSize: '12px' }}>Day Count (N)</label>
                <input
                  type="number"
                  min="1"
                  max={ALL_DAYS.length}
                  className="form-control"
                  style={{ fontSize: '13px' }}
                  value={dayCount}
                  onChange={e => setDayCount(parseInt(e.target.value, 10) || 1)}
                />
              </div>
            </div>
          </div>

          {/* ── Expandable Advanced Options ── */}
          <div>
            <button
              type="button"
              className="btn-ghost"
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                fontSize: '12px',
                color: 'var(--clr-text-2)',
                padding: '4px 0'
              }}
              onClick={() => setShowAdvancedOptions(o => !o)}
            >
              <Sliders size={13} /> {showAdvancedOptions ? 'Hide Solver Settings' : 'Advanced Solver Settings'}
            </button>

            {showAdvancedOptions && (
              <div style={{
                marginTop: 8,
                padding: 12,
                borderRadius: 'var(--radius)',
                background: 'var(--clr-bg-2, rgba(0,0,0,0.02))',
                border: '1px solid var(--clr-border)',
                display: 'flex',
                flexDirection: 'column',
                gap: 10
              }}>
                <div className="form-group" style={{ margin: 0 }}>
                  <label className="form-label" style={{ fontSize: '12px' }}>Outside Days Policy</label>
                  <select
                    className="form-control"
                    style={{ fontSize: '13px' }}
                    value={outsideDays}
                    onChange={e => setOutsideDays(e.target.value as any)}
                  >
                    <option value="auto">Auto (Suggest replacement days if preferred are blocked)</option>
                    <option value="allow">Allow (Freely consider other days)</option>
                    <option value="deny">Deny (Strictly restrict to selected days only)</option>
                  </select>
                </div>

                <div style={{ display: 'flex', gap: 16 }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '12px', cursor: 'pointer' }}>
                    <input
                      type="checkbox"
                      checked={includeAdvanced}
                      onChange={e => setIncludeAdvanced(e.target.checked)}
                    />
                    Include Advanced re-arrangements
                  </label>

                  <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '12px', cursor: 'pointer' }}>
                    <input
                      type="checkbox"
                      checked={allowCancel}
                      onChange={e => setAllowCancel(e.target.checked)}
                    />
                    Allow cancelling classes if necessary
                  </label>
                </div>
              </div>
            )}
          </div>
        </>
      )}

      {/* ── Loading View ── */}
      {step === 'loading' && (
        <div style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 16,
          padding: '40px 20px',
          textAlign: 'center'
        }}>
          <Loader2 size={36} style={{ animation: 'spin 0.8s linear infinite', color: 'var(--clr-primary)' }} />
          <div>
            <div style={{ fontWeight: 600, fontSize: 'var(--fs-md)' }}>Searching Weekly Schedule…</div>
            <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--clr-text-2)', marginTop: 4 }}>
              Evaluating slots across {selectedDays.join(', ')} for {weeklyHours} hours/week...
            </div>
          </div>
        </div>
      )}

      {/* ── Proposals View ── */}
      {step === 'proposals' && result && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {/* Status Message Banner */}
          <div style={{
            padding: '12px 14px',
            borderRadius: 'var(--radius)',
            border: '1px solid',
            borderColor: result.status === 'exact'
              ? 'rgba(34, 197, 94, 0.3)'
              : result.status === 'substituted'
              ? 'rgba(59, 130, 246, 0.3)'
              : 'rgba(245, 158, 11, 0.3)',
            background: result.status === 'exact'
              ? 'rgba(34, 197, 94, 0.08)'
              : result.status === 'substituted'
              ? 'rgba(59, 130, 246, 0.08)'
              : 'rgba(245, 158, 11, 0.08)',
            fontSize: 'var(--fs-sm)',
            color: 'var(--clr-text-1)',
            lineHeight: 1.5
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 600, marginBottom: 4 }}>
              {result.status === 'exact' && <CheckCircle2 size={16} style={{ color: 'var(--clr-success)' }} />}
              {result.status === 'substituted' && <Sparkles size={16} style={{ color: '#3b82f6' }} />}
              {(result.status === 'needs_changes' || result.status === 'partial') && <AlertTriangle size={16} style={{ color: '#f59e0b' }} />}
              <span>{result.message}</span>
            </div>
            {result.notes && result.notes.length > 0 && (
              <ul style={{ margin: '4px 0 0 18px', padding: 0, fontSize: '12px', color: 'var(--clr-text-2)' }}>
                {result.notes.map((n, i) => <li key={i}>{n}</li>)}
              </ul>
            )}
          </div>

          {/* Day Report Chips */}
          {result.day_report && result.day_report.length > 0 && (
            <div style={{
              display: 'flex',
              gap: 6,
              flexWrap: 'wrap',
              padding: '8px 10px',
              background: 'var(--clr-bg-2, rgba(0,0,0,0.02))',
              borderRadius: 'var(--radius)',
              border: '1px solid var(--clr-border)',
              fontSize: '11px'
            }}>
              <span style={{ fontWeight: 600, color: 'var(--clr-text-3)', alignSelf: 'center', marginRight: 4 }}>
                Days:
              </span>
              {result.day_report.map((d: DayReportItem) => (
                <div
                  key={d.day}
                  title={d.reason || (d.available ? 'Available' : 'Blocked')}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 4,
                    padding: '2px 8px',
                    borderRadius: 12,
                    background: d.available ? 'rgba(34, 197, 94, 0.12)' : 'rgba(239, 68, 68, 0.1)',
                    color: d.available ? 'var(--clr-success)' : 'var(--clr-error)',
                    fontWeight: 500
                  }}
                >
                  <span>{d.day.slice(0, 3)}</span>
                  <span>{d.available ? '✓' : '✗'}</span>
                </div>
              ))}
            </div>
          )}

          {/* Proposal Cards List */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, maxHeight: 380, overflowY: 'auto' }}>
            {result.proposals.map((proposal, idx) => {
              const isSelected = selectedProposal?.id === proposal.id
              const badge = categoryBadge(proposal.category)

              return (
                <div
                  key={proposal.id || idx}
                  onClick={() => setSelectedProposal(proposal)}
                  style={{
                    border: '2px solid',
                    borderColor: isSelected ? 'var(--clr-primary)' : 'var(--clr-border)',
                    background: isSelected ? 'var(--clr-primary-10)' : 'var(--card-bg, #fff)',
                    borderRadius: 'var(--radius)',
                    padding: 14,
                    cursor: 'pointer',
                    transition: 'all 0.15s ease'
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10 }}>
                    <div style={{ flex: 1 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                        <span style={{
                          fontSize: '11px',
                          fontWeight: 700,
                          padding: '2px 8px',
                          borderRadius: 12,
                          background: badge.bg,
                          color: badge.color
                        }}>
                          {badge.label}
                        </span>
                        <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--clr-text-1)' }}>
                          {proposal.title}
                        </span>
                      </div>

                      <div style={{ fontSize: '12px', color: 'var(--clr-text-2)', lineHeight: 1.5, marginBottom: 10 }}>
                        {proposal.description}
                      </div>

                      {/* Sessions Chips */}
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
                        {proposal.sessions.map((sess, sIdx) => (
                          <div
                            key={sIdx}
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: 6,
                              padding: '4px 10px',
                              background: 'var(--clr-bg-2, rgba(0,0,0,0.04))',
                              borderRadius: 'var(--radius-sm, 4px)',
                              fontSize: '12px',
                              border: '1px solid var(--clr-border)'
                            }}
                          >
                            <span style={{ fontWeight: 600 }}>{sess.day}</span>
                            <span style={{ color: 'var(--clr-text-2)' }}>{sess.start}–{sess.end}</span>
                            <span style={{
                              fontSize: '11px',
                              padding: '1px 6px',
                              background: 'var(--clr-primary-10)',
                              color: 'var(--clr-primary)',
                              borderRadius: 4
                            }}>
                              {sess.room}
                            </span>
                          </div>
                        ))}
                      </div>

                      {/* Modifications Breakdown (for advanced options) */}
                      {proposal.modifications && proposal.modifications.length > 0 && (
                        <div style={{
                          marginTop: 6,
                          padding: '6px 10px',
                          borderRadius: 'var(--radius)',
                          background: 'rgba(245, 158, 11, 0.08)',
                          border: '1px solid rgba(245, 158, 11, 0.2)',
                          fontSize: '11px',
                          color: 'var(--clr-text-2)'
                        }}>
                          <div style={{ fontWeight: 600, color: '#f59e0b', marginBottom: 2 }}>
                            Requires class moves/cancels:
                          </div>
                          {proposal.modifications.map((m, mIdx) => (
                            <div key={mIdx}>• {m.description}</div>
                          ))}
                        </div>
                      )}
                    </div>

                    <div style={{ flexShrink: 0, marginTop: 4 }}>
                      {isSelected ? (
                        <CheckCircle2 size={20} style={{ color: 'var(--clr-primary)' }} />
                      ) : (
                        <div style={{
                          width: 18,
                          height: 18,
                          borderRadius: '50%',
                          border: '2px solid var(--clr-border)'
                        }} />
                      )}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* ── Confirmation View ── */}
      {step === 'confirm' && selectedProposal && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{
            padding: 14,
            background: 'var(--clr-primary-10)',
            border: '1px solid var(--clr-primary-20)',
            borderRadius: 'var(--radius)'
          }}>
            <div style={{ fontWeight: 700, fontSize: 'var(--fs-md)', marginBottom: 6, display: 'flex', alignItems: 'center', gap: 6 }}>
              <Sparkles size={16} style={{ color: 'var(--clr-primary)' }} />
              Confirm Weekly Schedule Booking
            </div>
            <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--clr-text-2)', lineHeight: 1.6 }}>
              {selectedProposal.title}
            </div>
          </div>

          <div style={{
            padding: 14,
            background: 'var(--card-bg, #fff)',
            border: '1px solid var(--clr-border)',
            borderRadius: 'var(--radius)'
          }}>
            <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--clr-text-3)', textTransform: 'uppercase', marginBottom: 8 }}>
              Classes to be scheduled:
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {selectedProposal.sessions.map((sess, i) => (
                <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 'var(--fs-sm)' }}>
                  <div>
                    <strong>{subjectName}</strong> ({entryType})
                  </div>
                  <div style={{ color: 'var(--clr-text-2)' }}>
                    {sess.day} {sess.start}–{sess.end} · <strong>{sess.room}</strong>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {selectedProposal.modifications && selectedProposal.modifications.length > 0 && (
            <div style={{
              padding: 12,
              background: 'rgba(245, 158, 11, 0.1)',
              border: '1px solid rgba(245, 158, 11, 0.3)',
              borderRadius: 'var(--radius)',
              fontSize: '12px'
            }}>
              <strong style={{ color: '#f59e0b' }}>Automatic adjustments:</strong>
              <div style={{ marginTop: 4 }}>
                {selectedProposal.modifications.map((m, i) => (
                  <div key={i}>• {m.description}</div>
                ))}
              </div>
            </div>
          )}

          <div style={{ fontSize: '11px', color: 'var(--clr-text-3)', display: 'flex', gap: 4, alignItems: 'center' }}>
            <AlertCircle size={12} />
            This will apply all {selectedProposal.actions_to_apply.length} actions atomically and generate a new version.
          </div>
        </div>
      )}

      {/* ── Done View ── */}
      {step === 'done' && (
        <div style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 12,
          padding: '30px 20px',
          textAlign: 'center'
        }}>
          {doneSuccess ? (
            <>
              <CheckCircle2 size={44} style={{ color: 'var(--clr-success)' }} />
              <div style={{ fontWeight: 700, fontSize: 'var(--fs-lg)' }}>Schedule Applied!</div>
            </>
          ) : (
            <>
              <AlertCircle size={44} style={{ color: 'var(--clr-error)' }} />
              <div style={{ fontWeight: 700, fontSize: 'var(--fs-lg)' }}>Could Not Complete</div>
            </>
          )}

          <div style={{ color: 'var(--clr-text-2)', fontSize: 'var(--fs-sm)', maxWidth: 420 }}>
            {doneMessage}
          </div>
        </div>
      )}

      {/* ── Action Buttons Footer ── */}
      <div style={{
        display: 'flex',
        justifyContent: 'flex-end',
        gap: 8,
        marginTop: 6,
        paddingTop: 12,
        borderTop: '1px solid var(--clr-border)'
      }}>
        {step === 'form' && (
          <>
            <button type="button" className="btn btn-ghost" onClick={onClose}>Cancel</button>
            <button
              type="button"
              className="btn btn-primary"
              disabled={!canSubmit || loading}
              onClick={handleSearch}
              style={{ display: 'flex', alignItems: 'center', gap: 6 }}
            >
              <Sparkles size={14} /> Find Weekly Schedule
            </button>
          </>
        )}

        {step === 'proposals' && (
          <>
            <button type="button" className="btn btn-ghost" onClick={() => setStep('form')}>← Adjust Request</button>
            <button
              type="button"
              className="btn btn-primary"
              disabled={!selectedProposal}
              onClick={() => setStep('confirm')}
              style={{ display: 'flex', alignItems: 'center', gap: 6 }}
            >
              Continue with Selected Plan <ChevronRight size={14} />
            </button>
          </>
        )}

        {step === 'confirm' && (
          <>
            <button type="button" className="btn btn-ghost" onClick={() => setStep('proposals')}>← Back</button>
            <button
              type="button"
              className="btn btn-primary"
              disabled={applying}
              onClick={handleApplyProposal}
              style={{ display: 'flex', alignItems: 'center', gap: 6 }}
            >
              {applying ? (
                <>
                  <Loader2 size={14} style={{ animation: 'spin 0.7s linear infinite' }} /> Applying…
                </>
              ) : (
                <>
                  <Check size={14} /> Apply Weekly Schedule
                </>
              )}
            </button>
          </>
        )}

        {step === 'done' && (
          <button type="button" className="btn btn-primary" onClick={onClose}>
            Close
          </button>
        )}
      </div>
    </div>
  )
}
