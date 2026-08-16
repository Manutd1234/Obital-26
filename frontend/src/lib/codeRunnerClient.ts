// Lazy-singleton wrapper around the code runner worker (mirrors embeddingClient.ts).

export type CodeLang = 'js' | 'python'

export interface RunResult {
  ok: boolean
  output: string
  error: string
}

type WorkerMessage =
  | { id: number; type: 'progress'; status: string }
  | { id: number; type: 'result'; ok: boolean; output: string; error: string }

let worker: Worker | null = null
let nextId = 1
const pending = new Map<
  number,
  { resolve: (value: RunResult) => void; reject: (reason?: unknown) => void; onProgress?: (s: string) => void }
>()

function getWorker(): Worker {
  if (!worker) {
    worker = new Worker(new URL('../workers/codeRunner.worker.ts', import.meta.url), { type: 'module' })
    worker.onmessage = (event: MessageEvent<WorkerMessage>) => {
      const msg = event.data
      const waiter = pending.get(msg.id)
      if (!waiter) return
      if (msg.type === 'progress') {
        waiter.onProgress?.(msg.status)
        return
      }
      pending.delete(msg.id)
      waiter.resolve({ ok: msg.ok, output: msg.output, error: msg.error })
    }
    worker.onerror = (event) => {
      for (const waiter of pending.values()) {
        waiter.reject(new Error(event.message || 'Code runner worker failed'))
      }
      pending.clear()
      worker?.terminate()
      worker = null
    }
  }
  return worker
}

export function runCode(lang: CodeLang, code: string, onProgress?: (s: string) => void): Promise<RunResult> {
  const id = nextId++
  return new Promise<RunResult>((resolve, reject) => {
    pending.set(id, { resolve, reject, onProgress })
    getWorker().postMessage({ id, lang, code })
  })
}
