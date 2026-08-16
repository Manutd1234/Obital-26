import { api } from './client'

export async function sendHeartbeat(status: 'online' | 'offline' = 'online') {
  return api.post<{ status: string }>('/presence/heartbeat', { status })
}
