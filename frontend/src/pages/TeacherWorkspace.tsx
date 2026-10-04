import { useEffect, useState, useRef } from 'react'
import { RefreshCw, Search, LayoutDashboard, History, ShieldCheck, Rocket, Calendar, X } from 'lucide-react'
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

function ReelSpin({ value }: { value: string | number }) {
  const str = String(value)
  return (
    <div className="t-reel">
      {str.split('').map((char, i) => {
        if (!/\d/.test(char)) return <span key={i} style={{ padding: '0 2px' }}>{char}</span>
        const targetDigit = parseInt(char, 10)
        return (
          <div key={i} className="t-reel-col" style={{ width: '0.6em' }}>
            <div 
              className="t-reel-strip" 
              ref={el => {
                if (!el) return
                // reset state
                el.style.transition = 'none'
                el.style.transform = `translateY(-${targetDigit} * var(--reel-cell))`
                el.style.filter = 'blur(0)'
                
                // If it's the first time landing or recovering from spin, 
                // just doing a quick jump to 0 and spinning to digit handles the visual
                const yTarget = targetDigit > 0 ? targetDigit : 10
                el.style.transform = `translateY(0)`
                void el.offsetWidth // reflow
                
                el.style.transition = `transform var(--reel-dur) var(--reel-ease) ${i * 90}ms`
                el.style.transform = `translateY(calc(-${yTarget} * var(--reel-cell)))`
              }}
            >
              {[0,1,2,3,4,5,6,7,8,9,0].map((n, idx) => (
                <div key={idx} className="t-reel-digit">{n}</div>
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}

function ClearableSearch({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [clearing, setClearing] = useState(false)
  
  function handleClear() {
    if (!value) return
    setClearing(true)
    onChange('')
    // Add fake glow by directly doing radial gradient on glow div
    const glow = document.getElementById('search-glow')
    if (glow && value) {
      const words = value.split(' ').length
      const layers = Array.from({length: words}, () => `radial-gradient(circle 20px at ${Math.random()*100}% 50%, rgba(0,0,0,0.5), transparent)`).join(', ')
      glow.style.background = layers
      glow.animate([{ opacity: 0 }, { opacity: 1, offset: 0.2 }, { opacity: 0 }], { duration: 1000 })
    }
    setTimeout(() => setClearing(false), 1000)
  }

  return (
    <div className={`t-clear ${value ? 'has-value' : ''} ${clearing ? 'is-clearing' : ''}`} style={{ flex: 1, display: 'flex', alignItems: 'center', height: '100%', position: 'relative' }}>
      <input
        type="text"
        placeholder="Search timetable…"
        value={value}
        onChange={e => onChange(e.target.value)}
        style={{ width: '100%', background: 'transparent', border: 'none', outline: 'none', color: 'var(--ink)' }}
      />
      <div className="t-clear-mirror" aria-hidden="true" style={{ left: 0, right: 30, justifyContent: 'flex-start' }}>{value}</div>
      <div className="t-clear-placeholder" aria-hidden="true" style={{ left: 0, right: 30, justifyContent: 'flex-start', color: 'var(--ink-soft)' }}>Search timetable…</div>
      <div id="search-glow" className="t-clear-glow" aria-hidden="true"></div>
      {value && !clearing && (
        <button className="btn-icon" onClick={handleClear} style={{ position: 'absolute', right: 0, width: 24, height: 24, zIndex: 10 }}>
          <X size={12} />
        </button>
      )}
    </div>
  )
}

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
            <div className="header-search" style={{ position: 'relative', overflow: 'hidden' }}>
              <Search size={13} style={{ color: 'var(--ink-soft)', flexShrink: 0 }} />
              <ClearableSearch value={filters.search} onChange={val => setFilters({ search: val })} />
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
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)' }}>
                    Version #<span className="t-digit-group is-animating">
                      {String(currentVersionId).split('').map((d, i) => (
                        <span key={i} className="t-digit" data-stagger={i > 0 ? String(i) : undefined}>{d}</span>
                      ))}
                    </span>
                  </span>
                  <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--line)' }}>·</span>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)' }}>
                    <ReelSpin value={entries.length} /> entries
                  </span>
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
