import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowLeft, Plus, Shield, UserCheck, UserX, RefreshCw,
  Activity, Search, AlertCircle, BookOpen, CalendarOff,
} from 'lucide-react'
import { useAuthStore } from '../store'
import {
  adminListUsers, adminCreateUser, adminEnableUser, adminDisableUser,
  adminResetPassword, adminGetUserActivity, type AdminUser,
} from '../api'
import CatalogManager from '../components/catalog/CatalogManager'
import BusySlotsManager from '../components/busyslots/BusySlotsManager'

type Tab = 'users' | 'activity' | 'catalog' | 'availability'

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

  // Redirect if not admin
  useEffect(() => {
    if (user && user.role !== 'ADMIN') navigate('/teacher')
  }, [user, navigate])

  useEffect(() => {
    fetchUsers()
  }, [])

  async function fetchUsers() {
    setLoading(true); setError(null)
    try { setUsers(await adminListUsers()) }
    catch (e: any) { setError(e.message) }
    finally { setLoading(false) }
  }

  async function viewActivity(userId: number) {
    setActivityUserId(userId)
    setTab('activity')
    try { setActivity(await adminGetUserActivity(userId)) }
    catch { setActivity([]) }
  }

  const filtered = users.filter(u =>
    u.full_name.toLowerCase().includes(search.toLowerCase()) ||
    u.email.toLowerCase().includes(search.toLowerCase())
  )

  if (!user || user.role !== 'ADMIN') return null

  return (
    <div style={{ minHeight: '100vh', background: 'var(--clr-bg)', padding: 24 }}>
      <div style={{ maxWidth: 1000, margin: '0 auto' }}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
          <div>
            <button onClick={() => navigate('/teacher')}
              style={{ display: 'flex', alignItems: 'center', gap: 6, background: 'none', border: 'none', color: 'var(--clr-text-3)', cursor: 'pointer', marginBottom: 8, fontSize: 'var(--fs-sm)' }}>
              <ArrowLeft size={14} /> Back to Workspace
            </button>
            <h1 style={{ fontSize: 22, fontWeight: 800, color: 'var(--clr-text)', display: 'flex', alignItems: 'center', gap: 8 }}>
              <Shield size={20} style={{ color: 'var(--clr-purple)' }} /> Admin Panel
            </h1>
          </div>
          <button onClick={() => setShowCreate(true)} className="btn btn-primary" style={{ display: 'flex', alignItems: 'center', gap: 6 }} id="create-teacher-btn">
            <Plus size={14} /> Add Teacher
          </button>
        </div>

        {/* Tabs */}
        <div style={{ display: 'flex', gap: 4, marginBottom: 20, background: 'var(--clr-bg-2)', padding: 4, borderRadius: 'var(--radius)', width: 'fit-content', flexWrap: 'wrap' }}>
          <TabBtn active={tab === 'users'} onClick={() => setTab('users')} label="Teachers" icon={<UserCheck size={13} />} />
          <TabBtn active={tab === 'catalog'} onClick={() => setTab('catalog')} label="Catalog" icon={<BookOpen size={13} />} />
          <TabBtn active={tab === 'availability'} onClick={() => setTab('availability')} label="Faculty Availability" icon={<CalendarOff size={13} />} />
          <TabBtn active={tab === 'activity'} onClick={() => {}} label="Activity Log" icon={<Activity size={13} />} disabled={activityUserId === null} />
        </div>

        {error && (
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', color: 'var(--clr-error)', marginBottom: 16, fontSize: 'var(--fs-sm)' }}>
            <AlertCircle size={14} /> {error}
          </div>
        )}

        {tab === 'users' && (
          <>
            {/* Search */}
            <div style={{ position: 'relative', marginBottom: 16 }}>
              <Search size={14} style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--clr-text-3)' }} />
              <input className="input" value={search} onChange={e => setSearch(e.target.value)} placeholder="Search by name or email…" style={{ paddingLeft: 36, width: '100%' }} id="admin-search-input" />
            </div>

            {/* Table */}
            {loading ? (
              <div style={{ textAlign: 'center', padding: 40, color: 'var(--clr-text-3)' }}>Loading…</div>
            ) : (
              <div className="card" style={{ overflow: 'hidden' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ background: 'var(--clr-bg-3)' }}>
                      {['Name / Email', 'Role', 'Status', 'Last Login', 'Actions'].map(h => (
                        <th key={h} style={{ padding: '10px 16px', textAlign: 'left', fontSize: 'var(--fs-xs)', fontWeight: 700, color: 'var(--clr-text-3)', borderBottom: '1px solid var(--clr-border)' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.map(u => (
                      <UserRow
                        key={u.id}
                        u={u}
                        isSelf={u.id === user.id}
                        onRefresh={fetchUsers}
                        onViewActivity={() => viewActivity(u.id)}
                      />
                    ))}
                    {filtered.length === 0 && (
                      <tr><td colSpan={5} style={{ padding: 32, textAlign: 'center', color: 'var(--clr-text-3)' }}>No users found</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}

        {tab === 'activity' && (
          <ActivityLog activity={activity} userId={activityUserId} users={users} />
        )}

        {tab === 'catalog' && (
          <div className="card" style={{ padding: 24 }}>
            <h3 style={{ fontSize: 15, fontWeight: 700, marginBottom: 20 }}>Catalog Management</h3>
            <CatalogManager />
          </div>
        )}

        {tab === 'availability' && (
          <div className="card" style={{ padding: 24 }}>
            <h3 style={{ fontSize: 15, fontWeight: 700, marginBottom: 20 }}>Faculty Availability (Busy Slots)</h3>
            <BusySlotsManager />
          </div>
        )}
      </div>

      {showCreate && <CreateUserModal onClose={() => setShowCreate(false)} onCreated={fetchUsers} />}
    </div>
  )
}

function TabBtn({ active, onClick, label, icon, disabled }: any) {
  return (
    <button onClick={onClick} disabled={disabled}
      style={{
        display: 'flex', alignItems: 'center', gap: 6,
        padding: '7px 16px', borderRadius: 'var(--radius)', border: 'none',
        cursor: disabled ? 'not-allowed' : 'pointer', fontSize: 'var(--fs-sm)',
        fontWeight: active ? 700 : 400,
        background: active ? 'var(--clr-bg-4)' : 'none',
        color: active ? 'var(--clr-text)' : 'var(--clr-text-3)',
        opacity: disabled ? 0.5 : 1,
      }}>
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
      setMsg('Password reset!')
      setResetOpen(false); setNewPw('')
    } catch (e: any) { setMsg(e.message) }
    finally { setLoading('') }
  }

  const isLocked = u.locked_until && new Date(u.locked_until) > new Date()

  return (
    <>
      <tr style={{ borderBottom: '1px solid var(--clr-border)', background: isSelf ? 'var(--clr-primary-5)' : undefined }}>
        <td style={{ padding: '12px 16px' }}>
          <div style={{ fontWeight: 600, fontSize: 'var(--fs-sm)', color: 'var(--clr-text)' }}>
            {u.full_name} {isSelf && <span style={{ fontSize: 10, color: 'var(--clr-primary)', marginLeft: 4 }}>(you)</span>}
          </div>
          <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--clr-text-3)' }}>{u.email}</div>
          {isLocked && <div style={{ fontSize: 10, color: 'var(--clr-warning)', marginTop: 2 }}>🔒 Locked</div>}
        </td>
        <td style={{ padding: '12px 16px' }}>
          <span className={`badge ${u.role === 'ADMIN' ? 'badge-purple' : 'badge-blue'}`}>{u.role}</span>
        </td>
        <td style={{ padding: '12px 16px' }}>
          <span className={`badge ${u.is_active ? 'badge-green' : 'badge-red'}`}>{u.is_active ? 'Active' : 'Disabled'}</span>
        </td>
        <td style={{ padding: '12px 16px', fontSize: 'var(--fs-xs)', color: 'var(--clr-text-3)' }}>
          {u.last_login ? new Date(u.last_login).toLocaleDateString() : 'Never'}
        </td>
        <td style={{ padding: '12px 16px' }}>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {!isSelf && (
              <button onClick={toggle} disabled={loading === 'toggle'} className={`btn btn-xs ${u.is_active ? 'btn-warning' : 'btn-success'}`} title={u.is_active ? 'Disable account' : 'Enable account'}>
                {u.is_active ? <UserX size={12} /> : <UserCheck size={12} />}
                {loading === 'toggle' ? '…' : u.is_active ? 'Disable' : 'Enable'}
              </button>
            )}
            <button onClick={() => setResetOpen(r => !r)} className="btn btn-xs btn-secondary">
              <RefreshCw size={12} /> Reset PW
            </button>
            <button onClick={onViewActivity} className="btn btn-xs btn-ghost">
              <Activity size={12} /> Activity
            </button>
          </div>
          {msg && <div style={{ fontSize: 10, color: 'var(--clr-error)', marginTop: 4 }}>{msg}</div>}
        </td>
      </tr>
      {resetOpen && (
        <tr style={{ background: 'var(--clr-bg-3)' }}>
          <td colSpan={5} style={{ padding: '10px 16px' }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <input className="input" type="password" value={newPw} onChange={e => setNewPw(e.target.value)} placeholder="New password (min 8 chars)" style={{ width: 220 }} />
              <button onClick={doReset} disabled={loading === 'reset'} className="btn btn-primary btn-sm">
                {loading === 'reset' ? '…' : 'Set Password'}
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
    <div className="card" style={{ padding: 24 }}>
      <h3 style={{ fontSize: 15, fontWeight: 700, marginBottom: 16, color: 'var(--clr-text)' }}>
        Activity — {displayUser?.full_name ?? `User #${userId}`}
      </h3>
      {activity.length === 0 ? (
        <div style={{ color: 'var(--clr-text-3)', fontSize: 'var(--fs-sm)', textAlign: 'center', padding: 32 }}>No activity recorded</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {activity.map(log => (
            <div key={log.id} style={{ display: 'flex', gap: 12, padding: '10px 14px', background: 'var(--clr-bg-3)', borderRadius: 'var(--radius)', fontSize: 'var(--fs-xs)' }}>
              <span style={{ color: 'var(--clr-text-3)', whiteSpace: 'nowrap' }}>
                {new Date(log.created_at).toLocaleString()}
              </span>
              <span style={{ fontWeight: 600, color: 'var(--clr-primary)', minWidth: 120 }}>{log.action}</span>
              <span style={{ color: 'var(--clr-text-2)' }}>{log.details}</span>
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
    try {
      await adminCreateUser(form)
      onCreated()
      onClose()
    } catch (e: any) { setError(e.message) }
    finally { setLoading(false) }
  }

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100,
    }} onClick={onClose}>
      <div className="card" style={{ width: 440, padding: 28 }} onClick={e => e.stopPropagation()}>
        <h2 style={{ fontSize: 16, fontWeight: 800, marginBottom: 20, color: 'var(--clr-text)' }}>
          Create Teacher Account
        </h2>
        <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {[
            { id: 'cn-name', label: 'Full Name', field: 'full_name', type: 'text' },
            { id: 'cn-email', label: 'Email', field: 'email', type: 'email' },
            { id: 'cn-pw', label: 'Temporary Password', field: 'password', type: 'password' },
          ].map(({ id, label, field, type }) => (
            <div key={field}>
              <label htmlFor={id} style={{ display: 'block', fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--clr-text-3)', marginBottom: 5 }}>{label}</label>
              <input id={id} className="input" type={type} value={(form as any)[field]} required
                onChange={e => setForm(f => ({ ...f, [field]: e.target.value }))} style={{ width: '100%' }} />
            </div>
          ))}
          <div>
            <label style={{ display: 'block', fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--clr-text-3)', marginBottom: 5 }}>Role</label>
            <select className="input" value={form.role} onChange={e => setForm(f => ({ ...f, role: e.target.value }))} style={{ width: '100%' }}>
              <option value="TEACHER">Teacher</option>
              <option value="ADMIN">Admin</option>
            </select>
          </div>
          {error && <div style={{ color: 'var(--clr-error)', fontSize: 'var(--fs-xs)', display: 'flex', gap: 6 }}><AlertCircle size={13} />{error}</div>}
          <div style={{ display: 'flex', gap: 10, marginTop: 4 }}>
            <button type="submit" disabled={loading} className="btn btn-primary" style={{ flex: 1 }}>
              {loading ? 'Creating…' : 'Create Account'}
            </button>
            <button type="button" onClick={onClose} className="btn btn-ghost">Cancel</button>
          </div>
        </form>
      </div>
    </div>
  )
}
