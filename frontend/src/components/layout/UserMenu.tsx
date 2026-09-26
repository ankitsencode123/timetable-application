import { useState, useRef, useEffect } from 'react'
import { ChevronDown, User, Lock, LogOut, Shield } from 'lucide-react'
import { useAuthStore } from '../../store'
import { logout as apiLogout } from '../../api'
import { useNavigate } from 'react-router-dom'

export default function UserMenu() {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const { user, logout } = useAuthStore()
  const navigate = useNavigate()

  // Close on outside click
  useEffect(() => {
    function handle(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handle)
    return () => document.removeEventListener('mousedown', handle)
  }, [])

  async function handleLogout() {
    try { await apiLogout() } catch { /* ignore */ }
    logout()
    navigate('/login')
  }

  if (!user) return null
  const isAdmin = user.role === 'ADMIN'

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      {/* Trigger */}
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          background: 'var(--clr-bg-3)', border: '1px solid var(--clr-border)',
          borderRadius: 'var(--radius)', padding: '6px 12px',
          cursor: 'pointer', transition: 'var(--transition)',
        }}
        aria-expanded={open}
        aria-haspopup="true"
        id="user-menu-button"
      >
        <div style={{
          width: 28, height: 28, borderRadius: '50%',
          background: isAdmin ? 'var(--clr-purple-bg)' : 'var(--clr-primary-20)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 'var(--fs-xs)', fontWeight: 700,
          color: isAdmin ? 'var(--clr-purple)' : 'var(--clr-primary)',
        }}>
          {user.full_name.charAt(0).toUpperCase()}
        </div>
        <div style={{ textAlign: 'left' }}>
          <div style={{ fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--clr-text)', maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {user.full_name}
          </div>
          <div style={{ fontSize: 10, color: 'var(--clr-text-3)' }}>{user.role}</div>
        </div>
        <ChevronDown size={13} style={{ color: 'var(--clr-text-3)', transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 200ms' }} />
      </button>

      {/* Dropdown */}
      {open && (
        <div
          role="menu"
          aria-labelledby="user-menu-button"
          style={{
            position: 'absolute', top: 'calc(100% + 8px)', right: 0,
            minWidth: 220, background: 'var(--clr-bg-2)',
            border: '1px solid var(--clr-border)', borderRadius: 'var(--radius-lg)',
            boxShadow: 'var(--shadow)', zIndex: 50, overflow: 'hidden',
            animation: 'slideUp 180ms cubic-bezier(0.34,1.56,0.64,1)',
          }}
        >
          {/* User info header */}
          <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--clr-border)', background: 'var(--clr-bg-3)' }}>
            <div style={{ fontWeight: 700, fontSize: 'var(--fs-sm)', color: 'var(--clr-text)' }}>{user.full_name}</div>
            <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--clr-text-3)', marginTop: 2 }}>{user.email}</div>
            <div style={{ marginTop: 6 }}>
              <span className={`badge ${isAdmin ? 'badge-purple' : 'badge-blue'}`}>
                {user.role}
              </span>
            </div>
          </div>

          {/* Menu items */}
          <div style={{ padding: '6px 0' }}>
            <MenuItem icon={<User size={14} />} label="Profile" onClick={() => { navigate('/teacher/profile'); setOpen(false) }} />
            <MenuItem icon={<Lock size={14} />} label="Change Password" onClick={() => { navigate('/teacher/profile?tab=password'); setOpen(false) }} />
            {isAdmin && (
              <MenuItem icon={<Shield size={14} />} label="Admin Panel" onClick={() => { navigate('/admin'); setOpen(false) }} accent />
            )}
            <div style={{ borderTop: '1px solid var(--clr-border)', margin: '4px 0' }} />
            <MenuItem icon={<LogOut size={14} />} label="Logout" onClick={handleLogout} danger />
          </div>
        </div>
      )}
    </div>
  )
}

function MenuItem({ icon, label, onClick, danger, accent }: {
  icon: React.ReactNode; label: string; onClick: () => void;
  danger?: boolean; accent?: boolean
}) {
  const color = danger ? 'var(--clr-error)' : accent ? 'var(--clr-purple)' : 'var(--clr-text-2)'
  return (
    <button
      role="menuitem"
      onClick={onClick}
      style={{
        display: 'flex', alignItems: 'center', gap: 10,
        width: '100%', padding: '8px 16px',
        background: 'none', border: 'none', cursor: 'pointer',
        fontSize: 'var(--fs-sm)', color, textAlign: 'left',
        transition: 'var(--transition)',
      }}
      onMouseEnter={e => (e.currentTarget.style.background = 'var(--clr-bg-4)')}
      onMouseLeave={e => (e.currentTarget.style.background = 'none')}
    >
      {icon} {label}
    </button>
  )
}
