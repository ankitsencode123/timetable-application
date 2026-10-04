import { useState, useRef, useCallback } from 'react'
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

  // refs for shake animation
  const emailWrapRef = useRef<HTMLDivElement>(null)
  const pwWrapRef = useRef<HTMLDivElement>(null)

  const shakeInput = useCallback((ref: React.RefObject<HTMLDivElement | null>) => {
    const el = ref.current?.querySelector('.t-input') as HTMLElement | null
    if (!el) return
    el.classList.remove('is-shaking')
    void el.offsetWidth // force reflow
    el.classList.add('is-shaking')
    el.addEventListener('animationend', () => el.classList.remove('is-shaking'), { once: true })
  }, [])

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
      // Shake both fields on login failure
      shakeInput(emailWrapRef)
      shakeInput(pwWrapRef)
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

            {/* Email field with shake */}
            <div className={`form-group t-input-wrap${error ? ' is-error' : ''}`} ref={emailWrapRef}>
              <label className="form-label">Email</label>
              <div className={`t-input${error ? ' is-error' : ''}`} style={{ background: 'none', padding: 0, border: 'none', transition: 'none' }}>
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
            </div>

            {/* Password field with icon swap + shake */}
            <div className={`form-group t-input-wrap${error ? ' is-error' : ''}`} ref={pwWrapRef}>
              <label className="form-label">Password</label>
              <div className={`t-input${error ? ' is-error' : ''}`} style={{ background: 'none', padding: 0, border: 'none', transition: 'none' }}>
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
                  {/* Icon swap: eye ↔ eye-off */}
                  <button
                    type="button"
                    className="input-icon-right btn-icon t-icon-swap"
                    data-state={showPw ? 'b' : 'a'}
                    style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--ink-soft)' }}
                    onClick={() => setShowPw(s => !s)}
                    aria-label={showPw ? 'Hide password' : 'Show password'}
                  >
                    <span className="t-icon" data-icon="a"><Eye size={14} /></span>
                    <span className="t-icon" data-icon="b"><EyeOff size={14} /></span>
                  </button>
                </div>
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
