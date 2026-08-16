import { api } from './client'

export interface WorkspaceTask {
  id: string
  text: string
  done: boolean
}

export interface WorkspaceSession {
  id: string
  date: string // local day key 'YYYY-MM-DD'
  duration_seconds: number
  completed_at: string
  label?: string | null
}

export interface WorkspaceCode {
  js: string
  python: string
}

export interface WorkspaceData {
  code: WorkspaceCode
  tasks: Record<string, WorkspaceTask[]>
  sessions: WorkspaceSession[]
}

export const emptyWorkspace = (): WorkspaceData => ({
  code: { js: '', python: '' },
  tasks: {},
  sessions: [],
})

// account_id + X-Unlock-Token are attached automatically by the client wrapper.
export async function getWorkspace() {
  return api.get<WorkspaceData>('/workspace')
}

export async function putWorkspace(data: WorkspaceData) {
  return api.put<WorkspaceData>('/workspace', data)
}
