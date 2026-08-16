import { EMBEDDING_DIMENSION, EMBEDDING_MODEL_ID, type EmbeddingMetadata, type EmbeddingRuntime } from '../lib/embeddings'

type RequestMessage =
  | { id: number; type: 'embedText'; text: string }
  | { id: number; type: 'embedTexts'; texts: string[] }
  | { id: number; type: 'metadata' }
  | { id: number; type: 'warm' }

type ResponseMessage =
  | { id: number; ok: true; vector?: number[]; vectors?: number[][]; metadata?: EmbeddingMetadata }
  | { id: number; ok: false; error: string }

let extractorPromise: Promise<{ extractor: any; runtime: EmbeddingRuntime }> | null = null

function hasWebGPU() {
  return typeof navigator !== 'undefined' && 'gpu' in navigator
}

async function createExtractor(runtime: EmbeddingRuntime) {
  const { pipeline, env } = await import('@huggingface/transformers')
  env.allowLocalModels = false
  return pipeline('feature-extraction', EMBEDDING_MODEL_ID, {
    device: runtime === 'webgpu' ? 'webgpu' : 'wasm',
  })
}

async function getExtractor() {
  if (!extractorPromise) {
    extractorPromise = (async () => {
      if (hasWebGPU()) {
        try {
          return { extractor: await createExtractor('webgpu'), runtime: 'webgpu' as const }
        } catch (err) {
          console.warn('WebGPU embeddings unavailable, falling back to WASM:', err)
        }
      }
      return { extractor: await createExtractor('wasm'), runtime: 'wasm' as const }
    })()
  }
  return extractorPromise
}

function normalizeVector(values: number[]) {
  const norm = Math.sqrt(values.reduce((sum, value) => sum + value * value, 0))
  if (!Number.isFinite(norm) || norm < 1e-12) return values
  return values.map((value) => value / norm)
}

function vectorFromOutput(output: any): number[] {
  const data = Array.from(output?.data || []) as number[]
  if (data.length === EMBEDDING_DIMENSION) return normalizeVector(data)
  if (Array.isArray(output) && Array.isArray(output[0])) return normalizeVector(output[0].map(Number))
  throw new Error(`Unexpected embedding output shape (${data.length} values)`)
}

async function embedText(text: string): Promise<number[]> {
  const { extractor } = await getExtractor()
  const output = await extractor(text, { pooling: 'mean', normalize: true })
  const vector = vectorFromOutput(output)
  if (vector.length !== EMBEDDING_DIMENSION) {
    throw new Error(`Expected ${EMBEDDING_DIMENSION} embedding dimensions, got ${vector.length}`)
  }
  return vector
}

async function metadata(): Promise<EmbeddingMetadata> {
  const { runtime } = await getExtractor()
  return {
    model: EMBEDDING_MODEL_ID,
    dimension: EMBEDDING_DIMENSION,
    provider: 'browser-transformers',
    runtime,
  }
}

self.onmessage = async (event: MessageEvent<RequestMessage>) => {
  const request = event.data
  try {
    if (request.type === 'embedText') {
      postMessage({ id: request.id, ok: true, vector: await embedText(request.text) } satisfies ResponseMessage)
      return
    }
    if (request.type === 'embedTexts') {
      const vectors: number[][] = []
      for (const text of request.texts) {
        vectors.push(await embedText(text))
      }
      postMessage({ id: request.id, ok: true, vectors } satisfies ResponseMessage)
      return
    }
    if (request.type === 'metadata') {
      postMessage({ id: request.id, ok: true, metadata: await metadata() } satisfies ResponseMessage)
      return
    }
    await getExtractor()
    postMessage({ id: request.id, ok: true } satisfies ResponseMessage)
  } catch (err) {
    postMessage({
      id: request.id,
      ok: false,
      error: err instanceof Error ? err.message : String(err),
    } satisfies ResponseMessage)
  }
}
