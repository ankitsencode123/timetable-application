import { useState, useEffect, useCallback } from 'react'
import { ChevronLeft, ChevronRight, X, Calendar as CalIcon } from 'lucide-react'
import { getCalendarMonth, getCalendarDay, type CalendarMonthDay, type CalendarDayResult } from '../../api'

const MONTH_NAMES = ['January','February','March','April','May','June','July','August','September','October','November','December']
const DOW = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']

function toISO(y: number, m: number, d: number) {
  return `${y}-${String(m).padStart(2,'0')}-${String(d).padStart(2,'0')}`
}
function todayStr() {
  const d = new Date()
  return toISO(d.getFullYear(), d.getMonth()+1, d.getDate())
}
function firstDayOfMonth(y: number, m: number) {
  return (new Date(y, m-1, 1).getDay() + 6) % 7
}
function daysInMonth(y: number, m: number) {
  return new Date(y, m, 0).getDate()
}

export default function PublicCalendar() {
  const now = new Date()
  const [year, setYear] = useState(now.getFullYear())
  const [month, setMonth] = useState(now.getMonth()+1)
  const [monthData, setMonthData] = useState<CalendarMonthDay[]>([])
  const [loading, setLoading] = useState(false)
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const [dayData, setDayData] = useState<CalendarDayResult | null>(null)
  const [dayLoading, setDayLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try { setMonthData((await getCalendarMonth(year, month)).days) }
    catch { setMonthData([]) }
    finally { setLoading(false) }
  }, [year, month])

  useEffect(() => { load() }, [load])

  async function selectDay(date: string) {
    setSelectedDate(date); setDayData(null); setDayLoading(true)
    try { setDayData(await getCalendarDay(date)) }
    catch { setDayData(null) }
    finally { setDayLoading(false) }
  }

  const dayMap = Object.fromEntries(monthData.map(d => [d.date, d]))
  const offset = firstDayOfMonth(year, month)
  const total = daysInMonth(year, month)
  const cells: (number|null)[] = [...Array(offset).fill(null), ...Array.from({length: total}, (_,i) => i+1)]
  while (cells.length % 7 !== 0) cells.push(null)
  const today = todayStr()

  return (
    <div style={{ background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', overflow: 'hidden' }}>
      {/* Header */}
      <div style={{ padding: 'var(--sp-4)', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: 'var(--paper)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <CalIcon size={15} style={{ color: 'var(--accent)' }} />
          <span style={{ fontFamily: 'var(--font-display)', fontWeight: 600, fontSize: 'var(--fs-md)', color: 'var(--ink)' }}>Schedule Calendar</span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-soft)', background: 'color-mix(in oklab, var(--ink) 5%, transparent)', borderRadius: 4, padding: '2px 7px' }}>date-aware view</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <button className="btn btn-ghost btn-sm btn-icon" onClick={() => { if (month===1) { setYear(y=>y-1); setMonth(12) } else setMonth(m=>m-1) }} id="pub-cal-prev">
            <ChevronLeft size={14} />
          </button>
          <span style={{ fontFamily: 'var(--font-display)', fontWeight: 600, fontSize: 'var(--fs-sm)', color: 'var(--ink)', minWidth: 110, textAlign: 'center' }}>{MONTH_NAMES[month-1]} {year}</span>
          <button className="btn btn-ghost btn-sm btn-icon" onClick={() => { if (month===12) { setYear(y=>y+1); setMonth(1) } else setMonth(m=>m+1) }} id="pub-cal-next">
            <ChevronRight size={14} />
          </button>
        </div>
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap' }}>
        {/* Grid */}
        <div style={{ flex: '1 1 300px', padding: 'var(--sp-3)' }}>
          {/* Day headers */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', marginBottom: 4 }}>
            {DOW.map(d => (
              <div key={d} style={{ textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 500, color: 'var(--ink-soft)', textTransform: 'uppercase', letterSpacing: '0.1em', padding: '4px 0' }}>{d}</div>
            ))}
          </div>

          {loading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: 'var(--sp-6)' }}><span className="spinner" /></div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 2 }}>
              {cells.map((day, idx) => {
                if (!day) return <div key={idx} />
                const dateStr = toISO(year, month, day)
                const info = dayMap[dateStr]
                const isToday = dateStr === today
                const isSelected = selectedDate === dateStr
                const isHoliday = info?.is_holiday
                const hasChanges = info?.has_changes

                return (
                  <button key={idx} onClick={() => selectDay(dateStr)} id={`pub-cal-day-${dateStr}`} style={{
                    minHeight: 52, padding: '4px 3px', borderRadius: 'var(--radius)',
                    background: isSelected ? 'var(--accent)' : isHoliday ? 'var(--violation-soft)' : isToday ? 'var(--amber-soft)' : 'transparent',
                    border: `1px solid ${isSelected ? 'var(--accent)' : isToday ? 'var(--amber)' : isHoliday ? 'color-mix(in oklab, var(--violation) 25%, transparent)' : 'transparent'}`,
                    cursor: 'pointer', textAlign: 'center', transition: 'var(--transition)',
                  }}
                  onMouseEnter={e => { if (!isSelected) (e.currentTarget as HTMLElement).style.background = 'color-mix(in oklab, var(--ink) 4%, transparent)' }}
                  onMouseLeave={e => { if (!isSelected) (e.currentTarget as HTMLElement).style.background = isHoliday ? 'var(--violation-soft)' : isToday ? 'var(--amber-soft)' : 'transparent' }}
                  >
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: isToday ? 700 : 400, color: isSelected ? 'var(--paper)' : isToday ? 'var(--amber)' : isHoliday ? 'var(--violation)' : 'var(--ink)' }}>
                      {day}
                    </div>
                    {info && (
                      <div style={{ marginTop: 2, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1 }}>
                        {info.classes > 0 && !isHoliday && (
                          <div style={{ width: 4, height: 4, borderRadius: '50%', background: isSelected ? 'var(--paper)' : 'var(--accent)' }} />
                        )}
                        {isHoliday && <div style={{ width: 4, height: 4, borderRadius: '50%', background: 'var(--violation)' }} />}
                        {hasChanges && !isHoliday && <div style={{ width: 4, height: 4, borderRadius: '50%', background: isSelected ? 'rgba(255,255,255,0.7)' : 'var(--amber)' }} />}
                      </div>
                    )}
                  </button>
                )
              })}
            </div>
          )}

          {/* Legend */}
          <div style={{ display: 'flex', gap: 12, marginTop: 'var(--sp-3)', flexWrap: 'wrap' }}>
            {[
              { dot: 'var(--accent)', label: 'Classes' },
              { dot: 'var(--amber)', label: 'Modified' },
              { dot: 'var(--violation)', label: 'Holiday' },
            ].map(({ dot, label }) => (
              <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 10, color: 'var(--ink-soft)' }}>
                <div style={{ width: 6, height: 6, borderRadius: '50%', background: dot }} />
                {label}
              </div>
            ))}
          </div>
        </div>

        {/* Day Detail Drawer */}
        {selectedDate && (
          <div style={{ flex: '1 1 260px', borderLeft: '1px solid var(--line)', padding: 'var(--sp-4)', display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)', minWidth: 240, maxWidth: 360 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-soft)', textTransform: 'uppercase', letterSpacing: '0.12em' }}>{dayData?.weekday ?? ''}</div>
                <div style={{ fontFamily: 'var(--font-display)', fontWeight: 600, fontSize: 'var(--fs-md)', color: 'var(--ink)' }}>{selectedDate}</div>
              </div>
              <button className="btn-icon" onClick={() => { setSelectedDate(null); setDayData(null) }} id="pub-cal-close-day"><X size={13} /></button>
            </div>

            {dayLoading ? (
              <div style={{ display: 'flex', justifyContent: 'center', padding: 'var(--sp-4)' }}><span className="spinner" /></div>
            ) : dayData ? (
              <>
                {/* Holiday / notices */}
                {dayData.is_holiday && (
                  <div style={{ background: 'var(--violation-soft)', border: '1px solid color-mix(in oklab, var(--violation) 20%, transparent)', borderRadius: 'var(--radius)', padding: 'var(--sp-2) var(--sp-3)', fontSize: 'var(--fs-xs)', color: 'var(--violation)', fontWeight: 600 }}>
                    🚫 Holiday / No classes
                  </div>
                )}
                {dayData.notices.filter(n => !n.startsWith('No timetable')).map((n, i) => (
                  <div key={i} style={{ fontSize: 'var(--fs-xs)', color: 'var(--amber)', background: 'var(--amber-soft)', border: '1px solid color-mix(in oklab, var(--amber) 20%, transparent)', borderRadius: 'var(--radius)', padding: 'var(--sp-2) var(--sp-3)' }}>{n}</div>
                ))}
                {dayData.validity_label && (
                  <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--clr-purple)', fontFamily: 'var(--font-mono)' }}>📋 {dayData.validity_label}</div>
                )}

                {/* Classes */}
                <div>
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-soft)', textTransform: 'uppercase', letterSpacing: '0.15em', marginBottom: 'var(--sp-2)' }}>
                    Classes ({dayData.entries.length})
                  </div>
                  {dayData.entries.length === 0 && !dayData.is_holiday ? (
                    <div style={{ color: 'var(--ink-soft)', fontSize: 'var(--fs-xs)', textAlign: 'center', padding: 'var(--sp-3)', border: '1px dashed var(--line)', borderRadius: 'var(--radius)' }}>No classes scheduled</div>
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                      {dayData.entries.map((e, i) => (
                        <div key={i} style={{ padding: '7px 10px', background: e.type === 'Practical' ? 'var(--amber-soft)' : 'var(--accent-soft)', border: `1px solid ${e.type === 'Practical' ? 'color-mix(in oklab, var(--amber) 25%, transparent)' : 'color-mix(in oklab, var(--accent) 25%, transparent)'}`, borderRadius: 'var(--radius)' }}>
                          <div style={{ fontWeight: 600, fontSize: 'var(--fs-xs)', color: 'var(--ink)' }}>
                            {e.subject_name}
                            {e.cal_source === 'modified' && <span style={{ marginLeft: 5, fontFamily: 'var(--font-mono)', fontSize: 8, color: 'var(--clr-purple)', textTransform: 'uppercase' }}>modified</span>}
                            {e.cal_source === 'added' && <span style={{ marginLeft: 5, fontFamily: 'var(--font-mono)', fontSize: 8, color: 'var(--accent)', textTransform: 'uppercase' }}>added</span>}
                          </div>
                          <div style={{ fontSize: 10, color: 'var(--ink-soft)', fontFamily: 'var(--font-mono)', marginTop: 2 }}>
                            {e.start}–{e.end} · {e.teacher} · {e.room}
                          </div>
                          <div style={{ fontSize: 9, color: 'var(--ink-soft)', marginTop: 1 }}>{e.program} {e.semester}</div>
                          {e.cal_note && <div style={{ fontSize: 9, color: 'var(--amber)', marginTop: 2 }}>{e.cal_note}</div>}
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* Cancelled */}
                {dayData.cancelled.length > 0 && (
                  <div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--violation)', textTransform: 'uppercase', letterSpacing: '0.15em', marginBottom: 'var(--sp-2)' }}>Cancelled ({dayData.cancelled.length})</div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                      {dayData.cancelled.map((e, i) => (
                        <div key={i} style={{ padding: '6px 10px', background: 'var(--violation-soft)', border: '1px solid color-mix(in oklab, var(--violation) 20%, transparent)', borderRadius: 'var(--radius)', textDecoration: 'line-through', opacity: 0.7 }}>
                          <div style={{ fontWeight: 600, fontSize: 'var(--fs-xs)', color: 'var(--violation)' }}>{e.subject_name}</div>
                          <div style={{ fontSize: 10, color: 'var(--violation)', fontFamily: 'var(--font-mono)' }}>{e.start}–{e.end} · {e.teacher}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            ) : null}
          </div>
        )}
      </div>
    </div>
  )
}
