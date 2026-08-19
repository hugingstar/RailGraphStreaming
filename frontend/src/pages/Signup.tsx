import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { useAuth } from '../lib/auth'

const USERNAME_RE = /^[a-zA-Z0-9_]{4,20}$/

export default function Signup() {
  const { signup } = useAuth()
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [email, setEmail] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    if (!USERNAME_RE.test(username)) {
      setError('아이디는 영문/숫자/밑줄로 4~20자여야 합니다')
      return
    }
    if (password.length < 8) {
      setError('비밀번호는 8자 이상이어야 합니다')
      return
    }
    if (password !== confirm) {
      setError('비밀번호가 일치하지 않습니다')
      return
    }
    setBusy(true)
    try {
      await signup(username, email, password, displayName)
      navigate('/', { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : '회원가입에 실패했습니다')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-page">
      <form className="auth-card" onSubmit={submit}>
        <h1 className="auth-title">RailGraph</h1>
        <p className="auth-sub">새 계정을 만듭니다</p>

        <label className="auth-field">
          <span>아이디</span>
          <input required autoComplete="username" value={username}
                 onChange={(e) => setUsername(e.target.value)} />
        </label>
        <label className="auth-field">
          <span>비밀번호</span>
          <input type="password" required autoComplete="new-password" value={password}
                 onChange={(e) => setPassword(e.target.value)} />
        </label>
        <label className="auth-field">
          <span>비밀번호 확인</span>
          <input type="password" required autoComplete="new-password" value={confirm}
                 onChange={(e) => setConfirm(e.target.value)} />
        </label>
        <label className="auth-field">
          <span>이름</span>
          <input required autoComplete="nickname" value={displayName}
                 onChange={(e) => setDisplayName(e.target.value)} />
        </label>
        <label className="auth-field">
          <span>이메일</span>
          <input type="email" required autoComplete="email" value={email}
                 onChange={(e) => setEmail(e.target.value)} />
        </label>

        {error && <p className="auth-error">{error}</p>}

        <button className="auth-submit" type="submit" disabled={busy}>
          {busy ? '가입하는 중…' : '회원가입'}
        </button>

        <p className="auth-switch">
          이미 계정이 있으신가요? <Link to="/login">로그인</Link>
        </p>
      </form>
    </div>
  )
}
