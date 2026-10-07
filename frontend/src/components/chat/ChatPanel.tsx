import { useState, useRef, useEffect, useCallback } from 'react'
import { Send, X, Trash2, Bot, Loader2, CheckCircle2, AlertCircle, ChevronDown, ChevronUp, Lightbulb, ChevronRight, RefreshCw } from 'lucide-react'
import type { ChatMessage, ParsedActionItem, ActionExecuteResponse, ActionResult } from '../../types'
import { actionChat, executeActions, req } from '../../api'
import { useWorkspaceStore } from '../../store'
import { getVersion } from '../../api'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

// Utility: stream-reveal words in a freshly-mounted message element
function streamRevealWords(el: HTMLElement, gapMs = 40) {
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
  const textNodes: Text[] = []
  let node: Node | null
  while ((node = walker.nextNode())) textNodes.push(node as Text)

  const spans: HTMLElement[] = []
  textNodes.forEach(tn => {
    const words = tn.textContent?.split(/(\s+)/) ?? []
    const frag = document.createDocumentFragment()
    words.forEach(w => {
      if (!w) return
      const sp = document.createElement('span')
      sp.className = 't-stream-w'
      sp.textContent = w
      frag.appendChild(sp)
      spans.push(sp)
    })
    tn.replaceWith(frag)
  })
  spans.forEach((sp, i) => setTimeout(() => sp.classList.add('is-in'), i * gapMs))
}

