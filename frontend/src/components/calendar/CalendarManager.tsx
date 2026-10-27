import { useState, useEffect, useCallback, useMemo } from 'react'
import {
  ChevronLeft, ChevronRight, Plus, Trash2, AlertCircle,
  Calendar, Shield, ClipboardList, X, RefreshCw, Info, Lightbulb, Home,
} from 'lucide-react'
import {
  getCalendarMonth, adminGetCalendarDay, adminListOverrides,
  adminCreateOverride, adminDeleteOverride, adminListValidity,
  adminCreateValidity, adminDeleteValidity, adminCalendarAudit,
  listVersions, executeActions, adminCalendarSuggest, formatViolation, req,
  type CalendarMonthDay, type CalendarDayResult, type CalendarDayEntry,
  type CalendarOverrideRecord, type CalendarValidityRecord,
} from '../../api'
import type { TimetableVersion } from '../../types'
import { useWorkspaceStore } from '../../store'
import SuggestInput, { type SuggestOption } from '../common/SuggestInput'

// ── availability helpers (teacher / room free-busy for a time window) ────────

const DEFAULT_ROOMS = ['R#205', 'R#207A', 'R#207B', 'R#208', 'R#209', 'R#303', 'R#403']
const SEMESTERS = ['1st', '2nd', '3rd', '4th', '5th', '6th', '7th', '8th']
const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

interface BusySlotLite {
  teacher_short_name: string
  scope: string
  day_of_week?: string | null
  specific_date?: string | null
  reason?: string | null
}

function toMin(t: string): number {
  const m = /^(\d{1,2}):(\d{2})/.exec(t || '')
  return m ? Number(m[1]) * 60 + Number(m[2]) : NaN
}
function splitTeachers(t: string): string[] {
  return (t || '').split(/[,/&]+/).map(x => x.trim()).filter(Boolean)
}
function normRoom(r: string): string {
  return (r || '').replace(/\s+/g, '').toUpperCase()
}

/**
 * Who / what is already occupied on `date` between start–end.
 * Uses the effective day schedule (base + overrides, cancelled classes already
 * removed) plus the teachers' busy-slot declarations. Returns null when the
 * window is not yet valid. Pure + cheap: O(classes on that day).
 */
function computeAvailability(
  entries: CalendarDayEntry[], start: string, end: string, excludeKey: string,
  busySlots: BusySlotLite[], date: string, weekday?: string,
): { teachers: Map<string, string>; rooms: Map<string, string> } | null {
  const s = toMin(start), e = toMin(end)
  if (!Number.isFinite(s) || !Number.isFinite(e) || e <= s) return null
  const teachers = new Map<string, string>()
  const rooms = new Map<string, string>()

  for (const c of entries) {
    if (excludeKey && c.cal_key === excludeKey) continue        // the class being modified
    const cs = toMin(c.start), ce = toMin(c.end)
    if (!(cs < e && s < ce)) continue                             // no overlap
    const what = `${c.program} ${c.semester} · ${c.subject_code || c.subject_name} ${c.start}–${c.end}`
    splitTeachers(c.teacher).forEach(t => { if (!teachers.has(t)) teachers.set(t, what) })
    if (c.room?.trim()) { const k = normRoom(c.room); if (!rooms.has(k)) rooms.set(k, what) }
  }

  const wd = weekday || WEEKDAYS[new Date(date + 'T00:00:00').getDay()]
  for (const b of busySlots) {
    const hit = b.scope === 'temporary'
      ? (b.specific_date || '').slice(0, 10) === date
      : (b.day_of_week || '').toLowerCase() === wd.toLowerCase()
    if (hit && !teachers.has(b.teacher_short_name)) {
      teachers.set(b.teacher_short_name, `marked busy${b.reason ? ` (${b.reason})` : ''}`)
    }
  }
  return { teachers, rooms }
}

