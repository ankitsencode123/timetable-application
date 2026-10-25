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
  dayDateMap?: Record<string, string>
  todayDay?: string
}

export default function TimetableGrid({ entries, filters, density = 'comfortable', onEntryClick, showDays, dayDateMap, todayDay }: Props) {
  const [selected, setSelected] = useState<TimetableEntry | null>(null)
  const days = useMemo(() => {
    if (showDays) return showDays
    const hasSunday = entries.some(e => e.day === 'Sunday') || todayDay === 'Sunday'
    return hasSunday ? ['Sunday', ...DAYS] : [...DAYS]
  }, [showDays, entries, todayDay])

  // Filter entries
  const filtered = useMemo(() => {
    const q = filters.search?.toLowerCase() ?? ''
    return entries.filter(e => {
      const sanitizedProgram = (e.program || '').replace(/\.$/, '')
      if (filters.program && filters.program !== 'All' && sanitizedProgram !== filters.program) return false
      if (filters.semester && filters.semester !== 'All' && e.semester !== filters.semester) return false
      if (filters.teacher && !e.teacher.toLowerCase().includes(filters.teacher.toLowerCase())) return false
      if (filters.subject && !e.subject_name.toLowerCase().includes(filters.subject.toLowerCase()) && !e.subject_code.toLowerCase().includes(filters.subject.toLowerCase())) return false
      if (filters.room && !e.room.toLowerCase().includes(filters.room.toLowerCase())) return false
      if (filters.day && e.day !== filters.day) return false
      if (q && !e.subject_name.toLowerCase().includes(q) && !e.subject_code.toLowerCase().includes(q) && !e.teacher.toLowerCase().includes(q) && !e.room.toLowerCase().includes(q) && !sanitizedProgram.toLowerCase().includes(q)) return false
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
          {days.map(d => {
            const isToday = d === todayDay
            const dateLabel = dayDateMap?.[d]
            return (
              <div
                key={d}
                className={`tt-day-header ${isToday ? 'tt-day-header-today' : ''}`}
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 2,
                  padding: '6px 4px',
                  background: isToday ? 'var(--accent-soft)' : undefined,
                  borderBottom: isToday ? '2px solid var(--accent)' : undefined,
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                  <span style={{ fontWeight: isToday ? 700 : 600, color: isToday ? 'var(--accent)' : undefined }}>{d}</span>
                  {isToday && (
                    <span style={{
                      fontSize: 9,
                      fontFamily: 'var(--font-mono)',
                      background: 'var(--accent)',
                      color: 'var(--paper)',
                      padding: '1px 5px',
                      borderRadius: 3,
                      textTransform: 'uppercase',
                      letterSpacing: '0.04em',
                      lineHeight: '1.2'
                    }}>
                      Today
                    </span>
                  )}
                </div>
                {dateLabel && (
                  <span style={{
                    fontSize: 10,
                    fontFamily: 'var(--font-mono)',
                    color: isToday ? 'var(--accent)' : 'var(--ink-soft)',
                    fontWeight: isToday ? 600 : 400
                  }}>
                    {dateLabel}
                  </span>
                )}
              </div>
            )
          })}

          {/* Data rows */}
          {dynamicTimeRows.map(row => (
            <div key={`row-${row.label}`} style={{ display: 'contents' }}>
              <div className="tt-time-col" style={{ minHeight: minH }}>
                <span>{row.start}</span>
                <span style={{ fontSize: '9px', color: 'var(--clr-text-3)', display: 'block', marginTop: 2 }}>{row.end}</span>
              </div>
              {days.map(day => {
                const cells = getCellEntries(day, row)
                const isToday = day === todayDay
                return (
                  <div
                    key={`${day}-${row.label}`}
                    className={`tt-cell ${isToday ? 'tt-cell-today' : ''}`}
                    style={{ minHeight: minH }}
                  >
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
            </div>
          ))}
        </div>
      </div>

      {selected && (
        <ClassDetailModal entry={selected} onClose={() => setSelected(null)} />
      )}
    </>
  )
}
