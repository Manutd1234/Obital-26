import { BrowserRouter } from 'react-router-dom'
import { useAuthStore } from './stores/authStore'
import { LoginOverlay } from './components/auth/LoginOverlay'
import { AppShell } from './components/layout/AppShell'

export default function App() {
  const isUnlocked = useAuthStore((s) => s.isUnlocked)

  return (
    <BrowserRouter>
      {isUnlocked ? <AppShell /> : <LoginOverlay />}
    </BrowserRouter>
  )
}
