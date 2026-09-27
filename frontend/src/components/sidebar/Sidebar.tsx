import { LayoutDashboard, History, ShieldCheck, Rocket, LogOut, Calendar } from 'lucide-react'
import { useAuthStore, useWorkspaceStore } from '../../store'
import { useNavigate } from 'react-router-dom'

const NAV = [
  { id: 'dashboard',  icon: <LayoutDashboard size={16} />, label: 'Dashboard' },
  { id: 'versions',   icon: <History size={16} />,         label: 'Versions' },
  { id: 'validation', icon: <ShieldCheck size={16} />,     label: 'Validation' },
  { id: 'publish',    icon: <Rocket size={16} />,          label: 'Publish' },
]

export default function Sidebar() {
  const { sidebarTab, setSidebarTab, currentVersionId } = useWorkspaceStore()
  const { user, logout } = useAuthStore()
  const navigate = useNavigate()

  function handleLogout() { logout(); navigate('/login') }

  return (
    <aside className="sidebar">
      {/* Branding */}
      <div style={{ padding: '0 var(--sp-4) var(--sp-4)', borderBottom: '1px solid var(--clr-border)', marginBottom: 'var(--sp-3)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Calendar size={18} style={{ color: 'var(--clr-primary)' }} />
          <span style={{ fontSize: 'var(--fs-sm)', fontWeight: 800, color: 'var(--clr-text)' }}>Timely</span>
        </div>
        {user && (
          <div style={{ marginTop: 10 }}>
            <div style={{ fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--clr-text)' }}>{user.full_name}</div>
            <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--clr-text-3)', marginTop: 1 }}>{user.role}</div>
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
          {item.icon}
          {item.label}
        </button>
      ))}

      {currentVersionId && (
        <>
          <hr className="nav-divider" />
          <div className="nav-section">Current Draft</div>
          <div style={{ padding: '4px var(--sp-4)', fontSize: 'var(--fs-xs)', color: 'var(--clr-text-2)', display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ width: 6, height: 6, background: 'var(--clr-warning)', borderRadius: '50%', flexShrink: 0 }} />
            Version #{currentVersionId}
          </div>
        </>
      )}

      <div style={{ marginTop: 'auto', padding: 'var(--sp-4)', borderTop: '1px solid var(--clr-border)' }}>
        <button className="nav-item" onClick={handleLogout} style={{ color: 'var(--clr-error)' }}>
          <LogOut size={16} /> Logout
        </button>
      </div>
    </aside>
  )
}
