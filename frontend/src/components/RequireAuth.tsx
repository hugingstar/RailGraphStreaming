import { Navigate, Outlet, useLocation } from 'react-router-dom'

import { useAuth } from '../lib/auth'

function Splash() {
  return <div className="auth-splash">불러오는 중…</div>
}

/** Gate: only signed-in users reach the nested routes (the dashboard). */
export function RequireAuth() {
  const { status } = useAuth()
  const location = useLocation()

  if (status === 'loading') return <Splash />
  if (status === 'anon') return <Navigate to="/login" replace state={{ from: location }} />
  return <Outlet />
}

/** Inverse gate: signed-in users skip past /login and /signup straight to the dashboard. */
export function GuestOnly() {
  const { status } = useAuth()

  if (status === 'loading') return <Splash />
  if (status === 'authed') return <Navigate to="/" replace />
  return <Outlet />
}
