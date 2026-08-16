import { api } from './client'

export interface BootstrapResponse {
  token: string | null
  is_cloud: boolean
  sync: { downloaded_files: number; deleted_local_files: number } | null
  lessons: unknown[]
  docs: unknown[]
}

export async function register(username: string, pin: string) {
  return api.post<{ status: string; username: string }>('/auth/register', { username, pin })
}

export async function accountExists(username: string) {
  return api.get<{ username: string; exists: boolean }>(`/auth/account-exists/${encodeURIComponent(username)}`)
}

export async function login(username: string, pin: string) {
  return api.post<{ status: string; username: string }>('/auth/login', { username, pin })
}

export async function unlock(passphrase: string) {
  return api.post<{ token: string }>('/auth/unlock', { passphrase })
}

export async function lock() {
  return api.post<{ status: string }>('/auth/lock')
}

export async function bootstrap(passphrase: string) {
  return api.post<BootstrapResponse>('/auth/bootstrap', { passphrase })
}
