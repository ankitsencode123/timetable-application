import { useEffect } from 'react'
import { Calendar, Search, RefreshCw, Maximize2 } from 'lucide-react'
import { useWorkspaceStore, useAuthStore } from '../store'
import { getCurrentDraft, listVersions, getVersion } from '../api'
import Sidebar from '../components/sidebar/Sidebar'
import FilterBar from '../components/filters/FilterBar'
import TimetableGrid from '../components/timetable/TimetableGrid'
import ActionPanel from '../components/actions/ActionPanel'
import ChatPanel from '../components/chat/ChatPanel'
import ValidationPanel from '../components/validation/ValidationPanel'
import VersionsList from '../components/versions/VersionsList'
import UserMenu from '../components/layout/UserMenu'

export default function TeacherWorkspace() {
  const {
    filters, setFilters, density, setDensity, setSidebarTab,
    entries, currentVersionId, setCurrentVersion, setVersions, sidebarTab,
  } = useWorkspaceStore()
  const user = useAuthStore((s) => s.user)

  // Load initial draft + version list on mount
  useEffect(() => {
    async function init() {
      try {
        const draft = await getCurrentDraft()
        const detail = await getVersion(draft.id)
        setCurrentVersion(detail.id, detail.entries)
        const vList = await listVersions()
        setVersions(vList)
      } catch { /* no draft exists yet */ }
    }
    init()
  }, [])

  async function refresh() {
    try {
      if (currentVersionId) {
        const v = await getVersion(currentVersionId)
        setCurrentVersion(v.id, v.entries)
      }
      setVersions(await listVersions())
    } catch { /* ignore */ }
  }

  function renderPanel() {
    switch (sidebarTab) {
      case 'versions':   return <VersionsList />
      case 'validation': return <ValidationPanel versionId={currentVersionId} />
      case 'publish':    return <PublishPanel />
      default:           return null
    }
  }

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden' }}>
      {/* Sidebar */}
      <Sidebar />

      {/* Main area */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0 }}>
        {/* Top header */}
        <header className="app-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <Calendar size={20} style={{ color: 'var(--clr-primary)' }} />
            <div>
              <div style={{ fontWeight: 800, fontSize: 'var(--fs-md)', background: 'linear-gradient(135deg, var(--clr-primary), var(--clr-purple))', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
                ChronoSync — Teacher Workspace
              </div>
              {currentVersionId && (
                <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--clr-text-3)', marginTop: 1 }}>
                  Editing Version #{currentVersionId}
                </div>
              )}
            </div>
          </div>
          <div className="header-actions">
            <button className="btn btn-ghost btn-sm" onClick={refresh} title="Refresh">
              <RefreshCw size={13} />
            </button>
            <div className="header-search">
              <Search size={13} style={{ color: 'var(--clr-text-3)', flexShrink: 0 }} />
              <input
                placeholder="Search timetable…"
                value={filters.search}
                onChange={e => setFilters({ search: e.target.value })}
              />
            </div>
            <UserMenu />
          </div>
        </header>

        {/* If sidebar tab is not dashboard, show that panel full-width */}
        {sidebarTab !== 'dashboard' ? (
          <div style={{ flex: 1, overflow: 'auto' }}>
            {renderPanel()}
          </div>
        ) : (
          <>
            {/* Action buttons */}
            <ActionPanel />

            {/* Filters */}
            <FilterBar
              filters={filters}
              onChange={setFilters}
              density={density}
              onDensityChange={setDensity}
            />

            {/* Timetable — fills remaining space */}
            <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column', padding: '0 var(--sp-4) var(--sp-2)', minHeight: 0 }}>
              {/* Version info bar */}
              {currentVersionId ? (
                <div style={{ padding: '6px 0', display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
                  <span className="badge badge-yellow">Draft</span>
                  <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--clr-text-3)' }}>Version #{currentVersionId}</span>
                  <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--clr-text-3)' }}>·</span>
                  <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--clr-text-3)' }}>{entries.length} entries</span>
                  <button
                    className="btn btn-ghost btn-sm"
                    style={{ marginLeft: 'auto' }}
                    onClick={() => setSidebarTab('validation')}
                  >
                    <Maximize2 size={11} /> Validate
                  </button>
                  <button className="btn btn-success btn-sm" onClick={() => setSidebarTab('publish')}>
                    Publish →
                  </button>
                </div>
              ) : (
                <div style={{ padding: '8px 0' }}>
                  <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--clr-text-3)' }}>No version loaded — generate or select a version from the sidebar.</span>
                </div>
              )}

              <TimetableGrid
                entries={entries}
                filters={filters}
                density={density}
              />
            </div>

            {/* AI Chat panel at bottom */}
            <ChatPanel />
          </>
        )}
      </div>
    </div>
  )
}

function PublishPanel() {
  const { currentVersionId } = useWorkspaceStore()
  return (
    <div style={{ padding: 'var(--sp-5)', display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>
      <h3 style={{ fontSize: 'var(--fs-md)', fontWeight: 700 }}>Publish Timetable</h3>
      {currentVersionId ? (
        <>
          <div className="publish-banner">
            <Calendar size={28} style={{ color: 'var(--clr-success)', flexShrink: 0 }} />
            <div>
              <div style={{ fontWeight: 700, fontSize: 'var(--fs-md)' }}>Version #{currentVersionId}</div>
              <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--clr-text-2)', marginTop: 2 }}>
                Run validation first to ensure all H1–H11 constraints pass before publishing.
              </div>
            </div>
          </div>
          <div style={{ display: 'flex', gap: 'var(--sp-3)' }}>
            <VersionsList />
          </div>
        </>
      ) : (
        <div className="empty-state">
          <h3>No version loaded</h3>
          <p>Select a version from the Versions panel to publish it.</p>
        </div>
      )}
    </div>
  )
}
