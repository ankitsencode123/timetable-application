import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Eye, EyeOff } from 'lucide-react'
import { login } from '../api'
import { useAuthStore } from '../store'
import type { User } from '../types'

export default function LoginPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPw, setShowPw] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const loginStore = useAuthStore((s) => s.login)
  const navigate = useNavigate()

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const data = await login({ email, password })
      const user: User = {
        id: data.user?.id ?? 0,
        email,
        full_name: data.user?.full_name ?? email,
        role: (data.user?.role as User['role']) ?? 'TEACHER',
      }
      loginStore(user)
      navigate('/teacher')
    } catch (err: unknown) {
      setError((err as Error).message || 'Invalid credentials')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'var(--paper)',
      padding: 'var(--sp-4)',
    }}>
      <div style={{ width: '100%', maxWidth: 380 }}>

        {/* Branding */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 'var(--sp-5)' }}>
          <div className="header-logo-box">CT</div>
          <div className="header-logo-text">
            <div className="name">Courselab</div>
            <div className="sub">Timetable Studio</div>
          </div>
        </div>

        {/* Card */}
        <div className="card" style={{ padding: 'var(--sp-5)' }}>
          <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-xl)', fontWeight: 600, letterSpacing: '-0.015em', marginBottom: 'var(--sp-4)', color: 'var(--ink)' }}>
            Staff sign in
          </h1>

          {error && (
            <div style={{
              background: 'var(--violation-soft)',
              border: '1px solid color-mix(in oklab, var(--violation) 25%, transparent)',
              borderRadius: 'var(--radius)',
              padding: '9px 12px',
              fontSize: 'var(--fs-sm)',
              color: 'var(--violation)',
              marginBottom: 'var(--sp-4)',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
            }}>
              <span>⚠</span> {error}
            </div>
          )}

          <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>
            <div className="form-group">
              <label className="form-label">Email</label>
              <input
                type="email"
                className="form-control"
                placeholder="you@university.edu"
                value={email}
                onChange={e => setEmail(e.target.value)}
                required
                autoComplete="email"
              />
            </div>

            <div className="form-group">
              <label className="form-label">Password</label>
              <div className="input-group">
                <input
                  type={showPw ? 'text' : 'password'}
                  className="form-control"
                  placeholder="••••••••"
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  required
                  autoComplete="current-password"
                  style={{ paddingRight: 36 }}
                />
                <button
                  type="button"
                  className="input-icon-right btn-icon"
                  style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--ink-soft)' }}
                  onClick={() => setShowPw(s => !s)}
                >
                  {showPw ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
            </div>

            <button
              type="submit"
              className="btn btn-primary btn-lg"
              disabled={loading}
              style={{ justifyContent: 'center', marginTop: 4 }}
            >
              {loading ? (
                <>
                  <span className="spinner spinner-sm" />
                  Signing in…
                </>
              ) : 'Sign in'}
            </button>
          </form>
        </div>

        <div style={{ textAlign: 'center', marginTop: 'var(--sp-4)' }}>
          <a href="/" style={{ fontSize: 'var(--fs-xs)', color: 'var(--ink-soft)' }}>
            ← View published timetable
          </a>
        </div>
      </div>
    </div>
  )
}
