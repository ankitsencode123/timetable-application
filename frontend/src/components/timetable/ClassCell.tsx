import type { TimetableEntry } from '../../types'

interface Props {
  entry: TimetableEntry
  compact?: boolean
  onClick?: () => void
}

export default function ClassCell({ entry, compact, onClick }: Props) {
  const isPractical = entry.type === 'Practical'
  const cls = [
    'class-cell',
    isPractical ? 'practical' : 'theory',
    entry._cancelled ? 'cancelled' : '',
    entry._conflict ? 'conflict' : '',
  ].filter(Boolean).join(' ')

  return (
    <div className={cls} onClick={onClick} title={`${entry.program} ${entry.semester} — ${entry.subject_name} — ${entry.teacher} — ${entry.room}`}>
      {!compact && (
        <div style={{ fontSize: '9px', fontWeight: 700, opacity: 0.65, letterSpacing: '0.04em', marginBottom: 1, color: 'var(--clr-text-3)', textTransform: 'uppercase' }}>
          {entry.program} {entry.semester}
        </div>
      )}
      <div className="cell-subject">
        {compact ? entry.subject_code.toUpperCase() : entry.subject_name}
      </div>
      {!compact && (
        <div className="cell-meta">
          <span style={{ fontSize: '10px', fontWeight: 700, opacity: 0.85 }}>{entry.teacher}</span>
          <span style={{ opacity: 0.5 }}>·</span>
          <span>{entry.room}</span>
          {isPractical && (
            <>
              <span style={{ opacity: 0.5 }}>·</span>
              <span style={{ fontSize: '9px', fontWeight: 700, color: 'var(--clr-prac-text)' }}>LAB</span>
            </>
          )}
        </div>
      )}
      {entry._modified && (
        <div style={{
          position: 'absolute', top: 3, right: 3, width: 5, height: 5,
          background: 'var(--clr-warning)', borderRadius: '50%'
        }} title="Modified" />
      )}
    </div>
  )
}
