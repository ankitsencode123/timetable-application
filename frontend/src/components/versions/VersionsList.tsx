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
      <div className="modal modal-sm">
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
                ? <CheckCircle2 size={14} style={{ color: 'var(--accent)' }} />
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
          <div key={v.id} className={`version-card ${isPublished ? 'published' : isCurrent ? 'current' : ''}`}>
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8 }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ fontFamily: 'var(--font-display)', fontWeight: 600, fontSize: 'var(--fs-sm)', color: 'var(--ink)' }}>Version #{v.id}</span>
                  {statusBadge(v.status)}
                  {isCurrent && <span className="badge badge-blue">Current</span>}
                </div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)', display: 'flex', alignItems: 'center', gap: 5 }}>
                  <Clock size={11} /> {fmtDate(v.created_at)}
                </div>
                {v.change_summary && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)', marginTop: 2 }}>{v.change_summary}</div>}
              </div>
            </div>
            <div style={{ display: 'flex', gap: 'var(--sp-2)', flexWrap: 'wrap' }}>
              <button className="btn btn-ghost btn-sm" onClick={() => loadVersion(v.id)}>
                <Eye size={12} /> View
              </button>
              {!isPublished && (
                <button className="btn btn-success btn-sm" onClick={() => setPublishTarget(v)} disabled={v.status === 'ARCHIVED'}>
                  <Rocket size={12} /> Publish
                </button>
              )}
              <button className="btn btn-ghost btn-sm" onClick={() => loadVersion(v.id)}>
                <RotateCcw size={12} /> Restore
              </button>
            </div>
          </div>
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
