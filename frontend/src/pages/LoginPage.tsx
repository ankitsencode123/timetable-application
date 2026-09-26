import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Calendar, Lock, Mail, Loader2, Eye, EyeOff } from 'lucide-react'
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
      // Backend returns access_token + user info
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
      minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'radial-gradient(ellipse at 30% 40%, rgba(59,130,246,0.05) 0%, transparent 60%), var(--clr-bg)',
      padding: 'var(--sp-4)',
    }}>
      <div style={{ width: '100%', maxWidth: 380 }}>
        {/* Logo */}
        <div style={{ textAlign: 'center', marginBottom: 'var(--sp-8)' }}>
          <div style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 52, height: 52, background: 'var(--clr-primary-20)', borderRadius: 'var(--radius-xl)', marginBottom: 'var(--sp-3)' }}>
            <Calendar size={26} style={{ color: 'var(--clr-primary)' }} />
          </div>
          <h1 style={{ fontSize: 'var(--fs-2xl)', fontWeight: 800, background: 'linear-gradient(135deg, var(--clr-primary), var(--clr-purple))', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
            TimeRAG
          </h1>
          <p style={{ color: 'var(--clr-text-3)', marginTop: 6, fontSize: 'var(--fs-sm)' }}>
            Teacher / Admin Portal
          </p>
        </div>

        {/* Card */}
        <div className="card" style={{ padding: 'var(--sp-6)' }}>
          <h2 style={{ fontSize: 'var(--fs-xl)', fontWeight: 700, marginBottom: 'var(--sp-5)' }}>Sign in</h2>

          {error && (
            <div style={{ background: 'var(--clr-error-bg)', border: '1px solid rgba(239,68,68,0.3)', borderRadius: 'var(--radius)', padding: '10px 14px', fontSize: 'var(--fs-sm)', color: 'var(--clr-error)', marginBottom: 'var(--sp-4)', display: 'flex', alignItems: 'center', gap: 8 }}>
              <span>⚠</span> {error}
            </div>
          )}

          <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>
            <div className="form-group">
              <label className="form-label">Email</label>
              <div className="input-group">
                <Mail size={14} className="input-icon" />
                <input
                  type="email"
                  className="form-control"
                  placeholder="you@example.com"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  required
                  autoComplete="email"
                />
              </div>
            </div>

            <div className="form-group">
              <label className="form-label">Password</label>
              <div className="input-group">
                <Lock size={14} className="input-icon" />
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
                <button type="button" className="input-icon-right" style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--clr-text-3)' }} onClick={() => setShowPw(s => !s)}>
                  {showPw ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
            </div>

            <button type="submit" className="btn btn-primary btn-lg" disabled={loading} style={{ marginTop: 4, justifyContent: 'center' }}>
              {loading ? <Loader2 size={16} style={{ animation: 'spin 0.7s linear infinite' }} /> : null}
              {loading ? 'Signing in…' : 'Sign in'}
            </button>
          </form>
        </div>

        <div style={{ textAlign: 'center', marginTop: 'var(--sp-4)' }}>
          <a href="/" style={{ fontSize: 'var(--fs-sm)', color: 'var(--clr-text-3)' }}>← Back to Public Timetable</a>
        </div>
      </div>
    </div>
  )
}
