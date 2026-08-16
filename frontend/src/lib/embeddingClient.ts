import type { EmbeddingMetadata } from './embeddings'

type WorkerRequest =
  | { id: number; type: 'embedText'; text: string }
  | { id: number; type: 'embedTexts'; texts: string[] }
  | { id: number; type: 'metadata' }
  | { id: number; type: 'warm' }

type WorkerRequestBody =
  | { type: 'embedText'; text: string }
  | { type: 'embedTexts'; texts: string[] }
  | { type: 'metadata' }
  | { type: 'warm' }

type WorkerResponse =
  | { id: number; ok: true; vector?: number[]; vectors?: number[][]; metadata?: EmbeddingMetadata }
  | { id: number; ok: false; error: string }

let worker: Worker | null = null
let nextId = 1
const pending = new Map<number, { resolve: (value: WorkerResponse) => void; reject: (reason?: unknown) => void }>()

function getWorker() {
  if (!worker) {
    worker = new Worker(new URL('../workers/embedding.worker.ts', import.meta.url), { type: 'module' })
    worker.onmessage = (event: MessageEvent<WorkerResponse>) => {
      const waiter = pending.get(event.data.id)
      if (!waiter) return
      pending.delete(event.data.id)
      waiter.resolve(event.data)
    }
    worker.onerror = (event) => {
      for (const waiter of pending.values()) {
        waiter.reject(new Error(event.message || 'Embedding worker failed'))
      }
      pending.clear()
      worker?.terminate()
      worker = null
    }
  }
  return worker
}

function request(message: WorkerRequestBody): Promise<WorkerResponse> {
  const id = nextId++
  const payload = { ...message, id } as WorkerRequest
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject })
    getWorker().postMessage(payload)
  })
}

function unwrap<T>(response: WorkerResponse, key: 'vector' | 'vectors' | 'metadata'): T {
  if (!response.ok) throw new Error(response.error)
  const value = response[key]
  if (value == null) throw new Error(`Embedding worker returned no ${key}`)
  return value as T
}

export async function embedText(text: string): Promise<number[]> {
  return unwrap<number[]>(await request({ type: 'embedText', text }), 'vector')
}

export async function embedTexts(texts: string[]): Promise<number[][]> {
  return unwrap<number[][]>(await request({ type: 'embedTexts', texts }), 'vectors')
}

export async function getEmbeddingMetadata(): Promise<EmbeddingMetadata> {
  return unwrap<EmbeddingMetadata>(await request({ type: 'metadata' }), 'metadata')
}

export async function warmEmbeddingModel(): Promise<void> {
  const response = await request({ type: 'warm' })
  if (!response.ok) throw new Error(response.error)
}
