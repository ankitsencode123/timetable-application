import { useState, useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import { Calendar, Search, LogIn, Clock, Bot, Send, Loader2 } from 'lucide-react'
import { getPublicTimetable, getPublicMeta, publicChat } from '../api'
import type { TimetableEntry, TimetableFilters } from '../types'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import FilterBar from '../components/filters/FilterBar'
import TimetableGrid from '../components/timetable/TimetableGrid'
import ClassDetailModal from '../components/timetable/ClassDetailModal'

function fmtDate(s: string) {
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

  // Filter for grid
  // In mobile mode, also filter by activeDay
  const mobileFilters = { ...filters, day: isMobile ? activeDay : filters.day }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>
      {/* Header */}
      <header className="public-header" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 32, height: 32, background: 'var(--clr-primary-20)', borderRadius: 'var(--radius)' }}>
            <Calendar size={18} style={{ color: 'var(--clr-primary)' }} />
          </div>
          <div style={{ fontWeight: 800, fontSize: 'var(--fs-lg)', background: 'linear-gradient(135deg, var(--clr-primary), var(--clr-purple))', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
            ChronoSync
          </div>
        </div>
        <div style={{ display: 'flex', gap: 'var(--sp-3)' }}>
          <button className="btn btn-primary" onClick={() => setChatOpen(true)}>
            <Bot size={14} /> Ask AI
          </button>
          <Link to="/login" className="btn btn-ghost">
            <LogIn size={14} /> Teacher Login
          </Link>
        </div>
      </header>

      {/* Hero section */}
      <div className="public-hero" style={{ flexShrink: 0 }}>
        <h1>Academic Timetable</h1>
        <p>Current published schedule for all programs</p>
        
        {loading ? (
          <div style={{ marginTop: 'var(--sp-4)', display: 'flex', justifyContent: 'center' }}><Loader2 size={24} style={{ animation: 'spin 1s linear infinite', color: 'var(--clr-primary)' }} /></div>
        ) : meta ? (
          <div style={{ marginTop: 'var(--sp-4)', display: 'inline-flex', alignItems: 'center', gap: 8, background: 'var(--clr-success-bg)', border: '1px solid rgba(16,185,129,0.3)', padding: '6px 12px', borderRadius: 999 }}>
            <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--clr-success)', fontWeight: 600 }}>Active Version #{meta.version_id}</span>
            <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--clr-text-3)', display: 'flex', alignItems: 'center', gap: 4 }}>
              <Clock size={11} /> Published {fmtDate(meta.published_at)}
            </span>
          </div>
        ) : (
          <div style={{ marginTop: 'var(--sp-4)', fontSize: 'var(--fs-sm)', color: 'var(--clr-warning)' }}>No published timetable currently available.</div>
        )}
      </div>

      <FilterBar filters={filters} onChange={f => setFilters(s => ({ ...s, ...f }))} />

      {/* Main Grid / Mobile view */}
      <div style={{ flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        {!loading && entries.length > 0 && (
          <div style={{ flex: 1, padding: 'var(--sp-4)', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
            {isMobile && (
              <div className="tabs" style={{ background: 'transparent', padding: 0, marginBottom: 'var(--sp-3)', overflowX: 'auto', gap: 8 }}>
                {['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'].map(d => (
                  <button key={d} className={`tab ${activeDay === d ? 'active' : ''}`} style={{ flexShrink: 0, background: activeDay === d ? 'var(--clr-primary)' : 'var(--clr-bg-3)', color: activeDay === d ? '#fff' : 'inherit' }} onClick={() => setActiveDay(d)}>{d}</button>
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
      </div>

      {/* AI Chat Drawer */}
      {chatOpen && (
        <div style={{ position: 'fixed', bottom: 0, right: 0, width: isMobile ? '100%' : 400, height: isMobile ? '70vh' : '500px', background: 'var(--clr-bg-2)', borderTop: '1px solid var(--clr-border)', borderLeft: '1px solid var(--clr-border)', borderTopLeftRadius: isMobile ? 24 : 16, boxShadow: 'var(--shadow)', display: 'flex', flexDirection: 'column', zIndex: 100 }}>
          <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--clr-border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 700, color: 'var(--clr-primary)' }}>
              <Bot size={16} /> Timetable Assistant
            </div>
            <button className="btn-icon" onClick={() => setChatOpen(false)}>✕</button>
          </div>
          <div style={{ flex: 1, overflowY: 'auto', padding: 'var(--sp-3)', display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)' }}>
            {messages.map((m, i) => (
              <div key={i} className={`chat-msg ${m.role}`} style={{ maxWidth: '90%' }}>
                <div style={{ wordBreak: 'break-word', whiteSpace: 'normal', fontSize: '13px' }}>
                  {m.role === 'assistant' ? (
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
                  ) : (
                    m.content
                  )}
                </div>
              </div>
            ))}
            <div ref={chatEndRef} />
          </div>
          <div style={{ padding: '10px var(--sp-3)', borderTop: '1px solid var(--clr-border)' }}>
            <div className="chat-suggestions" style={{ marginBottom: 8, gap: 4 }}>
              <button className="chat-suggestion" style={{ padding: '2px 8px', fontSize: 10 }} onClick={() => setInput('Where is DBMS?')}>Where is DBMS?</button>
              <button className="chat-suggestion" style={{ padding: '2px 8px', fontSize: 10 }} onClick={() => setInput('What does B.Tech 5th have today?')}>What does B.Tech 5th have today?</button>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <input
                className="form-control"
                placeholder="Ask a question..."
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && sendChat()}
                disabled={chatLoading}
              />
              <button className="btn btn-primary" onClick={sendChat} disabled={!input.trim() || chatLoading} style={{ padding: '0 12px' }}>
                {chatLoading ? <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} /> : <Send size={14} />}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
