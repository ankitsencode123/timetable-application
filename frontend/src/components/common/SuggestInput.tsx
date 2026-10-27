import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

/**
 * SuggestInput — a free-text input that shows matching suggestions below it
 * while the user types (or as soon as it is focused).
 *
 * - Typing is never blocked: any value can be entered, suggestions are hints.
 * - Optional availability markers (free / busy) per option. Free options are
 *   listed first; busy ones stay selectable (the admin may still "force").
 * - Keyboard: ↑ / ↓ to move, Enter / Tab to pick, Esc to close.
 * - Rendered through a portal with fixed positioning so it is never clipped by
 *   a scrollable modal, and flips upward when there is no room below.
 */

export type SuggestStatus = 'free' | 'busy'

export interface SuggestOption {
  value: string
  label?: string          // secondary text, e.g. full name / subject name
  status?: SuggestStatus
  reason?: string         // why it is busy, e.g. "B.Tech 3rd · CN 12:00–14:00"
}

interface Props {
  id?: string
  value: string
  onChange: (v: string) => void
  onPick?: (opt: SuggestOption) => void
  options: SuggestOption[]
  placeholder?: string
  /** Header shown above the list, e.g. "Availability 12:00–14:00". */
  header?: string
  /** Shown instead of markers when availability can't be computed yet. */
  hintWhenNoStatus?: string
  maxItems?: number
}

const MAX_H = 260

