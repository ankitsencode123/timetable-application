import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowLeft, Plus, Shield, UserCheck, UserX, RefreshCw,
  Activity, Search, AlertCircle, BookOpen, CalendarOff, Calendar,
} from 'lucide-react'
import { useAuthStore } from '../store'
import {
  adminListUsers, adminCreateUser, adminEnableUser, adminDisableUser,
  adminResetPassword, adminGetUserActivity, type AdminUser,
} from '../api'
import CatalogManager from '../components/catalog/CatalogManager'
import BusySlotsManager from '../components/busyslots/BusySlotsManager'
import CalendarManager from '../components/calendar/CalendarManager'

type Tab = 'users' | 'activity' | 'catalog' | 'availability' | 'calendar'

export default function AdminPanel() {
  const { user } = useAuthStore()
  const navigate = useNavigate()
  const [tab, setTab] = useState<Tab>('users')
  const [users, setUsers] = useState<AdminUser[]>([])
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [activityUserId, setActivityUserId] = useState<number | null>(null)
  const [activity, setActivity] = useState<any[]>([])

  useEffect(() => {
    if (user && user.role !== 'ADMIN') navigate('/teacher')
  }, [user, navigate])

  useEffect(() => { fetchUsers() }, [])

  async function fetchUsers() {
    setLoading(true); setError(null)
    try { setUsers(await adminListUsers()) }
    catch (e: any) { setError(e.message) }
    finally { setLoading(false) }
  }

  async function viewActivity(userId: number) {
    setActivityUserId(userId); setTab('activity')
    try { setActivity(await adminGetUserActivity(userId)) }
    catch { setActivity([]) }
  }

  const filtered = users.filter(u =>
    u.full_name.toLowerCase().includes(search.toLowerCase()) ||
    u.email.toLowerCase().includes(search.toLowerCase())
  )

  if (!user || user.role !== 'ADMIN') return null

  return (
    <div style={{ minHeight: '100vh', background: 'var(--paper)', padding: 'var(--sp-5)' }}>
      <div style={{ maxWidth: 1000, margin: '0 auto' }}>

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 'var(--sp-5)' }}>
          <div>
            <button
              onClick={() => navigate('/teacher')}
              style={{ display: 'flex', alignItems: 'center', gap: 5, background: 'none', border: 'none', color: 'var(--ink-soft)', cursor: 'pointer', marginBottom: 8, fontSize: 'var(--fs-xs)', fontFamily: 'var(--font)' }}
            >
              <ArrowLeft size={13} /> Back to Workspace
            </button>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div className="header-logo-box"><span style={{ fontSize: 11 }}>CT</span></div>
              <div className="header-logo-text">
                <div className="name">Courselab</div>
                <div className="sub">Admin panel</div>
              </div>
            </div>
          </div>
          <button onClick={() => setShowCreate(true)} className="btn btn-primary" id="create-teacher-btn">
            <Plus size={14} /> Add teacher
          </button>
        </div>

        {/* Tabs */}
        <div className="tabs" style={{ marginBottom: 'var(--sp-4)' }}>
          <TabBtn active={tab === 'users'}        onClick={() => setTab('users')}        label="Teachers"    icon={<UserCheck size={13} />} />
          <TabBtn active={tab === 'catalog'}      onClick={() => setTab('catalog')}      label="Catalog"     icon={<BookOpen size={13} />} />
          <TabBtn active={tab === 'availability'} onClick={() => setTab('availability')} label="Busy Slots"  icon={<CalendarOff size={13} />} />
          <TabBtn active={tab === 'calendar'}     onClick={() => setTab('calendar')}     label="Calendar"    icon={<Calendar size={13} />} />
          <TabBtn active={tab === 'activity'}     onClick={() => {}}                     label="Activity Log" icon={<Activity size={13} />} disabled={activityUserId === null} />
        </div>

        {error && (
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', color: 'var(--violation)', marginBottom: 16, fontSize: 'var(--fs-sm)', background: 'var(--violation-soft)', border: '1px solid color-mix(in oklab, var(--violation) 20%, transparent)', borderRadius: 'var(--radius)', padding: 'var(--sp-3)' }}>
            <AlertCircle size={14} /> {error}
          </div>
        )}

        {tab === 'users' && (
          <>
            <div style={{ position: 'relative', marginBottom: 'var(--sp-4)' }}>
              <Search size={13} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--ink-soft)' }} />
              <input
                className="input"
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="Search by name or email…"
                style={{ paddingLeft: 32 }}
                id="admin-search-input"
              />
            </div>

            {loading ? (
              <div style={{ textAlign: 'center', padding: 40, color: 'var(--ink-soft)' }}>
                <span className="spinner-lg spinner" style={{ margin: '0 auto' }} />
              </div>
            ) : (
              <div className="card" style={{ padding: 0, overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 500 }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--line)' }}>
                      {['Name / Email', 'Role', 'Status', 'Last Login', 'Actions'].map(h => (
                        <th key={h} style={{ padding: '10px 14px', textAlign: 'left', fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500, color: 'var(--ink-soft)', textTransform: 'uppercase', letterSpacing: '0.18em', background: 'var(--paper)' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.map(u => (
                      <UserRow key={u.id} u={u} isSelf={u.id === user.id} onRefresh={fetchUsers} onViewActivity={() => viewActivity(u.id)} />
                    ))}
                    {filtered.length === 0 && (
                      <tr><td colSpan={5} style={{ padding: 32, textAlign: 'center', color: 'var(--ink-soft)', fontSize: 'var(--fs-sm)' }}>No users found</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}

        {tab === 'activity' && <ActivityLog activity={activity} userId={activityUserId} users={users} />}

        {tab === 'catalog' && (
          <div className="card">
            <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-md)', fontWeight: 600, marginBottom: 'var(--sp-4)', color: 'var(--ink)' }}>Catalog Management</h3>
            <CatalogManager />
          </div>
        )}

        {tab === 'availability' && (
          <div className="card">
            <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-md)', fontWeight: 600, marginBottom: 'var(--sp-4)', color: 'var(--ink)' }}>Faculty Availability — Busy Slots</h3>
            <BusySlotsManager />
          </div>
        )}

        {tab === 'calendar' && (
          <div className="card">
            <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-md)', fontWeight: 600, marginBottom: 'var(--sp-4)', color: 'var(--ink)' }}>Calendar Management</h3>
            <p style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)', marginBottom: 'var(--sp-4)', lineHeight: 1.6 }}>Manage date-specific overrides (ADD / CANCEL / MODIFY / DAY_OFF) and validity windows that assign a specific timetable version to a date range.</p>
            <CalendarManager />
          </div>
        )}
      </div>

      {showCreate && <CreateUserModal onClose={() => setShowCreate(false)} onCreated={fetchUsers} />}
    </div>
  )
}

function TabBtn({ active, onClick, label, icon, disabled }: any) {
  return (
    <button onClick={onClick} disabled={disabled} className={`tab ${active ? 'active' : ''}`} style={{ display: 'flex', alignItems: 'center', gap: 6, opacity: disabled ? 0.4 : 1, cursor: disabled ? 'not-allowed' : 'pointer' }}>
      {icon} {label}
    </button>
  )
}

function UserRow({ u, isSelf, onRefresh, onViewActivity }: { u: AdminUser; isSelf: boolean; onRefresh: () => void; onViewActivity: () => void }) {
  const [loading, setLoading] = useState('')
  const [resetOpen, setResetOpen] = useState(false)
  const [newPw, setNewPw] = useState('')
  const [msg, setMsg] = useState('')

  async function toggle() {
    setLoading('toggle')
    try {
      if (u.is_active) await adminDisableUser(u.id)
      else await adminEnableUser(u.id)
      onRefresh()
    } catch (e: any) { setMsg(e.message) }
    finally { setLoading('') }
  }

  async function doReset() {
    if (newPw.length < 8) { setMsg('Min 8 chars'); return }
    setLoading('reset')
    try {
      await adminResetPassword(u.id, newPw)
      setMsg('Password reset!'); setResetOpen(false); setNewPw('')
    } catch (e: any) { setMsg(e.message) }
    finally { setLoading('') }
  }

  const isLocked = u.locked_until && new Date(u.locked_until) > new Date()

  return (
    <>
      <tr style={{ borderBottom: '1px solid var(--line)', background: isSelf ? 'var(--accent-soft)' : undefined }}>
        <td style={{ padding: '11px 14px' }}>
          <div style={{ fontFamily: 'var(--font-display)', fontWeight: 600, fontSize: 'var(--fs-sm)', color: 'var(--ink)' }}>
            {u.full_name} {isSelf && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--accent)', marginLeft: 4, textTransform: 'uppercase', letterSpacing: '0.1em' }}>(you)</span>}
          </div>
          <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)', marginTop: 1 }}>{u.email}</div>
          {isLocked && <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--amber)', marginTop: 2, letterSpacing: '0.1em', textTransform: 'uppercase' }}>🔒 Locked</div>}
        </td>
        <td style={{ padding: '11px 14px' }}>
          <span className={`badge ${u.role === 'ADMIN' ? 'badge-purple' : 'badge-blue'}`}>{u.role}</span>
        </td>
        <td style={{ padding: '11px 14px' }}>
          <span className={`badge ${u.is_active ? 'badge-green' : 'badge-red'}`}>{u.is_active ? 'Active' : 'Disabled'}</span>
        </td>
        <td style={{ padding: '11px 14px', fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)' }}>
          {u.last_login ? new Date(u.last_login).toLocaleDateString() : 'Never'}
        </td>
        <td style={{ padding: '11px 14px' }}>
          <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
            {!isSelf && (
              <button onClick={toggle} disabled={loading === 'toggle'} className={`btn btn-xs ${u.is_active ? 'btn-warning' : 'btn-success'}`}>
                {u.is_active ? <UserX size={11} /> : <UserCheck size={11} />}
                {loading === 'toggle' ? '…' : u.is_active ? 'Disable' : 'Enable'}
              </button>
            )}
            <button onClick={() => setResetOpen(r => !r)} className="btn btn-xs btn-secondary">
              <RefreshCw size={11} /> Reset PW
            </button>
            <button onClick={onViewActivity} className="btn btn-xs btn-ghost">
              <Activity size={11} /> Activity
            </button>
          </div>
          {msg && <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: msg.includes('reset') ? 'var(--accent)' : 'var(--violation)', marginTop: 4, textTransform: 'uppercase', letterSpacing: '0.08em' }}>{msg}</div>}
        </td>
      </tr>
      {resetOpen && (
        <tr style={{ background: 'var(--paper)' }}>
          <td colSpan={5} style={{ padding: '8px 14px' }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <input className="input" type="password" value={newPw} onChange={e => setNewPw(e.target.value)} placeholder="New password (min 8 chars)" style={{ width: 220 }} />
              <button onClick={doReset} disabled={loading === 'reset'} className="btn btn-primary btn-sm">
                {loading === 'reset' ? '…' : 'Set password'}
              </button>
              <button onClick={() => { setResetOpen(false); setNewPw(''); setMsg('') }} className="btn btn-ghost btn-sm">Cancel</button>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

function ActivityLog({ activity, userId, users }: { activity: any[]; userId: number | null; users: AdminUser[] }) {
  const displayUser = users.find(u => u.id === userId)
  return (
    <div className="card">
      <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-md)', fontWeight: 600, marginBottom: 'var(--sp-4)', color: 'var(--ink)' }}>
        Activity — {displayUser?.full_name ?? `User #${userId}`}
      </h3>
      {activity.length === 0 ? (
        <div style={{ color: 'var(--ink-soft)', fontSize: 'var(--fs-sm)', textAlign: 'center', padding: 'var(--sp-8)' }}>No activity recorded</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {activity.map(log => (
            <div key={log.id} style={{ display: 'flex', gap: 12, padding: '9px 12px', background: 'var(--paper)', borderRadius: 'var(--radius)', fontSize: 'var(--fs-xs)', border: '1px solid var(--line)' }}>
              <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--ink-soft)', whiteSpace: 'nowrap' }}>{new Date(log.created_at).toLocaleString()}</span>
              <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, color: 'var(--accent)', minWidth: 120, textTransform: 'uppercase', letterSpacing: '0.05em', fontSize: 10 }}>{log.action}</span>
              <span style={{ color: 'var(--ink-soft)' }}>{log.details}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function CreateUserModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [form, setForm] = useState({ full_name: '', email: '', password: '', role: 'TEACHER' })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setError(null)
    if (form.password.length < 8) { setError('Password must be at least 8 characters.'); return }
    setLoading(true)
    try { await adminCreateUser(form); onCreated(); onClose() }
    catch (e: any) { setError(e.message) }
    finally { setLoading(false) }
  }

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.25)', backdropFilter: 'blur(4px)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100 }} onClick={onClose}>
      <div className="card" style={{ width: 400, padding: 'var(--sp-5)' }} onClick={e => e.stopPropagation()}>
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-lg)', fontWeight: 600, marginBottom: 'var(--sp-4)', color: 'var(--ink)', letterSpacing: '-0.015em' }}>
          Create teacher account
        </h2>
        <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)' }}>
          {[
            { id: 'cn-name', label: 'Full Name',          field: 'full_name', type: 'text' },
            { id: 'cn-email', label: 'Email',             field: 'email',     type: 'email' },
            { id: 'cn-pw', label: 'Temporary Password',   field: 'password',  type: 'password' },
          ].map(({ id, label, field, type }) => (
            <div key={field} className="form-group">
              <label htmlFor={id} className="form-label">{label}</label>
              <input id={id} className="input" type={type} value={(form as any)[field]} required
                onChange={e => setForm(f => ({ ...f, [field]: e.target.value }))} />
            </div>
          ))}
          <div className="form-group">
            <label className="form-label">Role</label>
            <select className="input" value={form.role} onChange={e => setForm(f => ({ ...f, role: e.target.value }))}>
              <option value="TEACHER">Teacher</option>
              <option value="ADMIN">Admin</option>
            </select>
          </div>
          {error && (
            <div style={{ color: 'var(--violation)', fontSize: 'var(--fs-xs)', display: 'flex', gap: 6, alignItems: 'center' }}>
              <AlertCircle size={13} />{error}
            </div>
          )}
          <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
            <button type="submit" disabled={loading} className="btn btn-primary" style={{ flex: 1 }}>
              {loading ? 'Creating…' : 'Create account'}
            </button>
            <button type="button" onClick={onClose} className="btn btn-ghost">Cancel</button>
          </div>
        </form>
      </div>
    </div>
  )
}
