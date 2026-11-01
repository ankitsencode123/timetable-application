import React, { useState, useMemo } from 'react'
import {
  Check, User, MapPin, AlertCircle, CheckCircle2, Clock, Calendar,
  Search, ShieldAlert, Sparkles, Filter, AlertTriangle
} from 'lucide-react'
import type { TimetableEntry, CatalogTeacher } from '../../types'

export interface BusySlotLite {
  id: number
  teacher_short_name: string
  day_of_week: string
  start_time: string | null
  end_time: string | null
  scope: 'permanent' | 'temporary'
  specific_date?: string | null
  reason?: string | null
}

export function toMinutes(t: string | undefined | null): number {
  if (!t) return 0
  const [h, m] = t.split(':').map(Number)
  return (h || 0) * 60 + (m || 0)
}

export function timesOverlap(s1: string, e1: string, s2: string, e2: string): boolean {
  const start1 = toMinutes(s1)
  const end1 = toMinutes(e1)
  const start2 = toMinutes(s2)
  const end2 = toMinutes(e2)
  return start1 < end2 && end1 > start2
}

export function teacherMatches(teacherString: string | null | undefined, targetShort: string): boolean {
  if (!teacherString || !targetShort) return false
  const parts = teacherString.split(/[,/+&]/).map(s => s.trim().replace(/\+$/, '').toLowerCase())
  return parts.includes(targetShort.trim().toLowerCase())
}

const AUTHORITATIVE_LABS = new Set(['r#207a', 'r#207b'])
const DEFAULT_ROOMS = ['R#205', 'R#207A', 'R#207B', 'R#208', 'R#209', 'R#303', 'R#403']
const KNOWN_TEACHERS = [
  'AK', 'SC', 'SK', 'PB', 'RD', 'SKS', 'RKP', 'NC', 'AD', 'SN',
  'GM', 'AB', 'TD', 'AG', 'SR', 'PBn', 'SCh', 'RS', 'DC', 'SGC'
]

// ─────────────────────────────────────────────────────────────────────────────
// 1. LIVE TEACHER AVAILABILITY PICKER
// ─────────────────────────────────────────────────────────────────────────────

interface LiveTeacherPickerProps {
  target: TimetableEntry | null
  entries: TimetableEntry[]
  catalogTeachers: CatalogTeacher[]
  busySlots: BusySlotLite[]
  selectedTeacher: string
  onSelectTeacher: (shortName: string) => void
}

