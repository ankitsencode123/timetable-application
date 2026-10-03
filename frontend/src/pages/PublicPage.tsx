import { useState, useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import { Send, Loader2, X, CalendarDays, ChevronDown } from 'lucide-react'
import { getPublicTimetable, getPublicMeta, publicChat } from '../api'
import type { TimetableEntry, TimetableFilters } from '../types'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import FilterBar from '../components/filters/FilterBar'
import TimetableGrid from '../components/timetable/TimetableGrid'
import PublicCalendar from '../components/calendar/PublicCalendar'

function fmtDate(s?: string | null) {
  if (!s) return 'Recently'
  return new Date(s).toLocaleString('en-IN', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export default function PublicPage() {
  const [entries, setEntries] = useState<TimetableEntry[]>([])
  const [meta, setMeta] = useState<{ version_id: number; published_at: string; change_summary: string } | null>(null)
  const [filters, setFilters] = useState<TimetableFilters>({ program: 'All', semester: 'All', teacher: '', subject: '', room: '', day: '', search: '' })
  const [loading, setLoading] = useState(true)
  const [isMobile, setIsMobile] = useState(window.innerWidth <= 768)
  const [activeDay, setActiveDay] = useState('Monday')

  // Public Chat state
  const [chatOpen, setChatOpen] = useState(false)
  const [input, setInput] = useState('')
  const [chatLoading, setChatLoading] = useState(false)
  const [messages, setMessages] = useState<{role: 'user'|'assistant'|'system', content: string}[]>([
    { role: 'system', content: 'Hi! I can answer questions about the published timetable. E.g., "Where is DBMS?" or "What classes does SK teach on Monday?"' }
  ])
  const chatEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handleResize = () => setIsMobile(window.innerWidth <= 768)
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  useEffect(() => {
    async function load() {
      setLoading(true)
      try {
        const [ens, m] = await Promise.all([getPublicTimetable(), getPublicMeta()])
        setEntries(ens)
        setMeta(m)
      } catch { /* ignore */ }
      finally { setLoading(false) }
    }
    load()
  }, [])

  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages, chatOpen])

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

  const mobileFilters = { ...filters, day: isMobile ? activeDay : filters.day }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden', background: 'var(--paper)' }}>
      
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
            Ask AI
          </button>
          <Link to="/login" className="btn btn-ghost btn-sm">
            Staff login
          </Link>
        </div>
      </header>

      {/* Title + meta */}
      <div style={{ padding: 'var(--sp-5) var(--sp-6) var(--sp-3)', borderBottom: '1px solid var(--line)', background: 'var(--paper)', display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12, flexShrink: 0 }}>
        <div>
          <div className="mono-label" style={{ marginBottom: 4 }}>Published schedule</div>
          <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-2xl)', fontWeight: 600, color: 'var(--ink)', letterSpacing: '-0.015em' }}>
            Class timetable
          </h1>
        </div>
        {loading ? (
          <span className="spinner" />
        ) : meta ? (
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-soft)', background: 'color-mix(in oklab, var(--ink) 5%, transparent)', borderRadius: 'var(--radius)', padding: '5px 10px' }}>
            v{meta.version_id} · {fmtDate(meta.published_at)}
          </span>
        ) : (
          <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--violation)' }}>No published timetable.</span>
        )}
      </div>

      <FilterBar filters={filters} onChange={f => setFilters(s => ({ ...s, ...f }))} />

      {/* Main Grid */}
      <div style={{ flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        {!loading && entries.length > 0 && (
          <div style={{ flex: 1, padding: 'var(--sp-4)', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
            {isMobile && (
              <div style={{ display: 'flex', overflowX: 'auto', gap: 6, marginBottom: 'var(--sp-3)', paddingBottom: 4 }}>
                {['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'].map(d => (
                  <button
                    key={d}
                    className={`day-btn ${activeDay === d ? 'active' : ''}`}
                    onClick={() => setActiveDay(d)}
                  >{d}</button>
                ))}
              </div>
            )}
            <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
              <TimetableGrid
                entries={entries}
                filters={mobileFilters}
                showDays={isMobile ? [activeDay] : undefined}
                density="comfortable"
              />
            </div>
          </div>
        )}
        {!loading && entries.length === 0 && (
          <div className="empty-state" style={{ flex: 1 }}>
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>
            <h3>No timetable published yet</h3>
            <p>Check back later for the latest schedule.</p>
          </div>
        )}

        {/* Calendar section */}
        <CalendarSection />
      </div>

      {/* AI Chat Drawer */}
      {chatOpen && (
        <div style={{
          position: 'fixed', bottom: 0, right: 0,
          width: isMobile ? '100%' : 520,
          height: isMobile ? '80vh' : '600px',
          background: 'var(--card-bg)',
          borderTop: '1px solid var(--line)',
          borderLeft: isMobile ? 'none' : '1px solid var(--line)',
          borderTopLeftRadius: isMobile ? 20 : 12,
          boxShadow: 'var(--shadow)',
          display: 'flex', flexDirection: 'column', zIndex: 100,
        }}>
          <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--line)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{ width: 24, height: 24, background: 'var(--accent-soft)', borderRadius: 6, display: 'grid', placeItems: 'center' }}>
                <span style={{ fontSize: 12, color: 'var(--accent)', fontWeight: 700 }}>AI</span>
              </div>
              <span style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--ink)' }}>
                Timetable Assistant
              </span>
            </div>
            <button className="btn-icon" onClick={() => setChatOpen(false)}><X size={14} /></button>
          </div>

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
            <div ref={chatEndRef} />
          </div>

          <div style={{ padding: '10px var(--sp-3)', borderTop: '1px solid var(--line)' }}>
            <div className="chat-suggestions" style={{ marginBottom: 8 }}>
              <button className="chat-suggestion" onClick={() => setInput('Where is DBMS?')}>Where is DBMS?</button>
              <button className="chat-suggestion" onClick={() => setInput('What does B.Tech 5th have today?')}>B.Tech 5th today?</button>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <input
                className="form-control"
                placeholder="Ask about the timetable…"
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
              >
                {chatLoading ? <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} /> : <Send size={14} />}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function CalendarSection() {
  const [open, setOpen] = useState(false)
  return (
    <div style={{ borderTop: '1px solid var(--line)', flexShrink: 0 }}>
      <button
        id="pub-cal-toggle"
        onClick={() => setOpen(o => !o)}
        style={{
          width: '100%', padding: 'var(--sp-3) var(--sp-5)',
          display: 'flex', alignItems: 'center', gap: 8,
          background: open ? 'var(--accent-soft)' : 'var(--paper)',
          border: 'none', cursor: 'pointer',
          borderBottom: open ? '1px solid var(--line)' : 'none',
          transition: 'var(--transition)',
          color: open ? 'var(--accent)' : 'var(--ink-soft)',
        }}
      >
        <CalendarDays size={14} />
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.15em' }}>
          Date-Aware Calendar View
        </span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--ink-soft)', marginLeft: 4 }}>click to {open ? 'hide' : 'show'}</span>
        <ChevronDown size={13} style={{ marginLeft: 'auto', transform: open ? 'rotate(180deg)' : 'none', transition: 'var(--transition)' }} />
      </button>
      {open && (
        <div style={{ padding: 'var(--sp-4) var(--sp-5) var(--sp-5)' }}>
          <PublicCalendar />
        </div>
      )}
    </div>
  )
}
