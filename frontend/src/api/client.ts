import { broadcastAuthInvalidated, useAuthStore } from '../stores/authStore'
import { DEV_NO_AUTH, mockApi } from '../lib/devMock'

const BASE = ''

// Account identity is read from the in-memory auth store on every request so
// switching accounts cannot reuse a URL assembled for the previous user.
function currentAccountId(): string {
  const { username } = useAuthStore.getState()
  return (username || 'admin').trim().toLowerCase()
}

export function withAccount(path: string): string {
  const accountId = currentAccountId()
  const separator = path.includes('?') ? '&' : '?'
  return `${path}${separator}account_id=${encodeURIComponent(accountId)}`
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  if (DEV_NO_AUTH) return mockApi<T>(path, options.method ?? 'GET', options.body ?? null)
  const { unlockToken, clientUUID } = useAuthStore.getState()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> || {}),
  }
  if (unlockToken) headers['X-Unlock-Token'] = unlockToken
  if (clientUUID) headers['X-Client-Id'] = clientUUID

  const res = await fetch(`${BASE}${withAccount(path)}`, { ...options, headers })
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    if (res.status === 401 || (res.status === 409 && body.includes('ACCOUNT_KEY_MISMATCH'))) {
      useAuthStore.getState().invalidateUnlock()
      broadcastAuthInvalidated()
    }
    throw new Error(`API ${res.status}: ${body}`)
  }
  const contentType = res.headers.get('content-type') || ''
  if (!contentType.includes('application/json')) {
    const body = await res.text().catch(() => '')
    const preview = body.trim().slice(0, 80)
    throw new Error(`API returned ${contentType || 'non-JSON response'}: ${preview}`)
  }
  return res.json()
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PUT', body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PATCH', body: body ? JSON.stringify(body) : undefined }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}