export function LiveTeacherPicker({
  target,
  entries,
  catalogTeachers,
  busySlots,
  selectedTeacher,
  onSelectTeacher,
}: LiveTeacherPickerProps) {
  const [filter, setFilter] = useState<'all' | 'free' | 'busy'>('all')
  const [search, setSearch] = useState('')

  // Compute availability for every teacher
  const teacherStatuses = useMemo(() => {
    if (!target) return []

    // Master list of all teachers (Catalog + Known Static + Entries)
    const allTeacherCodes = Array.from(new Set([
      ...catalogTeachers.map(t => t.short_name),
      ...KNOWN_TEACHERS,
      ...entries.flatMap(e => (e.teacher || '').split(/[,/+&]/).map(s => s.trim().replace(/\+$/, '')).filter(Boolean)),
    ])).filter(Boolean).sort()

    const teacherMap = new Map<string, CatalogTeacher>()
    catalogTeachers.forEach(t => teacherMap.set(t.short_name.toLowerCase(), t))

    const isTheory = (target.type || 'Theory').toLowerCase() === 'theory'

    return allTeacherCodes.map(code => {
      const cat = teacherMap.get(code.toLowerCase())
      const fullName = cat?.full_name || code
      const isCurrent = code.toLowerCase() === (target.teacher || '').toLowerCase()

      // Find other classes taught by this teacher on the same day
      const dayClasses = entries.filter(e => {
        if (e === target) return false
        // Skip exact same slot if it's the target class
        if (
          e.day === target.day &&
          e.start === target.start &&
          e.end === target.end &&
          e.program === target.program &&
          e.semester === target.semester &&
          e.subject_code === target.subject_code
        ) {
          return false
        }
        return e.day === target.day && teacherMatches(e.teacher, code)
      })

      // 1. Direct Time Conflict (teaching overlapping class)
      const overlappingClass = dayClasses.find(c => timesOverlap(target.start, target.end, c.start, c.end))

      // 2. Teacher Busy Slot (administrative leave/block)
      const busySlot = busySlots.find(b => {
        if (!b.teacher_short_name || b.teacher_short_name.toLowerCase() !== code.toLowerCase()) return false
        const dayMatches = !b.day_of_week || b.day_of_week.toLowerCase() === target.day.toLowerCase()
        if (!dayMatches) return false
        if (b.start_time && b.end_time) {
          return timesOverlap(target.start, target.end, b.start_time, b.end_time)
        }
        return true // All day block
      })

      // 3. Rule H5: Max 1 Theory Class Per Day
      const existingTheory = isTheory
        ? dayClasses.find(c => (c.type || 'Theory').toLowerCase() === 'theory')
        : undefined

      let status: 'free' | 'busy_class' | 'busy_slot' | 'theory_limit' | 'current' = 'free'
      let reason = ''
      let detailWhere = ''

      if (isCurrent) {
        status = 'current'
        reason = 'Currently assigned to this class'
        detailWhere = `Currently in ${target.room || 'this room'}`
      } else if (overlappingClass) {
        status = 'busy_class'
        reason = `Busy with ${overlappingClass.subject_name || overlappingClass.subject_code}`
        detailWhere = `In Room ${overlappingClass.room || '—'} teaching ${overlappingClass.subject_name || overlappingClass.subject_code} (${overlappingClass.program} ${overlappingClass.semester}, ${overlappingClass.start}–${overlappingClass.end})`
      } else if (busySlot) {
        status = 'busy_slot'
        reason = busySlot.reason ? `Unavailable: ${busySlot.reason}` : 'Designated busy slot'
        detailWhere = `Unavailable on ${target.day}${busySlot.start_time ? ` (${busySlot.start_time}–${busySlot.end_time})` : ' (full day)'}`
      } else if (existingTheory) {
        status = 'theory_limit'
        reason = `Max 1 Theory/day reached (Rule H5)`
        detailWhere = `Already teaching Theory today: ${existingTheory.subject_name || existingTheory.subject_code} in Room ${existingTheory.room} (${existingTheory.start}–${existingTheory.end})`
      } else {
        status = 'free'
        reason = 'Free & Available'
        if (dayClasses.length > 0) {
          const times = dayClasses.map(c => `${c.start}–${c.end}`).join(', ')
          detailWhere = `Has other classes today at ${times}`
        } else {
          detailWhere = `No other classes on ${target.day}`
        }
      }

      return {
        code,
        fullName,
        isCurrent,
        status,
        reason,
        detailWhere,
        isFree: status === 'free' || status === 'current',
      }
    })
  }, [target, entries, catalogTeachers, busySlots])

  if (!target) {
    return (
      <div style={{
        padding: '16px',
        background: 'var(--clr-bg-3)',
        borderRadius: 'var(--radius)',
        border: '1px dashed var(--clr-border)',
        textAlign: 'center',
        color: 'var(--clr-text-2)',
        fontSize: 'var(--fs-sm)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 6
      }}>
        <Clock size={20} style={{ color: 'var(--clr-primary)', opacity: 0.8 }} />
        <span style={{ fontWeight: 600, color: 'var(--clr-text)' }}>Select a class above first</span>
        <span>Once you select a class, live teacher availability will be calculated for that day and time slot.</span>
      </div>
    )
  }

  const freeCount = teacherStatuses.filter(t => t.status === 'free').length
  const busyCount = teacherStatuses.filter(t => t.status !== 'free' && t.status !== 'current').length

  const filteredTeachers = teacherStatuses.filter(t => {
    if (filter === 'free' && t.status !== 'free' && t.status !== 'current') return false
    if (filter === 'busy' && (t.status === 'free' || t.status === 'current')) return false
    if (search.trim()) {
      const q = search.toLowerCase()
      return t.code.toLowerCase().includes(q) || t.fullName.toLowerCase().includes(q) || t.detailWhere.toLowerCase().includes(q)
    }
    return true
  })

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* Slot Overview Bar */}
      <div style={{
        padding: '10px 14px',
        background: 'var(--clr-primary-10)',
        border: '1px solid var(--clr-primary-20)',
        borderRadius: 'var(--radius)',
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 8,
        fontSize: 'var(--fs-xs)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: 'var(--clr-text)' }}>
          <Calendar size={14} style={{ color: 'var(--clr-primary)' }} />
          <strong>{target.day}</strong>
          <span>•</span>
          <Clock size={14} style={{ color: 'var(--clr-primary)' }} />
          <strong>{target.start}–{target.end}</strong>
          <span>•</span>
          <span>{target.program} {target.semester} ({target.type || 'Theory'})</span>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ color: '#16a34a', fontWeight: 600 }}>🟢 {freeCount} Free</span>
          <span style={{ color: 'var(--clr-text-3)' }}>|</span>
          <span style={{ color: '#dc2626', fontWeight: 600 }}>🔴 {busyCount} Busy</span>
        </div>
      </div>

      {/* Filter and Search Bar */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', background: 'var(--clr-bg-3)', padding: 2, borderRadius: 'var(--radius)', border: '1px solid var(--clr-border)' }}>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => setFilter('all')}
            style={{
              background: filter === 'all' ? 'var(--card-bg)' : 'transparent',
              boxShadow: filter === 'all' ? 'var(--shadow-sm)' : 'none',
              padding: '4px 10px', fontSize: 'var(--fs-xs)', fontWeight: filter === 'all' ? 700 : 500,
              border: 'none', borderRadius: 'calc(var(--radius) - 2px)'
            }}
          >
            All ({teacherStatuses.length})
          </button>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => setFilter('free')}
            style={{
              background: filter === 'free' ? 'var(--card-bg)' : 'transparent',
              color: filter === 'free' ? '#16a34a' : 'inherit',
              boxShadow: filter === 'free' ? 'var(--shadow-sm)' : 'none',
              padding: '4px 10px', fontSize: 'var(--fs-xs)', fontWeight: filter === 'free' ? 700 : 500,
              border: 'none', borderRadius: 'calc(var(--radius) - 2px)'
            }}
          >
            🟢 Free Only ({freeCount})
          </button>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => setFilter('busy')}
            style={{
              background: filter === 'busy' ? 'var(--card-bg)' : 'transparent',
              color: filter === 'busy' ? '#dc2626' : 'inherit',
              boxShadow: filter === 'busy' ? 'var(--shadow-sm)' : 'none',
              padding: '4px 10px', fontSize: 'var(--fs-xs)', fontWeight: filter === 'busy' ? 700 : 500,
              border: 'none', borderRadius: 'calc(var(--radius) - 2px)'
            }}
          >
            🔴 Busy ({busyCount})
          </button>
        </div>

        <div style={{ flex: 1, minWidth: 150, position: 'relative' }}>
          <Search size={13} style={{ position: 'absolute', left: 9, top: '50%', transform: 'translateY(-50%)', color: 'var(--clr-text-3)' }} />
          <input
            className="form-control"
            placeholder="Search teacher name or code…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{ paddingLeft: 28, fontSize: 'var(--fs-xs)', height: 32 }}
          />
        </div>
      </div>

      {/* Teachers List / Grid */}
      <div style={{
        maxHeight: 280,
        overflowY: 'auto',
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
        paddingRight: 2,
      }}>
        {filteredTeachers.map(t => {
          const isSelected = selectedTeacher.toLowerCase() === t.code.toLowerCase()

          // Status colors
          let statusBg = 'rgba(34, 197, 94, 0.12)'
          let statusColor = '#15803d'
          let statusBorder = 'rgba(34, 197, 94, 0.3)'
          let statusPill = '🟢 Free'

          if (t.status === 'current') {
            statusBg = 'rgba(59, 130, 246, 0.12)'
            statusColor = '#2563eb'
            statusBorder = 'rgba(59, 130, 246, 0.3)'
            statusPill = '👤 Current'
          } else if (t.status === 'busy_class') {
            statusBg = 'rgba(239, 68, 68, 0.12)'
            statusColor = '#b91c1c'
            statusBorder = 'rgba(239, 68, 68, 0.3)'
            statusPill = '🔴 Busy'
          } else if (t.status === 'theory_limit') {
            statusBg = 'rgba(245, 158, 11, 0.12)'
            statusColor = '#b45309'
            statusBorder = 'rgba(245, 158, 11, 0.3)'
            statusPill = '⚠️ Max Theory'
          } else if (t.status === 'busy_slot') {
            statusBg = 'rgba(168, 85, 247, 0.12)'
            statusColor = '#7e22ce'
            statusBorder = 'rgba(168, 85, 247, 0.3)'
            statusPill = '⛔ Unavailable'
          }

          return (
            <div
              key={t.code}
              onClick={() => onSelectTeacher(t.code)}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '9px 12px',
                borderRadius: 'var(--radius)',
                cursor: 'pointer',
                transition: 'all 0.15s ease',
                border: isSelected ? '2px solid var(--accent)' : '1px solid var(--clr-border)',
                background: isSelected
                  ? 'var(--accent-soft)'
                  : t.isFree
                    ? 'var(--card-bg)'
                    : 'var(--clr-bg-3)',
                boxShadow: isSelected ? '0 0 0 1px var(--accent)' : 'none',
              }}
            >
              {/* Left Side: Teacher info and location/activity */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0, flex: 1, marginRight: 8 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{
                    fontFamily: 'var(--font-mono)',
                    fontWeight: 700,
                    fontSize: 'var(--fs-xs)',
                    padding: '2px 6px',
                    borderRadius: 4,
                    background: isSelected ? 'var(--accent)' : 'var(--clr-bg-3)',
                    color: isSelected ? '#fff' : 'var(--clr-text)',
                  }}>
                    {t.code}
                  </span>
                  <span style={{ fontWeight: 600, fontSize: 'var(--fs-sm)', color: 'var(--clr-text)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {t.fullName}
                  </span>
                </div>

                {/* Where / Context detail */}
                <div style={{ fontSize: '11px', color: 'var(--clr-text-2)', display: 'flex', alignItems: 'center', gap: 4 }}>
                  <span style={{
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    fontWeight: t.status === 'free' ? 400 : 500,
                    color: t.status === 'free' ? 'var(--clr-text-3)' : statusColor,
                  }}>
                    {t.detailWhere}
                  </span>
                </div>
              </div>

              {/* Right Side: Status badge and selection mark */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
                <span style={{
                  fontSize: '11px',
                  fontWeight: 600,
                  padding: '3px 8px',
                  borderRadius: 999,
                  background: statusBg,
                  color: statusColor,
                  border: `1px solid ${statusBorder}`,
                  whiteSpace: 'nowrap',
                }}>
                  {statusPill}
                </span>

                {isSelected && (
                  <span style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    width: 20,
                    height: 20,
                    borderRadius: '50%',
                    background: 'var(--accent)',
                    color: '#fff',
                  }}>
                    <Check size={12} strokeWidth={3} />
                  </span>
                )}
              </div>
            </div>
          )
        })}

        {filteredTeachers.length === 0 && (
          <div style={{ padding: '16px', textAlign: 'center', color: 'var(--clr-text-3)', fontSize: 'var(--fs-sm)' }}>
            No teachers found matching your filter.
          </div>
        )}
      </div>
    </div>
  )
}


