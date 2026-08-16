import { useCallback, useRef } from 'react'
import { readSSE } from '../lib/sse'
import { useChatStore } from '../stores/chatStore'
import { useAuthStore } from '../stores/authStore'
import { withAccount } from '../api/client'
import { createSession, type ChatActivityItem } from '../api/chat'
import { listLessons } from '../api/lessons'
import { useLessonStore } from '../stores/lessonStore'
import { useUIStore } from '../stores/uiStore'

function formatEventData(data: unknown): string {
  if (typeof data === 'string') return data
  if (data == null) return ''
  try {
    return JSON.stringify(data, null, 2)
  } catch {
    return String(data)
  }
}

function activityTitleFromToolPayload(data: unknown, fallback: string) {
  if (data && typeof data === 'object') {
    const payload = data as Record<string, unknown>
    return String(payload.label || payload.name || fallback)
  }
  return fallback
}

function normalizeActivityStatus(status: unknown): ChatActivityItem['status'] {
  if (status === 'failed' || status === 'error') return 'error'
  if (status === 'succeeded' || status === 'done') return 'done'
  if (status === 'waiting' || status === 'queued') return 'waiting'
  return 'running'
}

function titleFromFirstInput(text: string, maxLen = 48): string {
  const title = text.trim().replace(/\s+/g, ' ')
  if (title.length <= maxLen) return title
  const trimmed = title.slice(0, maxLen).replace(/\s+\S*$/, '').trim()
  return trimmed || title.slice(0, maxLen).trim()
}

