import { useState } from 'react'
import { Clock, Calendar, ArrowRight, X, AlertTriangle, Lightbulb } from 'lucide-react'
import type { TimetableEntry } from '../../types'
import { executeActions } from '../../api'

interface Props {
  entry: TimetableEntry
  onClose: () => void
  onSuccess: () => void
}

const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

const TIME_SLOTS = [
  '08:00', '09:00', '10:00', '11:00', '12:00',
  '13:00', '14:00', '14:30', '15:00', '15:30',
  '16:00', '16:30', '17:00', '17:30', '18:00',
]

export default function RescheduleModal({ entry, onClose, onSuccess }: Props) {
  const [newDay, setNewDay] = useState(entry.day)
  const [newStart, setNewStart] = useState(entry.start)
  const [newEnd, setNewEnd] = useState(entry.end)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [suggestions, setSuggestions] = useState<any[] | null>(null)

  const isCustomEnd = !TIME_SLOTS.includes(newEnd)

  async function handleReschedule() {
    if (!newDay || !newStart || !newEnd) {
      setError('Please fill in all fields.')
      return
    }
    if (newStart >= newEnd) {
      setError('Start time must be before end time.')
      return
    }

    setLoading(true)
    setError(null)
    setSuggestions(null)

    try {
      const moveAction = {
        action: 'MOVE_CLASS',
        target: {
          day: entry.day,
          program: entry.program,
          semester: entry.semester,
          start_time: entry.start,
          end_time: entry.end,
          subject_code: entry.subject_code,
          teacher: entry.teacher,
        },
        new_day: newDay,
        new_start_time: newStart,
        new_end_time: newEnd,
      }

      const result = await executeActions([moveAction])
      if (result.success) {
        onSuccess()
        onClose()
      } else {
        const firstResult = result.results?.[0]
        setError(firstResult?.error ?? 'Could not reschedule. Check for conflicts.')
        setSuggestions(firstResult?.suggestions?.rich_suggestions ?? null)
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'An error occurred.')
    } finally {
      setLoading(false)
    }
  }

  async function applySuggestion(action: any) {
    setLoading(true); setError(null); setSuggestions(null)
    try {
      const result = await executeActions([action])
      if (result.success) {
        onSuccess()
        onClose()
      } else {
        const firstResult = result.results?.[0]
        setError(firstResult?.error ?? 'Could not reschedule with suggestion.')
        setSuggestions(firstResult?.suggestions?.rich_suggestions ?? null)
      }
    } catch(err: any) {
       setError(err.message)
    } finally { setLoading(false) }
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal modal-sm" style={{ background: 'var(--paper)', maxWidth: 520 }}>

        {/* Header */}
        <div className="modal-header">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span className="badge badge-blue">RESCHEDULE</span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)' }}>
                {entry.program} · {entry.semester}
              </span>
            </div>
            <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-lg)', fontWeight: 600, color: 'var(--ink)' }}>
              {entry.subject_name}
            </h2>
            <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)', display: 'flex', gap: 8 }}>
              <span>Currently: <strong style={{ color: 'var(--ink)' }}>{entry.day}</strong></span>
              <span>·</span>
              <span><strong style={{ color: 'var(--ink)' }}>{entry.start}–{entry.end}</strong></span>
              <span>·</span>
              <span>{entry.teacher}</span>
            </div>
          </div>
          <button className="btn-icon" onClick={onClose}><X size={18} /></button>
        </div>

        {/* Arrow divider */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10, padding: '12px 0',
          color: 'var(--ink-soft)', fontSize: 'var(--fs-xs)'
        }}>
          <div style={{ flex: 1, height: 1, background: 'var(--line)' }} />
          <ArrowRight size={14} style={{ color: 'var(--accent)' }} />
          <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, color: 'var(--accent)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Move to new slot</span>
          <div style={{ flex: 1, height: 1, background: 'var(--line)' }} />
        </div>

        {/* New Slot Picker */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>

          {/* Day */}
          <div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', fontWeight: 600, marginBottom: 8, color: 'var(--ink)' }}>
              <Calendar size={14} style={{ color: 'var(--accent)' }} /> New Day
            </label>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {DAYS.map(d => (
                <button
                  key={d}
                  onClick={() => setNewDay(d)}
                  style={{
                    padding: '5px 12px', borderRadius: 8, fontSize: 'var(--fs-xs)',
                    fontWeight: 600, cursor: 'pointer', border: '1.5px solid',
                    borderColor: newDay === d ? 'var(--accent)' : 'var(--line)',
                    background: newDay === d ? 'var(--accent-soft)' : 'transparent',
                    color: newDay === d ? 'var(--accent)' : 'var(--ink-soft)',
                    transition: 'all 0.15s',
                  }}
                >{d.slice(0, 3)}</button>
              ))}
            </div>
          </div>

          {/* Start Time */}
          <div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', fontWeight: 600, marginBottom: 8, color: 'var(--ink)' }}>
              <Clock size={14} style={{ color: 'var(--accent)' }} /> New Start Time
            </label>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {TIME_SLOTS.map(t => (
                <button
                  key={t}
                  onClick={() => setNewStart(t)}
                  style={{
                    padding: '5px 10px', borderRadius: 8, fontSize: 'var(--fs-xs)',
                    fontWeight: 600, cursor: 'pointer', border: '1.5px solid',
                    borderColor: newStart === t ? 'var(--accent)' : 'var(--line)',
                    background: newStart === t ? 'var(--accent-soft)' : 'transparent',
                    color: newStart === t ? 'var(--accent)' : 'var(--ink-soft)',
                    transition: 'all 0.15s',
                  }}
                >{t}</button>
              ))}
            </div>
            {/* Custom input */}
            <input
              type="time"
              value={newStart}
              onChange={e => setNewStart(e.target.value)}
              className="input"
              style={{ marginTop: 8, width: 140 }}
            />
          </div>

          {/* End Time */}
          <div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', fontWeight: 600, marginBottom: 8, color: 'var(--ink)' }}>
              <Clock size={14} style={{ color: 'var(--accent)' }} /> New End Time
            </label>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {TIME_SLOTS.map(t => (
                <button
                  key={t}
                  onClick={() => setNewEnd(t)}
                  style={{
                    padding: '5px 10px', borderRadius: 8, fontSize: 'var(--fs-xs)',
                    fontWeight: 600, cursor: 'pointer', border: '1.5px solid',
                    borderColor: newEnd === t ? 'var(--accent)' : 'var(--line)',
                    background: newEnd === t ? 'var(--accent-soft)' : 'transparent',
                    color: newEnd === t ? 'var(--accent)' : 'var(--ink-soft)',
                    transition: 'all 0.15s',
                  }}
                >{t}</button>
              ))}
            </div>
            <input
              type="time"
              value={newEnd}
              onChange={e => setNewEnd(e.target.value)}
              className="input"
              style={{ marginTop: 8, width: 140 }}
            />
          </div>
        </div>

        {/* Error display */}
        {error && (
          <div style={{
            marginTop: 'var(--sp-4)', padding: '10px 14px', borderRadius: 8,
            background: 'var(--violation-soft)', border: '1px solid color-mix(in oklab, var(--violation) 25%, transparent)',
            color: 'var(--violation)', fontSize: 'var(--fs-xs)', display: 'flex', alignItems: 'flex-start', gap: 8
          }}>
            <AlertTriangle size={14} style={{ marginTop: 2, flexShrink: 0 }} />
            {error}
          </div>
        )}

        {/* Suggestion Engine logic */}
        {suggestions && suggestions.length > 0 && (
          <div style={{ marginTop: 'var(--sp-3)', padding: '12px 14px', background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 8 }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 600, color: 'var(--accent)', marginBottom: 8, display: 'flex', alignItems: 'center', gap: 4, textTransform: 'uppercase', letterSpacing: '0.1em' }}>
              <Lightbulb size={12} /> Conflict-Free Alternatives
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {suggestions.map((rs: any, i: number) => (
                <div key={i} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, padding: '8px 12px', background: 'var(--paper)', border: '1px solid color-mix(in oklab, var(--line) 40%, transparent)', borderRadius: 6 }}>
                  <div>
                    <div style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--ink)' }}>{rs.title}</div>
                    <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)' }}>{rs.description}</div>
                  </div>
                  <button 
                    onClick={() => applySuggestion(rs.action)}
                    className="btn btn-primary btn-sm"
                    disabled={loading}
                    style={{ flexShrink: 0 }}
                  >
                    Apply
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Footer */}
        <div className="modal-footer" style={{ justifyContent: 'flex-end', marginTop: 'var(--sp-5)', gap: 'var(--sp-2)' }}>
          <button className="btn btn-ghost btn-sm" onClick={onClose} disabled={loading}>
            Cancel
          </button>
          <button
            className="btn btn-primary btn-sm"
            onClick={handleReschedule}
            disabled={loading}
          >
            {loading ? 'Rescheduling…' : <><ArrowRight size={14} /> Confirm Reschedule</>}
          </button>
        </div>
      </div>
    </div>
  )
}
