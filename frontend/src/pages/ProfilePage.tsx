import { useState, useEffect } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { User, Lock, CheckCircle, AlertCircle, ArrowLeft } from 'lucide-react'
import { useAuthStore } from '../store'
import { getMyProfile, updateMyProfile, changePassword } from '../api'

type Tab = 'profile' | 'password'

export default function ProfilePage() {
  const [params] = useSearchParams()
  const [tab, setTab] = useState<Tab>(params.get('tab') as Tab ?? 'profile')
  const { user, updateUser } = useAuthStore()
  const navigate = useNavigate()

  return (
    <div style={{ minHeight: '100vh', background: 'var(--clr-bg)', padding: 24 }}>
      <div style={{ maxWidth: 680, margin: '0 auto' }}>
        {/* Header */}
        <button
          onClick={() => navigate('/teacher')}
          style={{ display: 'flex', alignItems: 'center', gap: 6, background: 'none', border: 'none', color: 'var(--clr-text-3)', cursor: 'pointer', marginBottom: 20, fontSize: 'var(--fs-sm)' }}
        >
          <ArrowLeft size={14} /> Back to Workspace
        </button>
        <h1 style={{ fontSize: 22, fontWeight: 800, color: 'var(--clr-text)', marginBottom: 24 }}>
          My Account
        </h1>

        {/* Tabs */}
        <div style={{ display: 'flex', gap: 4, marginBottom: 24, background: 'var(--clr-bg-2)', padding: 4, borderRadius: 'var(--radius)', width: 'fit-content' }}>
          {([['profile', 'Profile', <User size={14} />], ['password', 'Change Password', <Lock size={14} />]] as const).map(([value, label, icon]) => (
            <button key={value} onClick={() => setTab(value as Tab)}
              style={{
                display: 'flex', alignItems: 'center', gap: 6, padding: '7px 16px',
                borderRadius: 'var(--radius)', border: 'none', cursor: 'pointer',
                fontSize: 'var(--fs-sm)', fontWeight: tab === value ? 700 : 400,
                background: tab === value ? 'var(--clr-bg-4)' : 'none',
                color: tab === value ? 'var(--clr-text)' : 'var(--clr-text-3)',
              }}>
              {icon} {label}
            </button>
          ))}
        </div>

        {tab === 'profile' && <ProfileTab user={user} onUpdate={updateUser} />}
        {tab === 'password' && <PasswordTab />}
      </div>
    </div>
  )
}

function ProfileTab({ user, onUpdate }: { user: any; onUpdate: (u: any) => void }) {
  const [name, setName] = useState(user?.full_name ?? '')
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)
  const [profile, setProfile] = useState<any>(null)

  useEffect(() => {
    getMyProfile().then(setProfile).catch(() => {})
  }, [])

  async function save() {
    setLoading(true); setMsg(null)
    try {
      const updated = await updateMyProfile(name)
      onUpdate({ full_name: name })
      setMsg({ type: 'ok', text: 'Name updated successfully.' })
    } catch (e: any) {
      setMsg({ type: 'err', text: e.message })
    } finally { setLoading(false) }
  }

  return (
    <div className="card" style={{ padding: 28 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20, marginBottom: 20 }}>
        <InfoField label="Email" value={user?.email ?? '—'} />
        <InfoField label="Role" value={
          <span className={`badge ${user?.role === 'ADMIN' ? 'badge-purple' : 'badge-blue'}`}>{user?.role}</span>
        } />
        <InfoField label="Account Status" value={
          <span className={`badge ${user?.is_active ? 'badge-green' : 'badge-red'}`}>{user?.is_active ? 'Active' : 'Disabled'}</span>
        } />
        <InfoField label="Last Login" value={
          profile?.last_login
            ? new Date(profile.last_login).toLocaleString()
            : 'No previous login'
        } />
      </div>

      <div style={{ borderTop: '1px solid var(--clr-border)', paddingTop: 20 }}>
        <label style={{ display: 'block', fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--clr-text-3)', marginBottom: 6 }}>
          DISPLAY NAME
        </label>
        <div style={{ display: 'flex', gap: 10 }}>
          <input
            value={name} onChange={e => setName(e.target.value)}
            className="input" style={{ flex: 1 }}
            placeholder="Your full name"
          />
          <button onClick={save} disabled={loading} className="btn btn-primary">
            {loading ? 'Saving…' : 'Save'}
          </button>
        </div>
        {msg && (
          <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 6, color: msg.type === 'ok' ? 'var(--clr-success)' : 'var(--clr-error)', fontSize: 'var(--fs-xs)' }}>
            {msg.type === 'ok' ? <CheckCircle size={13} /> : <AlertCircle size={13} />}
            {msg.text}
          </div>
        )}
      </div>
    </div>
  )
}

function InfoField({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div style={{ fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--clr-text-3)', marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--clr-text)' }}>{value}</div>
    </div>
  )
}

function PasswordTab() {
  const [state, setState] = useState({ old: '', new1: '', new2: '' })
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)
  const navigate = useNavigate()
  const { logout } = useAuthStore()

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setMsg(null)
    if (state.new1 !== state.new2) { setMsg({ type: 'err', text: 'New passwords do not match.' }); return }
    if (state.new1.length < 8) { setMsg({ type: 'err', text: 'Password must be at least 8 characters.' }); return }
    setLoading(true)
    try {
      await changePassword(state.old, state.new1)
      setMsg({ type: 'ok', text: 'Password changed. All sessions revoked. Please log in again.' })
      setTimeout(() => { logout(); navigate('/login') }, 2200)
    } catch (e: any) {
      setMsg({ type: 'err', text: e.message })
    } finally { setLoading(false) }
  }

  return (
    <div className="card" style={{ padding: 28 }}>
      <p style={{ fontSize: 'var(--fs-sm)', color: 'var(--clr-text-3)', marginBottom: 20 }}>
        Changing your password will log you out of all active sessions.
      </p>
      <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <FormField label="Current Password" value={state.old} onChange={v => setState(s => ({ ...s, old: v }))} type="password" id="current-password" />
        <FormField label="New Password" value={state.new1} onChange={v => setState(s => ({ ...s, new1: v }))} type="password" id="new-password" hint="At least 8 characters" />
        <FormField label="Confirm New Password" value={state.new2} onChange={v => setState(s => ({ ...s, new2: v }))} type="password" id="confirm-password" />
        {msg && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: msg.type === 'ok' ? 'var(--clr-success)' : 'var(--clr-error)', fontSize: 'var(--fs-sm)' }}>
            {msg.type === 'ok' ? <CheckCircle size={14} /> : <AlertCircle size={14} />}
            {msg.text}
          </div>
        )}
        <button type="submit" disabled={loading} className="btn btn-primary" style={{ alignSelf: 'flex-start' }}>
          {loading ? 'Changing…' : 'Change Password'}
        </button>
      </form>
    </div>
  )
}

function FormField({ label, value, onChange, type, id, hint }: {
  label: string; value: string; onChange: (v: string) => void;
  type?: string; id: string; hint?: string
}) {
  return (
    <div>
      <label htmlFor={id} style={{ display: 'block', fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--clr-text-3)', marginBottom: 6 }}>{label}</label>
      <input id={id} className="input" type={type ?? 'text'} value={value} onChange={e => onChange(e.target.value)} style={{ width: '100%' }} required />
      {hint && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--clr-text-3)', marginTop: 4 }}>{hint}</div>}
    </div>
  )
}
