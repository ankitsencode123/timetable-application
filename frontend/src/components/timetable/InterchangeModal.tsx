import { useState, useEffect } from 'react'
import { X, RefreshCw, AlertTriangle, CheckCircle2, ChevronDown } from 'lucide-react'
import type { TimetableEntry } from '../../types'
import { executeActions, getCurrentDraft, getVersion, listVersions } from '../../api'
import { useWorkspaceStore } from '../../store'

interface Props {
  entry: TimetableEntry
  onClose: () => void
  onSuccess: () => void
}

async function fetchCurrentEntries(): Promise<TimetableEntry[]> {
  const storeEntries = useWorkspaceStore.getState().entries
  if (storeEntries && storeEntries.length > 0) {
    return storeEntries
  }
  try {
    const draft = await getCurrentDraft().catch(() => null)
    if (draft && draft.id) {
      const detail = await getVersion(draft.id)
      return detail.entries || []
    }
    const versions = await listVersions().catch(() => [])
    const pub = versions.find(v => v.status === 'PUBLISHED') ?? versions[0]
    if (pub) {
      const detail = await getVersion(pub.id)
      return detail.entries || []
    }
  } catch (err) {
    console.error('Failed to fetch entries for interchange modal', err)
  }
  return []
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
    let active = true
    setFetching(true)
    fetchCurrentEntries()
      .then(entries => {
        if (active) setAllEntries(entries)
      })
      .catch(err => {
        console.error('Error loading interchange classes', err)
      })
      .finally(() => {
        if (active) setFetching(false)
      })
    return () => { active = false }
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
      <div className="modal modal-sm" style={{ background: 'var(--clr-bg-2)', maxWidth: 540, maxHeight: 'min(90vh, calc(100vh - 32px))', display: 'flex', flexDirection: 'column', overflowY: 'auto' }}>

        {/* Header */}
        <div className="modal-header" style={{ flexShrink: 0 }}>
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
          ) : eligible.length === 0 ? (
            <div style={{ color: 'var(--clr-text-3)', fontSize: 'var(--fs-sm)' }}>No eligible classes available to interchange with.</div>
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
            marginTop: 'var(--sp-3)', padding: '12px 14px', borderRadius: 8,
            background: 'var(--violation-soft)', border: '1px solid color-mix(in oklab, var(--violation) 25%, transparent)',
            color: 'var(--violation)', fontSize: 'var(--fs-xs)', flexShrink: 0, lineHeight: 1.45
          }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
              <AlertTriangle size={16} style={{ marginTop: 2, flexShrink: 0 }} />
              <div style={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere' }}>
                <div style={{ fontWeight: 700, marginBottom: violations.length > 0 ? 4 : 0 }}>{error}</div>
                {violations.map((v, i) => (
                  <div key={i} style={{ marginTop: 2, opacity: 0.9 }}>• {v}</div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* Suggestions */}
        {suggestions.length > 0 && (
          <div className="conflict-suggestions-box" style={{ marginTop: 'var(--sp-4)' }}>
            <div className="conflict-suggestions-header">
              <div className="conflict-suggestions-title">
                <CheckCircle2 size={13} style={{ color: '#10b981' }} />
                <span>Conflict-Free Alternatives</span>
              </div>
              <span className="conflict-suggestions-badge" style={{ background: 'rgba(16, 185, 129, 0.1)', color: '#10b981', borderColor: 'rgba(16, 185, 129, 0.25)' }}>
                {suggestions.length} {suggestions.length === 1 ? 'match available' : 'matches available'}
              </span>
            </div>
            <div className="conflict-suggestions-subtitle">
              Select a validated swap or alternative schedule to resolve conflicts:
            </div>
            <div className="conflict-suggestions-list">
              {suggestions.map((s, i) => {
                const isSelected = selectedSuggestionIdx === i
                return (
                  <label
                    key={i}
                    className="suggestion-card"
                    style={{
                      cursor: 'pointer',
                      border: isSelected ? '1.5px solid #10b981' : '1.5px solid var(--line)',
                      background: isSelected ? 'rgba(16, 185, 129, 0.06)' : 'var(--paper)',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, width: '100%' }}>
                      <input
                        type="radio"
                        name="interchange-suggestion"
                        checked={isSelected}
                        onChange={() => setSelectedSuggestionIdx(i)}
                        style={{ marginTop: 3, accentColor: '#10b981' }}
                      />
                      <div className="suggestion-card-content">
                        <div className="suggestion-slot-title" style={{ color: isSelected ? '#047857' : 'var(--ink)' }}>
                          <CheckCircle2 size={13} style={{ color: '#10b981', flexShrink: 0 }} />
                          <span>{s.title}</span>
                        </div>
                        {s.description && (
                          <div className="suggestion-desc">
                            {s.description}
                          </div>
                        )}
                      </div>
                    </div>
                  </label>
                )
              })}
            </div>
          </div>
        )}

        {/* Footer */}
        <div className="modal-footer" style={{ justifyContent: 'flex-end', marginTop: 'var(--sp-5)', gap: 'var(--sp-2)', flexShrink: 0 }}>
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
