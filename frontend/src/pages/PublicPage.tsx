import { useState, useEffect, useRef, useMemo } from 'react'
import { Link } from 'react-router-dom'
import {
  Send, Loader2, X, CalendarDays, ChevronDown, ChevronLeft, ChevronRight,
  Clock, MapPin, User, Calendar as CalendarIcon, CheckCircle2, AlertTriangle, ArrowRight,
  Sparkles, Layers
} from 'lucide-react'
import { getPublicTimetable, getPublicMeta, publicChat, getCalendarDay, getCalendarRange } from '../api'
import type { TimetableEntry, TimetableFilters } from '../types'
import type { CalendarDayResult, CalendarDayEntry } from '../api'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import FilterBar from '../components/filters/FilterBar'
import TimetableGrid from '../components/timetable/TimetableGrid'
import PublicCalendar from '../components/calendar/PublicCalendar'

function fmtDate(s?: string | null) {
  if (!s) return 'Recently'
  return new Date(s).toLocaleString('en-IN', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function toISODate(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function getDayName(dateStr: string): string {
  const d = new Date(dateStr + 'T00:00:00')
  return ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'][d.getDay()]
}

function formatLongDate(dateStr: string): string {
  const d = new Date(dateStr + 'T00:00:00')
  return d.toLocaleDateString('en-IN', {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    year: 'numeric'
  })
}

function formatShortDate(dateStr: string): string {
  const d = new Date(dateStr + 'T00:00:00')
  return d.toLocaleDateString('en-IN', {
    day: 'numeric',
    month: 'short'
  })
}

function getWeekRangeAndDays(selectedDateStr: string) {
  const d = new Date(selectedDateStr + 'T00:00:00')
  const dayOfWeek = d.getDay() // 0 = Sunday
  const sunday = new Date(d)
  sunday.setDate(d.getDate() - dayOfWeek)

  const days = []
  for (let i = 0; i < 7; i++) {
    const cur = new Date(sunday)
    cur.setDate(sunday.getDate() + i)
    const iso = toISODate(cur)
    days.push({
      date: iso,
      weekday: ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'][cur.getDay()],
      shortDay: ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][cur.getDay()],
      dayNum: cur.getDate(),
      monthShort: cur.toLocaleDateString('en-US', { month: 'short' }),
    })
  }
  return {
    start: days[0].date,
    end: days[6].date,
    days,
  }
}

export default function PublicPage() {
  const todayStr = useMemo(() => toISODate(new Date()), [])
  const [selectedDate, setSelectedDate] = useState<string>(todayStr)
  const [viewMode, setViewMode] = useState<'day' | 'week' | 'month'>('day')
  const mainContentRef = useRef<HTMLDivElement>(null)
  const pillsScrollerRef = useRef<HTMLDivElement>(null)

  // Auto-center active pill when selectedDate changes
  useEffect(() => {
    if (!pillsScrollerRef.current) return
    const activeEl = pillsScrollerRef.current.querySelector('.date-pill-btn.active') as HTMLElement | null
    if (activeEl) {
      activeEl.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' })
    }
  }, [selectedDate])

  const handleJumpToDay = (date: string) => {
    setSelectedDate(date)
    setViewMode('day')
    setTimeout(() => {
      mainContentRef.current?.scrollTo({ top: 0, behavior: 'smooth' })
    }, 50)
  }

  const [baseEntries, setBaseEntries] = useState<TimetableEntry[]>([])
  const [meta, setMeta] = useState<{ version_id: number; published_at: string; change_summary: string } | null>(null)
  const [filters, setFilters] = useState<TimetableFilters>({ program: 'All', semester: 'All', teacher: '', subject: '', room: '', day: '', search: '' })
  const [loading, setLoading] = useState(true)
  const [isMobile, setIsMobile] = useState(window.innerWidth <= 768)

  // Date-resolved Day Data
  const [dayData, setDayData] = useState<CalendarDayResult | null>(null)
  const [dayLoading, setDayLoading] = useState(false)

  // Date-resolved Week Data
  const [weekDays, setWeekDays] = useState<CalendarDayResult[]>([])
  const [weekLoading, setWeekLoading] = useState(false)

  // Public Chat state
  const [chatOpen, setChatOpen] = useState(false)
  const [input, setInput] = useState('')
  const [chatLoading, setChatLoading] = useState(false)
  const [messages, setMessages] = useState<{role: 'user'|'assistant'|'system', content: string}[]>([
    { role: 'system', content: 'Hi! I can answer questions about the published timetable. E.g., "Where is DBMS?" or "What classes does SK teach on Monday?"' }
  ])
  const chatEndRef = useRef<HTMLDivElement>(null)

  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handleResize = () => setIsMobile(window.innerWidth <= 768)
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  // Lock horizontal scroll to 0 to prevent mobile viewport shifting during search typing
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const clampScroll = () => {
      if (el.scrollLeft !== 0) el.scrollLeft = 0
    }
    el.addEventListener('scroll', clampScroll, { passive: true })
    return () => el.removeEventListener('scroll', clampScroll)
  }, [])

  useEffect(() => {
    const clampWindowScroll = () => {
      if (window.scrollX !== 0) window.scrollTo(0, window.scrollY)
    }
    window.addEventListener('scroll', clampWindowScroll, { passive: true })
    return () => window.removeEventListener('scroll', clampWindowScroll)
  }, [])

  useEffect(() => {
    if (containerRef.current && containerRef.current.scrollLeft !== 0) {
      containerRef.current.scrollLeft = 0
    }
  }, [filters.search])

  // Load initial base timetable + metadata
  useEffect(() => {
    async function loadInitial() {
      setLoading(true)
      try {
        const [ens, m] = await Promise.all([getPublicTimetable(), getPublicMeta()])
        setBaseEntries(ens)
        setMeta(m)
      } catch { /* ignore */ }
      finally { setLoading(false) }
    }
    loadInitial()
  }, [])

  // Fetch date-specific classes whenever selectedDate or key filters change
  useEffect(() => {
    let cancelled = false
    async function fetchDateSchedule() {
      setDayLoading(true)
      try {
        const prog = filters.program !== 'All' ? filters.program : undefined
        const sem = filters.semester !== 'All' ? filters.semester : undefined
        const teacher = filters.teacher || undefined
        const res = await getCalendarDay(selectedDate, { program: prog, semester: sem, teacher })
        if (!cancelled) setDayData(res)
      } catch {
        if (!cancelled) setDayData(null)
      } finally {
        if (!cancelled) setDayLoading(false)
      }
    }
    fetchDateSchedule()
    return () => { cancelled = true }
  }, [selectedDate, filters.program, filters.semester, filters.teacher])

  // Fetch week range whenever selectedDate changes
  useEffect(() => {
    let cancelled = false
    async function fetchWeekSchedule() {
      setWeekLoading(true)
      try {
        const { start, end } = getWeekRangeAndDays(selectedDate)
        const prog = filters.program !== 'All' ? filters.program : undefined
        const sem = filters.semester !== 'All' ? filters.semester : undefined
        const teacher = filters.teacher || undefined
        const res = await getCalendarRange(start, end, { program: prog, semester: sem, teacher })
        if (!cancelled) setWeekDays(res.days)
      } catch {
        if (!cancelled) setWeekDays([])
      } finally {
        if (!cancelled) setWeekLoading(false)
      }
    }
    fetchWeekSchedule()
    return () => { cancelled = true }
  }, [selectedDate, filters.program, filters.semester, filters.teacher])

  // Auto-scroll chat when messages change or typing indicator appears
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, chatLoading, chatOpen])

  async function sendChat() {
    const text = input.trim()
    if (!text || chatLoading) return
    setInput('')
    setMessages(prev => [...prev, { role: 'user', content: text }])
    setChatLoading(true)
    try {
      const resp = await publicChat(text)
      setMessages(prev => [...prev, { role: 'assistant', content: resp.message }])
    } catch (e: unknown) {
      setMessages(prev => [...prev, { role: 'assistant', content: `Error: ${(e as Error).message}` }])
    } finally {
      setChatLoading(false)
    }
  }

  // Navigation handlers
  function prevDay() {
    const d = new Date(selectedDate + 'T00:00:00')
    d.setDate(d.getDate() - 1)
    setSelectedDate(toISODate(d))
    pillsScrollerRef.current?.scrollBy({ left: -100, behavior: 'smooth' })
  }

  function nextDay() {
    const d = new Date(selectedDate + 'T00:00:00')
    d.setDate(d.getDate() + 1)
    setSelectedDate(toISODate(d))
    pillsScrollerRef.current?.scrollBy({ left: 100, behavior: 'smooth' })
  }

  function selectTomorrow() {
    const d = new Date(selectedDate + 'T00:00:00')
    d.setDate(d.getDate() + 1)
    setSelectedDate(toISODate(d))
  }

  // Week days calculation
  const weekInfo = useMemo(() => getWeekRangeAndDays(selectedDate), [selectedDate])

  const dayDateMap = useMemo(() => {
    const map: Record<string, string> = {}
    for (const d of weekInfo.days) {
      map[d.weekday] = `${d.dayNum} ${d.monthShort}`
    }
    return map
  }, [weekInfo])

  // Assemble week grid entries from date-resolved week data
  const weekGridEntries = useMemo(() => {
    if (!weekDays || weekDays.length === 0) return baseEntries
    const all: TimetableEntry[] = []
    for (const d of weekDays) {
      for (const e of d.entries) {
        all.push({
          ...e,
          type: (e.type === 'Practical' ? 'Practical' : 'Theory') as 'Theory' | 'Practical',
          _modified: e.cal_source === 'modified',
        })
      }
      for (const c of d.cancelled) {
        all.push({
          ...c,
          type: (c.type === 'Practical' ? 'Practical' : 'Theory') as 'Theory' | 'Practical',
          _cancelled: true,
        })
      }
    }
    return all
  }, [weekDays, baseEntries])

  // Filter day entries client-side for search, subject, and room
  const filteredDayEntries = useMemo(() => {
    if (!dayData?.entries) return []
    const q = filters.search?.toLowerCase() ?? ''
    return dayData.entries.filter(e => {
      const sanitizedProgram = (e.program || '').replace(/\.$/, '')
      if (filters.program && filters.program !== 'All' && sanitizedProgram !== filters.program) return false
      if (filters.semester && filters.semester !== 'All' && e.semester !== filters.semester) return false
      if (filters.teacher && !e.teacher.toLowerCase().includes(filters.teacher.toLowerCase())) return false
      if (filters.subject && !e.subject_name.toLowerCase().includes(filters.subject.toLowerCase()) && !e.subject_code.toLowerCase().includes(filters.subject.toLowerCase())) return false
      if (filters.room && !e.room.toLowerCase().includes(filters.room.toLowerCase())) return false
      if (q && !e.subject_name.toLowerCase().includes(q) && !e.subject_code.toLowerCase().includes(q) && !e.teacher.toLowerCase().includes(q) && !e.room.toLowerCase().includes(q) && !sanitizedProgram.toLowerCase().includes(q)) return false
      return true
    })
  }, [dayData, filters])

  const selectedWeekday = getDayName(selectedDate)
  const isSelectedToday = selectedDate === todayStr
  const todayDayName = getDayName(todayStr)

  return (
    <div
      ref={containerRef}
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100dvh',
        minHeight: '-webkit-fill-available',
        width: '100%',
        maxWidth: '100vw',
        overflow: 'hidden',
        overflowX: 'hidden',
        boxSizing: 'border-box',
        background: 'var(--paper)',
      }}
    >

      {/* Header */}
      <header className="public-header" style={{ justifyContent: 'space-between' }}>
        <div className="header-logo">
          <div className="header-logo-box">CT</div>
          <div className="header-logo-text">
            <div className="name">Courselab</div>
            <div className="sub">Timetable Studio</div>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-2)' }}>
          <button className="btn btn-primary btn-sm" onClick={() => setChatOpen(true)}>
            <Sparkles size={13} style={{ marginRight: 4 }} />
            Ask AI
          </button>
          <Link to="/login" className="btn btn-ghost btn-sm">
            Staff login
          </Link>
        </div>
      </header>

      {/* Title + meta */}
      <div className="public-title-strip">
        <div>
          <div className="mono-label" style={{ marginBottom: 2 }}>Live Academic Schedule</div>
          <h1 className="public-title-h1">
            Class Timetable
          </h1>
        </div>
        {loading ? (
          <span className="spinner" />
        ) : meta ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span className="public-version-badge">
              v<span className="t-digit-group is-animating">
                {String(meta.version_id).split('').map((d, i) => (
                  <span key={i} className="t-digit" data-stagger={i > 0 ? String(i) : undefined}>{d}</span>
                ))}
              </span> · {fmtDate(meta.published_at)}
            </span>
          </div>
        ) : (
          <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--violation)' }}>No published timetable.</span>
        )}
      </div>

      {/* Filter bar */}
      <FilterBar filters={filters} onChange={f => setFilters(s => ({ ...s, ...f }))} />

      {/* ── Date Navigation & View Mode Strip ── */}
      <div className="date-nav-bar">
        {/* View toggle + Jump to date controls */}
        <div className="date-nav-controls">
          <div className="date-nav-view-toggle">
            <button
              className={`btn btn-sm ${viewMode === 'day' ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => setViewMode('day')}
              style={{ fontSize: 11, padding: isMobile ? '4px 6px' : '4px 8px', height: 28, flexShrink: 0, whiteSpace: 'nowrap' }}
              title="Day Schedule"
            >
              <CalendarIcon size={12} style={{ marginRight: 3 }} />
              {isMobile ? 'Day' : 'Day Schedule'}
            </button>
            <button
              className={`btn btn-sm ${viewMode === 'week' ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => setViewMode('week')}
              style={{ fontSize: 11, padding: isMobile ? '4px 6px' : '4px 8px', height: 28, flexShrink: 0, whiteSpace: 'nowrap' }}
              title="Week Grid"
            >
              <Layers size={12} style={{ marginRight: 3 }} />
              {isMobile ? 'Week' : 'Week Grid'}
            </button>
            <button
              className={`btn btn-sm ${viewMode === 'month' ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => setViewMode('month')}
              style={{ fontSize: 11, padding: isMobile ? '4px 6px' : '4px 8px', height: 28, flexShrink: 0, whiteSpace: 'nowrap' }}
              title="Monthly Calendar"
            >
              <CalendarDays size={12} style={{ marginRight: 3 }} />
              {isMobile ? 'Month' : 'Month Overview'}
            </button>
          </div>

          <div className="date-nav-date-actions">
            <button
              className={`btn btn-sm ${isSelectedToday ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => setSelectedDate(todayStr)}
              style={{ fontFamily: 'var(--font-mono)', fontSize: 11, padding: isMobile ? '4px 7px' : '4px 9px', height: 28, whiteSpace: 'nowrap', flexShrink: 0 }}
              title="Jump to Today"
            >
              Today ({todayStr.slice(8)})
            </button>

            <input
              type="date"
              className="form-control date-nav-input"
              value={selectedDate}
              onChange={e => e.target.value && setSelectedDate(e.target.value)}
              title="Jump to date"
              style={{ flexShrink: 0 }}
            />
          </div>
        </div>

        {/* Quick Day Pills for the week */}
        {viewMode !== 'month' && (
          <div className="date-nav-pills-row">
            <button
              className="btn btn-ghost btn-icon btn-sm date-nav-arrow"
              onClick={prevDay}
              title="Previous Day"
            >
              <ChevronLeft size={15} />
            </button>

            <div ref={pillsScrollerRef} className="date-nav-pills-scroller">
              {weekInfo.days.map(d => {
                const isToday = d.date === todayStr
                const isSelected = d.date === selectedDate
                return (
                  <button
                    key={d.date}
                    className={`date-pill-btn ${isSelected ? 'active' : ''} ${isToday ? 'is-today' : ''}`}
                    onClick={() => setSelectedDate(d.date)}
                    title={`${d.weekday}, ${d.dayNum} ${d.monthShort}`}
                  >
                    <span className="date-pill-day">
                      {d.shortDay}
                    </span>
                    <span className="date-pill-num">
                      {d.dayNum}
                    </span>
                    {isToday && (
                      <span className="date-pill-sub">
                        Today
                      </span>
                    )}
                  </button>
                )
              })}
            </div>

            <button
              className="btn btn-ghost btn-icon btn-sm date-nav-arrow"
              onClick={nextDay}
              title="Next Day"
            >
              <ChevronRight size={15} />
            </button>
          </div>
        )}
      </div>

      {/* Main Content Area */}
      <div ref={mainContentRef} className="public-main-content">

        {/* VIEW MODE 1: DAY SCHEDULE (Focused Date View) */}
        {viewMode === 'day' && (
          <div className="public-day-container" style={{ display: 'flex', flexDirection: 'column', gap: 14, width: '100%' }}>
            {/* Day Header Banner */}
            <div className="public-day-banner" style={{
              background: 'var(--card-bg)',
              border: `1px solid ${isSelectedToday ? 'var(--accent)' : 'var(--line)'}`,
              borderRadius: 'var(--radius-lg)',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              flexWrap: 'wrap',
              gap: 10,
              boxShadow: isSelectedToday ? '0 2px 12px color-mix(in oklab, var(--accent) 12%, transparent)' : undefined
            }}>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3 }}>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, textTransform: 'uppercase', color: 'var(--ink-soft)', letterSpacing: '0.12em', fontWeight: 600 }}>
                    {selectedWeekday}
                  </span>
                  {isSelectedToday && (
                    <span style={{ background: 'var(--accent)', color: 'var(--paper)', fontSize: 10, fontFamily: 'var(--font-mono)', padding: '2px 7px', borderRadius: 4, fontWeight: 700, letterSpacing: '0.08em' }}>
                      TODAY
                    </span>
                  )}
                  {dayData?.validity_label && (
                    <span style={{ background: 'color-mix(in oklab, var(--clr-purple) 15%, transparent)', color: 'var(--clr-purple)', fontSize: 10, fontFamily: 'var(--font-mono)', padding: '2px 7px', borderRadius: 4, fontWeight: 600 }}>
                      📋 {dayData.validity_label}
                    </span>
                  )}
                </div>
                <h2 className="public-day-h2" style={{ fontFamily: 'var(--font-display)', fontWeight: 600, color: 'var(--ink)', margin: 0 }}>
                  {formatLongDate(selectedDate)}
                </h2>
              </div>

              {/* Status Chips */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                {dayLoading ? (
                  <span className="spinner" />
                ) : dayData?.is_holiday ? (
                  <span style={{ background: 'var(--violation-soft)', color: 'var(--violation)', border: '1px solid color-mix(in oklab, var(--violation) 25%, transparent)', padding: '5px 12px', borderRadius: 'var(--radius)', fontSize: 'var(--fs-xs)', fontWeight: 600 }}>
                    🚫 Holiday / No Classes
                  </span>
                ) : (
                  <span style={{ background: 'var(--accent-soft)', color: 'var(--accent)', border: '1px solid color-mix(in oklab, var(--accent) 25%, transparent)', padding: '5px 12px', borderRadius: 'var(--radius)', fontSize: 'var(--fs-xs)', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 5 }}>
                    <CheckCircle2 size={13} />
                    {filteredDayEntries.length} {filteredDayEntries.length === 1 ? 'Class' : 'Classes'} Scheduled
                  </span>
                )}
                {dayData && dayData.cancelled.length > 0 && (
                  <span style={{ background: 'var(--violation-soft)', color: 'var(--violation)', border: '1px solid color-mix(in oklab, var(--violation) 25%, transparent)', padding: '5px 12px', borderRadius: 'var(--radius)', fontSize: 'var(--fs-xs)', fontWeight: 600 }}>
                    {dayData.cancelled.length} Cancelled
                  </span>
                )}
              </div>
            </div>

            {/* Notices / Announcements */}
            {dayData?.notices.filter(n => !n.startsWith('No timetable')).map((notice, i) => (
              <div key={i} style={{ background: 'var(--amber-soft)', border: '1px solid color-mix(in oklab, var(--amber) 25%, transparent)', color: 'var(--amber)', padding: '9px 14px', borderRadius: 'var(--radius)', fontSize: 'var(--fs-xs)', display: 'flex', alignItems: 'center', gap: 8, fontWeight: 500 }}>
                <AlertTriangle size={14} style={{ flexShrink: 0 }} />
                <span>{notice}</span>
              </div>
            ))}

            {/* Classes List / Empty State */}
            {dayLoading ? (
              <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', flex: 1, minHeight: 250 }}>
                <span className="spinner" />
              </div>
            ) : filteredDayEntries.length === 0 ? (
              <div className="empty-state" style={{ background: 'var(--card-bg)', border: '1px dashed var(--line)', borderRadius: 'var(--radius-lg)', padding: '48px 20px', textAlign: 'center', flex: 1 }}>
                <CalendarIcon size={40} style={{ color: 'var(--ink-soft)', margin: '0 auto 12px', opacity: 0.6 }} />
                <h3 style={{ fontSize: 'var(--fs-lg)', fontWeight: 600, color: 'var(--ink)', marginBottom: 6 }}>
                  No classes scheduled for {selectedWeekday}, {selectedDate}
                </h3>
                <p style={{ color: 'var(--ink-soft)', fontSize: 'var(--fs-sm)', maxWidth: 460, margin: '0 auto 16px' }}>
                  {isSelectedToday
                    ? "There are no academic classes scheduled on your calendar for today! You can review other dates or view the full week schedule."
                    : "There are no classes scheduled on this date matching your active filters."}
                </p>
                <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
                  {selectedWeekday === 'Sunday' && (
                    <button className="btn btn-primary btn-sm" onClick={selectTomorrow}>
                      View Monday's Classes <ArrowRight size={13} style={{ marginLeft: 4 }} />
                    </button>
                  )}
                  <button className="btn btn-ghost btn-sm" onClick={() => setViewMode('week')}>
                    Switch to Week Grid
                  </button>
                </div>
              </div>
            ) : (
              <div className="public-cards-grid">
                {filteredDayEntries.map((e, idx) => {
                  const isPractical = e.type === 'Practical'
                  const isModified = e.cal_source === 'modified'
                  const isAdded = e.cal_source === 'added'
                  return (
                    <div
                      key={idx}
                      style={{
                        background: 'var(--card-bg)',
                        border: `1.5px solid ${isModified ? 'var(--amber)' : isPractical ? 'color-mix(in oklab, var(--amber) 30%, var(--line))' : 'color-mix(in oklab, var(--accent) 30%, var(--line))'}`,
                        borderRadius: 'var(--radius-lg)',
                        padding: '14px 16px',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: 10,
                        position: 'relative',
                        boxShadow: '0 2px 8px rgba(0,0,0,0.02)'
                      }}
                    >
                      {/* Top row: Time + Type badge */}
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 700, color: 'var(--ink)' }}>
                          <Clock size={14} style={{ color: isPractical ? 'var(--amber)' : 'var(--accent)' }} />
                          <span>{e.start} – {e.end}</span>
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                          {isModified && (
                            <span style={{ fontSize: 9, fontFamily: 'var(--font-mono)', background: 'var(--amber-soft)', color: 'var(--amber)', border: '1px solid var(--amber)', padding: '1px 6px', borderRadius: 3, fontWeight: 700, textTransform: 'uppercase' }}>
                              Modified
                            </span>
                          )}
                          {isAdded && (
                            <span style={{ fontSize: 9, fontFamily: 'var(--font-mono)', background: 'var(--accent-soft)', color: 'var(--accent)', border: '1px solid var(--accent)', padding: '1px 6px', borderRadius: 3, fontWeight: 700, textTransform: 'uppercase' }}>
                              Added
                            </span>
                          )}
                          <span style={{
                            fontSize: 10,
                            fontFamily: 'var(--font-mono)',
                            padding: '2px 8px',
                            borderRadius: 4,
                            fontWeight: 600,
                            background: isPractical ? 'var(--amber-soft)' : 'var(--accent-soft)',
                            color: isPractical ? 'var(--amber)' : 'var(--accent)',
                            border: `1px solid ${isPractical ? 'color-mix(in oklab, var(--amber) 25%, transparent)' : 'color-mix(in oklab, var(--accent) 25%, transparent)'}`
                          }}>
                            {e.type}
                          </span>
                        </div>
                      </div>

                      {/* Middle row: Subject */}
                      <div>
                        <div style={{ fontFamily: 'var(--font-display)', fontSize: 16, fontWeight: 700, color: 'var(--ink)', wordBreak: 'break-word' }}>
                          {e.subject_name}
                        </div>
                        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-soft)', marginTop: 2 }}>
                          {e.subject_code} · {e.program} {e.semester}
                        </div>
                      </div>

                      {/* Bottom row: Teacher + Room */}
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', paddingTop: 8, borderTop: '1px solid var(--line)', marginTop: 2, flexWrap: 'wrap', gap: 6 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--ink-soft)' }}>
                          <User size={13} style={{ color: 'var(--ink-soft)' }} />
                          <span style={{ fontWeight: 500, color: 'var(--ink)' }}>{e.teacher}</span>
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--ink-soft)', fontFamily: 'var(--font-mono)' }}>
                          <MapPin size={13} style={{ color: 'var(--accent)' }} />
                          <span style={{ fontWeight: 600, color: 'var(--ink)' }}>{e.room}</span>
                        </div>
                      </div>

                      {/* Note if modified/added */}
                      {e.cal_note && (
                        <div style={{ fontSize: 11, color: 'var(--amber)', background: 'var(--amber-soft)', padding: '4px 8px', borderRadius: 4, marginTop: 4 }}>
                          Note: {e.cal_note}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}

            {/* Cancelled Classes for the day */}
            {dayData && dayData.cancelled.length > 0 && (
              <div style={{ marginTop: 12 }}>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--violation)', textTransform: 'uppercase', letterSpacing: '0.12em', fontWeight: 600, marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span>🚫 Cancelled Classes on {formatShortDate(selectedDate)} ({dayData.cancelled.length})</span>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'repeat(auto-fill, minmax(300px, 1fr))', gap: 10 }}>
                  {dayData.cancelled.map((c, i) => (
                    <div
                      key={i}
                      style={{
                        padding: '12px 14px',
                        background: 'var(--violation-soft)',
                        border: '1px solid color-mix(in oklab, var(--violation) 25%, transparent)',
                        borderRadius: 'var(--radius)',
                        opacity: 0.85
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <div style={{ fontWeight: 700, fontSize: 14, color: 'var(--violation)', textDecoration: 'line-through' }}>
                          {c.subject_name} ({c.type})
                        </div>
                        <span style={{ fontSize: 9, fontFamily: 'var(--font-mono)', background: 'var(--violation)', color: 'var(--paper)', padding: '1px 5px', borderRadius: 3, fontWeight: 700 }}>
                          CANCELLED
                        </span>
                      </div>
                      <div style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--violation)', marginTop: 4 }}>
                        {c.start}–{c.end} · {c.teacher} · {c.room} · {c.program} {c.semester}
                      </div>
                      {c.cal_note && (
                        <div style={{ fontSize: 11, color: 'var(--violation)', fontWeight: 600, marginTop: 4 }}>
                          Reason: {c.cal_note}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Expandable Monthly Calendar Drawer at bottom of Day Schedule */}
            <CalendarSection onJumpToDay={handleJumpToDay} />
          </div>
        )}

        {/* VIEW MODE 2: WEEK TIMETABLE GRID */}
        {viewMode === 'week' && (
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, width: '100%' }}>
            {/* Week info header */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10, flexWrap: 'wrap', gap: 6 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-soft)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
                  Week of {formatShortDate(weekInfo.start)} – {formatShortDate(weekInfo.end)}
                </span>
                {weekLoading && <span className="spinner" style={{ width: 14, height: 14 }} />}
              </div>
              <span style={{ fontSize: 11, color: 'var(--ink-soft)', fontFamily: 'var(--font-mono)' }}>
                Date-aware timetable (live overrides)
              </span>
            </div>

            <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
              <TimetableGrid
                entries={weekGridEntries}
                filters={filters}
                dayDateMap={dayDateMap}
                todayDay={todayDayName}
                density="comfortable"
              />
            </div>
          </div>
        )}

        {/* VIEW MODE 3: FULL MONTH CALENDAR */}
        {viewMode === 'month' && (
          <div className="public-month-container" style={{ width: '100%', maxWidth: 860, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div className="public-day-banner" style={{
              background: 'var(--card-bg)',
              border: '1px solid var(--line)',
              borderRadius: 'var(--radius-lg)',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              flexWrap: 'wrap',
              gap: 8
            }}>
              <div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, textTransform: 'uppercase', color: 'var(--ink-soft)', letterSpacing: '0.12em', fontWeight: 600, marginBottom: 2 }}>
                  Academic Calendar
                </div>
                <h2 className="public-day-h2" style={{ fontFamily: 'var(--font-display)', fontWeight: 600, color: 'var(--ink)', margin: 0 }}>
                  Monthly Schedule Overview
                </h2>
              </div>
              <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)', fontFamily: 'var(--font-mono)' }}>
                Tap any date to inspect classes
              </div>
            </div>

            <PublicCalendar
              initialDate={selectedDate}
              onSelectDate={d => setSelectedDate(d)}
              onJumpToDay={handleJumpToDay}
            />
          </div>
        )}
      </div>

      {/* ── AI Chatbot Drawer ── */}
      {chatOpen && (
        <>
          {isMobile && (
            <div
              className="chat-backdrop"
              onClick={() => setChatOpen(false)}
              style={{
                position: 'fixed',
                inset: 0,
                background: 'rgba(0, 0, 0, 0.45)',
                zIndex: 99,
                backdropFilter: 'blur(2px)',
              }}
            />
          )}
          <div
            className="t-panel-slide"
            data-open="true"
            style={{
              position: 'fixed', bottom: 0, right: 0,
              width: isMobile ? '100%' : 520,
              height: isMobile ? '82dvh' : '600px',
              background: 'var(--card-bg)',
              borderTop: '1px solid var(--line)',
              borderLeft: isMobile ? 'none' : '1px solid var(--line)',
              borderTopLeftRadius: isMobile ? 20 : 12,
              borderTopRightRadius: isMobile ? 20 : 0,
              boxShadow: 'var(--shadow)',
              display: 'flex', flexDirection: 'column', zIndex: 100,
            }}
          >
            {/* Chat Header */}
            <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--line)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <div style={{ width: 24, height: 24, background: 'var(--accent-soft)', borderRadius: 6, display: 'grid', placeItems: 'center' }}>
                  <Sparkles size={13} style={{ color: 'var(--accent)' }} />
                </div>
                <div>
                  <span style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--ink)' }}>
                    Timetable Assistant
                  </span>
                  <span style={{ fontSize: 10, color: 'var(--accent)', fontFamily: 'var(--font-mono)', marginLeft: 8 }}>
                    ● Active
                  </span>
                </div>
              </div>
              <button className="btn-icon" onClick={() => setChatOpen(false)} title="Close Chat">
                <X size={14} />
              </button>
            </div>

            {/* Chat Messages */}
            <div style={{ flex: 1, overflowY: 'auto', padding: 'var(--sp-3)', display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)' }}>
              {messages.map((m, i) => (
                <div key={i} className={`chat-msg ${m.role}`}>
                  <div style={{ wordBreak: 'break-word', whiteSpace: 'normal' }}>
                    {m.role === 'assistant' ? (
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
                    ) : m.content}
                  </div>
                </div>
              ))}

              {/* Typing Indicator while AI is generating response */}
              {chatLoading && (
                <div className="chat-msg assistant chat-typing-indicator">
                  <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                    <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--ink-soft)' }}>
                      AI is typing
                    </span>
                    <div className="chat-typing-dots">
                      <span className="chat-typing-dot" />
                      <span className="chat-typing-dot" />
                      <span className="chat-typing-dot" />
                    </div>
                  </div>
                </div>
              )}

              <div ref={chatEndRef} />
            </div>

            {/* Chat Input */}
            <div style={{ padding: '10px var(--sp-3)', borderTop: '1px solid var(--line)' }}>
              <div className="chat-suggestions" style={{ marginBottom: 8 }}>
                <button className="chat-suggestion" onClick={() => setInput('What classes are scheduled today?')}>Classes today?</button>
                <button className="chat-suggestion" onClick={() => setInput('Where is DBMS?')}>Where is DBMS?</button>
                <button className="chat-suggestion" onClick={() => setInput('What does B.Tech 5th have on Monday?')}>B.Tech 5th Monday?</button>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <input
                  className="form-control"
                  placeholder="Ask about classes, dates, teachers, rooms…"
                  value={input}
                  onChange={e => setInput(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && sendChat()}
                  disabled={chatLoading}
                />
                <button
                  className="btn btn-primary"
                  onClick={sendChat}
                  disabled={!input.trim() || chatLoading}
                  style={{ padding: '0 12px', flexShrink: 0 }}
                  title="Send message"
                >
                  {chatLoading ? <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} /> : <Send size={14} />}
                </button>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

function CalendarSection({ onJumpToDay }: { onJumpToDay?: (date: string) => void }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="public-cal-section-card">
      <button
        id="pub-cal-toggle"
        className="public-cal-toggle-btn"
        aria-expanded={open}
        onClick={() => setOpen(o => !o)}
      >
        <div className="public-cal-toggle-left">
          <div className="public-cal-toggle-icon">
            <CalendarDays size={16} />
          </div>
          <div className="public-cal-toggle-text">
            <span className="public-cal-toggle-title">Monthly Calendar</span>
            <span className="public-cal-toggle-sub">
              {open ? 'Tap to collapse month view' : 'Tap to expand full month overview'}
            </span>
          </div>
        </div>
        <div className="public-cal-toggle-right">
          <span className="public-cal-toggle-badge">{open ? 'Hide' : 'Expand'}</span>
          <span className={`public-cal-chevron ${open ? 'open' : ''}`}>
            <ChevronDown size={16} />
          </span>
        </div>
      </button>

      {open && (
        <div className="public-cal-expand-body">
          <PublicCalendar initialDate={null} onJumpToDay={onJumpToDay} />
        </div>
      )}
    </div>
  )
}
