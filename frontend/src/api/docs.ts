import { api, withAccount } from './client'
import { useAuthStore } from '../stores/authStore'
import type { EmbeddingMetadata } from '../lib/embeddings'

export interface DocSummary {
  doc_id: string
  name: string
  chunk_count: number
  uploaded_at?: string
  tags?: string[]
  embedding?: DocEmbeddingMetadata | null
}

export interface DocDetail extends DocSummary {
  chunks: { chunk_id: string; text: string; tags?: string[] }[]
}

export interface DocEmbeddingMetadata {
  model?: string | null
  dimension?: number | null
  provider?: string | null
  runtime?: string | null
  chunk_count?: number
}

interface BackendDoc {
  id: string
  name: string
  uploaded_at?: string
  chunks: { chunk_id: string; text: string; vector?: unknown; tags?: string[] }[]
  chunk_count?: number
  tags?: string[]
  embedding?: DocEmbeddingMetadata | null
}

function mapDoc(d: BackendDoc): DocSummary {
  return { doc_id: d.id, name: d.name, chunk_count: d.chunk_count ?? d.chunks?.length ?? 0, uploaded_at: d.uploaded_at, tags: d.tags, embedding: d.embedding }
}

function mapDocDetail(d: BackendDoc): DocDetail {
  return {
    doc_id: d.id,
    name: d.name,
    chunk_count: d.chunk_count ?? d.chunks?.length ?? 0,
    uploaded_at: d.uploaded_at,
    tags: d.tags,
    embedding: d.embedding,
    chunks: (d.chunks || []).map((c) => ({ chunk_id: c.chunk_id, text: c.text, tags: c.tags })),
  }
}

export async function listDocs(limit = 100) {
  const res = await api.get<BackendDoc[]>(`/docs?limit=${limit}`)
  return res.map(mapDoc)
}

export async function getDoc(docId: string) {
  const res = await api.get<BackendDoc>(`/docs/${docId}`)
  return mapDocDetail(res)
}

export async function searchDocs(query: string) {
  const res = await api.get<BackendDoc[]>(`/docs/search?q=${encodeURIComponent(query)}`)
  return res.map(mapDoc)
}

export type EmbeddingStatus = 'idle' | 'loading' | 'ready' | 'unavailable' | 'disabled'

export async function getEmbeddingStatus() {
  return api.get<{ status: EmbeddingStatus; model: string; mode: string }>('/docs/embedding-status')
}

export async function semanticSearchDocs(queryVector: number[], topK = 8, threshold = 0.35) {
  return api.post<
    {
      doc_id: string
      chunk_id: string
      doc_name: string
      text: string
      score: number
      tags?: string[]
    }[]
  >('/docs/search', {
    query_vector: queryVector,
    top_k: topK,
    threshold,
  })
}

export async function semanticSearchDocsByText(query: string, topK = 8, threshold = 0.35) {
  return api.post<
    {
      doc_id: string
      chunk_id: string
      doc_name: string
      text: string
      score: number
      tags?: string[]
    }[]
  >('/docs/search/text', { query, top_k: topK, threshold })
}

export async function saveDocEmbeddings(
  docId: string,
  chunks: { chunk_id: string; text: string }[],
  vectors: number[][],
  metadata: EmbeddingMetadata,
) {
  return api.put<{ status: string; chunks_embedded: number }>(`/docs/${docId}/embeddings`, {
    ...metadata,
    chunks: chunks.map((chunk, index) => ({
      chunk_id: chunk.chunk_id,
      text: chunk.text,
      vector: vectors[index] || [],
    })),
  })
}

export async function deleteDoc(docId: string) {
  return api.del<{ status: string; doc_id: string }>(`/docs/${docId}`)
}

export async function uploadDoc(file: File) {
  const { unlockToken, clientUUID } = useAuthStore.getState()
  const formData = new FormData()
  formData.append('file', file)
  const headers: Record<string, string> = {}
  if (unlockToken) headers['X-Unlock-Token'] = unlockToken
  if (clientUUID) headers['X-Client-Id'] = clientUUID
  const res = await fetch(withAccount('/docs/upload'), { method: 'POST', headers, body: formData })
  if (!res.ok) {
    const responseText = await res.text()

    // Vercel rejects request bodies over 4.5 MB before they reach FastAPI.
    // Its 413 response is plain text, so it cannot be handled through the
    // backend's normal JSON `detail` field.
    if (res.status === 413 && responseText.includes('FUNCTION_PAYLOAD_TOO_LARGE')) {
      throw new Error('This file is too large for cloud upload. Cloud uploads are limited to 4.5 MB.')
    }

    let detail = ''
    try {
      const payload = JSON.parse(responseText)
      detail = typeof payload?.detail === 'string' ? `: ${payload.detail}` : ''
    } catch {
      // Keep the status-based error when the server/proxy returns plain text.
    }
    throw new Error(`Upload failed: ${res.status}${detail}`)
  }
  const doc = await res.json()
  return mapDocDetail(doc)
}