function ReasoningStream() {
  const scrollRef = useRef<HTMLDivElement>(null)
  
  useEffect(() => {
    const interval = setInterval(() => {
      if (!scrollRef.current) return
      const el = scrollRef.current
      const offset = (parseFloat(el.dataset.offset || '0') + 16) % 32 // fake line height
      el.dataset.offset = String(offset)
      el.style.transition = 'transform var(--reason-step, 500ms) var(--reason-ease, ease)'
      el.style.transform = `translateY(-${offset}px)`
    }, 840) // --reason-hold
    return () => clearInterval(interval)
  }, [])
  
  return (
    <div className="t-reason" style={{ height: 40, width: 200, fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)' }}>
      <div className="t-reason-viewport">
        <div className="t-reason-scroll" ref={scrollRef}>
          <div className="t-reason-text">
            <div>Processing timetable constraints...</div>
            <div>Evaluating overlaps...</div>
            <div>Finding alternative slots...</div>
            <div>Processing timetable constraints...</div>
          </div>
        </div>
      </div>
    </div>
  )
}

const SUGGESTIONS = [
  'Move DBMS lab to Thursday',
  "Cancel SK's Tuesday CN class",
  'Swap Monday and Wednesday classes',
  'Optimize this timetable',
  'What conflicts are there?',
]

function genId() { return Math.random().toString(36).slice(2) }

interface ConfirmState {
  msgId: string
  actions: ParsedActionItem[]
  interpretation: string
}

// ── Alternative card shown when an action fails with a suggested fix ──────────
function AlternativeCard({
  failedResult,
  versionId,
  onApplied,
  onDismiss,
}: {
  failedResult: ActionResult
  versionId: number | null | undefined
  onApplied: (res: ActionExecuteResponse) => void
  onDismiss: () => void
}) {
  const [loading, setLoading] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [selectedIndex, setSelectedIndex] = useState(0)
  
  // Advanced State
  const [advancedLoading, setAdvancedLoading] = useState(false)
  const [advancedResult, setAdvancedResult] = useState<any>(null)
  const [advSelectedIndex, setAdvSelectedIndex] = useState(0)

  const s = failedResult.suggestions
  const rich = s?.rich_suggestions ?? []

  async function runAdvancedSchedule() {
    setAdvancedLoading(true)
    try {
      const entryDetail: any = failedResult.after || failedResult.before || {}
      const violA: any = failedResult.violated_constraint?.a || {}
      
      const subject = entryDetail.subject_code || violA.subject_code || ''
      const prog = entryDetail.program || violA.program || ''
      const sem = entryDetail.semester || violA.semester || ''
      const teach = entryDetail.teacher || violA.teacher || ''
      const type = entryDetail.type || entryDetail.entry_type || violA.type || 'Theory'
      const room = entryDetail.room || violA.room
      
      const payload = {
        program: prog,
        semester: sem,
        subject_code: subject,
        subject_name: entryDetail.subject_name || violA.subject_name || subject,
        teacher: teach,
        entry_type: type,
        room: room || undefined,
        preferred_day: entryDetail.day || violA.day || undefined
      }
      const data = await req<any>('/actions/smart-schedule/advanced', {
        method: 'POST', body: JSON.stringify(payload)
      })
      setAdvancedResult(data)
    } finally {
      setAdvancedLoading(false)
    }
  }

  async function applySelectedAdvanced() {
    const selected = advancedResult?.proposals?.[advSelectedIndex]
    if (!selected?.actions_to_apply) return
    setLoading(true)
    try {
      const res = await executeActions(selected.actions_to_apply, versionId, false)
      onApplied(res)
    } finally {
      setLoading(false)
    }
  }

  async function applySelected() {
    const selected = rich[selectedIndex]
    if (!selected?.action) return
    setLoading(true)
    try {
      const res = await executeActions([selected.action], versionId)
      onApplied(res)
    } finally {
      setLoading(false)
    }
  }

  async function applySlot(slot: { day: string; start: string; end: string; room?: string }) {
    if (!rich[0]?.action) return
    const action = {
      ...rich[0].action,
      new_day: slot.day,
      new_start_time: slot.start,
      new_end_time: slot.end,
    }
    if (slot.room) (action as any).new_room = slot.room
    setLoading(true)
    try {
      const res = await executeActions([action], versionId)
      onApplied(res)
    } finally {
      setLoading(false)
    }
  }

  async function applyRoom(room: string) {
    if (!rich[0]?.action) return
    const action = { ...rich[0].action, new_room: room }
    setLoading(true)
    try {
      const res = await executeActions([action], versionId)
      onApplied(res)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      alignSelf: 'flex-start',
      background: 'var(--card-bg)',
      border: '1px solid var(--line)',
      borderRadius: 'var(--radius-lg)',
      padding: '12px 14px',
      maxWidth: '92%',
      display: 'flex',
      flexDirection: 'column',
      gap: 10,
    }}>
      {/* Error reason */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
        <AlertCircle size={14} style={{ color: 'var(--violation)', flexShrink: 0, marginTop: 2 }} />
        <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--violation)', fontWeight: 600 }}>
          {failedResult.error || 'Action could not be applied due to a constraint conflict.'}
        </span>
      </div>

      {/* Suggested fix */}
      {rich.length > 0 && !advancedResult && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 4 }}>
          {rich.map((rs: any, i: number) => (
            <label key={i} style={{
              background: selectedIndex === i ? 'var(--accent-soft)' : 'color-mix(in oklab, var(--accent) 6%, transparent)',
              border: `1px solid ${selectedIndex === i ? 'var(--accent)' : 'color-mix(in oklab, var(--accent) 20%, transparent)'}`,
              borderRadius: 'var(--radius)',
              padding: '10px 12px',
              display: 'flex',
              gap: 8,
              alignItems: 'flex-start',
              cursor: 'pointer'
            }}>
              <input type="radio" name="rich-suggestion" checked={selectedIndex === i} onChange={() => setSelectedIndex(i)} style={{ marginTop: 2 }} />
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--ink)', fontWeight: 600 }}>
                  Suggestion {i + 1}: {rs.title}
                </span>
                <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)' }}>
                  — {rs.description}
                </span>
                <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--accent)', fontWeight: 600 }}>
                  {rs.status || 'Conflict-free and validated'}
                </span>
              </div>
            </label>
          ))}
        </div>
      )}

      {/* Action buttons */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 6, alignItems: 'center' }}>
        {rich.length > 0 && !advancedResult && (
          <button
            className="btn btn-primary btn-sm"
            disabled={loading}
            onClick={applySelected}
            style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 'var(--fs-xs)' }}
          >
            {loading ? <Loader2 size={11} style={{ animation: 'spin 0.7s linear infinite' }} /> : <RefreshCw size={11} />}
            Apply Suggestions
          </button>
        )}
        
        {!advancedResult && ((s?.free_rooms?.length ?? 0) > 0 || (s?.free_slots?.length ?? 0) > 0) ? (
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => setExpanded(e => !e)}
            style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 'var(--fs-xs)' }}
          >
            Find Other Suggestions
            <ChevronRight size={11} style={{ transform: expanded ? 'rotate(90deg)' : undefined, transition: 'transform 0.15s' }} />
          </button>
        ) : (
          !advancedResult && <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)', fontWeight: 600, padding: '4px 8px' }}>
            Nothing available.
          </span>
        )}

        {!advancedResult && (
          <button 
            className="btn btn-ghost btn-sm" 
            disabled={advancedLoading}
            onClick={runAdvancedSchedule} 
            style={{ display: 'flex', alignItems: 'center', gap: 4, color: 'var(--clr-primary)', fontSize: 'var(--fs-xs)' }}
          >
            {advancedLoading ? <Loader2 size={11} style={{ animation: 'spin 0.7s linear infinite' }} /> : <Lightbulb size={11} />}
            Try Advanced Smart Schedule
          </button>
        )}

        <button className="btn btn-ghost btn-sm" onClick={onDismiss} style={{ fontSize: 'var(--fs-xs)' }}>
          Cancel
        </button>
      </div>
      {/* Expanded alternatives list */}
      {expanded && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 2 }}>
          {s?.free_rooms?.map((room: string) => (
            <button
              key={room}
              className="badge badge-blue"
              disabled={loading}
              onClick={() => applyRoom(room)}
              style={{ cursor: 'pointer', fontSize: 'var(--fs-xs)', padding: '4px 8px' }}
            >
              {room}
            </button>
          ))}
          {s?.free_slots?.map((slot: any, i: number) => (
            <button
              key={i}
              className="badge badge-blue"
              disabled={loading}
              onClick={() => applySlot(slot)}
              style={{ cursor: 'pointer', fontSize: 'var(--fs-xs)', padding: '4px 8px' }}
            >
              {slot.day} {slot.start}–{slot.end}
            </button>
          ))}
        </div>
      )}
      {/* Advanced Result State */}
      {advancedResult && advancedResult.proposals?.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 4 }}>
          <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--clr-primary)', fontWeight: 600 }}>Advanced Mode Options Found ({advancedResult.proposals.length})</div>
          {advancedResult.proposals.map((prop: any, i: number) => (
            <label key={i} style={{
              background: advSelectedIndex === i ? 'var(--accent-soft)' : 'color-mix(in oklab, var(--accent) 6%, transparent)',
              border: `1px solid ${advSelectedIndex === i ? 'var(--accent)' : 'color-mix(in oklab, var(--accent) 20%, transparent)'}`,
              borderRadius: 'var(--radius)',
              padding: '10px 12px',
              display: 'flex',
              gap: 8,
              alignItems: 'flex-start',
              cursor: 'pointer'
            }}>
              <input type="radio" name="adv-suggestion" checked={advSelectedIndex === i} onChange={() => setAdvSelectedIndex(i)} style={{ marginTop: 2 }} />
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--ink)', fontWeight: 600 }}>
                  Option {i + 1}
                </span>
                {prop.moves.map((mv: any, j: number) => {
                   if (mv.action === 'ADD_CLASS') return <span key={j} style={{ fontSize: 'var(--fs-xs)' }}>Add constraint-free timeslot {mv.spec.day} {mv.spec.start_time}–{mv.spec.end_time}</span>;
                   if (mv.action === 'MOVE_CLASS') return <span key={j} style={{ fontSize: 'var(--fs-xs)' }}>Move {mv.target?.subject_code} to {mv.new_day} {mv.new_start_time}</span>;
                   return <span key={j} style={{ fontSize: 'var(--fs-xs)' }}>{mv.action}</span>;
                })}
              </div>
            </label>
          ))}
          <div style={{ display: 'flex', gap: 6, marginTop: 4 }}>
            <button className="btn btn-primary btn-sm" disabled={loading} onClick={applySelectedAdvanced}>
              {loading ? <Loader2 size={11} style={{ animation: 'spin 0.7s linear infinite' }} /> : 'Apply Selected'}
            </button>
          </div>
        </div>
      )}
      {advancedResult && (!advancedResult.proposals || advancedResult.proposals.length === 0) && (
        <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--red)', fontWeight: 600, padding: '4px 8px' }}>
          No Advanced Options available either.
        </span>
      )}
    </div>
  )
}