export default function SuggestInput({
  id, value, onChange, onPick, options, placeholder, header, hintWhenNoStatus, maxItems = 50,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const [typed, setTyped] = useState(false)   // filter only after the user types
  const [pos, setPos] = useState<{ left: number; top: number; width: number; up: boolean } | null>(null)

  const hasStatus = options.some(o => o.status)

  const filtered = useMemo(() => {
    const q = typed ? value.trim().toLowerCase() : ''
    const match = (o: SuggestOption) =>
      !q || o.value.toLowerCase().includes(q) || (o.label ?? '').toLowerCase().includes(q)
    const rank = (o: SuggestOption) => {
      const v = o.value.toLowerCase()
      const starts = q && (v.startsWith(q) || (o.label ?? '').toLowerCase().startsWith(q)) ? 0 : 1
      const busy = o.status === 'busy' ? 1 : 0
      return busy * 10 + starts
    }
    return options.filter(match).sort((a, b) => rank(a) - rank(b) || a.value.localeCompare(b.value)).slice(0, maxItems)
  }, [options, value, typed, maxItems])

  const freeCount = filtered.filter(o => o.status === 'free').length
  const busyCount = filtered.filter(o => o.status === 'busy').length

  function place() {
    const el = inputRef.current
    if (!el) return
    const r = el.getBoundingClientRect()
    const below = window.innerHeight - r.bottom
    const up = below < 200 && r.top > below
    setPos({ left: r.left, width: Math.max(r.width, 240), top: up ? r.top - 4 : r.bottom + 4, up })
  }

  useLayoutEffect(() => { if (open) place() }, [open, filtered.length])

  useEffect(() => {
    if (!open) return
    const onMove = () => place()
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (inputRef.current?.contains(t) || listRef.current?.contains(t)) return
      setOpen(false)
    }
    window.addEventListener('resize', onMove)
    window.addEventListener('scroll', onMove, true)
    document.addEventListener('mousedown', onDown)
    return () => {
      window.removeEventListener('resize', onMove)
      window.removeEventListener('scroll', onMove, true)
      document.removeEventListener('mousedown', onDown)
    }
  }, [open])

  useEffect(() => { setActive(0) }, [value, open])

  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-idx="${active}"]`)
    el?.scrollIntoView({ block: 'nearest' })
  }, [active])

  function pick(o: SuggestOption) {
    onChange(o.value)
    onPick?.(o)
    setOpen(false)
    setTyped(false)
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!open && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) { setOpen(true); return }
    if (!open || filtered.length === 0) return
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive(i => (i + 1) % filtered.length) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(i => (i - 1 + filtered.length) % filtered.length) }
    else if (e.key === 'Enter') { e.preventDefault(); pick(filtered[active]) }
    else if (e.key === 'Tab') { if (typed && value.trim()) pick(filtered[active]); else setOpen(false) }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); setOpen(false) }
  }

  const list = open && pos && (filtered.length > 0 || header) ? createPortal(
    <div
      ref={listRef}
      role="listbox"
      style={{
        position: 'fixed', left: pos.left, width: pos.width, zIndex: 2000,
        ...(pos.up ? { bottom: window.innerHeight - pos.top } : { top: pos.top }),
        maxHeight: MAX_H, overflowY: 'auto',
        background: 'var(--card-bg)', border: '1px solid var(--line)', borderRadius: 'var(--radius)',
        boxShadow: '0 10px 30px rgba(0,0,0,0.18)', padding: 4, fontSize: 'var(--fs-xs)',
      }}
    >
      {(header || hasStatus || hintWhenNoStatus) && (
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8,
          padding: '6px 8px', fontFamily: 'var(--font-mono)', fontSize: 9.5, letterSpacing: '0.08em',
          textTransform: 'uppercase', color: 'var(--ink-soft)', borderBottom: '1px solid var(--line)', marginBottom: 4,
        }}>
          <span>{header ?? 'Suggestions'}</span>
          {hasStatus ? (
            <span style={{ display: 'flex', gap: 8 }}>
              <span style={{ color: 'var(--accent)' }}>● {freeCount} free</span>
              <span style={{ color: 'var(--violation)' }}>● {busyCount} busy</span>
            </span>
          ) : hintWhenNoStatus ? <span style={{ textTransform: 'none', letterSpacing: 0 }}>{hintWhenNoStatus}</span> : null}
        </div>
      )}
      {filtered.length === 0 && (
        <div style={{ padding: '8px 10px', color: 'var(--ink-soft)', fontStyle: 'italic' }}>No matches — your typed value will be used.</div>
      )}
      {filtered.map((o, i) => {
        const busy = o.status === 'busy'
        return (
          <div
            key={o.value + i}
            data-idx={i}
            role="option"
            aria-selected={i === active}
            onMouseDown={e => { e.preventDefault(); pick(o) }}
            onMouseEnter={() => setActive(i)}
            style={{
              display: 'flex', alignItems: 'center', gap: 8, padding: '7px 8px', borderRadius: 6, cursor: 'pointer',
              background: i === active ? 'var(--paper)' : 'transparent',
              opacity: busy ? 0.75 : 1,
            }}
          >
            {o.status && (
              <span style={{
                width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                background: busy ? 'var(--violation)' : 'var(--accent)',
              }} />
            )}
            <span style={{ fontWeight: 600, color: 'var(--ink)', textDecoration: busy ? 'line-through' : 'none' }}>{o.value}</span>
            {o.label && <span style={{ color: 'var(--ink-soft)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{o.label}</span>}
            {o.status && (
              <span
                title={o.reason}
                style={{
                  marginLeft: 'auto', flexShrink: 0, fontFamily: 'var(--font-mono)', fontSize: 9, fontWeight: 600,
                  textTransform: 'uppercase', letterSpacing: '0.06em', padding: '1px 6px', borderRadius: 4,
                  color: busy ? 'var(--violation)' : 'var(--accent)',
                  background: busy ? 'var(--violation-soft)' : 'var(--accent-soft)',
                  maxWidth: 190, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}
              >
                {busy ? (o.reason ? `Busy · ${o.reason}` : 'Busy') : 'Free'}
              </span>
            )}
          </div>
        )
      })}
    </div>,
    document.body,
  ) : null

  return (
    <>
      <input
        ref={inputRef}
        id={id}
        className="input"
        autoComplete="off"
        spellCheck={false}
        placeholder={placeholder}
        value={value}
        onFocus={() => { setTyped(false); setOpen(true) }}
        onClick={() => setOpen(true)}
        onChange={e => { onChange(e.target.value); setTyped(true); setOpen(true) }}
        onKeyDown={onKeyDown}
        role="combobox"
        aria-expanded={open}
        aria-autocomplete="list"
      />
      {list}
    </>
  )
}
