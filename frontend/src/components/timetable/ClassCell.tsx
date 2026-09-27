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

  const subjectColor = entry._conflict
    ? 'var(--violation)'
    : isPractical
      ? 'var(--amber)'
      : 'var(--accent)'

  return (
    <div className={cls} onClick={onClick} title={`${entry.program} ${entry.semester} — ${entry.subject_name} — ${entry.teacher} — ${entry.room}`}>
      {!compact && (
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.1em', marginBottom: 2, color: 'var(--ink-soft)', textTransform: 'uppercase', opacity: 0.8 }}>
          {entry.program} {entry.semester}
        </div>
      )}
      <div className="cell-subject" style={{ color: subjectColor, fontFamily: 'var(--font-display)', fontWeight: 600 }}>
        {compact ? entry.subject_code.toUpperCase() : entry.subject_name}
      </div>
      {!compact && (
        <div className="cell-meta">
          <span>{entry.teacher}</span>
          <span style={{ opacity: 0.4 }}>·</span>
          <span>{entry.room}</span>
          {isPractical && (
            <>
              <span style={{ opacity: 0.4 }}>·</span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 600, color: 'var(--amber)', letterSpacing: '0.1em' }}>LAB</span>
            </>
          )}
        </div>
      )}
      {entry._modified && (
        <div style={{
          position: 'absolute', top: 3, right: 3, width: 5, height: 5,
          background: 'var(--amber)', borderRadius: '50%'
        }} title="Modified" />
      )}
    </div>
  )
}
