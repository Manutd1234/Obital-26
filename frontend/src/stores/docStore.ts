import { create } from 'zustand'

export interface DocChunk {
  chunk_id: string
  text: string
  tags?: string[]
}

export interface Doc {
  doc_id: string
  name: string
  chunk_count: number
  uploaded_at?: string
  chunks?: DocChunk[]
  tags?: string[]
}

interface DocState {
  docs: Doc[]
  activeDocId: string | null
  selectedChunkIds: Set<string>
  setDocs: (d: Doc[]) => void
  setActiveDoc: (id: string | null) => void
  addSelectedChunks: (ids: string[]) => void
  removeChunkIds: (ids: string[]) => void
  toggleChunk: (id: string) => void
  clearChunks: () => void
}

export const useDocStore = create<DocState>((set) => ({
  docs: [],
  activeDocId: null,
  selectedChunkIds: new Set(),
  setDocs: (docs) => set({ docs }),
  setActiveDoc: (id) => set({ activeDocId: id, selectedChunkIds: new Set() }),
  addSelectedChunks: (ids) =>
    set((state) => {
      const next = new Set(state.selectedChunkIds)
      ids.forEach((id) => next.add(id))
      return { selectedChunkIds: next }
    }),
  removeChunkIds: (ids) =>
    set((state) => {
      const next = new Set(state.selectedChunkIds)
      ids.forEach((id) => next.delete(id))
      return { selectedChunkIds: next }
    }),
  toggleChunk: (id) =>
    set((state) => {
      const next = new Set(state.selectedChunkIds)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return { selectedChunkIds: next }
    }),
  clearChunks: () => set({ selectedChunkIds: new Set() }),
}))
