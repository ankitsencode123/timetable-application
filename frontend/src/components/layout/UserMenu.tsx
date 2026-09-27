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
  const initials = (user.full_name || user.email).slice(0, 2).toUpperCase()

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          background: 'var(--paper)', border: '1px solid var(--line)',
          borderRadius: 'var(--radius)', padding: '5px 10px',
          cursor: 'pointer', transition: 'var(--transition)',
        }}
        aria-expanded={open}
        aria-haspopup="true"
        id="user-menu-button"
      >
        <div style={{
          width: 26, height: 26, borderRadius: '50%',
          background: 'var(--accent)',
          display: 'grid', placeItems: 'center',
          fontFamily: 'var(--font-display)', fontSize: 10, fontWeight: 600,
          color: 'var(--paper)', flexShrink: 0,
        }}>
          {initials}
        </div>
        <div style={{ textAlign: 'left' }}>
          <div style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--ink)', maxWidth: 110, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {user.full_name}
          </div>
        </div>
        <ChevronDown size={12} style={{ color: 'var(--ink-soft)', transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 200ms' }} />
      </button>

      {open && (
        <div
          role="menu"
          aria-labelledby="user-menu-button"
          style={{
            position: 'absolute', top: 'calc(100% + 8px)', right: 0,
            minWidth: 220, background: 'var(--card-bg)',
            border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)',
            boxShadow: 'var(--shadow)', zIndex: 50, overflow: 'hidden',
            animation: 'slideUp 180ms cubic-bezier(0.34,1.56,0.64,1)',
          }}
        >
          <div style={{ padding: '12px 14px', borderBottom: '1px solid var(--line)', background: 'var(--paper)' }}>
            <div style={{ fontFamily: 'var(--font-display)', fontWeight: 600, fontSize: 'var(--fs-sm)', color: 'var(--ink)' }}>{user.full_name}</div>
            <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)', marginTop: 2 }}>{user.email}</div>
            <div style={{ marginTop: 6 }}>
              <span className={`badge ${isAdmin ? 'badge-purple' : 'badge-blue'}`}>{user.role}</span>
            </div>
          </div>

          <div style={{ padding: '6px 0' }}>
            <MenuItem icon={<User size={14} />} label="Profile" onClick={() => { navigate('/teacher/profile'); setOpen(false) }} />
            <MenuItem icon={<Lock size={14} />} label="Change Password" onClick={() => { navigate('/teacher/profile?tab=password'); setOpen(false) }} />
            {isAdmin && (
              <MenuItem icon={<Shield size={14} />} label="Admin Panel" onClick={() => { navigate('/admin'); setOpen(false) }} accent />
            )}
            <div style={{ borderTop: '1px solid var(--line)', margin: '4px 0' }} />
            <MenuItem icon={<LogOut size={14} />} label="Sign out" onClick={handleLogout} danger />
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
  const color = danger ? 'var(--violation)' : accent ? 'var(--clr-purple)' : 'var(--ink-soft)'
  return (
    <button
      role="menuitem"
      onClick={onClick}
      style={{
        display: 'flex', alignItems: 'center', gap: 10,
        width: '100%', padding: '7px 14px',
        background: 'none', border: 'none', cursor: 'pointer',
        fontSize: 'var(--fs-sm)', color, textAlign: 'left',
        transition: 'var(--transition)',
      }}
      onMouseEnter={e => (e.currentTarget.style.background = 'color-mix(in oklab, var(--ink) 4%, transparent)')}
      onMouseLeave={e => (e.currentTarget.style.background = 'none')}
    >
      {icon} {label}
    </button>
  )
}
