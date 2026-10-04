import { useState } from 'react'
import { X, Clock, User, MapPin, BookOpen, GraduationCap, CalendarClock, RefreshCw } from 'lucide-react'
import type { TimetableEntry } from '../../types'
import RescheduleModal from './RescheduleModal'
import InterchangeModal from './InterchangeModal'

import { executeActions } from '../../api'

interface Props {
  entry: TimetableEntry
  onClose: () => void
}

export default function ClassDetailModal({ entry, onClose }: Props) {
  const isPractical = entry.type === 'Practical'
  const [showReschedule, setShowReschedule] = useState(false)
  const [showInterchange, setShowInterchange] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleCancel() {
    if (!window.confirm('Are you sure you want to cancel this class?')) return
    setLoading(true)
    setError(null)
    try {
      const cancelAction = {
        action: 'CANCEL_CLASS',
        target: {
          day: entry.day,
          program: entry.program,
          semester: entry.semester,
          start_time: entry.start,
          end_time: entry.end,
          subject_code: entry.subject_code,
          teacher: entry.teacher,
        }
      }
      const res = await executeActions([cancelAction])
      if (res.success) {
        window.location.reload()
      } else {
        setError(res.results?.[0]?.error ?? 'Could not cancel class.')
      }
    } catch (err: any) {
      setError(err.message || 'Error occurred')
    } finally {
      setLoading(false)
    }
  }


  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal modal-sm t-modal is-open" style={{ background: 'var(--clr-bg-2)' }}>
        <div className="modal-header">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span className={`badge ${isPractical ? 'badge-purple' : 'badge-blue'}`}>
                {entry.type}
              </span>
              <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--clr-text-3)' }}>
                {entry.program} · {entry.semester}
              </span>
            </div>
            <h2 style={{ fontSize: 'var(--fs-lg)', fontWeight: 700, marginTop: 4 }}>
              {entry.subject_name}
            </h2>
            <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--clr-text-3)' }}>
              {entry.subject_code.toUpperCase()}
            </div>
          </div>
          <button className="btn-icon" onClick={onClose}>
            <X size={18} />
          </button>
        </div>

        <div className="class-detail">
          <div className="class-detail-row">
            <span className="class-detail-label">Day</span>
            <span className="class-detail-value">{entry.day}</span>
          </div>
          <div className="class-detail-row">
            <span className="class-detail-label">Time</span>
            <span className="class-detail-value" style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              <Clock size={13} style={{ color: 'var(--clr-text-3)' }} />
              {entry.start} – {entry.end}
            </span>
          </div>
          <div className="class-detail-row">
            <span className="class-detail-label">Teacher</span>
            <span className="class-detail-value" style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              <User size={13} style={{ color: 'var(--clr-text-3)' }} />
              {entry.teacher}
            </span>
          </div>
          <div className="class-detail-row">
            <span className="class-detail-label">Room</span>
            <span className="class-detail-value" style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              <MapPin size={13} style={{ color: 'var(--clr-text-3)' }} />
              {entry.room}
            </span>
          </div>
          <div className="class-detail-row">
            <span className="class-detail-label">Program</span>
            <span className="class-detail-value" style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              <GraduationCap size={13} style={{ color: 'var(--clr-text-3)' }} />
              {entry.program}
            </span>
          </div>
          <div className="class-detail-row">
            <span className="class-detail-label">Semester</span>
            <span className="class-detail-value" style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              <BookOpen size={13} style={{ color: 'var(--clr-text-3)' }} />
              {entry.semester}
            </span>
          </div>
        </div>

        {(entry._conflict || entry._cancelled || entry._modified) && (
          <div style={{ marginTop: 'var(--sp-4)', display: 'flex', gap: 'var(--sp-2)', flexWrap: 'wrap' }}>
            {entry._conflict && <span className="badge badge-red">⚠ Conflict</span>}
            {entry._cancelled && <span className="badge badge-gray">Cancelled</span>}
            {entry._modified && <span className="badge badge-yellow">Modified</span>}
          </div>
        )}

        {error && (
          <div style={{ marginTop: 'var(--sp-2)', color: 'var(--clr-error)', fontSize: 'var(--fs-xs)', fontWeight: 600 }}>
            {error}
          </div>
        )}

        <div className="modal-footer" style={{ justifyContent: 'flex-end', gap: 'var(--sp-2)', flexWrap: 'wrap' }}>
          <button className="btn btn-ghost btn-sm" onClick={onClose} disabled={loading}>Close</button>
          {!entry._cancelled && (
            <>
              <button
                className="btn btn-ghost btn-sm"
                onClick={handleCancel}
                disabled={loading}
                style={{ color: 'var(--clr-error)' }}
              >
                Cancel Class
              </button>
              <button
                className="btn btn-primary btn-sm"
                onClick={() => setShowInterchange(true)}
                disabled={loading}
                style={{
                  background: 'linear-gradient(135deg, #f59e0b, #d97706)',
                  border: 'none', display: 'flex', alignItems: 'center', gap: 6
                }}
              >
                <RefreshCw size={13} /> Interchange
              </button>
              <button
                className="btn btn-primary btn-sm"
                onClick={() => setShowReschedule(true)}
                disabled={loading}
                style={{
                  background: 'linear-gradient(135deg, #6366f1, #8b5cf6)',
                  border: 'none', display: 'flex', alignItems: 'center', gap: 6
                }}
              >
                <CalendarClock size={14} /> Reschedule
              </button>
            </>
          )}
        </div>
      </div>

      {showReschedule && (
        <RescheduleModal
          entry={entry}
          onClose={() => setShowReschedule(false)}
          onSuccess={() => {
            setShowReschedule(false)
            onClose()
            window.location.reload()
          }}
        />
      )}
      {showInterchange && (
        <InterchangeModal
          entry={entry}
          onClose={() => setShowInterchange(false)}
          onSuccess={() => {
            setShowInterchange(false)
            onClose()
            window.location.reload()
          }}
        />
      )}
    </div>
  )
}
