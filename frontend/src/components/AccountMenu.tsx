import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { useAuth } from '../lib/auth'

export default function AccountMenu() {
  const { user, logout } = useAuth()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [open])

  if (!user) return null
  const initial = user.display_name.trim().charAt(0).toUpperCase() || '?'

  return (
    <div className="account-menu" ref={ref}>
      <button className="account-menu-btn" onClick={() => setOpen((v) => !v)}>
        <span className="account-avatar">{initial}</span>
        {user.display_name}
      </button>
      {open && (
        <div className="account-menu-list">
          <div className="account-menu-email">{user.email}</div>
          <Link to="/account" onClick={() => setOpen(false)}>회원정보 수정</Link>
          <button onClick={() => logout()}>로그아웃</button>
        </div>
      )}
    </div>
  )
}
