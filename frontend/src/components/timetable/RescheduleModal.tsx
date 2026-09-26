import { useState } from 'react'
import { Clock, Calendar, ArrowRight, X, AlertTriangle } from 'lucide-react'
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

    try {
      // Build a MOVE_CLASS action: same class, new day + time
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
        const firstError = result.results?.[0]?.error ?? 'Could not reschedule. Check for conflicts.'
        setError(firstError)
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'An error occurred.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal modal-sm" style={{ background: 'var(--clr-bg-2)', maxWidth: 480 }}>

        {/* Header */}
        <div className="modal-header">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{
                background: 'linear-gradient(135deg, #6366f1, #8b5cf6)',
                color: '#fff', borderRadius: 6, padding: '2px 10px',
                fontSize: 'var(--fs-xs)', fontWeight: 700, letterSpacing: '0.05em'
              }}>RESCHEDULE</span>
              <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--clr-text-3)' }}>
                {entry.program} · {entry.semester}
              </span>
            </div>
            <h2 style={{ fontSize: 'var(--fs-lg)', fontWeight: 700, marginTop: 4 }}>
              {entry.subject_name}
            </h2>
            <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--clr-text-3)', display: 'flex', gap: 8 }}>
              <span>Currently: <strong style={{ color: 'var(--clr-text-2)' }}>{entry.day}</strong></span>
              <span>·</span>
              <span><strong style={{ color: 'var(--clr-text-2)' }}>{entry.start}–{entry.end}</strong></span>
              <span>·</span>
              <span>{entry.teacher}</span>
            </div>
          </div>
          <button className="btn-icon" onClick={onClose}><X size={18} /></button>
        </div>

        {/* Arrow divider */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10, padding: '12px 0',
          color: 'var(--clr-text-3)', fontSize: 'var(--fs-xs)'
        }}>
          <div style={{ flex: 1, height: 1, background: 'var(--clr-border)' }} />
          <ArrowRight size={14} style={{ color: '#8b5cf6' }} />
          <span style={{ fontWeight: 600, color: '#8b5cf6' }}>Move to new slot</span>
          <div style={{ flex: 1, height: 1, background: 'var(--clr-border)' }} />
        </div>

        {/* New Slot Picker */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>

          {/* Day */}
          <div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', fontWeight: 600, marginBottom: 8 }}>
              <Calendar size={14} style={{ color: '#8b5cf6' }} /> New Day
            </label>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {DAYS.map(d => (
                <button
                  key={d}
                  onClick={() => setNewDay(d)}
                  style={{
                    padding: '5px 12px', borderRadius: 8, fontSize: 'var(--fs-xs)',
                    fontWeight: 600, cursor: 'pointer', border: '1.5px solid',
                    borderColor: newDay === d ? '#6366f1' : 'var(--clr-border)',
                    background: newDay === d ? 'rgba(99,102,241,0.12)' : 'transparent',
                    color: newDay === d ? '#818cf8' : 'var(--clr-text-2)',
                    transition: 'all 0.15s',
                  }}
                >{d.slice(0, 3)}</button>
              ))}
            </div>
          </div>

          {/* Start Time */}
          <div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', fontWeight: 600, marginBottom: 8 }}>
              <Clock size={14} style={{ color: '#8b5cf6' }} /> New Start Time
            </label>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {TIME_SLOTS.map(t => (
                <button
                  key={t}
                  onClick={() => setNewStart(t)}
                  style={{
                    padding: '5px 10px', borderRadius: 8, fontSize: 'var(--fs-xs)',
                    fontWeight: 600, cursor: 'pointer', border: '1.5px solid',
                    borderColor: newStart === t ? '#6366f1' : 'var(--clr-border)',
                    background: newStart === t ? 'rgba(99,102,241,0.12)' : 'transparent',
                    color: newStart === t ? '#818cf8' : 'var(--clr-text-2)',
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
              style={{
                marginTop: 8, padding: '6px 10px', borderRadius: 8, fontSize: 'var(--fs-sm)',
                border: '1.5px solid var(--clr-border)', background: 'var(--clr-bg-1)',
                color: 'var(--clr-text-1)', width: '140px'
              }}
            />
          </div>

          {/* End Time */}
          <div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', fontWeight: 600, marginBottom: 8 }}>
              <Clock size={14} style={{ color: '#8b5cf6' }} /> New End Time
            </label>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {TIME_SLOTS.map(t => (
                <button
                  key={t}
                  onClick={() => setNewEnd(t)}
                  style={{
                    padding: '5px 10px', borderRadius: 8, fontSize: 'var(--fs-xs)',
                    fontWeight: 600, cursor: 'pointer', border: '1.5px solid',
                    borderColor: newEnd === t ? '#6366f1' : 'var(--clr-border)',
                    background: newEnd === t ? 'rgba(99,102,241,0.12)' : 'transparent',
                    color: newEnd === t ? '#818cf8' : 'var(--clr-text-2)',
                    transition: 'all 0.15s',
                  }}
                >{t}</button>
              ))}
            </div>
            <input
              type="time"
              value={newEnd}
              onChange={e => setNewEnd(e.target.value)}
              style={{
                marginTop: 8, padding: '6px 10px', borderRadius: 8, fontSize: 'var(--fs-sm)',
                border: '1.5px solid var(--clr-border)', background: 'var(--clr-bg-1)',
                color: 'var(--clr-text-1)', width: '140px'
              }}
            />
          </div>
        </div>

        {/* Error display */}
        {error && (
          <div style={{
            marginTop: 'var(--sp-4)', padding: '10px 14px', borderRadius: 8,
            background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.25)',
            color: '#f87171', fontSize: 'var(--fs-xs)', display: 'flex', alignItems: 'flex-start', gap: 8
          }}>
            <AlertTriangle size={14} style={{ marginTop: 2, flexShrink: 0 }} />
            {error}
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
            style={{
              background: 'linear-gradient(135deg, #6366f1, #8b5cf6)',
              border: 'none', display: 'flex', alignItems: 'center', gap: 6
            }}
          >
            {loading ? (
              <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{
                  width: 12, height: 12, border: '2px solid rgba(255,255,255,0.3)',
                  borderTopColor: '#fff', borderRadius: '50%',
                  animation: 'spin 0.7s linear infinite', display: 'inline-block'
                }} />
                Rescheduling…
              </span>
            ) : (
              <><ArrowRight size={14} /> Confirm Reschedule</>
            )}
          </button>
        </div>
      </div>
    </div>
  )
}
