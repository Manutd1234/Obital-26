import type { DocSummary } from '../api/docs'
import { getDoc, saveDocEmbeddings } from '../api/docs'
import { EMBEDDING_DIMENSION, EMBEDDING_MODEL_ID } from './embeddings'

let running = false

function migrationKey(docs: DocSummary[]) {
  const signature = docs
    .map((doc) => `${doc.doc_id}:${doc.chunk_count}:${doc.uploaded_at || ''}`)
    .sort()
    .join('|')
  return `obital:semantic-index-session:${signature}`
}

export async function reindexDocsForSession(docs: DocSummary[]) {
  const candidates = docs.filter((doc) => {
    if (doc.chunk_count <= 0) return false
    const embedding = doc.embedding
    if (!embedding) return true
    return (
      embedding.model !== EMBEDDING_MODEL_ID ||
      embedding.provider !== 'browser-transformers' ||
      embedding.dimension !== EMBEDDING_DIMENSION ||
      embedding.chunk_count !== doc.chunk_count
    )
  })
  if (running || candidates.length === 0) return

  const key = migrationKey(candidates)
  if (sessionStorage.getItem(key) === 'done') return

  running = true
  try {
    const { embedTexts, getEmbeddingMetadata } = await import('./embeddingClient')
    const metadata = await getEmbeddingMetadata()

    for (const doc of candidates) {
      try {
        const detail = await getDoc(doc.doc_id)
        if (!detail.chunks.length) continue
        const vectors = await embedTexts(detail.chunks.map((chunk) => chunk.text))
        await saveDocEmbeddings(detail.doc_id, detail.chunks, vectors, metadata)
      } catch (err) {
        console.warn(`Semantic reindex failed for ${doc.name}:`, err)
      }
    }
    sessionStorage.setItem(key, 'done')
  } finally {
    running = false
  }
}
