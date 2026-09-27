import { LayoutDashboard, History, ShieldCheck, Rocket, LogOut } from 'lucide-react'
import { useAuthStore, useWorkspaceStore } from '../../store'
import { useNavigate } from 'react-router-dom'

const NAV = [
  { id: 'dashboard',  icon: <LayoutDashboard size={15} />, label: 'Dashboard' },
  { id: 'versions',   icon: <History size={15} />,         label: 'Versions' },
  { id: 'validation', icon: <ShieldCheck size={15} />,     label: 'Validation' },
  { id: 'publish',    icon: <Rocket size={15} />,          label: 'Publish' },
]

export default function Sidebar() {
  const { sidebarTab, setSidebarTab, currentVersionId } = useWorkspaceStore()
  const { user, logout } = useAuthStore()
  const navigate = useNavigate()

  function handleLogout() { logout(); navigate('/login') }

  return (
    <aside className="sidebar">
      {/* Branding */}
      <div style={{ padding: '0 var(--sp-4) var(--sp-4)', borderBottom: '1px solid var(--line)', marginBottom: 'var(--sp-3)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div className="header-logo-box" style={{ width: 28, height: 28, fontSize: 11 }}>CT</div>
          <div className="header-logo-text">
            <div className="name" style={{ fontSize: 13 }}>Courselab</div>
            <div className="sub">Timetable Studio</div>
          </div>
        </div>
        {user && (
          <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--line)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{ width: 28, height: 28, borderRadius: '50%', background: 'var(--accent)', display: 'grid', placeItems: 'center', fontFamily: 'var(--font-display)', fontSize: 11, fontWeight: 600, color: 'var(--paper)', flexShrink: 0 }}>
                {(user.full_name || user.email).slice(0, 2).toUpperCase()}
              </div>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{user.full_name}</div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--ink-soft)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>{user.role}</div>
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="nav-section">Workspace</div>
      {NAV.map(item => (
        <button
          key={item.id}
          className={`nav-item ${sidebarTab === item.id ? 'active' : ''}`}
          onClick={() => setSidebarTab(item.id as typeof sidebarTab)}
        >
          <span style={{ width: 6, height: 6, borderRadius: '50%', background: sidebarTab === item.id ? 'var(--accent)' : 'var(--line)', flexShrink: 0 }} />
          {item.label}
        </button>
      ))}

      {currentVersionId && (
        <>
          <hr className="nav-divider" />
          <div className="nav-section">Current draft</div>
          <div style={{ margin: '0 var(--sp-3)', padding: 'var(--sp-3)', background: 'var(--ink)', borderRadius: 'var(--radius)' }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.18em', color: 'rgba(255,255,255,0.5)', marginBottom: 4 }}>Version</div>
            <div style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--paper)' }}>#{currentVersionId}</div>
            <div style={{ marginTop: 8 }}>
              <span style={{ background: 'rgba(255,255,255,0.15)', borderRadius: 4, padding: '2px 6px', fontFamily: 'var(--font-mono)', fontSize: 9, color: 'rgba(255,255,255,0.8)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>DRAFT</span>
            </div>
          </div>
        </>
      )}

      <div style={{ marginTop: 'auto', padding: 'var(--sp-4)', borderTop: '1px solid var(--line)' }}>
        <button className="nav-item" onClick={handleLogout} style={{ color: 'var(--violation)', borderLeftColor: 'transparent' }}>
          <LogOut size={14} /> Sign out
        </button>
      </div>
    </aside>
  )
}
