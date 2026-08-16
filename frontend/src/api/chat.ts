import { api } from './client'

export interface ChatSession {
  session_id: string
  title?: string
  created_at?: string
}

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
  activity?: ChatActivityItem[]
}

interface BackendChatMessage {
  role: 'user' | 'assistant' | 'system'
  text: string
  created_at?: string
}

export type ChatActivityKind = 'thinking' | 'tool' | 'confirmation' | 'error' | 'plan' | 'subagent' | 'artifact'

export interface ChatActivityItem {
  id: string
  kind: ChatActivityKind
  title: string
  status?: 'running' | 'done' | 'error' | 'waiting'
  detail?: string
  confirmation?: {
    session_id: string
    action_id: string
    tool_name?: string
  }
  artifact?: {
    kind: 'lesson'
    action?: string
    lesson_id?: string
    title?: string
    job_id?: string
  }
}

export interface ConfirmActionResponse {
  session_id: string
  action_id: string
  status: string
  reply: string
}

export async function createSession(lessonId?: string) {
  const body: Record<string, unknown> = {}
  if (lessonId) body.lesson_id = lessonId
  return api.post<{ session_id: string; title?: string }>('/chat/sessions', body)
}

export async function listSessions() {
  const res = await api.get<{ id: string; title?: string; created_at?: string; updated_at?: string }[]>(
    '/chat/sessions',
  )
  return res.map((s) => ({ session_id: s.id, title: s.title, created_at: s.created_at }))
}

export async function getSession(sessionId: string) {
  const res = await api.get<{ id: string; title?: string; messages: BackendChatMessage[] }>(
    `/chat/sessions/${sessionId}`,
  )
  return {
    session_id: res.id,
    title: res.title,
    messages: res.messages.map((m) => ({ role: m.role, content: m.text })),
  }
}

export async function confirmAction(sessionId: string, actionId: string, decision: 'approve' | 'cancel') {
  return api.post<ConfirmActionResponse>(`/chat/sessions/${sessionId}/confirm`, {
    action_id: actionId,
    decision,
  })
}
