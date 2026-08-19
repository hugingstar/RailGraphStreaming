import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { useAuth } from '../lib/auth'

export default function Account() {
  const { user, updateAccount, deleteAccount, logout } = useAuth()
  const navigate = useNavigate()

  const [displayName, setDisplayName] = useState(user?.display_name ?? '')
  const [profileMsg, setProfileMsg] = useState<string | null>(null)
  const [profileErr, setProfileErr] = useState<string | null>(null)
  const [profileBusy, setProfileBusy] = useState(false)

  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [pwMsg, setPwMsg] = useState<string | null>(null)
  const [pwErr, setPwErr] = useState<string | null>(null)
  const [pwBusy, setPwBusy] = useState(false)

  const [deletePassword, setDeletePassword] = useState('')
  const [deleteErr, setDeleteErr] = useState<string | null>(null)
  const [deleteBusy, setDeleteBusy] = useState(false)
  const [confirming, setConfirming] = useState(false)

  if (!user) return null

  const saveProfile = async (e: FormEvent) => {
    e.preventDefault()
    setProfileErr(null)
    setProfileMsg(null)
    setProfileBusy(true)
    try {
      await updateAccount({ displayName })
      setProfileMsg('저장되었습니다')
    } catch (err) {
      setProfileErr(err instanceof Error ? err.message : '저장에 실패했습니다')
    } finally {
      setProfileBusy(false)
    }
  }

  const changePassword = async (e: FormEvent) => {
    e.preventDefault()
    setPwErr(null)
    setPwMsg(null)
    if (newPassword.length < 8) {
      setPwErr('새 비밀번호는 8자 이상이어야 합니다')
      return
    }
    setPwBusy(true)
    try {
      await updateAccount({ currentPassword, newPassword })
      setPwMsg('비밀번호가 변경되었습니다')
      setCurrentPassword('')
      setNewPassword('')
    } catch (err) {
      setPwErr(err instanceof Error ? err.message : '변경에 실패했습니다')
    } finally {
      setPwBusy(false)
    }
  }

  const doDelete = async (e: FormEvent) => {
    e.preventDefault()
    setDeleteErr(null)
    setDeleteBusy(true)
    try {
      await deleteAccount(deletePassword)
      navigate('/login', { replace: true })
    } catch (err) {
      setDeleteErr(err instanceof Error ? err.message : '탈퇴에 실패했습니다')
      setDeleteBusy(false)
    }
  }

  return (
    <div className="auth-page">
      <div className="account-card">
        <div className="account-head">
          <Link to="/" className="account-back">← 대시보드</Link>
          <button className="account-logout" onClick={() => logout()}>로그아웃</button>
        </div>

        <h1 className="auth-title">회원정보</h1>
        <p className="auth-sub">{user.username} · {user.email}</p>

        <section className="account-section">
          <h2>프로필</h2>
          <form onSubmit={saveProfile}>
            <label className="auth-field">
              <span>이름</span>
              <input required value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
            </label>
            {profileErr && <p className="auth-error">{profileErr}</p>}
            {profileMsg && <p className="auth-ok">{profileMsg}</p>}
            <button className="auth-submit" type="submit" disabled={profileBusy}>
              {profileBusy ? '저장 중…' : '저장'}
            </button>
          </form>
        </section>

        <section className="account-section">
          <h2>비밀번호 변경</h2>
          <form onSubmit={changePassword}>
            <label className="auth-field">
              <span>현재 비밀번호</span>
              <input type="password" required autoComplete="current-password"
                     value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)} />
            </label>
            <label className="auth-field">
              <span>새 비밀번호</span>
              <input type="password" required autoComplete="new-password"
                     value={newPassword} onChange={(e) => setNewPassword(e.target.value)} />
            </label>
            {pwErr && <p className="auth-error">{pwErr}</p>}
            {pwMsg && <p className="auth-ok">{pwMsg}</p>}
            <button className="auth-submit" type="submit" disabled={pwBusy}>
              {pwBusy ? '변경 중…' : '비밀번호 변경'}
            </button>
          </form>
        </section>

        <section className="account-section account-danger">
          <h2>회원 탈퇴</h2>
          <p className="auth-sub">탈퇴하면 계정과 세션이 즉시 비활성화됩니다.</p>
          {!confirming ? (
            <button className="account-danger-btn" onClick={() => setConfirming(true)}>탈퇴하기</button>
          ) : (
            <form onSubmit={doDelete}>
              <label className="auth-field">
                <span>비밀번호 확인</span>
                <input type="password" required autoComplete="current-password"
                       value={deletePassword} onChange={(e) => setDeletePassword(e.target.value)} />
              </label>
              {deleteErr && <p className="auth-error">{deleteErr}</p>}
              <div className="account-danger-actions">
                <button type="button" className="account-cancel" onClick={() => setConfirming(false)}>
                  취소
                </button>
                <button type="submit" className="account-danger-btn" disabled={deleteBusy}>
                  {deleteBusy ? '탈퇴하는 중…' : '정말 탈퇴합니다'}
                </button>
              </div>
            </form>
          )}
        </section>
      </div>
    </div>
  )
}
