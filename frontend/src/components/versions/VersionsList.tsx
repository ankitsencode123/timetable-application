import { useState, useEffect } from 'react'
import { Eye, RotateCcw, CheckCircle2, Clock, Loader2, Rocket } from 'lucide-react'
import type { TimetableVersion } from '../../types'
import { listVersions, getVersion, publishVersion } from '../../api'
import { useWorkspaceStore } from '../../store'

function statusBadge(status: string) {
  const map: Record<string, string> = {
    PUBLISHED: 'badge-green', VALIDATED: 'badge-blue',
    DRAFT: 'badge-yellow', ARCHIVED: 'badge-gray',
  }
  return <span className={`badge ${map[status] ?? 'badge-gray'}`}>{status}</span>
}

function fmtDate(s: string) {
  return new Date(s).toLocaleString('en-IN', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function TiltCard({ children, className = '' }: { children: React.ReactNode, className?: string }) {
  const [tilting, setTilting] = useState(false)
  const [hover, setHover] = useState(false)
  
  function handleMove(e: React.PointerEvent<HTMLDivElement>) {
    const el = e.currentTarget
    const rect = el.getBoundingClientRect()
    const x = e.clientX - rect.left
    const y = e.clientY - rect.top
    const rx = ((y / rect.height) - 0.5) * -15 // -7.5 to 7.5 deg
    const ry = ((x / rect.width) - 0.5) * 15  // -7.5 to 7.5 deg
    const gx = (x / rect.width) * 100
    const gy = (y / rect.height) * 100
    
    el.style.setProperty('--tilt-rx', `${rx}deg`)
    el.style.setProperty('--tilt-ry', `${ry}deg`)
    el.style.setProperty('--tilt-gx', `${gx}%`)
    el.style.setProperty('--tilt-gy', `${gy}%`)
  }

  return (
    <div 
      className={`t-tilt ${hover ? 'is-hover' : ''}`}
      onPointerEnter={() => { setHover(true); setTilting(false) }}
      onPointerLeave={(e) => { 
        setHover(false); 
        setTilting(false);
        e.currentTarget.style.setProperty('--tilt-rx', '0deg')
        e.currentTarget.style.setProperty('--tilt-ry', '0deg')
      }}
      onPointerMove={(e) => { setTilting(true); handleMove(e) }}
    >
      <div className={`t-tilt-card ${tilting ? 'is-tilting' : ''} ${className}`}>
        {children}
        <div className="t-tilt-glare"></div>
      </div>
    </div>
  )
}

function PublishDialog({ version, onClose, onPublished }: {
  version: TimetableVersion; onClose: () => void; onPublished: () => void
}) {
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function doPublish() {
    setLoading(true); setErr(null)
    try { await publishVersion(version.id); onPublished(); onClose() }
    catch (e: unknown) { setErr((e as Error).message) }
    finally { setLoading(false) }
  }

  const hasViolations = (version.validation_result?.violation_count ?? 0) > 0
  const isValidated = version.status === 'VALIDATED' || version.status === 'PUBLISHED'

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal modal-sm t-modal is-open">
        <div className="modal-header">
          <h2 className="modal-title">Publish Version #{version.id}?</h2>
          <button className="btn-icon" onClick={onClose}>✕</button>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)' }}>
          <p style={{ fontSize: 'var(--fs-sm)', color: 'var(--ink-soft)' }}>
            After publishing, this timetable becomes visible to everyone in the public area.
          </p>
          <div style={{ background: 'var(--paper)', border: '1px solid var(--line)', borderRadius: 'var(--radius)', padding: 'var(--sp-3)', fontSize: 'var(--fs-sm)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {isValidated && !hasViolations
                ? (
                  <span className="t-success-check" data-state="in" aria-hidden="true" style={{ width: 14, height: 14, color: 'var(--accent)' }}>
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
                      <path d="M22 4L12 14.01l-3-3" />
                    </svg>
                  </span>
                )
                : <span style={{ fontSize: 12 }}>⚠</span>
              }
              <span style={{ color: isValidated && !hasViolations ? 'var(--accent)' : 'var(--amber)' }}>
                {isValidated
                  ? (!hasViolations ? 'All constraints satisfied' : `${version.validation_result?.violation_count} violation(s) detected`)
                  : 'Pending validation — please run Validate first'}
              </span>
            </div>
          </div>
          {hasViolations && (
            <div style={{ background: 'var(--violation-soft)', borderRadius: 'var(--radius)', padding: 'var(--sp-3)', fontSize: 'var(--fs-xs)', color: 'var(--violation)' }}>
              This version has constraint violations. Publishing is blocked unless overridden by an Admin. Please fix violations first.
            </div>
          )}
          {err && <div style={{ color: 'var(--violation)', fontSize: 'var(--fs-sm)' }}>{err}</div>}
        </div>
        <div className="modal-footer">
          <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn btn-success" disabled={loading} onClick={doPublish}>
            {loading ? <Loader2 size={14} style={{ animation: 'spin 0.7s linear infinite' }} /> : <Rocket size={14} />}
            Publish
          </button>
        </div>
      </div>
    </div>
  )
}

export default function VersionsList() {
  const [versions, setVersions] = useState<TimetableVersion[]>([])
  const [loading, setLoading] = useState(true)
  const [publishTarget, setPublishTarget] = useState<TimetableVersion | null>(null)
  const { currentVersionId, setCurrentVersion, setSidebarTab } = useWorkspaceStore()

  useEffect(() => { fetchVersions() }, [])

  async function fetchVersions() {
    setLoading(true)
    try { setVersions(await listVersions()) }
    catch { /* ignore */ }
    finally { setLoading(false) }
  }

  async function loadVersion(id: number) {
    try {
      const v = await getVersion(id)
      setCurrentVersion(v.id, v.entries)
      setSidebarTab('dashboard')
    } catch { /* ignore */ }
  }

  if (loading) return (
    <div style={{ display: 'flex', justifyContent: 'center', padding: 'var(--sp-8)' }}>
      <Loader2 size={24} style={{ animation: 'spin 0.7s linear infinite', color: 'var(--accent)' }} />
    </div>
  )

  if (versions.length === 0) return (
    <div className="empty-state" style={{ minHeight: 200 }}>
      <h3>No versions yet</h3>
      <p>Generate or create a timetable to see version history here.</p>
    </div>
  )

  return (
    <div style={{ padding: 'var(--sp-4)', display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
        <div>
          <div className="mono-label" style={{ marginBottom: 2 }}>Workspace</div>
          <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-xl)', fontWeight: 600, color: 'var(--ink)', letterSpacing: '-0.015em' }}>Version history</h2>
        </div>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)' }}>{versions.length} version{versions.length !== 1 ? 's' : ''}</span>
      </div>

      {versions.map(v => {
        const isCurrent = v.id === currentVersionId
        const isPublished = v.status === 'PUBLISHED'
        return (
          <TiltCard key={v.id} className={`version-card ${isPublished ? 'published' : isCurrent ? 'current' : ''}`}>
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8 }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ fontFamily: 'var(--font-display)', fontWeight: 600, fontSize: 'var(--fs-sm)', color: 'var(--ink)' }}>
                    Version #<span className="t-digit-group is-animating">
                      {String(v.id).split('').map((d, i) => (
                        <span key={i} className="t-digit" data-stagger={i > 0 ? String(i) : undefined}>{d}</span>
                      ))}
                    </span>
                  </span>
                  {statusBadge(v.status)}
                  {isCurrent && <span className="badge badge-blue">Current</span>}
                </div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)', display: 'flex', alignItems: 'center', gap: 5 }}>
                  <Clock size={11} /> {fmtDate(v.created_at)}
                </div>
                {v.change_summary && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)', marginTop: 2 }}>{v.change_summary}</div>}
              </div>
            </div>
            <div style={{ display: 'flex', gap: 'var(--sp-2)', flexWrap: 'wrap', position: 'relative', zIndex: 10 }}>
              <button className="btn btn-ghost btn-sm" onClick={() => loadVersion(v.id)}>
                <Eye size={12} /> View
              </button>
              {!isPublished && (
                <button className="btn btn-success btn-sm" onClick={() => setPublishTarget(v)}>
                  <Rocket size={12} /> Publish
                </button>
              )}
              <button className="btn btn-ghost btn-sm" onClick={() => loadVersion(v.id)}>
                <RotateCcw size={12} /> Restore
              </button>
            </div>
          </TiltCard>
        )
      })}

      {publishTarget && (
        <PublishDialog
          version={publishTarget}
          onClose={() => setPublishTarget(null)}
          onPublished={() => { fetchVersions(); setPublishTarget(null) }}
        />
      )}
    </div>
  )
}
