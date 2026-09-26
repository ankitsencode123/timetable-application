import { useState, useEffect } from 'react'
import { X, RefreshCw, AlertTriangle, CheckCircle2, ChevronDown } from 'lucide-react'
import type { TimetableEntry } from '../../types'
import { executeActions } from '../../api'

interface Props {
  entry: TimetableEntry
  onClose: () => void
  onSuccess: () => void
}

async function fetchCurrentEntries(): Promise<TimetableEntry[]> {
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/)
  const csrf = match ? decodeURIComponent(match[1]) : null
  const res = await fetch('/api/versions', {
    headers: { ...(csrf ? { 'X-CSRF-Token': csrf } : {}) },
    credentials: 'include',
  })
  if (!res.ok) return []
  const versions: { id: number; status: string }[] = await res.json()
  const pub = versions.find(v => v.status === 'PUBLISHED') ?? versions[0]
  if (!pub) return []
  const dRes = await fetch(`/api/versions/${pub.id}`, {
    headers: { ...(csrf ? { 'X-CSRF-Token': csrf } : {}) },
    credentials: 'include',
  })
  if (!dRes.ok) return []
  const data: { entries?: TimetableEntry[] } = await dRes.json()
  return data.entries ?? []
}

export default function InterchangeModal({ entry, onClose, onSuccess }: Props) {
  const [allEntries, setAllEntries] = useState<TimetableEntry[]>([])
  const [selectedKey, setSelectedKey] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const [fetching, setFetching] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [violations, setViolations] = useState<string[]>([])
  interface SuggestionType { title: string; description: string; action: unknown }
  const [suggestions, setSuggestions] = useState<SuggestionType[]>([])
  const [selectedSuggestionIdx, setSelectedSuggestionIdx] = useState<number>(-1)

  useEffect(() => {
    fetchCurrentEntries().then(entries => {
      setAllEntries(entries)
      setFetching(false)
    })
  }, [])

  // Eligible: every class except the current one
  const eligible = allEntries.filter(
    e => !(e.day === entry.day && e.start === entry.start &&
      e.program === entry.program && e.semester === entry.semester &&
      e.subject_code === entry.subject_code)
  )

  const makeKey = (e: TimetableEntry) =>
    `${e.day}|${e.start}|${e.end}|${e.program}|${e.semester}|${e.subject_code}|${e.teacher}`

  const selectedEntry = eligible.find(e => makeKey(e) === selectedKey) ?? null

  async function handleInterchange() {
    if (!selectedEntry) { setError('Please select a class to interchange with.'); return }
    setLoading(true)
    setError(null)
    setViolations([])

    try {
      let actionsToExecute: unknown[] = []

      if (selectedSuggestionIdx >= 0 && suggestions[selectedSuggestionIdx]) {
      // Apply chosen suggestion instead of trying interchange again
      actionsToExecute = [suggestions[selectedSuggestionIdx].action]
    } else {
      // Attempt original interchange action
      actionsToExecute = [{
        action: 'INTERCHANGE_CLASSES',
        target_a: {
          day: entry.day,
          start_time: entry.start,
          end_time: entry.end,
          program: entry.program,
          semester: entry.semester,
          subject_code: entry.subject_code,
          teacher: entry.teacher,
        },
        target_b: {
          day: selectedEntry.day,
          start_time: selectedEntry.start,
          end_time: selectedEntry.end,
          program: selectedEntry.program,
          semester: selectedEntry.semester,
          subject_code: selectedEntry.subject_code,
          teacher: selectedEntry.teacher,
        },
      }]
    }

    const res = await executeActions(actionsToExecute)

      if (res.success) {
        onSuccess()
        onClose()
      } else {
        const r = res.results?.[0]
        setError(r?.error ?? 'Action failed. Constraint violations detected.')
        if (r?.violated_constraint) {
          const v = r.violated_constraint as unknown as Record<string, string>
          setViolations([
            `Violation: ${v['rule'] ?? 'Unknown constraint'}`,
            v['message'] ? `— ${v['message']}` : '',
          ].filter(Boolean))
        }
        if (r?.suggestions?.rich_suggestions?.length) {
          setSuggestions(
            r.suggestions.rich_suggestions.map((s: { title: string; description: string, action: unknown }) => ({
              title: s.title,
              description: s.description,
              action: s.action
            }))
          )
          setSelectedSuggestionIdx(-1)
        }
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'An error occurred.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal modal-sm" style={{ background: 'var(--clr-bg-2)', maxWidth: 520 }}>

        {/* Header */}
        <div className="modal-header">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{
                background: 'linear-gradient(135deg, #f59e0b, #d97706)',
                color: '#fff', borderRadius: 6, padding: '2px 10px',
                fontSize: 'var(--fs-xs)', fontWeight: 700, letterSpacing: '0.05em',
                display: 'flex', alignItems: 'center', gap: 4
              }}>
                <RefreshCw size={11} /> INTERCHANGE
              </span>
              <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--clr-text-3)' }}>
                {entry.program} · {entry.semester}
              </span>
            </div>
            <h2 style={{ fontSize: 'var(--fs-lg)', fontWeight: 700, marginTop: 4 }}>
              {entry.subject_name}
            </h2>
            <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--clr-text-3)', display: 'flex', gap: 8 }}>
              <span><strong style={{ color: 'var(--clr-text-2)' }}>{entry.day}</strong></span>
              <span>·</span>
              <span><strong style={{ color: 'var(--clr-text-2)' }}>{entry.start}–{entry.end}</strong></span>
              <span>·</span>
              <span>{entry.teacher}</span>
            </div>
          </div>
          <button className="btn-icon" onClick={onClose}><X size={18} /></button>
        </div>

        <div style={{ padding: '16px 0 8px' }}>
          <div style={{
            fontSize: 'var(--fs-xs)', color: 'var(--clr-text-3)',
            marginBottom: 12, padding: '8px 12px',
            background: 'rgba(245,158,11,0.08)', borderRadius: 8,
            border: '1px solid rgba(245,158,11,0.2)',
          }}>
            ↔ Only the <strong>Time</strong> and <strong>Room</strong> will be swapped between the two classes. Their Subjects and Teachers will stay as is. Constraints will be validated before applying.
          </div>

          {/* Class selector */}
          <label style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, display: 'block', marginBottom: 6 }}>
            Select class to interchange with:
          </label>
          {fetching ? (
            <div style={{ color: 'var(--clr-text-3)', fontSize: 'var(--fs-sm)' }}>Loading classes…</div>
          ) : (
            <div style={{ position: 'relative' }}>
              <select
                value={selectedKey}
                onChange={e => setSelectedKey(e.target.value)}
                className="form-control"
                style={{ paddingRight: 32, appearance: 'none', WebkitAppearance: 'none' }}
              >
                <option value="">— Choose a class —</option>
                {eligible.map(e => (
                  <option key={makeKey(e)} value={makeKey(e)}>
                    {e.day} {e.start}–{e.end} | {e.subject_code.toUpperCase()} | {e.program} {e.semester} | {e.teacher}
                  </option>
                ))}
              </select>
              <ChevronDown size={14} style={{ position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--clr-text-3)', pointerEvents: 'none' }} />
            </div>
          )}

          {/* Preview of selected */}
          {selectedEntry && (
            <div style={{
              marginTop: 10, padding: '10px 14px', borderRadius: 8,
              background: 'var(--clr-bg-3)', border: '1px solid var(--clr-border)',
              display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px 16px',
              fontSize: 'var(--fs-xs)', color: 'var(--clr-text-2)'
            }}>
              <span><strong>Subject:</strong> {selectedEntry.subject_name}</span>
              <span><strong>Teacher:</strong> {selectedEntry.teacher}</span>
              <span><strong>Day:</strong> {selectedEntry.day}</span>
              <span><strong>Time:</strong> {selectedEntry.start}–{selectedEntry.end}</span>
              <span><strong>Room:</strong> {selectedEntry.room}</span>
              <span><strong>Program:</strong> {selectedEntry.program} {selectedEntry.semester}</span>
            </div>
          )}
        </div>

        {/* Error & violations */}
        {error && (
          <div style={{
            marginTop: 8, padding: '10px 14px', borderRadius: 8,
            background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.25)',
            color: '#f87171', fontSize: 'var(--fs-xs)',
          }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
              <AlertTriangle size={14} style={{ marginTop: 2, flexShrink: 0 }} />
              <div>
                <div style={{ fontWeight: 700, marginBottom: 2 }}>{error}</div>
                {violations.map((v, i) => <div key={i}>{v}</div>)}
              </div>
            </div>
          </div>
        )}

        {/* Alternative suggestions */}
        {suggestions.length > 0 && (
          <div style={{ marginTop: 10 }}>
            <div style={{ fontSize: 'var(--fs-xs)', fontWeight: 700, color: 'var(--clr-text-2)', marginBottom: 6 }}>
              Validated Alternatives (Select to apply):
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {suggestions.map((s, i) => (
                <label key={i} style={{
                  display: 'flex', gap: 10, alignItems: 'flex-start',
                  padding: '8px 12px', borderRadius: 8,
                  background: selectedSuggestionIdx === i ? 'rgba(16,185,129,0.1)' : 'var(--clr-bg-3)',
                  border: `1px solid ${selectedSuggestionIdx === i ? '#10b981' : 'var(--clr-border)'}`,
                  cursor: 'pointer'
                }}>
                  <input
                    type="radio"
                    name="interchange_suggestion"
                    checked={selectedSuggestionIdx === i}
                    onChange={() => setSelectedSuggestionIdx(i)}
                    style={{ marginTop: 3 }}
                  />
                  <div style={{ fontSize: 'var(--fs-xs)' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 700, color: '#10b981' }}>
                      <CheckCircle2 size={12} /> {s.title}
                    </div>
                    {s.description && (
                      <div style={{ color: 'var(--clr-text-3)', marginTop: 2 }}>{s.description}</div>
                    )}
                  </div>
                </label>
              ))}
            </div>
          </div>
        )}

        {/* Footer */}
        <div className="modal-footer" style={{ justifyContent: 'flex-end', marginTop: 'var(--sp-5)', gap: 'var(--sp-2)' }}>
          <button className="btn btn-ghost btn-sm" onClick={onClose} disabled={loading}>
            Cancel
          </button>
          <button
            className="btn btn-primary btn-sm"
            onClick={handleInterchange}
            disabled={loading || !selectedEntry}
            style={{
              background: selectedSuggestionIdx >= 0
                ? 'linear-gradient(135deg, #10b981, #059669)'
                : 'linear-gradient(135deg, #f59e0b, #d97706)',
              border: 'none', display: 'flex', alignItems: 'center', gap: 6,
              opacity: !selectedEntry ? 0.6 : 1
            }}
          >
            {loading ? (
              <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{
                  width: 12, height: 12, border: '2px solid rgba(255,255,255,0.3)',
                  borderTopColor: '#fff', borderRadius: '50%',
                  animation: 'spin 0.7s linear infinite', display: 'inline-block'
                }} />
                {selectedSuggestionIdx >= 0 ? 'Applying…' : 'Interchanging…'}
              </span>
            ) : (
              selectedSuggestionIdx >= 0
                ? <><CheckCircle2 size={14} /> Apply Selected</>
                : <><RefreshCw size={14} /> Confirm Interchange</>
            )}
          </button>
        </div>
      </div>
    </div>
  )
}
