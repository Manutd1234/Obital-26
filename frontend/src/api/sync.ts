import { api } from './client'

export interface SyncReport {
  status: string
  chat_sessions: number
  reward_sessions: number
  uploaded_files?: number
  downloaded_files?: number
  deleted_remote_files?: number
  deleted_local_files?: number
  registry_synced: boolean
}

export async function syncPush() {
  return api.post<SyncReport>('/sync/push')
}

export async function syncPull() {
  return api.post<SyncReport>('/sync/pull')
}
