import { useEffect } from 'react'
import { RefreshCw, Search, LayoutDashboard, History, ShieldCheck, Rocket, Calendar } from 'lucide-react'
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

  useEffect(() => {
    async function init() {
      useWorkspaceStore.getState().loadCatalog()
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
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden', background: 'var(--paper)' }}>
      <Sidebar />

      <div className="work-area" style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0 }}>
        {/* Header */}
        <header className="app-header">
          <div className="header-logo">
            <div className="header-logo-box">CT</div>
            <div className="header-logo-text">
              <div className="name">Courselab</div>
              <div className="sub">Timetable Studio</div>
            </div>
          </div>
          <div className="header-actions">
            <button className="btn btn-ghost btn-sm" onClick={refresh} title="Refresh">
              <RefreshCw size={13} />
            </button>
            <div className="header-search">
              <Search size={13} style={{ color: 'var(--ink-soft)', flexShrink: 0 }} />
              <input
                placeholder="Search timetable…"
                value={filters.search}
                onChange={e => setFilters({ search: e.target.value })}
              />
            </div>
            <UserMenu />
          </div>
        </header>

        {sidebarTab !== 'dashboard' ? (
          <div style={{ flex: 1, overflow: 'auto' }}>
            {renderPanel()}
          </div>
        ) : (
          <>
            <ActionPanel />
            <FilterBar
              filters={filters}
              onChange={setFilters}
              density={density}
              onDensityChange={setDensity}
            />

            <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column', padding: '0 var(--sp-4) var(--sp-2)', minHeight: 0 }}>
              {/* Version info bar */}
              {currentVersionId ? (
                <div style={{ padding: '8px 0', display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
                  <span className="badge badge-yellow">Draft</span>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)' }}>Version #{currentVersionId}</span>
                  <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--line)' }}>·</span>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)' }}>{entries.length} entries</span>
                  <button
                    className="btn btn-ghost btn-sm"
                    style={{ marginLeft: 'auto' }}
                    onClick={() => setSidebarTab('validation')}
                  >
                    Validate
                  </button>
                  <button className="btn btn-success btn-sm" onClick={() => setSidebarTab('publish')}>
                    Publish →
                  </button>
                </div>
              ) : (
                <div style={{ padding: '8px 0', flexShrink: 0 }}>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)' }}>No version loaded — generate or select a version.</span>
                </div>
              )}
              <TimetableGrid
                entries={entries}
                filters={filters}
                density={density}
              />
            </div>

            <ChatPanel />
          </>
        )}
      </div>

      {/* Mobile Navigation */}
      <div className="mobile-nav">
        {[
          { id: 'dashboard',  icon: <LayoutDashboard size={20} />, label: 'Dashboard' },
          { id: 'versions',   icon: <History size={20} />,         label: 'Versions' },
          { id: 'validation', icon: <ShieldCheck size={20} />,     label: 'Validate' },
          { id: 'publish',    icon: <Rocket size={20} />,          label: 'Publish' },
        ].map(item => (
          <button
            key={item.id}
            className={`mobile-nav-item ${sidebarTab === item.id ? 'active' : ''}`}
            onClick={() => setSidebarTab(item.id as typeof sidebarTab)}
          >
            {item.icon}
            <span>{item.label}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

function PublishPanel() {
  const { currentVersionId } = useWorkspaceStore()
  return (
    <div style={{ padding: 'var(--sp-5)', display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>
      <div>
        <div className="mono-label" style={{ marginBottom: 4 }}>Workspace</div>
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-xl)', fontWeight: 600, color: 'var(--ink)', letterSpacing: '-0.015em' }}>Publish timetable</h2>
      </div>
      {currentVersionId ? (
        <>
          <div className="publish-banner">
            <Calendar size={24} style={{ color: 'var(--accent)', flexShrink: 0 }} />
            <div>
              <div style={{ fontFamily: 'var(--font-display)', fontWeight: 600, fontSize: 'var(--fs-md)', color: 'var(--ink)' }}>Version #{currentVersionId}</div>
              <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--ink-soft)', marginTop: 2 }}>
                Run validation first to ensure all H1–H11 constraints pass.
              </div>
            </div>
          </div>
          <VersionsList />
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
