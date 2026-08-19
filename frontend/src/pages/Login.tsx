import { useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'

import { useAuth } from '../lib/auth'

export default function Login() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation() as { state?: { from?: { pathname: string } } }
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await login(username, password)
      navigate(location.state?.from?.pathname ?? '/', { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : '로그인에 실패했습니다')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-page">
      <form className="auth-card" onSubmit={submit}>
        <h1 className="auth-title">RailGraph</h1>
        <p className="auth-sub">로그인하고 실시간 열차 위치 확률 지도를 확인하세요</p>

        <label className="auth-field">
          <span>아이디</span>
          <input required autoComplete="username" value={username}
                 onChange={(e) => setUsername(e.target.value)} />
        </label>
        <label className="auth-field">
          <span>비밀번호</span>
          <input type="password" required autoComplete="current-password" value={password}
                 onChange={(e) => setPassword(e.target.value)} />
        </label>

        {error && <p className="auth-error">{error}</p>}

        <button className="auth-submit" type="submit" disabled={busy}>
          {busy ? '로그인 중…' : '로그인'}
        </button>

        <p className="auth-switch">
          계정이 없으신가요? <Link to="/signup">회원가입</Link>
        </p>
      </form>
    </div>
  )
}