// ── Main ChatPanel ────────────────────────────────────────────────────────────

export default function ChatPanel() {
  const [messages, setMessages] = useState<ChatMessage[]>([
    { id: 'welcome', role: 'system', content: "Ask or change anything about your timetable. I'll parse your command and show you a preview before applying changes.", timestamp: new Date() }
  ])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)
  const [collapsed, setCollapsed] = useState(false)
  // Pending alternatives to show as inline cards (not persisted in messages)
  const [alternatives, setAlternatives] = useState<{ result: ActionResult; versionId: number | null }[]>([])
  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const { currentVersionId, setCurrentVersion, pendingChatQuery, pendingDeterminateActions, setPendingChatQuery, setPendingDeterminateActions } = useWorkspaceStore()

  useEffect(() => {
    if (pendingDeterminateActions) {
      const actions = pendingDeterminateActions
      setPendingDeterminateActions(null)
      setConfirm({
        msgId: genId(),
        actions: actions,
        interpretation: `I've prepared ${actions.length} action(s). Review below and confirm to apply.`,
      })
    }
  }, [pendingDeterminateActions, setPendingDeterminateActions])

  useEffect(() => {
    if (pendingChatQuery) {
      const q = pendingChatQuery
      setPendingChatQuery(null)
      send(q)
    }
  }, [pendingChatQuery, setPendingChatQuery])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, alternatives])

  const addMsg = useCallback((msg: Omit<ChatMessage, 'id' | 'timestamp'>) => {
    setMessages(prev => [...prev, { ...msg, id: genId(), timestamp: new Date() }])
  }, [])

  async function send(queryOverride?: string) {
    const text = (queryOverride || input).trim()
    if (!text || loading) return
    if (!queryOverride) setInput('')
    setAlternatives([])

    const history = messages
      .filter(m => m.role === 'user' || m.role === 'assistant')
      .slice(-6)
      .map(m => ({ role: m.role, content: m.content }));

    addMsg({ role: 'user', content: text })
    setLoading(true)
    try {
      const resp = await actionChat(text, currentVersionId, false, history)
      addMsg({
        role: 'assistant',
        content: resp.interpretation || `I parsed ${resp.action_count} action(s). Review below and confirm to apply.`,
        parsed_actions: resp.parsed_actions as ParsedActionItem[],
      })
      if (resp.parsed_actions.length > 0) {
        setConfirm({
          msgId: genId(),
          actions: resp.parsed_actions as ParsedActionItem[],
          interpretation: resp.interpretation,
        })
      }
    } catch (e: unknown) {
      addMsg({ role: 'assistant', content: `Sorry, I couldn't process that: ${(e as Error).message}`, error: (e as Error).message })
    } finally {
      setLoading(false)
    }
  }

  async function applyConfirmed(overrideActions?: ParsedActionItem[]) {
    const actionsToApply = overrideActions || confirm?.actions
    if (!actionsToApply) return
    if (confirm && !overrideActions) setConfirm(null)
    setAlternatives([])
    setLoading(true)
    addMsg({ role: 'system', content: 'Applying changes…' })
    try {
      const result: ActionExecuteResponse = await executeActions(actionsToApply, currentVersionId)
      if (result.new_version_id) {
        const v = await getVersion(result.new_version_id)
        setCurrentVersion(v.id, v.entries)
      }
      const failedActionResults = result.results.filter(r => !r.success)
      if (failedActionResults.length > 0) {
        const appliedCount = result.results.filter(r => r.success).length
        addMsg({
          role: 'assistant',
          content: `⚠ ${appliedCount} requested action(s) applied. ${failedActionResults.length} action(s) could not be applied.`,
          execution_result: result,
        })
        setAlternatives(failedActionResults.slice(0, 3).map(res => ({ result: res, versionId: result.new_version_id ?? currentVersionId })))
      } else if (result.success) {
        addMsg({ role: 'assistant', content: `✓ Changes applied successfully. New version: #${result.new_version_id}`, execution_result: result })
      } else {
        addMsg({ role: 'assistant', content: `⚠ Could not apply changes.`, execution_result: result })
      }
    } catch (e: unknown) {
      addMsg({ role: 'assistant', content: `Error applying changes: ${(e as Error).message}` })
    } finally {
      setLoading(false)
    }
  }

  async function handleAlternativeApplied(res: ActionExecuteResponse) {
    setAlternatives([])
    if (res.success) {
      addMsg({ role: 'assistant', content: `✓ Alternative applied. New version: #${res.new_version_id}`, execution_result: res })
      if (res.new_version_id) {
        const v = await getVersion(res.new_version_id)
        setCurrentVersion(v.id, v.entries)
      }
    } else {
      const failedResults = res.results.filter(r => !r.success)
      if (failedResults.length > 0) {
        // The server recalculates alternatives against the current timetable.
        // Keep the card open so the user can choose a valid fresh option.
        setAlternatives(failedResults.slice(0, 3).map(r => ({ result: r, versionId: currentVersionId })))
      } else {
        addMsg({ role: 'assistant', content: `⚠ Could not apply alternative.`, execution_result: res })
      }
    }
  }

  function onKey(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
  }

  function autoResize() {
    const ta = textareaRef.current
    if (ta) { ta.style.height = 'auto'; ta.style.height = `${Math.min(ta.scrollHeight, 140)}px` }
  }

  return (
    <div className="chat-panel" style={{ height: collapsed ? 44 : 'var(--chat-h)' }}>
      {/* Header */}
      <div className="chat-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ width: 22, height: 22, background: 'var(--accent-soft)', borderRadius: 4, display: 'grid', placeItems: 'center', flexShrink: 0 }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--accent)', fontWeight: 700, letterSpacing: '0.05em' }}>AI</span>
          </div>
          <span style={{ fontFamily: 'var(--font-display)', fontWeight: 600, fontSize: 'var(--fs-sm)', color: 'var(--ink)' }}>AI Assistant</span>
          {loading && <Loader2 size={14} style={{ color: 'var(--accent)', animation: 'spin 0.7s linear infinite' }} />}
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          <button className="btn-icon" title="Clear" onClick={() => { setMessages([]); setAlternatives([]) }}>
            <Trash2 size={14} />
          </button>
          <button className="btn-icon" title={collapsed ? 'Expand' : 'Collapse'} onClick={() => setCollapsed(c => !c)}>
            <span className="t-icon-swap" data-state={collapsed ? 'b' : 'a'}>
              <span className="t-icon" data-icon="a"><ChevronDown size={14} /></span>
              <span className="t-icon" data-icon="b"><ChevronUp size={14} /></span>
            </span>
          </button>
        </div>
      </div>

      {!collapsed && (
        <>
          {/* Messages */}
          <div className="chat-messages">
            {messages.map((msg, msgIdx) => (
              <div key={msg.id} className={`chat-msg ${msg.role}`}>
                <div
                  style={{ wordBreak: 'break-word', whiteSpace: 'normal' }}
                  ref={el => {
                    if (!el || el.dataset.streamed) return;
                    // Stream-reveal assistant words only on the latest message
                    if (msg.role === 'assistant' && msgIdx === messages.length - 1) {
                      el.dataset.streamed = 'true'
                      streamRevealWords(el, 40)
                    } else if (msg.role === 'assistant') {
                      el.dataset.streamed = 'true'
                      // older messages: make all words visible immediately
                      el.querySelectorAll<HTMLElement>('.t-stream-w').forEach(sp => sp.classList.add('is-in'))
                    }
                  }}
                >
                  {msg.role === 'assistant' ? (
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                  ) : (
                    msg.content
                  )}
                </div>
                {msg.parsed_actions && msg.parsed_actions.length > 0 && (
                  <div style={{ marginTop: 8 }}>
                    {msg.parsed_actions.map((a, i) => (
                      <div key={i} style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)', display: 'flex', alignItems: 'center', gap: 4, marginTop: 3 }}>
                        <span style={{ width: 18, height: 18, background: 'var(--accent-soft)', borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, fontWeight: 700, color: 'var(--accent)', flexShrink: 0 }}>{i + 1}</span>
                        <span style={{ fontWeight: 600, color: 'var(--ink)' }}>{a.action}</span>
                      </div>
                    ))}
                  </div>
                )}
                {msg.execution_result && (
                  <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                    {msg.execution_result.results.map((r, i) => (
                      <span key={i} className={`badge ${r.success ? 'badge-green' : 'badge-red'}`} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                        {r.success ? <CheckCircle2 size={10} /> : <AlertCircle size={10} />}
                        {r.action_type}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}

            {/* Confirm panel */}
            {confirm && alternatives.length === 0 && (
              <div style={{ alignSelf: 'flex-start', background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', padding: '12px 14px', maxWidth: '90%' }}>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 600, color: 'var(--amber)', marginBottom: 8, display: 'flex', alignItems: 'center', gap: 4, textTransform: 'uppercase', letterSpacing: '0.1em' }}>
                  <AlertCircle size={11} /> Preview — confirm to apply
                </div>
                <div className="confirm-actions-list" style={{ margin: 0, marginBottom: 10 }}>
                  {confirm.actions.map((a, i) => {
                    const targ = (a as any).target || {};
                    const spec = (a as any).spec || (a as any).new_spec || {};
                    const subj = targ.subject_name || targ.subject_code || spec.subject_name || spec.subject_code || '';
                    const day = (a as any).new_day || targ.day || spec.day || '';
                    const teacher = (a as any).new_teacher || targ.teacher || spec.teacher || '';
                    const details = [subj, day, teacher].filter(Boolean).join(', ');
                    return (
                      <div key={i} className="confirm-action-item" style={{ color: 'var(--ink)' }}>
                        <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, color: 'var(--accent)', fontSize: 'var(--fs-xs)' }}>{a.action}</span>
                        {details && <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)', marginLeft: 8 }}>—  {details}</span>}
                      </div>
                    )
                  })}
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button className="btn btn-success btn-sm" onClick={() => applyConfirmed()}>Apply Changes</button>
                  <button className="btn btn-ghost btn-sm" onClick={() => setConfirm(null)}>Cancel</button>
                </div>
              </div>
            )}

            {/* Alternative cards — shown after multiple constraint failures */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {alternatives.map((alt, idx) => (
                <AlternativeCard
                  key={idx}
                  failedResult={alt.result}
                  versionId={alt.versionId}
                  onApplied={handleAlternativeApplied}
                  onDismiss={() => setAlternatives(prev => prev.filter((_, i) => i !== idx))}
                />
              ))}
            </div>

            {/* Loading / Reasoning indicator */}
            {loading && !confirm && (
              <div className="chat-msg assistant" style={{ background: 'transparent', padding: '0 12px' }}>
                <ReasoningStream />
              </div>
            )}

            <div ref={bottomRef} />
          </div>

          {/* Input area */}
          <div className="chat-input-area">
            <div className="chat-suggestions">
              {SUGGESTIONS.map(s => (
                <button key={s} className="chat-suggestion" onClick={() => { setInput(s); textareaRef.current?.focus() }}>
                  {s}
                </button>
              ))}
            </div>
            <div className="chat-input-row">
              <textarea
                ref={textareaRef}
                className="chat-textarea"
                value={input}
                placeholder="Ask or change anything about your timetable…"
                rows={1}
                onChange={e => { setInput(e.target.value); autoResize() }}
                onKeyDown={onKey}
                disabled={loading}
              />
              {input && (
                <button className="btn-icon" onClick={() => setInput('')} title="Clear">
                  <X size={14} />
                </button>
              )}
              <button className="chat-send-btn" onClick={() => send()} disabled={!input.trim() || loading}>
                {loading ? <Loader2 size={14} style={{ animation: 'spin 0.7s linear infinite' }} /> : <Send size={14} />}
                Send
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