// ── helpers ───────────────────────────────────────────────────────────────────
function toISO(y: number, m: number, d: number) {
  return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`
}
function today() {
  const d = new Date()
  return toISO(d.getFullYear(), d.getMonth() + 1, d.getDate())
}
function daysInMonth(y: number, m: number) {
  return new Date(y, m, 0).getDate()
}
function firstDayOfMonth(y: number, m: number) {
  // 0=Sun..6=Sat → Mon-based offset
  return (new Date(y, m - 1, 1).getDay() + 6) % 7
}
const MONTH_NAMES = ['January','February','March','April','May','June','July','August','September','October','November','December']
const DOW = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']

function actionColor(a: string): string {
  return a === 'ADD' ? 'var(--accent)' : a === 'CANCEL' ? 'var(--violation)' : a === 'DAY_OFF' ? 'var(--amber)' : 'var(--clr-purple)'
}
function actionBg(a: string): string {
  return a === 'ADD' ? 'var(--accent-soft)' : a === 'CANCEL' ? 'var(--violation-soft)' : a === 'DAY_OFF' ? 'var(--amber-soft)' : 'color-mix(in oklab, var(--clr-purple) 10%, transparent)'
}

// ── sub-tabs ──────────────────────────────────────────────────────────────────
type SubTab = 'grid' | 'overrides' | 'validity' | 'audit'

export default function CalendarManager() {
  const [sub, setSub] = useState<SubTab>('grid')
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>
      {/* sub-tabs */}
      <div className="tabs" style={{ width: 'fit-content' }}>
        <TabBtn active={sub === 'grid'}      onClick={() => setSub('grid')}      label="Month View"       icon={<Calendar size={12} />} />
        <TabBtn active={sub === 'overrides'} onClick={() => setSub('overrides')} label="Day Overrides"    icon={<ClipboardList size={12} />} />
        <TabBtn active={sub === 'validity'}  onClick={() => setSub('validity')}  label="Validity Windows" icon={<Shield size={12} />} />
        <TabBtn active={sub === 'audit'}     onClick={() => setSub('audit')}     label="Audit Log"        icon={<ClipboardList size={12} />} />
      </div>

      {sub === 'grid'      && <MonthGridPanel />}
      {sub === 'overrides' && <OverridesPanel />}
      {sub === 'validity'  && <ValidityPanel />}
      {sub === 'audit'     && <AuditPanel />}
    </div>
  )
}

function TabBtn({ active, onClick, label, icon }: any) {
  return (
    <button onClick={onClick} className={`tab ${active ? 'active' : ''}`} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
      {icon} {label}
    </button>
  )
}

// ═══════════════════════════════════════════════════════════════════════════════
// Month Grid Panel
// ═══════════════════════════════════════════════════════════════════════════════
function MonthGridPanel() {
  const now = new Date()
  const [year, setYear] = useState(now.getFullYear())
  const [month, setMonth] = useState(now.getMonth() + 1)
  const [monthData, setMonthData] = useState<CalendarMonthDay[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const [dayData, setDayData] = useState<(CalendarDayResult & { violations: any[]; overrides: CalendarOverrideRecord[] }) | null>(null)
  const [dayLoading, setDayLoading] = useState(false)
  const [showOverrideModal, setShowOverrideModal] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try { setMonthData((await getCalendarMonth(year, month)).days) }
    catch (e: any) { setError(e.message) }
    finally { setLoading(false) }
  }, [year, month])

  useEffect(() => { load() }, [load])

  async function selectDay(date: string) {
    setSelectedDate(date); setDayData(null); setDayLoading(true)
    try { setDayData(await adminGetCalendarDay(date)) }
    catch (e: any) { setError(e.message) }
    finally { setDayLoading(false) }
  }

  async function deleteOverride(id: number, force = false) {
    try {
      await adminDeleteOverride(id, force)
      if (selectedDate) await selectDay(selectedDate)
      await load()
    } catch (e: any) {
      if ((e as any).status === 409 && !force) {
        if (confirm(`Removing this override may introduce constraint violations. Force delete?`)) {
          await deleteOverride(id, true)
        }
      } else { alert(e.message) }
    }
  }

  const dayMap = Object.fromEntries(monthData.map(d => [d.date, d]))
  const offset = firstDayOfMonth(year, month)
  const total = daysInMonth(year, month)
  const cells: (number | null)[] = [...Array(offset).fill(null), ...Array.from({length: total}, (_, i) => i + 1)]
  while (cells.length % 7 !== 0) cells.push(null)

  const todayStr = today()

  return (
    <div style={{ display: 'flex', gap: 'var(--sp-4)', flexWrap: 'wrap', alignItems: 'flex-start', flexDirection: 'column' }}>
      {/* Calendar Grid + Detail side-by-side on wide, stacked on mobile */}
      <div style={{ display: 'flex', gap: 'var(--sp-4)', flexWrap: 'wrap', alignItems: 'flex-start', width: '100%' }}>
      {/* Calendar Grid Column */}
      <div style={{ flex: '1 1 300px', minWidth: 0 }}>
        {/* Month navigation */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 'var(--sp-3)' }}>
          <button className="btn btn-ghost btn-sm btn-icon" onClick={() => { if (month === 1) { setYear(y => y-1); setMonth(12) } else { setMonth(m => m-1) } }}>
            <ChevronLeft size={14} />
          </button>
          <span style={{ fontFamily: 'var(--font-display)', fontWeight: 600, fontSize: 'var(--fs-md)', color: 'var(--ink)' }}>
            {MONTH_NAMES[month-1]} {year}
          </span>
          <button className="btn btn-ghost btn-sm btn-icon" onClick={() => { if (month === 12) { setYear(y => y+1); setMonth(1) } else { setMonth(m => m+1) } }}>
            <ChevronRight size={14} />
          </button>
        </div>

        {error && <ErrBanner msg={error} />}

        {loading ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 'var(--sp-8)' }}><span className="spinner-lg spinner" /></div>
        ) : (
          <div style={{ border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', overflow: 'hidden' }}>
            {/* Day headers */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', background: 'var(--paper)', borderBottom: '1px solid var(--line)' }}>
              {DOW.map(d => (
                <div key={d} style={{ padding: '6px 4px', textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500, color: 'var(--ink-soft)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
                  {d}
                </div>
              ))}
            </div>
            {/* Day cells */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)' }}>
              {cells.map((day, idx) => {
                if (!day) return <div key={idx} style={{ minHeight: 48, background: 'var(--paper)', borderRight: '1px solid var(--line)', borderBottom: '1px solid var(--line)' }} />
                const dateStr = toISO(year, month, day)
                const info = dayMap[dateStr]
                const isToday = dateStr === todayStr
                const isSelected = selectedDate === dateStr
                const isHoliday = info?.is_holiday
                const hasChanges = info?.has_changes
                return (
                  <button key={idx} onClick={() => selectDay(dateStr)} style={{
                    minHeight: 48, padding: '4px 4px 2px',
                    background: isSelected ? 'var(--accent-soft)' : isHoliday ? 'var(--violation-soft)' : isToday ? 'var(--amber-soft)' : 'var(--card-bg)',
                    border: 'none',
                    borderRight: '1px solid var(--line)', borderBottom: '1px solid var(--line)',
                    cursor: 'pointer', textAlign: 'left',
                    transition: 'var(--transition)',
                    outline: isSelected ? `2px solid var(--accent)` : isToday ? `2px solid var(--amber)` : 'none',
                    outlineOffset: -2,
                  }}>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: isToday ? 700 : 500, color: isSelected ? 'var(--accent)' : isHoliday ? 'var(--violation)' : isToday ? 'var(--amber)' : 'var(--ink)', marginBottom: 2 }}>
                      {day}
                    </div>
                    {info && (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
                        {info.classes > 0 && (
                          <span style={{ fontSize: 8, fontFamily: 'var(--font-mono)', color: 'var(--accent)', background: 'color-mix(in oklab, var(--accent) 10%, transparent)', borderRadius: 3, padding: '1px 3px' }}>
                            {info.classes}c
                          </span>
                        )}
                        {isHoliday && <span style={{ fontSize: 8, fontFamily: 'var(--font-mono)', color: 'var(--violation)', background: 'var(--violation-soft)', borderRadius: 3, padding: '1px 3px' }}>off</span>}
                        {hasChanges && !isHoliday && <span style={{ fontSize: 8, fontFamily: 'var(--font-mono)', color: 'var(--amber)', background: 'var(--amber-soft)', borderRadius: 3, padding: '1px 3px' }}>✎</span>}
                      </div>
                    )}
                  </button>
                )
              })}
            </div>
          </div>
        )}

        {/* Legend */}
        <div style={{ display: 'flex', gap: 'var(--sp-3)', marginTop: 'var(--sp-3)', flexWrap: 'wrap' }}>
          {[
            { color: 'var(--amber)', bg: 'var(--amber-soft)', label: 'Today' },
            { color: 'var(--violation)', bg: 'var(--violation-soft)', label: 'Holiday' },
            { color: 'var(--amber)', bg: 'var(--amber-soft)', label: 'Has overrides' },
            { color: 'var(--clr-purple)', bg: 'color-mix(in oklab, var(--clr-purple) 10%, transparent)', label: 'Custom validity' },
          ].map(({ color, bg, label }) => (
            <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)' }}>
              <div style={{ width: 10, height: 10, background: bg, border: `1px solid ${color}`, borderRadius: 2 }} />
              {label}
            </div>
          ))}
        </div>
      </div>

      {/* Day Detail Panel */}
      {selectedDate && (
        <div style={{ flex: '1 1 300px', minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 'var(--sp-3)' }}>
            <div>
              <div className="mono-label">{dayData?.weekday ?? ''}</div>
              <h3 style={{ fontFamily: 'var(--font-display)', fontWeight: 600, fontSize: 'var(--fs-lg)', color: 'var(--ink)', letterSpacing: '-0.015em' }}>{selectedDate}</h3>
            </div>
            <div style={{ display: 'flex', gap: 6 }}>
              <button className="btn btn-success btn-sm" onClick={() => setShowOverrideModal(true)} id="add-override-btn">
                <Plus size={12} /> Add Override
              </button>
              <button className="btn btn-ghost btn-sm btn-icon" onClick={() => { setSelectedDate(null); setDayData(null) }}>
                <X size={13} />
              </button>
            </div>
          </div>

          {dayLoading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: 'var(--sp-5)' }}><span className="spinner spinner-lg" /></div>
          ) : dayData ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)' }}>
              {/* Notices */}
              {dayData.notices.length > 0 && (
                <div style={{ background: 'var(--amber-soft)', border: '1px solid color-mix(in oklab, var(--amber) 25%, transparent)', borderRadius: 'var(--radius)', padding: 'var(--sp-3)', display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {dayData.notices.map((n, i) => <div key={i} style={{ fontSize: 'var(--fs-xs)', color: 'var(--amber)', display: 'flex', alignItems: 'center', gap: 5 }}><Info size={11} />{n}</div>)}
                </div>
              )}

              {/* Validity window */}
              {dayData.validity_label && (
                <div style={{ background: 'color-mix(in oklab, var(--clr-purple) 8%, transparent)', border: '1px solid color-mix(in oklab, var(--clr-purple) 20%, transparent)', borderRadius: 'var(--radius)', padding: 'var(--sp-2) var(--sp-3)', fontSize: 'var(--fs-xs)', color: 'var(--clr-purple)' }}>
                  <Shield size={11} style={{ display: 'inline', marginRight: 4 }} />
                  Validity: {dayData.validity_label} (v{dayData.version_id})
                </div>
              )}

              {/* Active Classes */}
              <div>
                <div className="mono-label" style={{ marginBottom: 'var(--sp-2)' }}>Active Classes ({dayData.entries.length})</div>
                {dayData.entries.length === 0 ? (
                  <div style={{ color: 'var(--ink-soft)', fontSize: 'var(--fs-xs)', padding: 'var(--sp-3)', textAlign: 'center', border: '1px dashed var(--line)', borderRadius: 'var(--radius)' }}>No classes</div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {dayData.entries.map((e, i) => (
                      <div key={i} className="class-cell" style={{ background: e.type === 'Practical' ? 'var(--amber-soft)' : 'var(--accent-soft)', border: `1px solid ${e.type === 'Practical' ? 'color-mix(in oklab, var(--amber) 25%, transparent)' : 'color-mix(in oklab, var(--accent) 25%, transparent)'}`, position: 'relative' }}>
                        {e.cal_source && e.cal_source !== 'base' && (
                          <span style={{ position: 'absolute', top: 4, right: 6, fontFamily: 'var(--font-mono)', fontSize: 8, textTransform: 'uppercase', letterSpacing: '0.08em', color: actionColor(e.cal_source === 'added' ? 'ADD' : 'MODIFY'), background: actionBg(e.cal_source === 'added' ? 'ADD' : 'MODIFY'), borderRadius: 3, padding: '1px 4px' }}>
                            {e.cal_source}
                          </span>
                        )}
                        <div className="cell-subject">{e.subject_code ? `${e.subject_code} — ` : ''}{e.subject_name}</div>
                        <div className="cell-meta">
                          <span>{e.start}–{e.end}</span>
                          <span>·</span><span>{e.teacher}</span>
                          <span>·</span><span>{e.room}</span>
                          <span>·</span><span>{e.program} {e.semester}</span>
                        </div>
                        {e.cal_note && <div style={{ fontSize: 9, color: 'var(--amber)', marginTop: 2, fontFamily: 'var(--font-mono)' }}>{e.cal_note}</div>}
                        {/* CANCEL/MODIFY actions */}
                        {e.cal_source === 'base' && (
                          <div style={{ display: 'flex', gap: 4, marginTop: 6 }}>
                            <button className="btn btn-xs btn-warning" onClick={async () => {
                              if (dayData && e.cal_key) {
                                const reason = prompt('Reason for cancellation (optional):') ?? ''
                                try {
                                  await adminCreateOverride({ date: selectedDate!, action: 'CANCEL', target_key: e.cal_key, reason })
                                  await selectDay(selectedDate!)
                                  load()
                                } catch (err: any) { alert(err.message) }
                              }
                            }}>Cancel class</button>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Cancelled Classes */}
              {dayData.cancelled.length > 0 && (
                <div>
                  <div className="mono-label" style={{ marginBottom: 'var(--sp-2)', color: 'var(--violation)' }}>Cancelled ({dayData.cancelled.length})</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {dayData.cancelled.map((e, i) => (
                      <div key={i} className="class-cell cancelled" style={{ background: 'var(--violation-soft)', border: '1px solid color-mix(in oklab, var(--violation) 20%, transparent)' }}>
                        <div className="cell-subject">{e.subject_code ? `${e.subject_code} — ` : ''}{e.subject_name}</div>
                        <div className="cell-meta"><span>{e.start}–{e.end}</span> · <span>{e.teacher}</span></div>
                        {e.cal_note && <div style={{ fontSize: 9, color: 'var(--violation)', fontFamily: 'var(--font-mono)', marginTop: 2 }}>{e.cal_note}</div>}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Overrides on this day */}
              {dayData.overrides.length > 0 && (
                <div>
                  <div className="mono-label" style={{ marginBottom: 'var(--sp-2)' }}>Overrides on this day</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {dayData.overrides.map(ov => (
                      <div key={ov.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 10px', background: actionBg(ov.action), border: `1px solid color-mix(in oklab, ${actionColor(ov.action)} 25%, transparent)`, borderRadius: 'var(--radius)' }}>
                        <div>
                          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 600, color: actionColor(ov.action), textTransform: 'uppercase', letterSpacing: '0.08em' }}>{ov.action}</span>
                          {ov.reason && <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)', marginLeft: 8 }}>{ov.reason}</span>}
                          {ov.forced && <span style={{ marginLeft: 6, fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--amber)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>forced</span>}
                        </div>
                        <button className="btn btn-xs btn-danger" onClick={() => deleteOverride(ov.id)}>
                          <Trash2 size={10} />
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Violations */}
              {dayData.violations.length > 0 && (
                <div style={{ background: 'var(--violation-soft)', border: '1px solid color-mix(in oklab, var(--violation) 25%, transparent)', borderRadius: 'var(--radius)', padding: 'var(--sp-3)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: 'var(--violation)', fontWeight: 600, fontSize: 'var(--fs-xs)', marginBottom: 6 }}>
                    <AlertCircle size={13} /> {dayData.violations.length} constraint violation{dayData.violations.length > 1 ? 's' : ''} on this day
                  </div>
                  {dayData.violations.map((v, i) => (
                    <div key={i} style={{ fontSize: 'var(--fs-xs)', color: 'var(--violation)', marginBottom: 3, fontFamily: 'var(--font-mono)' }}>{formatViolation(v)}</div>
                  ))}
                </div>
              )}
            </div>
          ) : null}
        </div>
      )}
      </div>{/* end side-by-side row */}

      {/* Override Modal */}
      {showOverrideModal && selectedDate && (
        <OverrideModal
          date={selectedDate}
          dayData={dayData}
          onClose={() => setShowOverrideModal(false)}
          onDone={async () => {
            setShowOverrideModal(false)
            await selectDay(selectedDate)
            load()
          }}
        />
      )}
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════════
// Override Create Modal
// ═══════════════════════════════════════════════════════════════════════════════
function OverrideModal({ date, dayData, onClose, onDone }: {
  date: string
  dayData: (CalendarDayResult & { violations: any[]; overrides: CalendarOverrideRecord[] }) | null
  onClose: () => void
  onDone: () => void
}) {
  type Action = 'ADD' | 'CANCEL' | 'MODIFY' | 'DAY_OFF'
  const [action, setAction] = useState<Action>('DAY_OFF')
  const [reason, setReason] = useState('')
  const [targetKey, setTargetKey] = useState('')
  const [program, setProgram] = useState('')
  const [semester, setSemester] = useState('')
  const [force, setForce] = useState(false)
  const [addEntry, setAddEntry] = useState({ program: '', semester: '', start: '', end: '', subject_code: '', subject_name: '', teacher: '', room: '', type: 'Theory' })
  const [modChanges, setModChanges] = useState({ start: '', end: '', subject_code: '', subject_name: '', teacher: '', room: '' })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [modSuggestions, setModSuggestions] = useState<any[] | null>(null)
  const [loadingSuggestions, setLoadingSuggestions] = useState(false)

  async function fetchModifySuggestions(key: string) {
    if (!key || action !== 'MODIFY') return
    setLoadingSuggestions(true)
    setModSuggestions(null)
    try {
      const result = await adminCalendarSuggest(date, key)
      const raw = result?.rich_suggestions ?? null
      if (raw) {
        // Room suggestions first
        const sorted = [...raw].sort((a: any, b: any) => {
          const aRoom = (a.action?.spec?.room || a.action?.new_room) ? 0 : 1
          const bRoom = (b.action?.spec?.room || b.action?.new_room) ? 0 : 1
          return aRoom - bRoom
        })
        setModSuggestions(sorted)
      }
    } catch { /* suggestions are optional */ }
    finally { setLoadingSuggestions(false) }
  }

  function applyModSuggestion(rs: any) {
    const a = rs.action
    if (!a) return
    const changes: Record<string, string> = {}
    if (a.new_start_time) changes.start       = a.new_start_time
    if (a.new_end_time)   changes.end         = a.new_end_time
    if (a.spec?.room)     changes.room        = a.spec.room
    if (a.new_room)       changes.room        = a.new_room
    if (a.spec?.teacher)  changes.teacher     = a.spec.teacher
    setModChanges(c => ({ ...c, ...changes }))
    setModSuggestions(null)
  }

  async function submit() {
    setLoading(true); setError(null)
    try {
      const payload: any = { date, action, reason, force }
      if (action === 'DAY_OFF') {
        if (program) payload.program = program
        if (semester) payload.semester = semester
      } else if (action === 'CANCEL') {
        payload.target_key = targetKey
      } else if (action === 'MODIFY') {
        payload.target_key = targetKey
        payload.changes = Object.fromEntries(Object.entries(modChanges).filter(([, v]) => v.trim()))
      } else if (action === 'ADD') {
        payload.entry = addEntry
      }
      await adminCreateOverride(payload)
      onDone()
    } catch (e: any) {
      const detail = e.message
      if (detail?.includes('409') || (e.status === 409)) {
        setError(`Constraint issue — enable "force" to override, or check the form. ${detail}`)
      } else {
        setError(detail)
      }
    } finally { setLoading(false) }
  }

  const entries = dayData?.entries ?? []

  // ── Suggestions + availability (computed in the background) ─────────────────
  const { catalogTeachers, catalogPrograms, catalogSubjects, loadCatalog } = useWorkspaceStore()
  const [busySlots, setBusySlots] = useState<BusySlotLite[]>([])
  useEffect(() => {
    if (!catalogTeachers.length || !catalogSubjects.length) loadCatalog()
    req<BusySlotLite[]>('/busy-slots').then(s => setBusySlots(Array.isArray(s) ? s : [])).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const targetEntry = useMemo(
    () => (targetKey ? entries.find(e => e.cal_key === targetKey) ?? null : null),
    [entries, targetKey],
  )

  // The time window being scheduled: ADD uses the form, MODIFY falls back to the
  // original class times for anything left blank.
  const slotStart = action === 'ADD' ? addEntry.start : action === 'MODIFY' ? (modChanges.start || targetEntry?.start || '') : ''
  const slotEnd   = action === 'ADD' ? addEntry.end   : action === 'MODIFY' ? (modChanges.end   || targetEntry?.end   || '') : ''
  const subjectForHint = action === 'ADD' ? addEntry.subject_code : (modChanges.subject_code || targetEntry?.subject_code || '')

  // Recomputed only when the window / day data / busy slots change — so by the
  // time the user opens the Teacher or Room dropdown the answer is ready.
  const availability = useMemo(
    () => computeAvailability(entries, slotStart, slotEnd, action === 'MODIFY' ? targetKey : '', busySlots, date, dayData?.weekday),
    [entries, slotStart, slotEnd, action, targetKey, busySlots, date, dayData?.weekday],
  )

  const teacherNames = useMemo(() => {
    const m = new Map<string, string>()
    catalogTeachers.forEach(t => m.set(t.short_name, t.full_name))
    entries.forEach(e => splitTeachers(e.teacher).forEach(t => { if (!m.has(t)) m.set(t, '') }))
    return m
  }, [catalogTeachers, entries])

  const roomNames = useMemo(() => {
    const s = new Set<string>(DEFAULT_ROOMS)
    entries.forEach(e => { if (e.room?.trim()) s.add(e.room.trim()) })
    return Array.from(s)
  }, [entries])

  function optionsFor(field: string, prog: string, sem: string): SuggestOption[] {
    if (field === 'teacher') {
      return Array.from(teacherNames.entries()).map(([short, full]): SuggestOption => {
        const teaches = subjectForHint && catalogTeachers.find(t => t.short_name === short)?.subjects_csv
          ?.toLowerCase().split(',').map(x => x.trim()).includes(subjectForHint.toLowerCase())
        const reason = availability?.teachers.get(short)
        return {
          value: short,
          label: [full, teaches ? `teaches ${subjectForHint}` : ''].filter(Boolean).join(' · '),
          status: availability ? (reason ? 'busy' : 'free') : undefined,
          reason,
        }
      })
    }
    if (field === 'room') {
      return roomNames.map((r): SuggestOption => {
        const reason = availability?.rooms.get(normRoom(r))
        return { value: r, status: availability ? (reason ? 'busy' : 'free') : undefined, reason }
      })
    }
    if (field === 'program') {
      return Array.from(new Set([...catalogPrograms.map(p => p.name), 'B.Tech', 'M.Tech', 'M.Sc'])).map(v => ({ value: v }))
    }
    if (field === 'semester') return SEMESTERS.map(v => ({ value: v }))
    if (field === 'subject_code' || field === 'subject_name') {
      let subs = catalogSubjects.filter(s => (!prog || s.program === prog) && (!sem || s.semester === sem))
      if (subs.length === 0) subs = catalogSubjects
      const seen = new Set<string>()
      return subs.flatMap(s => {
        const key = field === 'subject_code' ? s.code : s.name
        if (seen.has(key)) return []
        seen.add(key)
        return [field === 'subject_code'
          ? { value: s.code, label: s.name }
          : { value: s.name, label: s.code }]
      })
    }
    return []
  }

  function headerFor(field: string): string | undefined {
    if ((field === 'teacher' || field === 'room') && availability) return `Availability ${slotStart}–${slotEnd}`
    return undefined
  }
  function hintFor(field: string): string | undefined {
    if ((field === 'teacher' || field === 'room') && !availability) return 'Pick start & end time to see free / busy'
    return undefined
  }

  return (
    <div className="modal-overlay" onClick={onClose} id="override-modal-overlay">
      <div className="modal modal-lg" onClick={e => e.stopPropagation()} id="override-modal">
        <div className="modal-header">
          <div>
            <div className="mono-label">Add override for</div>
            <div className="modal-title">{date}</div>
          </div>
          <button className="btn-icon" onClick={onClose}><X size={16} /></button>
        </div>

        {/* Action selector */}
        <div style={{ display: 'flex', gap: 6, marginBottom: 'var(--sp-4)', flexWrap: 'wrap' }}>
          {(['DAY_OFF', 'CANCEL', 'MODIFY', 'ADD'] as Action[]).map(a => (
            <button key={a} onClick={() => setAction(a)} style={{
              padding: '5px 14px', borderRadius: 'var(--radius)',
              fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em',
              cursor: 'pointer', border: `1px solid ${action === a ? actionColor(a) : 'var(--line)'}`,
              background: action === a ? actionBg(a) : 'var(--card-bg)',
              color: action === a ? actionColor(a) : 'var(--ink-soft)',
              transition: 'var(--transition)',
            }}>
              {a}
            </button>
          ))}
        </div>

        {/* Action descriptions */}
        <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)', marginBottom: 'var(--sp-4)', background: 'var(--paper)', borderRadius: 'var(--radius)', padding: 'var(--sp-2) var(--sp-3)' }}>
          {action === 'DAY_OFF' && 'Mark this date as a holiday or no-class day (optionally scoped to a program/semester).'}
          {action === 'CANCEL' && 'Cancel one specific class on this date. Select the class from the list.'}
          {action === 'MODIFY' && 'Change time, teacher, room, or subject of a specific class on this date.'}
          {action === 'ADD' && 'Add an extra class on this date (a one-off session not in the weekly timetable).'}
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)' }}>
          {/* Reason */}
          <div className="form-group">
            <label className="form-label">Reason (optional)</label>
            <input className="input" placeholder="e.g. Diwali holiday, Guest lecture…" value={reason} onChange={e => setReason(e.target.value)} id="override-reason" />
          </div>

          {/* DAY_OFF filters */}
          {action === 'DAY_OFF' && (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--sp-3)' }}>
              <div className="form-group">
                <label className="form-label">Program (leave blank = all)</label>
                <select className="input" value={program} onChange={e => setProgram(e.target.value)} id="dayoff-program">
                  <option value="">— All programs —</option>
                  <option value="B.Tech">B.Tech</option>
                  <option value="M.Tech">M.Tech</option>
                  <option value="M.Sc">M.Sc</option>
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Semester (leave blank = all)</label>
                <select className="input" value={semester} onChange={e => setSemester(e.target.value)} id="dayoff-semester">
                  <option value="">— All semesters —</option>
                  {['1st','2nd','3rd','4th','5th','6th','7th','8th'].map(s => <option key={s} value={s}>{s}</option>)}
                </select>
              </div>
            </div>
          )}

          {/* CANCEL / MODIFY — pick base class */}
          {(action === 'CANCEL' || action === 'MODIFY') && (
            <div className="form-group">
              <label className="form-label">Select class to {action.toLowerCase()}</label>
              {entries.length === 0 ? (
                <div style={{ color: 'var(--ink-soft)', fontSize: 'var(--fs-xs)', fontStyle: 'italic' }}>No classes available on this date.</div>
              ) : (
                <select className="input" value={targetKey} onChange={e => {
                  setTargetKey(e.target.value)
                  setModSuggestions(null)
                  if (action === 'MODIFY' && e.target.value) {
                    setTimeout(() => fetchModifySuggestions(e.target.value), 0)
                  }
                }} id="override-target-select">
                  <option value="">— pick a class —</option>
                  {entries.filter(e => e.cal_source === 'base').map(e => (
                    <option key={e.cal_key} value={e.cal_key ?? ''}>
                      {e.start}–{e.end} | {e.subject_name} | {e.teacher} | {e.room} | {e.program} {e.semester}
                    </option>
                  ))}
                </select>
              )}
            </div>
          )}

          {/* MODIFY — suggestions */}
          {action === 'MODIFY' && targetKey && (
            <div style={{ background: 'var(--paper)', border: '1px solid var(--line)', borderRadius: 'var(--radius)', padding: 'var(--sp-3)' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 600, color: 'var(--accent)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
                  <Lightbulb size={12} /> Conflict-Free Suggestions
                </div>
                <button className="btn btn-ghost btn-sm" style={{ fontSize: 10 }} disabled={loadingSuggestions} onClick={() => fetchModifySuggestions(targetKey)}>
                  {loadingSuggestions ? 'Loading…' : '↻ Refresh'}
                </button>
              </div>
              {loadingSuggestions && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)' }}>Analyzing schedule…</div>}
              {modSuggestions && modSuggestions.length === 0 && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)' }}>No conflict-free alternatives found.</div>}
              {modSuggestions && modSuggestions.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {modSuggestions.slice(0, 6).map((rs: any, i: number) => {
                    const isRoom = !!(rs.action?.spec?.room || rs.action?.new_room)
                    return (
                      <div key={i} className={`suggestion-card ${isRoom ? 'is-room' : ''}`} style={{ padding: '9px 12px' }}>
                        <div className="suggestion-card-content">
                          {isRoom && (
                            <div className="suggestion-card-badges">
                              <span className="suggestion-type-pill room">
                                <Home size={9} /> Room change
                              </span>
                            </div>
                          )}
                          <div className="suggestion-slot-title" style={{ fontSize: 'var(--fs-xs)' }}>
                            {rs.title}
                          </div>
                          {rs.description && (
                            <div className="suggestion-desc">
                              {rs.description}
                            </div>
                          )}
                        </div>
                        <div className="suggestion-apply-action">
                          <button className="btn btn-primary suggestion-apply-btn" style={{ padding: '5px 12px', fontSize: 11 }} onClick={() => applyModSuggestion(rs)}>
                            Apply
                          </button>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )}

          {/* MODIFY — changes */}
          {action === 'MODIFY' && targetKey && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 'var(--sp-3)' }}>
              {[
                { field: 'start', label: 'New Start (HH:MM)', type: 'time' },
                { field: 'end', label: 'New End (HH:MM)', type: 'time' },
                { field: 'teacher', label: 'New Teacher', type: 'text' },
                { field: 'room', label: 'New Room', type: 'text' },
                { field: 'subject_code', label: 'New Subject Code', type: 'text' },
                { field: 'subject_name', label: 'New Subject Name', type: 'text' },
              ].map(({ field, label, type }) => (
                <div key={field} className="form-group">
                  <label className="form-label">{label} (optional)</label>
                  {type === 'time' ? (
                    <input
                      className="input" type="time" min="10:00" max="17:30"
                      value={(modChanges as any)[field]}
                      onChange={e => setModChanges(c => ({ ...c, [field]: e.target.value }))}
                    />
                  ) : (
                    <SuggestInput
                      id={`modify-${field}`}
                      placeholder={(targetEntry as any)?.[field] ? `keep: ${(targetEntry as any)[field]}` : 'leave blank to keep'}
                      value={(modChanges as any)[field]}
                      onChange={v => setModChanges(c => ({ ...c, [field]: v }))}
                      options={optionsFor(field, targetEntry?.program ?? '', targetEntry?.semester ?? '')}
                      header={headerFor(field)}
                      hintWhenNoStatus={hintFor(field)}
                      onPick={o => {
                        if (field === 'subject_code' && o.label) setModChanges(c => ({ ...c, subject_name: o.label! }))
                        if (field === 'subject_name' && o.label) setModChanges(c => ({ ...c, subject_code: o.label! }))
                      }}
                    />
                  )}
                </div>
              ))}
            </div>
          )}

          {/* ADD — entry form */}
          {action === 'ADD' && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 'var(--sp-3)' }}>
              {[
                { field: 'program', label: 'Program', placeholder: 'B.Tech', type: 'text' },
                { field: 'semester', label: 'Semester', placeholder: '3rd', type: 'text' },
                { field: 'start', label: 'Start (HH:MM)', placeholder: '10:00', type: 'time' },
                { field: 'end', label: 'End (HH:MM)', placeholder: '12:00', type: 'time' },
                { field: 'subject_code', label: 'Subject Code', placeholder: 'CS301', type: 'text' },
                { field: 'subject_name', label: 'Subject Name', placeholder: 'Algorithms', type: 'text' },
                { field: 'teacher', label: 'Teacher', placeholder: 'SK', type: 'text' },
                { field: 'room', label: 'Room', placeholder: 'R#205', type: 'text' },
              ].map(({ field, label, placeholder, type }) => (
                <div key={field} className="form-group">
                  <label className="form-label">{label}</label>
                  {type === 'time' ? (
                    <input
                      className="input" type="time" min="10:00" max="17:30"
                      placeholder={placeholder}
                      value={(addEntry as any)[field]}
                      onChange={e => setAddEntry(c => ({ ...c, [field]: e.target.value }))}
                    />
                  ) : (
                    <SuggestInput
                      id={`add-${field}`}
                      placeholder={placeholder}
                      value={(addEntry as any)[field]}
                      onChange={v => setAddEntry(c => ({ ...c, [field]: v }))}
                      options={optionsFor(field, addEntry.program, addEntry.semester)}
                      header={headerFor(field)}
                      hintWhenNoStatus={hintFor(field)}
                      onPick={o => {
                        if (field === 'subject_code') {
                          const s = catalogSubjects.find(x => x.code === o.value)
                          setAddEntry(c => ({
                            ...c,
                            subject_name: o.label || c.subject_name,
                            type: s && (s.entry_type === 'Theory' || s.entry_type === 'Practical') ? s.entry_type : c.type,
                            program: c.program || s?.program || '',
                            semester: c.semester || s?.semester || '',
                          }))
                        }
                        if (field === 'subject_name' && o.label) setAddEntry(c => ({ ...c, subject_code: o.label! }))
                      }}
                    />
                  )}
                </div>
              ))}
              <div className="form-group">
                <label className="form-label">Type</label>
                <select className="input" value={addEntry.type} onChange={e => setAddEntry(c => ({ ...c, type: e.target.value }))}>
                  <option value="Theory">Theory</option>
                  <option value="Practical">Practical</option>
                </select>
              </div>
            </div>
          )}

          {/* Force checkbox */}
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)', marginTop: 4 }}>
            <input type="checkbox" checked={force} onChange={e => setForce(e.target.checked)} id="override-force-cb" />
            Force (allow soft constraint violations)
          </label>
        </div>

        {error && <ErrBanner msg={error} style={{ marginTop: 'var(--sp-3)' }} />}

        <div className="modal-footer">
          <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={submit} disabled={loading} id="override-submit-btn">
            {loading ? 'Saving…' : `Apply ${action}`}
          </button>
        </div>
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════════
// Overrides Panel (list view)
// ═══════════════════════════════════════════════════════════════════════════════
function OverridesPanel() {
  const today30 = today()
  const end90 = (() => { const d = new Date(); d.setDate(d.getDate() + 90); return toISO(d.getFullYear(), d.getMonth() + 1, d.getDate()) })()
  const [overrides, setOverrides] = useState<CalendarOverrideRecord[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function load() {
    setLoading(true); setError(null)
    try { setOverrides(await adminListOverrides(today30, end90)) }
    catch (e: any) { setError(e.message) }
    finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])

  async function del(id: number, force = false) {
    try { await adminDeleteOverride(id, force); load() }
    catch (e: any) {
      if ((e as any).status === 409 && !force) {
        if (confirm('Force delete this override?')) del(id, true)
      } else { alert(e.message) }
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--sp-4)' }}>
        <div>
          <div className="mono-label">Upcoming 90 days</div>
          <h3 style={{ fontFamily: 'var(--font-display)', fontWeight: 600, fontSize: 'var(--fs-lg)', color: 'var(--ink)' }}>Day Overrides</h3>
        </div>
        <button className="btn btn-ghost btn-sm btn-icon" onClick={load}><RefreshCw size={13} /></button>
      </div>
      {error && <ErrBanner msg={error} />}
      {loading ? <div style={{ textAlign: 'center', padding: 40 }}><span className="spinner spinner-lg" /></div> : (
        overrides.length === 0 ? (
          <div className="empty-state"><Calendar size={32} /><h3>No overrides</h3><p>Use the Month View to select a date and add overrides.</p></div>
        ) : (
          <div style={{ border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 520 }}>
              <thead>
                <tr style={{ background: 'var(--paper)', borderBottom: '1px solid var(--line)' }}>
                  {['Date', 'Day', 'Action', 'Target / Scope', 'Reason', 'Forced', ''].map(h => (
                    <th key={h} style={{ padding: '8px 12px', textAlign: 'left', fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500, color: 'var(--ink-soft)', textTransform: 'uppercase', letterSpacing: '0.15em', whiteSpace: 'nowrap' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {overrides.map(ov => (
                  <tr key={ov.id} style={{ borderBottom: '1px solid var(--line)' }}>
                    <td style={{ padding: '10px 12px', fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', color: 'var(--ink)' }}>{ov.date}</td>
                    <td style={{ padding: '10px 12px', fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)' }}>{ov.weekday}</td>
                    <td style={{ padding: '10px 12px' }}>
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 600, color: actionColor(ov.action), background: actionBg(ov.action), borderRadius: 4, padding: '2px 7px', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{ov.action}</span>
                    </td>
                    <td style={{ padding: '10px 12px', fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {ov.action === 'DAY_OFF' ? `${ov.program ?? 'All'} / ${ov.semester ?? 'All'}` : (ov.target ? `${ov.target.subject_name ?? ''} ${ov.target.start ?? ''}–${ov.target.end ?? ''}` : ov.target_key.slice(0, 16)+'…')}
                    </td>
                    <td style={{ padding: '10px 12px', fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)' }}>{ov.reason || '—'}</td>
                    <td style={{ padding: '10px 12px' }}>{ov.forced && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--amber)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>yes</span>}</td>
                    <td style={{ padding: '10px 12px' }}>
                      <button className="btn btn-xs btn-danger" onClick={() => del(ov.id)}><Trash2 size={10} /></button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════════
// Validity Windows Panel
// ═══════════════════════════════════════════════════════════════════════════════
function ValidityPanel() {
  const [windows, setWindows] = useState<CalendarValidityRecord[]>([])
  const [versions, setVersions] = useState<TimetableVersion[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)

  async function load() {
    setLoading(true); setError(null)
    try {
      const [ws, vs] = await Promise.all([adminListValidity(), listVersions()])
      setWindows(ws); setVersions(vs)
    }
    catch (e: any) { setError(e.message) }
    finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])

  async function del(id: number, force = false) {
    try { await adminDeleteValidity(id, force); load() }
    catch (e: any) {
      if ((e as any).status === 409 && !force) {
        if (confirm('Force delete this validity window?')) del(id, true)
      } else { alert(e.message) }
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--sp-4)' }}>
        <div>
          <div className="mono-label">Schedule overrides for date ranges</div>
          <h3 style={{ fontFamily: 'var(--font-display)', fontWeight: 600, fontSize: 'var(--fs-lg)', color: 'var(--ink)' }}>Validity Windows</h3>
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          <button className="btn btn-ghost btn-sm btn-icon" onClick={load}><RefreshCw size={13} /></button>
          <button className="btn btn-primary btn-sm" onClick={() => setShowCreate(true)} id="create-validity-btn"><Plus size={13} /> New Window</button>
        </div>
      </div>
      {error && <ErrBanner msg={error} />}
      {loading ? <div style={{ textAlign: 'center', padding: 40 }}><span className="spinner spinner-lg" /></div> : (
        windows.length === 0 ? (
          <div className="empty-state"><Shield size={32} /><h3>No validity windows</h3><p>Assign a timetable version to a date range (week, month, or custom) so it takes effect for those days instead of the published version.</p></div>
        ) : (
          <div style={{ border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 480 }}>
              <thead>
                <tr style={{ background: 'var(--paper)', borderBottom: '1px solid var(--line)' }}>
                  {['Version', 'Scope', 'From', 'To', 'Label', 'Priority', ''].map(h => (
                    <th key={h} style={{ padding: '8px 12px', textAlign: 'left', fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500, color: 'var(--ink-soft)', textTransform: 'uppercase', letterSpacing: '0.15em', whiteSpace: 'nowrap' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {windows.map(w => (
                  <tr key={w.id} style={{ borderBottom: '1px solid var(--line)' }}>
                    <td style={{ padding: '10px 12px' }}><span className="badge badge-purple">v{w.version_id}</span></td>
                    <td style={{ padding: '10px 12px', fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)' }}>{w.scope}</td>
                    <td style={{ padding: '10px 12px', fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', color: 'var(--ink)' }}>{w.start_date}</td>
                    <td style={{ padding: '10px 12px', fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', color: 'var(--ink)' }}>{w.end_date}</td>
                    <td style={{ padding: '10px 12px', fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)' }}>{w.label || '—'}</td>
                    <td style={{ padding: '10px 12px', fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', color: 'var(--ink)' }}>{w.priority}</td>
                    <td style={{ padding: '10px 12px' }}>
                      <button className="btn btn-xs btn-danger" onClick={() => del(w.id)}><Trash2 size={10} /></button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}

      {showCreate && (
        <ValidityCreateModal
          versions={versions}
          onClose={() => setShowCreate(false)}
          onDone={() => { setShowCreate(false); load() }}
        />
      )}
    </div>
  )
}

function ValidityCreateModal({ versions, onClose, onDone }: { versions: TimetableVersion[]; onClose: () => void; onDone: () => void }) {
  const [form, setForm] = useState({ version_id: '', scope: 'WEEK' as 'WEEK'|'MONTH'|'RANGE', anchor_date: today(), start_date: today(), end_date: today(), label: '', priority: '0', force: false })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(autoForce = form.force) {
    if (!form.version_id) { setError('Select a version.'); return }
    setLoading(true); setError(null)
    try {
      const payload: any = { version_id: parseInt(form.version_id), scope: form.scope, label: form.label, priority: parseInt(form.priority) || 0, force: autoForce }
      if (form.scope === 'RANGE') { payload.start_date = form.start_date; payload.end_date = form.end_date }
      else { payload.anchor_date = form.anchor_date }
      await adminCreateValidity(payload)
      onDone()
    } catch (e: any) {
      if (e.status === 409 && e.can_force && !autoForce) {
        if (confirm("This version violated constraints. Do you still want to add it to the window?")) {
           submit(true);
           return;
        }
      }
      setError(e.message)
    }
    finally { setLoading(false) }
  }

  const validVersions = versions.filter(v => v.status === 'VALIDATED' || v.status === 'PUBLISHED')

  return (
    <div className="modal-overlay" onClick={onClose} id="validity-modal-overlay">
      <div className="modal" onClick={e => e.stopPropagation()} id="validity-modal">
        <div className="modal-header">
          <div className="modal-title">New Validity Window</div>
          <button className="btn-icon" onClick={onClose}><X size={16} /></button>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)' }}>
          <div className="form-group">
            <label className="form-label">Timetable Version</label>
            <select className="input" value={form.version_id} onChange={e => setForm(f => ({ ...f, version_id: e.target.value }))} id="validity-version-select">
              <option value="">— select —</option>
              {validVersions.map(v => <option key={v.id} value={v.id}>v{v.id} — {v.status} — {v.change_summary?.slice(0, 40) || 'no summary'}</option>)}
            </select>
            {validVersions.length === 0 && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--amber)', marginTop: 4 }}>No VALIDATED or PUBLISHED versions available. Validate a draft first.</div>}
          </div>
          <div className="form-group">
            <label className="form-label">Scope</label>
            <select className="input" value={form.scope} onChange={e => setForm(f => ({ ...f, scope: e.target.value as any }))} id="validity-scope-select">
              <option value="WEEK">Week (full Mon–Sun containing anchor date)</option>
              <option value="MONTH">Month (full month containing anchor date)</option>
              <option value="RANGE">Custom Range</option>
            </select>
          </div>
          {form.scope !== 'RANGE' && (
            <div className="form-group">
              <label className="form-label">Anchor Date (any date in the target {form.scope.toLowerCase()})</label>
              <input type="date" className="input" value={form.anchor_date} onChange={e => setForm(f => ({ ...f, anchor_date: e.target.value }))} id="validity-anchor-date" />
            </div>
          )}
          {form.scope === 'RANGE' && (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--sp-3)' }}>
              <div className="form-group">
                <label className="form-label">Start Date</label>
                <input type="date" className="input" value={form.start_date} onChange={e => setForm(f => ({ ...f, start_date: e.target.value }))} id="validity-start-date" />
              </div>
              <div className="form-group">
                <label className="form-label">End Date</label>
                <input type="date" className="input" value={form.end_date} onChange={e => setForm(f => ({ ...f, end_date: e.target.value }))} id="validity-end-date" />
              </div>
            </div>
          )}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 'var(--sp-3)' }}>
            <div className="form-group">
              <label className="form-label">Label (optional)</label>
              <input className="input" placeholder="e.g. Mid-term week, Exam schedule…" value={form.label} onChange={e => setForm(f => ({ ...f, label: e.target.value }))} id="validity-label-input" />
            </div>
            <div className="form-group">
              <label className="form-label">Priority</label>
              <input type="number" className="input" value={form.priority} onChange={e => setForm(f => ({ ...f, priority: e.target.value }))} style={{ width: 72 }} id="validity-priority-input" />
            </div>
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)' }}>
            <input type="checkbox" checked={form.force} onChange={e => setForm(f => ({ ...f, force: e.target.checked }))} id="validity-force-cb" />
            Force (allow soft constraint violations from existing overrides)
          </label>
        </div>
        {error && <ErrBanner msg={error} style={{ marginTop: 'var(--sp-3)' }} />}
        <div className="modal-footer">
          <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={() => submit()} disabled={loading} id="validity-submit-btn">
            {loading ? 'Creating…' : 'Create Window'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════════
// Audit Panel
// ═══════════════════════════════════════════════════════════════════════════════
function formatAuditPayload(log: any) {
  if (!log.payload) return ''
  const p = log.payload
  if (log.entity === 'validity') {
    if (log.action === 'CREATE') return `Created validity window for v${p.version_id} (${p.start} to ${p.end})`
    if (log.action === 'DELETE') return `Deleted validity window`
  }
  if (log.entity === 'override') {
    if (log.action === 'CREATE') {
      const act = p.action
      if (act === 'DAY_OFF') {
        const pr = p.payload?.program ? ` for ${p.payload.program} ${p.payload.semester}` : ''
        return `Set Day Off on ${p.date}${pr}`
      }
      const t = p.target
      const targetStr = t ? `${t.subject_code || t.subject} (${t.program} ${t.semester})` : ''
      if (act === 'CANCEL') return `Cancelled class ${targetStr} on ${p.date}`
      if (act === 'MODIFY') {
        const changes = p.payload?.changes || {}
        const moves = []
        if (changes.start || changes.end) moves.push(`time to ${changes.start || t?.start}-${changes.end || t?.end}`)
        if (changes.room) moves.push(`room to ${changes.room}`)
        if (changes.teacher) moves.push(`teacher to ${changes.teacher}`)
        return `Modified class ${targetStr} on ${p.date}: changed ${moves.join(', ')}`
      }
      if (act === 'ADD') {
        const t2 = p.payload?.entry
        return t2 ? `Added extra class ${t2.subject_code} for ${t2.program} ${t2.semester} on ${p.date} (${t2.start}-${t2.end})` : `Added extra class on ${p.date}`
      }
    }
    if (log.action === 'DELETE') return `Removed override on ${p.date}`
  }
  return JSON.stringify(p).slice(0, 100)
}

function AuditPanel() {
  const [logs, setLogs] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function load() {
    setLoading(true); setError(null)
    try { setLogs(await adminCalendarAudit(100)) }
    catch (e: any) { setError(e.message) }
    finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--sp-4)' }}>
        <div>
          <div className="mono-label">Last 100 events</div>
          <h3 style={{ fontFamily: 'var(--font-display)', fontWeight: 600, fontSize: 'var(--fs-lg)', color: 'var(--ink)' }}>Calendar Audit Log</h3>
        </div>
        <button className="btn btn-ghost btn-sm btn-icon" onClick={load}><RefreshCw size={13} /></button>
      </div>
      {error && <ErrBanner msg={error} />}
      {loading ? <div style={{ textAlign: 'center', padding: 40 }}><span className="spinner spinner-lg" /></div> : (
        logs.length === 0 ? (
          <div className="empty-state"><ClipboardList size={32} /><h3>No audit events yet</h3></div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {logs.map(log => (
              <div key={log.id} style={{ display: 'flex', gap: 12, alignItems: 'flex-start', padding: '9px 12px', background: 'var(--card-bg)', borderRadius: 'var(--radius)', border: '1px solid var(--line)', fontSize: 'var(--fs-xs)' }}>
                <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--ink-soft)', whiteSpace: 'nowrap', flexShrink: 0 }}>{log.created_at ? new Date(log.created_at).toLocaleString() : '—'}</span>
                <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, color: 'var(--accent)', minWidth: 60, textTransform: 'uppercase', letterSpacing: '0.05em', fontSize: 10, flexShrink: 0 }}>{log.action}</span>
                <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--clr-purple)', fontSize: 10, minWidth: 70, flexShrink: 0 }}>{log.entity}#{log.entity_id ?? '—'}</span>
                <span style={{ color: 'var(--ink)', flex: 1 }}>{formatAuditPayload(log)}</span>
              </div>
            ))}
          </div>
        )
      )}
    </div>
  )
}

// ── Shared error banner ────────────────────────────────────────────────────────
function ErrBanner({ msg, style: extraStyle }: { msg: string; style?: React.CSSProperties }) {
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', color: 'var(--violation)', fontSize: 'var(--fs-xs)', background: 'var(--violation-soft)', border: '1px solid color-mix(in oklab, var(--violation) 20%, transparent)', borderRadius: 'var(--radius)', padding: 'var(--sp-3)', marginBottom: 'var(--sp-3)', whiteSpace: 'pre-wrap', ...extraStyle }}>
      <AlertCircle size={13} style={{ flexShrink: 0, marginTop: 2 }} />
      <div>{msg}</div>
    </div>
  )
}
