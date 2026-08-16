export const EMBEDDING_MODEL_ID = 'Xenova/all-MiniLM-L6-v2'
export const EMBEDDING_DIMENSION = 384

export type EmbeddingRuntime = 'webgpu' | 'wasm'

export interface EmbeddingMetadata {
  model: string
  dimension: number
  provider: 'browser-transformers'
  runtime: EmbeddingRuntime
}
