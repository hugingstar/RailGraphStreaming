import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'

export interface User {
  id: string
  username: string
  email: string
  display_name: string
  created_at: string
}

type Status = 'loading' | 'authed' | 'anon'

interface AuthState {
  user: User | null
  status: Status
  login: (username: string, password: string) => Promise<void>
  signup: (username: string, email: string, password: string, displayName: string) => Promise<void>
  logout: () => Promise<void>
  updateAccount: (patch: { displayName?: string; currentPassword?: string; newPassword?: string }) => Promise<void>
  deleteAccount: (password: string) => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

// Status-code -> user-facing fallback, used only when the server didn't send
// a specific `detail` (FastAPI's HTTPException always does; a bare 404/405
// from routing, or an upstream proxy/5xx, generally won't).
const STATUS_FALLBACK: Record<number, string> = {
  400: '요청이 올바르지 않습니다.',
  401: '로그인이 필요합니다.',
  403: '권한이 없습니다.',
  404: '요청한 항목을 찾을 수 없습니다.',
  405: '서버가 이 요청을 지원하지 않습니다. 서버가 최신 상태인지 확인해주세요.',
  408: '요청 시간이 초과되었습니다. 다시 시도해주세요.',
  409: '이미 존재하는 요청입니다.',
  422: '입력값을 확인해주세요.',
  429: '요청이 너무 많습니다. 잠시 후 다시 시도해주세요.',
  500: '서버에 문제가 발생했습니다. 잠시 후 다시 시도해주세요.',
  502: '서버에 문제가 발생했습니다. 잠시 후 다시 시도해주세요.',
  503: '서비스를 일시적으로 사용할 수 없습니다.',
  504: '서버 응답이 지연되고 있습니다. 잠시 후 다시 시도해주세요.',
}

// FastAPI's own validation errors put `detail` as an array of {msg, ...}
// objects rather than a string; unwrap that so it doesn't print as
// "[object Object]" in the UI.
function extractDetail(body: unknown): string | null {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const msgs = detail.map((e) => (e as { msg?: string })?.msg).filter((m): m is string => !!m)
    if (msgs.length) return msgs.join(', ')
  }
  return null
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, {
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
  } catch {
    // fetch only throws on network failure (offline, DNS, CORS, refused) --
    // an HTTP error status resolves normally and is handled below.
    throw new Error('서버에 연결할 수 없습니다. 네트워크 상태를 확인해주세요.')
  }
  const body = res.status === 204 ? null : await res.json().catch(() => null)
  if (!res.ok) {
    const fallback = STATUS_FALLBACK[res.status] ?? `요청이 실패했습니다 (${res.status})`
    throw new Error(extractDetail(body) ?? fallback)
  }
  return body as T
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [status, setStatus] = useState<Status>('loading')

  useEffect(() => {
    call<User>('/api/auth/me')
      .then((u) => { setUser(u); setStatus('authed') })
      .catch(() => { setUser(null); setStatus('anon') })
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    const u = await call<User>('/api/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) })
    setUser(u)
    setStatus('authed')
  }, [])

  const signup = useCallback(async (username: string, email: string, password: string, displayName: string) => {
    const u = await call<User>('/api/auth/signup', {
      method: 'POST',
      body: JSON.stringify({ username, email, password, display_name: displayName }),
    })
    setUser(u)
    setStatus('authed')
  }, [])

  const logout = useCallback(async () => {
    await call('/api/auth/logout', { method: 'POST' })
    setUser(null)
    setStatus('anon')
  }, [])

  const updateAccount = useCallback(
    async (patch: { displayName?: string; currentPassword?: string; newPassword?: string }) => {
      const u = await call<User>('/api/auth/me', {
        method: 'PATCH',
        body: JSON.stringify({
          display_name: patch.displayName,
          current_password: patch.currentPassword,
          new_password: patch.newPassword,
        }),
      })
      setUser(u)
    },
    [],
  )

  const deleteAccount = useCallback(async (password: string) => {
    await call('/api/auth/me', { method: 'DELETE', body: JSON.stringify({ password }) })
    setUser(null)
    setStatus('anon')
  }, [])

  return (
    <AuthContext.Provider value={{ user, status, login, signup, logout, updateAccount, deleteAccount }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