export function useChatStream() {
  const abortRef = useRef<AbortController | null>(null)
  const streamFlushRef = useRef<number | null>(null)

  const sendMessage = useCallback(
    async (sessionId: string, content: string, selectedChunkIds?: string[], queryVector?: number[]) => {
      const { unlockToken, clientUUID } = useAuthStore.getState()
      const {
        appendMessage,
        replaceSession,
        setStreamingContent,
        setStreamingActivity,
        setStreamingSession,
        setIsStreaming,
        updateSession,
        sessions,
      } =
        useChatStore.getState()

      const controller = new AbortController()
      abortRef.current = controller
      let activeSessionId = sessionId

      if (sessionId.startsWith('pending_')) {
        try {
          const res = await createSession()
          activeSessionId = res.session_id
          replaceSession(sessionId, { session_id: activeSessionId, title: res.title || '' })
        } catch (err) {
          console.error('Failed to create session:', err)
          useUIStore.getState().showToast('Could not start chat')
          abortRef.current = null
          return
        }
      }

      // Append user message immediately
      appendMessage(activeSessionId, { role: 'user', content: content })
      const session = sessions.find((item) => item.session_id === sessionId)
      if (session && (!session.title || session.title === 'New chat' || sessionId.startsWith('pending_'))) {
        updateSession(activeSessionId, { title: titleFromFirstInput(content) })
      }

      setIsStreaming(true)
      setStreamingSession(activeSessionId)
      setStreamingContent('')
      setStreamingActivity([])

      const url = withAccount(`/chat/sessions/${activeSessionId}/messages/stream`)
      const body: Record<string, unknown> = { message: content }
      if (selectedChunkIds && selectedChunkIds.length > 0) {
        body.doc_chunk_ids = selectedChunkIds
      }
      if (queryVector && queryVector.length > 0) {
        body.query_vector = queryVector
        body.embedding_model = 'Xenova/all-MiniLM-L6-v2'
      }

      try {
        const headers: Record<string, string> = {}
        if (unlockToken) headers['X-Unlock-Token'] = unlockToken
        if (clientUUID) headers['X-Client-Id'] = clientUUID

        let fullContent = ''
        let pendingContent = ''
        let activityItems: ChatActivityItem[] = []

        const publishActivity = (updater: (items: ChatActivityItem[]) => ChatActivityItem[]) => {
          activityItems = updater(activityItems)
          setStreamingActivity(activityItems)
        }

        const flushStreamingContent = () => {
          if (streamFlushRef.current != null) {
            window.clearTimeout(streamFlushRef.current)
            streamFlushRef.current = null
          }
          setStreamingContent(pendingContent)
        }

        const scheduleStreamingFlush = () => {
          if (streamFlushRef.current != null) return
          // Markdown rendering is more expensive than plain text. Batch token
          // updates so long responses remain smooth on lower-memory devices.
          streamFlushRef.current = window.setTimeout(flushStreamingContent, 100)
        }

        for await (const event of readSSE(url, body, headers, controller.signal)) {
          if (event.event === 'token') {
            const text = formatEventData(event.data)
            fullContent += text
            pendingContent = fullContent
            scheduleStreamingFlush()
          } else if (event.event === 'thinking') {
            const payload =
              event.data && typeof event.data === 'object'
                ? (event.data as Record<string, unknown>)
                : {}
            const delta = typeof payload.delta === 'string' ? payload.delta : formatEventData(event.data)
            const status = payload.status === 'done' ? 'done' : 'running'
            publishActivity((items) => {
              const existingIndex = items.findIndex((item) => item.id === 'thinking')
              if (existingIndex >= 0) {
                const next = [...items]
                next[existingIndex] = {
                  ...next[existingIndex],
                  status,
                  detail: `${next[existingIndex].detail || ''}${status === 'done' ? '' : delta}`,
                }
                return next
              }
              return [
                ...items,
                {
                  id: 'thinking',
                  kind: 'thinking',
                  title: 'Reasoning trace',
                  status,
                  detail: status === 'done' ? '' : delta,
                },
              ]
            })
          } else if (event.event === 'tool_start') {
            const payload =
              event.data && typeof event.data === 'object'
                ? (event.data as Record<string, unknown>)
                : {}
            const name = String(payload?.name || `tool-${activityItems.length}`)
            const existingId = `tool-${name}-${activityItems.filter((item) => item.id.startsWith(`tool-${name}-`)).length}`
            publishActivity((items) => [
              ...items,
              {
                id: existingId,
                kind: 'tool',
                title: activityTitleFromToolPayload(event.data, name),
                status: 'running',
                detail: formatEventData(event.data),
              },
            ])
          } else if (event.event === 'tool_done' || event.event === 'tool') {
            const legacyToolName =
              typeof event.data === 'string' && event.data.startsWith('Executed tool:')
                ? event.data.replace('Executed tool:', '').trim().split(' ')[0]
                : ''
            const payload =
              event.data && typeof event.data === 'object'
                ? (event.data as Record<string, unknown>)
                : {}
            const name = String(payload.name || legacyToolName)
            publishActivity((items) => {
              const next = [...items]
              const index = name
                ? [...next].reverse().findIndex((item) => item.kind === 'tool' && item.id.startsWith(`tool-${name}-`))
                : -1
              if (index >= 0) {
                const actualIndex = next.length - 1 - index
                const status = payload.status === 'error' ? 'error' : 'done'
                next[actualIndex] = {
                  ...next[actualIndex],
                  status,
                  detail: `${next[actualIndex].detail || ''}\n\n${formatEventData(event.data)}`.trim(),
                }
                return next
              }
              return [
                ...next,
                {
                  id: `tool-${next.length}`,
                  kind: 'tool',
                  title: activityTitleFromToolPayload(event.data, 'Tool call'),
                  status: 'done',
                  detail: formatEventData(event.data),
                },
              ]
            })
          } else if (event.event === 'confirmation_required') {
            const payload =
              event.data && typeof event.data === 'object'
                ? (event.data as Record<string, unknown>)
                : {}
            const actionId = typeof payload.action_id === 'string' ? payload.action_id : ''
            const toolName = typeof payload.tool_name === 'string' ? payload.tool_name : undefined
            publishActivity((items) => [
              ...items,
              {
                id: `confirmation-${actionId || items.length}`,
                kind: 'confirmation',
                title: activityTitleFromToolPayload(event.data, 'Confirmation required'),
                status: 'waiting',
                detail: formatEventData(event.data),
                confirmation: actionId
                  ? {
                      session_id: activeSessionId,
                      action_id: actionId,
                      tool_name: toolName,
                    }
                  : undefined,
              },
            ])
          } else if (event.event === 'plan_step') {
            const payload =
              event.data && typeof event.data === 'object'
                ? (event.data as Record<string, unknown>)
                : {}
            const id = String(payload.id || `plan-${activityItems.length}`)
            publishActivity((items) => {
              const next = [...items]
              const existingIndex = next.findIndex((item) => item.id === id)
              const item: ChatActivityItem = {
                id,
                kind: 'plan',
                title: String(payload.title || 'Plan step'),
                status: normalizeActivityStatus(payload.status),
                detail: String(payload.detail || ''),
              }
              if (existingIndex >= 0) {
                next[existingIndex] = { ...next[existingIndex], ...item }
                return next
              }
              return [...next, item]
            })
          } else if (event.event === 'subagent_start' || event.event === 'subagent_done' || event.event === 'subagent_error') {
            const payload =
              event.data && typeof event.data === 'object'
                ? (event.data as Record<string, unknown>)
                : {}
            const id = String(payload.id || `subagent-${activityItems.length}`)
            publishActivity((items) => {
              const next = [...items]
              const existingIndex = next.findIndex((item) => item.id === id)
              const progress = typeof payload.progress === 'number' ? ` (${payload.progress}%)` : ''
              const phase = payload.phase ? String(payload.phase).replace(/_/g, ' ') : ''
              const detail = [phase, payload.detail ? String(payload.detail) : ''].filter(Boolean).join('\n')
              const item: ChatActivityItem = {
                id,
                kind: 'subagent',
                title: `${String(payload.title || payload.name || 'Subagent')}${progress}`,
                status: event.event === 'subagent_error' ? 'error' : normalizeActivityStatus(payload.status),
                detail,
              }
              if (existingIndex >= 0) {
                next[existingIndex] = { ...next[existingIndex], ...item }
                return next
              }
              return [...next, item]
            })
          } else if (event.event === 'artifact') {
            const payload =
              event.data && typeof event.data === 'object'
                ? (event.data as Record<string, unknown>)
                : {}
            const artifact =
              payload.kind === 'lesson'
                ? {
                    kind: 'lesson' as const,
                    action: String(payload.action || 'open'),
                    lesson_id: typeof payload.lesson_id === 'string' ? payload.lesson_id : undefined,
                    title: typeof payload.title === 'string' ? payload.title : undefined,
                    job_id: typeof payload.job_id === 'string' ? payload.job_id : undefined,
                  }
                : undefined
            if (artifact?.lesson_id) {
              useLessonStore.getState().setActiveLesson(artifact.lesson_id)
              useUIStore.getState().openFullScreen('lesson')
              useUIStore.getState().showToast('Lesson ready')
              listLessons()
                .then((lessons) => {
                  useLessonStore.getState().setLessons(Array.isArray(lessons) ? lessons : [])
                })
                .catch(() => {
                  useUIStore.getState().showToast('Lesson ready, refresh lessons to open it')
                })
            }
            publishActivity((items) => [
              ...items,
              {
                id: `artifact-${artifact?.job_id || artifact?.lesson_id || items.length}`,
                kind: 'artifact',
                title: artifact?.title ? `Lesson ready: ${artifact.title}` : 'Lesson ready',
                status: 'done',
                detail: artifact?.lesson_id || formatEventData(event.data),
                artifact,
              },
            ])
          } else if (event.event === 'chat_status') {
            const payload =
              event.data && typeof event.data === 'object'
                ? (event.data as Record<string, unknown>)
                : {}
            const jobId = typeof payload.job_id === 'string' ? payload.job_id : undefined
            const kind = String(payload.kind || 'chat')
            const detail = String(payload.detail || formatEventData(event.data))
            publishActivity((items) => {
              const id = `status-${jobId || kind}`
              const next = [...items]
              const existingIndex = next.findIndex((item) => item.id === id)
              const item: ChatActivityItem = {
                id,
                kind: 'plan',
                title:
                  kind === 'lesson'
                    ? 'Lesson building in background'
                    : kind === 'rag'
                    ? 'Document retrieval'
                    : 'Background task',
                status: payload.status === 'background' ? 'waiting' : normalizeActivityStatus(payload.status),
                detail,
              }
              if (existingIndex >= 0) {
                next[existingIndex] = { ...next[existingIndex], ...item }
                return next
              }
              return [...next, item]
            })
          } else if (event.event === 'error') {
            const text = formatEventData(event.data)
            fullContent += `\n\nError: ${text}`
            pendingContent = fullContent
            publishActivity((items) => [
              ...items,
              {
                id: `error-${items.length}`,
                kind: 'error',
                title: 'Stream error',
                status: 'error',
                detail: text,
              },
            ])
            scheduleStreamingFlush()
          }
          // 'done' events are handled by stream ending
        }

        flushStreamingContent()

        // After stream ends, add the full assistant message
        if (fullContent) {
          appendMessage(activeSessionId, { role: 'assistant', content: fullContent, activity: activityItems })
        } else if (!controller.signal.aborted) {
          appendMessage(activeSessionId, {
            role: 'assistant',
            content: 'No response received.',
            activity: activityItems,
          })
        }
      } catch (err) {
        if (controller.signal.aborted) return
        console.error('Chat stream error:', err)
        appendMessage(activeSessionId, {
          role: 'assistant',
          content: `Error: ${err instanceof Error ? err.message : 'Unknown error'}`,
        })
      } finally {
        if (streamFlushRef.current != null) {
          window.clearTimeout(streamFlushRef.current)
          streamFlushRef.current = null
        }
        setStreamingContent('')
        setStreamingActivity([])
        setStreamingSession(null)
        setIsStreaming(false)
        abortRef.current = null
      }
    },
    [],
  )

  const abort = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  return { sendMessage, abort }
}
