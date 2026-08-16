import { create } from 'zustand'
import { DEV_NO_AUTH } from '../lib/devMock'

interface AuthState {
  clientUUID: string
  username: string
  unlockToken: string
  isUnlocked: boolean
  isLocked: boolean
  setUsername: (u: string) => void
  setUnlockToken: (t: string) => void
  unlock: (token: string) => void
  invalidateUnlock: () => void
  lock: () => void
  logout: () => void
}

function getOrCreateClientUUID(): string {
  let id = sessionStorage.getItem('obital_client_uuid')
  if (!id) {
    id = crypto.randomUUID()
    sessionStorage.setItem('obital_client_uuid', id)
  }
  return id
}

export const useAuthStore = create<AuthState>((set) => ({
  clientUUID: getOrCreateClientUUID(),
  username: DEV_NO_AUTH ? 'dev' : localStorage.getItem('obital_username') || '',
  unlockToken: DEV_NO_AUTH ? 'dev-token' : sessionStorage.getItem('obital_unlock_token') || '',
  isUnlocked: DEV_NO_AUTH || !!sessionStorage.getItem('obital_unlock_token'),
  isLocked: false,
  setUsername: (u) => {
    const username = u.trim()
    if (username) localStorage.setItem('obital_username', username)
    else localStorage.removeItem('obital_username')
    set({ username })
  },
  setUnlockToken: (t) => {
    sessionStorage.setItem('obital_unlock_token', t)
    set({ unlockToken: t })
  },
  unlock: (token) => {
    sessionStorage.setItem('obital_unlock_token', token)
    set({ unlockToken: token, isUnlocked: true })
  },
  invalidateUnlock: () => {
    sessionStorage.removeItem('obital_unlock_token')
    set({ unlockToken: '', isUnlocked: false, isLocked: true })
  },
  lock: () => {
    sessionStorage.removeItem('obital_unlock_token')
    localStorage.removeItem('obital_username')
    set({ unlockToken: '', isUnlocked: false, username: '' })
  },
  logout: () => {
    sessionStorage.removeItem('obital_unlock_token')
    localStorage.removeItem('obital_username')
    set({ unlockToken: '', isUnlocked: false, username: '' })
  },
}))

const authChannel = typeof BroadcastChannel !== 'undefined'
  ? new BroadcastChannel('obital-auth')
  : null

export function broadcastAuthInvalidated(): void {
  authChannel?.postMessage({ type: 'unlock-invalidated' })
}

authChannel?.addEventListener('message', (event) => {
  if (event.data?.type === 'unlock-invalidated') {
    useAuthStore.getState().invalidateUnlock()
  }
})