// ─────────────────────────────────────────────────────────────────────────────
// 2. LIVE ROOM AVAILABILITY PICKER
// ─────────────────────────────────────────────────────────────────────────────

interface LiveRoomPickerProps {
  target: TimetableEntry | null
  entries: TimetableEntry[]
  selectedRoom: string
  onSelectRoom: (roomName: string) => void
}

export function LiveRoomPicker({
  target,
  entries,
  selectedRoom,
  onSelectRoom,
}: LiveRoomPickerProps) {
  const [filter, setFilter] = useState<'all' | 'free' | 'busy'>('all')

  const roomStatuses = useMemo(() => {
    if (!target) return []

    // Master list of all rooms
    const allRooms = Array.from(new Set([
      ...DEFAULT_ROOMS,
      ...entries.map(e => e.room).filter(Boolean),
    ])).sort()

    const isPractical = (target.type || 'Theory').toLowerCase() === 'practical'

    return allRooms.map(room => {
      const isCurrent = room.toLowerCase() === (target.room || '').toLowerCase()

      // Find any overlapping class in this room on the same day
      const overlappingClass = entries.find(e => {
        if (e === target) return false
        if (
          e.day === target.day &&
          e.start === target.start &&
          e.end === target.end &&
          e.program === target.program &&
          e.semester === target.semester &&
          e.subject_code === target.subject_code
        ) {
          return false
        }
        return (
          e.day === target.day &&
          (e.room || '').toLowerCase() === room.toLowerCase() &&
          timesOverlap(target.start, target.end, e.start, e.end)
        )
      })

      const isLab = AUTHORITATIVE_LABS.has(room.toLowerCase()) || room.toLowerCase().includes('lab')
      const labMismatch = isPractical && !isLab

      let status: 'free' | 'occupied' | 'lab_mismatch' | 'current' = 'free'
      let reason = ''
      let detailWhere = ''

      if (isCurrent) {
        status = 'current'
        reason = 'Currently assigned room'
        detailWhere = `Current room for this class`
      } else if (overlappingClass) {
        status = 'occupied'
        reason = `Occupied by ${overlappingClass.subject_name || overlappingClass.subject_code}`
        detailWhere = `Occupied by ${overlappingClass.subject_name || overlappingClass.subject_code} (${overlappingClass.program} ${overlappingClass.semester}, Prof. ${overlappingClass.teacher}, ${overlappingClass.start}–${overlappingClass.end})`
      } else if (labMismatch) {
        status = 'lab_mismatch'
        reason = 'Lecture Hall (No Lab)'
        detailWhere = `Practical classes require a computer lab (R#207A, R#207B)`
      } else {
        status = 'free'
        reason = 'Free & Available'
        detailWhere = isLab ? '🔬 Lab Room • Available at this slot' : '🏛 Lecture Hall • Available at this slot'
      }

      return {
        room,
        isLab,
        isCurrent,
        status,
        reason,
        detailWhere,
        isFree: status === 'free' || status === 'current',
      }
    })
  }, [target, entries])

  if (!target) {
    return (
      <div style={{
        padding: '16px',
        background: 'var(--clr-bg-3)',
        borderRadius: 'var(--radius)',
        border: '1px dashed var(--clr-border)',
        textAlign: 'center',
        color: 'var(--clr-text-2)',
        fontSize: 'var(--fs-sm)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 6
      }}>
        <MapPin size={20} style={{ color: 'var(--clr-primary)', opacity: 0.8 }} />
        <span style={{ fontWeight: 600, color: 'var(--clr-text)' }}>Select a class above first</span>
        <span>Once you select a class, live room occupancy will be calculated for that day and time slot.</span>
      </div>
    )
  }

  const freeCount = roomStatuses.filter(r => r.status === 'free').length
  const occupiedCount = roomStatuses.filter(r => r.status === 'occupied' || r.status === 'lab_mismatch').length

  const filteredRooms = roomStatuses.filter(r => {
    if (filter === 'free' && r.status !== 'free' && r.status !== 'current') return false
    if (filter === 'busy' && (r.status === 'free' || r.status === 'current')) return false
    return true
  })

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* Slot Overview Bar */}
      <div style={{
        padding: '10px 14px',
        background: 'var(--clr-primary-10)',
        border: '1px solid var(--clr-primary-20)',
        borderRadius: 'var(--radius)',
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 8,
        fontSize: 'var(--fs-xs)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: 'var(--clr-text)' }}>
          <Calendar size={14} style={{ color: 'var(--clr-primary)' }} />
          <strong>{target.day}</strong>
          <span>•</span>
          <Clock size={14} style={{ color: 'var(--clr-primary)' }} />
          <strong>{target.start}–{target.end}</strong>
          <span>•</span>
          <span>{target.program} {target.semester} ({target.type || 'Theory'})</span>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ color: '#16a34a', fontWeight: 600 }}>🟢 {freeCount} Free</span>
          <span style={{ color: 'var(--clr-text-3)' }}>|</span>
          <span style={{ color: '#dc2626', fontWeight: 600 }}>🔴 {occupiedCount} Busy</span>
        </div>
      </div>

      {/* Filter Tabs */}
      <div style={{ display: 'flex', background: 'var(--clr-bg-3)', padding: 2, borderRadius: 'var(--radius)', border: '1px solid var(--clr-border)', width: 'fit-content' }}>
        <button
          type="button"
          className="btn btn-sm"
          onClick={() => setFilter('all')}
          style={{
            background: filter === 'all' ? 'var(--card-bg)' : 'transparent',
            boxShadow: filter === 'all' ? 'var(--shadow-sm)' : 'none',
            padding: '4px 10px', fontSize: 'var(--fs-xs)', fontWeight: filter === 'all' ? 700 : 500,
            border: 'none', borderRadius: 'calc(var(--radius) - 2px)'
          }}
        >
          All Rooms ({roomStatuses.length})
        </button>
        <button
          type="button"
          className="btn btn-sm"
          onClick={() => setFilter('free')}
          style={{
            background: filter === 'free' ? 'var(--card-bg)' : 'transparent',
            color: filter === 'free' ? '#16a34a' : 'inherit',
            boxShadow: filter === 'free' ? 'var(--shadow-sm)' : 'none',
            padding: '4px 10px', fontSize: 'var(--fs-xs)', fontWeight: filter === 'free' ? 700 : 500,
            border: 'none', borderRadius: 'calc(var(--radius) - 2px)'
          }}
        >
          🟢 Free Only ({freeCount})
        </button>
        <button
          type="button"
          className="btn btn-sm"
          onClick={() => setFilter('busy')}
          style={{
            background: filter === 'busy' ? 'var(--card-bg)' : 'transparent',
            color: filter === 'busy' ? '#dc2626' : 'inherit',
            boxShadow: filter === 'busy' ? 'var(--shadow-sm)' : 'none',
            padding: '4px 10px', fontSize: 'var(--fs-xs)', fontWeight: filter === 'busy' ? 700 : 500,
            border: 'none', borderRadius: 'calc(var(--radius) - 2px)'
          }}
        >
          🔴 Occupied ({occupiedCount})
        </button>
      </div>

      {/* Rooms List */}
      <div style={{
        maxHeight: 280,
        overflowY: 'auto',
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
        paddingRight: 2,
      }}>
        {filteredRooms.map(r => {
          const isSelected = selectedRoom.toLowerCase() === r.room.toLowerCase()

          // Status colors
          let statusBg = 'rgba(34, 197, 94, 0.12)'
          let statusColor = '#15803d'
          let statusBorder = 'rgba(34, 197, 94, 0.3)'
          let statusPill = '🟢 Free'

          if (r.status === 'current') {
            statusBg = 'rgba(59, 130, 246, 0.12)'
            statusColor = '#2563eb'
            statusBorder = 'rgba(59, 130, 246, 0.3)'
            statusPill = '🏛 Current'
          } else if (r.status === 'occupied') {
            statusBg = 'rgba(239, 68, 68, 0.12)'
            statusColor = '#b91c1c'
            statusBorder = 'rgba(239, 68, 68, 0.3)'
            statusPill = '🔴 Occupied'
          } else if (r.status === 'lab_mismatch') {
            statusBg = 'rgba(245, 158, 11, 0.12)'
            statusColor = '#b45309'
            statusBorder = 'rgba(245, 158, 11, 0.3)'
            statusPill = '⚠️ No Lab'
          }

          return (
            <div
              key={r.room}
              onClick={() => onSelectRoom(r.room)}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '9px 12px',
                borderRadius: 'var(--radius)',
                cursor: 'pointer',
                transition: 'all 0.15s ease',
                border: isSelected ? '2px solid var(--accent)' : '1px solid var(--clr-border)',
                background: isSelected
                  ? 'var(--accent-soft)'
                  : r.isFree
                    ? 'var(--card-bg)'
                    : 'var(--clr-bg-3)',
                boxShadow: isSelected ? '0 0 0 1px var(--accent)' : 'none',
              }}
            >
              {/* Left Side: Room code and info */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0, flex: 1, marginRight: 8 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{
                    fontFamily: 'var(--font-mono)',
                    fontWeight: 700,
                    fontSize: 'var(--fs-sm)',
                    padding: '2px 8px',
                    borderRadius: 4,
                    background: isSelected ? 'var(--accent)' : 'var(--clr-bg-3)',
                    color: isSelected ? '#fff' : 'var(--clr-text)',
                  }}>
                    {r.room}
                  </span>
                  <span style={{
                    fontSize: '11px',
                    fontWeight: 600,
                    color: r.isLab ? 'var(--amber)' : 'var(--ink-soft)',
                    background: r.isLab ? 'var(--amber-soft)' : 'var(--clr-bg-3)',
                    padding: '1px 6px',
                    borderRadius: 4,
                  }}>
                    {r.isLab ? '🔬 Lab' : '🏛 Lecture Hall'}
                  </span>
                </div>

                {/* Occupancy details */}
                <div style={{ fontSize: '11px', color: 'var(--clr-text-2)', display: 'flex', alignItems: 'center', gap: 4 }}>
                  <span style={{
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    fontWeight: r.status === 'free' ? 400 : 500,
                    color: r.status === 'free' ? 'var(--clr-text-3)' : statusColor,
                  }}>
                    {r.detailWhere}
                  </span>
                </div>
              </div>

              {/* Right Side: Status badge and selection check */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
                <span style={{
                  fontSize: '11px',
                  fontWeight: 600,
                  padding: '3px 8px',
                  borderRadius: 999,
                  background: statusBg,
                  color: statusColor,
                  border: `1px solid ${statusBorder}`,
                  whiteSpace: 'nowrap',
                }}>
                  {statusPill}
                </span>

                {isSelected && (
                  <span style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    width: 20,
                    height: 20,
                    borderRadius: '50%',
                    background: 'var(--accent)',
                    color: '#fff',
                  }}>
                    <Check size={12} strokeWidth={3} />
                  </span>
                )}
              </div>
            </div>
          )
        })}

        {filteredRooms.length === 0 && (
          <div style={{ padding: '16px', textAlign: 'center', color: 'var(--clr-text-3)', fontSize: 'var(--fs-sm)' }}>
            No rooms found matching your filter.
          </div>
        )}
      </div>
    </div>
  )
}
