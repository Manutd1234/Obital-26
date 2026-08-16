import { DEV_NO_AUTH, mockSSE } from './devMock'

export interface SSEEvent {
  event: string
  data: unknown
  session_id?: string
}

export async function* readSSE(
  url: string,
  body: unknown,
  headers?: Record<string, string>,
  signal?: AbortSignal,
): AsyncGenerator<SSEEvent> {
  if (DEV_NO_AUTH) {
    yield* mockSSE(body)
    return
  }
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify(body),
    signal,
  })

  if (!res.ok || !res.body) {
    throw new Error(`SSE request failed: ${res.status}`)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          const event = JSON.parse(line.slice(6))
          if (event.event && event.data !== undefined) {
            yield event as SSEEvent
          }
        } catch {
          // skip malformed lines
        }
      }
    }
  }

  // Process remaining buffer
  if (buffer.trim()) {
    for (const line of buffer.split('\n')) {
      if (line.startsWith('data: ')) {
        try {
          const event = JSON.parse(line.slice(6))
          if (event.event && event.data !== undefined) {
            yield event as SSEEvent
          }
        } catch {
          // skip
        }
      }
    }
  }
}
