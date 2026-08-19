import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'

import App from './App'
import Login from './pages/Login'
import Signup from './pages/Signup'
import Account from './pages/Account'
import { RequireAuth, GuestOnly } from './components/RequireAuth'
import { AuthProvider } from './lib/auth'
import './styles.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route element={<GuestOnly />}>
            <Route path="/login" element={<Login />} />
            <Route path="/signup" element={<Signup />} />
          </Route>
          <Route element={<RequireAuth />}>
            <Route path="/" element={<App />} />
            <Route path="/account" element={<Account />} />
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
)
