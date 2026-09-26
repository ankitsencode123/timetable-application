import { useState, useMemo } from 'react'
import type { TimetableEntry, TimetableFilters } from '../../types'
import { DAYS } from '../../types'
import ClassCell from './ClassCell'
import ClassDetailModal from './ClassDetailModal'

// ── Time slots to display ────────────────────────────────────
const DEFAULT_TIME_ROWS = [
  { label: '10:00–12:00', start: '10:00', end: '12:00' },
  { label: '12:00–14:00', start: '12:00', end: '14:00' },
  { label: '14:30–16:30', start: '14:30', end: '16:30' },
  { label: '14:30–17:30', start: '14:30', end: '17:30' },
  { label: '16:30–17:30', start: '16:30', end: '17:30' },
]

interface Props {
  entries: TimetableEntry[]
  filters: TimetableFilters
  density?: 'comfortable' | 'compact'
  onEntryClick?: (e: TimetableEntry) => void
  showDays?: string[]
}

export default function TimetableGrid({ entries, filters, density = 'comfortable', onEntryClick, showDays }: Props) {
  const [selected, setSelected] = useState<TimetableEntry | null>(null)
  const days = showDays ?? DAYS

  // Filter entries
  const filtered = useMemo(() => {
    const q = filters.search?.toLowerCase() ?? ''
    return entries.filter(e => {
      if (filters.program && filters.program !== 'All' && e.program !== filters.program) return false
      if (filters.semester && filters.semester !== 'All' && e.semester !== filters.semester) return false
      if (filters.teacher && !e.teacher.toLowerCase().includes(filters.teacher.toLowerCase())) return false
      if (filters.subject && !e.subject_name.toLowerCase().includes(filters.subject.toLowerCase()) && !e.subject_code.toLowerCase().includes(filters.subject.toLowerCase())) return false
      if (filters.room && !e.room.toLowerCase().includes(filters.room.toLowerCase())) return false
      if (filters.day && e.day !== filters.day) return false
      if (q && !e.subject_name.toLowerCase().includes(q) && !e.subject_code.toLowerCase().includes(q) && !e.teacher.toLowerCase().includes(q) && !e.room.toLowerCase().includes(q) && !e.program.toLowerCase().includes(q)) return false
      return true
    })
  }, [entries, filters])

  // Build a map: day → entries
  const byDay = useMemo(() => {
    const m: Record<string, TimetableEntry[]> = {}
    for (const d of days) m[d] = []
    for (const e of filtered) {
      if (m[e.day]) m[e.day].push(e)
    }
    return m
  }, [filtered, days])

  // Dynamically assemble time rows from entries + defaults
  const dynamicTimeRows = useMemo(() => {
    const chunkMap = new Map<string, {label: string, start: string, end: string}>()
    for (const d of DEFAULT_TIME_ROWS) {
      chunkMap.set(d.label, d)
    }
    for (const e of entries) {
      const label = `${e.start}–${e.end}`
      if (!chunkMap.has(label)) {
        chunkMap.set(label, { label, start: e.start, end: e.end })
      }
    }
    const arr = Array.from(chunkMap.values())
    arr.sort((a, b) => {
      if (a.start === b.start) return a.end.localeCompare(b.end)
      return a.start.localeCompare(b.start)
    })
    return arr
  }, [entries])

  // For each time slot + day, find matching entries
  function getCellEntries(day: string, row: { start: string, end: string }) {
    return (byDay[day] ?? []).filter(e => e.start === row.start && e.end === row.end)
  }

  const colCount = days.length + 1
  const minH = density === 'compact' ? '54px' : '80px'

  if (filtered.length === 0) {
    return (
      <div className="empty-state" style={{ flex: 1, minHeight: 300 }}>
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>
        <h3>No classes found</h3>
        <p>Try adjusting your filters or search query.</p>
      </div>
    )
  }

  return (
    <>
      <div className="tt-grid-wrapper">
        <div
          className="tt-grid"
          style={{ gridTemplateColumns: `80px repeat(${colCount - 1}, 1fr)` }}
        >
          {/* Header row */}
          <div className="tt-day-header" style={{ background: 'var(--clr-bg-3)' }}>Time</div>
          {days.map(d => (
            <div key={d} className="tt-day-header">{d}</div>
          ))}

          {/* Data rows */}
          {dynamicTimeRows.map(row => (
            <>
              <div key={`t-${row.label}`} className="tt-time-col" style={{ minHeight: minH }}>
                <span>{row.start}</span>
                <span style={{ fontSize: '9px', color: 'var(--clr-text-3)', display: 'block', marginTop: 2 }}>{row.end}</span>
              </div>
              {days.map(day => {
                const cells = getCellEntries(day, row)
                return (
                  <div key={`${day}-${row.label}`} className="tt-cell" style={{ minHeight: minH }}>
                    {cells.map((entry, i) => (
                      <ClassCell
                        key={`${entry.subject_code}-${entry.teacher}-${i}`}
                        entry={entry}
                        compact={density === 'compact'}
                        onClick={() => {
                          setSelected(entry)
                          onEntryClick?.(entry)
                        }}
                      />
                    ))}
                  </div>
                )
              })}
            </>
          ))}
        </div>
      </div>

      {selected && (
        <ClassDetailModal entry={selected} onClose={() => setSelected(null)} />
      )}
    </>
  )
}
