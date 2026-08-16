import { create } from 'zustand'
import type { ChatSession, ChatMessage, ChatActivityItem } from '../api/chat'

interface ChatState {
  sessions: ChatSession[]
  activeSessionId: string | null
  messages: Record<string, ChatMessage[]>
  streamingContent: string
  streamingActivity: ChatActivityItem[]
  streamingSessionId: string | null
  isStreaming: boolean
  setSessions: (s: ChatSession[]) => void
  setActiveSession: (id: string | null) => void
  addSession: (s: ChatSession) => void
  removeSession: (sessionId: string) => void
  replaceSession: (tempSessionId: string, session: ChatSession) => void
  updateSession: (sessionId: string, patch: Partial<ChatSession>) => void
  setMessages: (sessionId: string, m: ChatMessage[]) => void
  appendMessage: (sessionId: string, m: ChatMessage) => void
  updateActivityItem: (sessionId: string, itemId: string, patch: Partial<ChatActivityItem>) => void
  setStreamingContent: (c: string) => void
  appendStreamingToken: (token: string) => void
  setStreamingActivity: (items: ChatActivityItem[]) => void
  setStreamingSession: (sessionId: string | null) => void
  setIsStreaming: (v: boolean) => void
}

function lessonArtifactKey(item: ChatActivityItem) {
  if (item.kind !== 'artifact' || item.artifact?.kind !== 'lesson') return null
  return item.artifact.job_id || item.artifact.lesson_id || item.detail || null
}

export const useChatStore = create<ChatState>((set) => ({
  sessions: [],
  activeSessionId: null,
  messages: {},
  streamingContent: '',
  streamingActivity: [],
  streamingSessionId: null,
  isStreaming: false,
  setSessions: (sessions) =>
    set((state) => {
      const serverSessionIds = new Set(sessions.map((session) => session.session_id))
      const pendingSessions = state.sessions.filter(
        (session) =>
          session.session_id.startsWith('pending_') &&
          !serverSessionIds.has(session.session_id),
      )
      return { sessions: [...pendingSessions, ...sessions] }
    }),
  setActiveSession: (id) => set({ activeSessionId: id }),
  addSession: (s) => set((state) => ({ sessions: [s, ...state.sessions] })),
  removeSession: (sessionId) =>
    set((state) => {
      const { [sessionId]: _removed, ...messages } = state.messages
      return {
        sessions: state.sessions.filter((session) => session.session_id !== sessionId),
        activeSessionId: state.activeSessionId === sessionId ? null : state.activeSessionId,
        messages,
      }
    }),
  replaceSession: (tempSessionId, session) =>
    set((state) => {
      const tempMessages = state.messages[tempSessionId]
      const { [tempSessionId]: _removed, ...messages } = state.messages
      return {
        sessions: state.sessions.map((item) =>
          item.session_id === tempSessionId ? session : item,
        ),
        activeSessionId: state.activeSessionId === tempSessionId ? session.session_id : state.activeSessionId,
        messages: tempMessages ? { ...messages, [session.session_id]: tempMessages } : messages,
      }
    }),
  updateSession: (sessionId, patch) =>
    set((state) => ({
      sessions: state.sessions.map((session) =>
        session.session_id === sessionId ? { ...session, ...patch } : session,
      ),
    })),
  setMessages: (sessionId, messages) =>
    set((state) => ({ messages: { ...state.messages, [sessionId]: messages } })),
  appendMessage: (sessionId, m) =>
    set((state) => {
      const existingMessages = state.messages[sessionId] || []
      const existingLessonArtifacts = new Set(
        existingMessages
          .flatMap((message) => message.activity || [])
          .map(lessonArtifactKey)
          .filter((key): key is string => Boolean(key)),
      )
      const activity = m.activity?.filter((item) => {
        const key = lessonArtifactKey(item)
        if (!key) return true
        if (existingLessonArtifacts.has(key)) return false
        existingLessonArtifacts.add(key)
        return true
      })
      if (m.activity && activity?.length === 0 && !m.content.trim()) {
        return state
      }
      const message = m.activity ? { ...m, activity } : m
      return {
        messages: {
          ...state.messages,
          [sessionId]: [...existingMessages, message],
        },
      }
    }),
  updateActivityItem: (sessionId, itemId, patch) =>
    set((state) => {
      const messages = state.messages[sessionId]
      if (!messages) return state
      return {
        messages: {
          ...state.messages,
          [sessionId]: messages.map((message) => {
            if (!message.activity?.some((item) => item.id === itemId)) return message
            return {
              ...message,
              activity: message.activity.map((item) =>
                item.id === itemId ? { ...item, ...patch } : item,
              ),
            }
          }),
        },
      }
    }),
  setStreamingContent: (c) => set({ streamingContent: c }),
  appendStreamingToken: (token) =>
    set((state) => ({ streamingContent: state.streamingContent + token })),
  setStreamingActivity: (items) => set({ streamingActivity: items }),
  setStreamingSession: (sessionId) => set({ streamingSessionId: sessionId }),
  setIsStreaming: (v) => set({ isStreaming: v }),
}))
