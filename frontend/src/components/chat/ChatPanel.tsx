import { useState, useRef, useEffect, useCallback } from 'react'
import { Send, X, Trash2, Bot, Loader2, CheckCircle2, AlertCircle, ChevronDown, ChevronUp, Lightbulb, ChevronRight, RefreshCw } from 'lucide-react'
import type { ChatMessage, ParsedActionItem, ActionExecuteResponse, ActionResult } from '../../types'
import { actionChat, executeActions } from '../../api'
import { useWorkspaceStore } from '../../store'
import { getVersion } from '../../api'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

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
  const s = failedResult.suggestions
  const rich = s?.rich_suggestions ?? []

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
      {rich.length > 0 && (
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
        {rich.length > 0 && (
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
        
        {((s?.free_rooms?.length ?? 0) > 0 || (s?.free_slots?.length ?? 0) > 0) ? (
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => setExpanded(e => !e)}
            style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 'var(--fs-xs)' }}
          >
            Find Other Suggestions
            <ChevronRight size={11} style={{ transform: expanded ? 'rotate(90deg)' : undefined, transition: 'transform 0.15s' }} />
          </button>
        ) : (
          <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)', fontWeight: 600, padding: '4px 8px' }}>
            Nothing available.
          </span>
        )}

        <button className="btn btn-ghost btn-sm" onClick={onDismiss} style={{ fontSize: 'var(--fs-xs)' }}>
          Cancel
        </button>
      </div>

      {/* Expanded alternatives list */}
      {expanded && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 2 }}>
          {s?.free_rooms?.map(room => (
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
          {s?.free_slots?.map((slot, i) => (
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
  // Pending alternative to show as an inline card (not persisted in messages)
  const [alternative, setAlternative] = useState<{ result: ActionResult; versionId: number | null } | null>(null)
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
  }, [messages, alternative])

  const addMsg = useCallback((msg: Omit<ChatMessage, 'id' | 'timestamp'>) => {
    setMessages(prev => [...prev, { ...msg, id: genId(), timestamp: new Date() }])
  }, [])

  async function send(queryOverride?: string) {
    const text = (queryOverride || input).trim()
    if (!text || loading) return
    if (!queryOverride) setInput('')
    setAlternative(null)
    addMsg({ role: 'user', content: text })
    setLoading(true)
    try {
      const resp = await actionChat(text, currentVersionId, false)
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
    setAlternative(null)
    setLoading(true)
    addMsg({ role: 'system', content: 'Applying changes…' })
    try {
      const result: ActionExecuteResponse = await executeActions(actionsToApply, currentVersionId)
      if (result.new_version_id) {
        const v = await getVersion(result.new_version_id)
        setCurrentVersion(v.id, v.entries)
      }
      const failedActionResult = result.results.find(r => !r.success)
      if (failedActionResult) {
        const appliedCount = result.results.filter(r => r.success).length
        addMsg({
          role: 'assistant',
          content: `⚠ ${appliedCount} requested action(s) applied. ${result.results.filter(r => !r.success).length} action(s) could not be applied.`,
          execution_result: result,
        })
        setAlternative({ result: failedActionResult, versionId: result.new_version_id ?? currentVersionId })
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
    setAlternative(null)
    if (res.success) {
      addMsg({ role: 'assistant', content: `✓ Alternative applied. New version: #${res.new_version_id}`, execution_result: res })
      if (res.new_version_id) {
        const v = await getVersion(res.new_version_id)
        setCurrentVersion(v.id, v.entries)
      }
    } else {
      const failedResult = res.results.find(r => !r.success)
      if (failedResult) {
        // The server recalculates alternatives against the current timetable.
        // Keep the card open so the user can choose a valid fresh option.
        setAlternative({ result: failedResult, versionId: currentVersionId })
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
          <button className="btn-icon" title="Clear" onClick={() => { setMessages([]); setAlternative(null) }}>
            <Trash2 size={14} />
          </button>
          <button className="btn-icon" title={collapsed ? 'Expand' : 'Collapse'} onClick={() => setCollapsed(c => !c)}>
            {collapsed ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>
        </div>
      </div>

      {!collapsed && (
        <>
          {/* Messages */}
          <div className="chat-messages">
            {messages.map(msg => (
              <div key={msg.id} className={`chat-msg ${msg.role}`}>
                <div style={{ wordBreak: 'break-word', whiteSpace: 'normal' }}>
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
            {confirm && !alternative && (
              <div style={{ alignSelf: 'flex-start', background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', padding: '12px 14px', maxWidth: '90%' }}>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 600, color: 'var(--amber)', marginBottom: 8, display: 'flex', alignItems: 'center', gap: 4, textTransform: 'uppercase', letterSpacing: '0.1em' }}>
                  <AlertCircle size={11} /> Preview — confirm to apply
                </div>
                <div className="confirm-actions-list" style={{ margin: 0, marginBottom: 10 }}>
                  {confirm.actions.map((a, i) => (
                    <div key={i} className="confirm-action-item" style={{ color: 'var(--ink)' }}>
                      <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, color: 'var(--accent)', fontSize: 'var(--fs-xs)' }}>{a.action}</span>
                    </div>
                  ))}
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button className="btn btn-success btn-sm" onClick={() => applyConfirmed()}>Apply Changes</button>
                  <button className="btn btn-ghost btn-sm" onClick={() => setConfirm(null)}>Cancel</button>
                </div>
              </div>
            )}

            {/* Alternative card — shown after a constraint failure */}
            {alternative && (
              <AlternativeCard
                failedResult={alternative.result}
                versionId={alternative.versionId}
                onApplied={handleAlternativeApplied}
                onDismiss={() => setAlternative(null)}
              />
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
