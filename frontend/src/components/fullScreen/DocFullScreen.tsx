import { useEffect, useState, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import remarkMath from 'remark-math'
import { useDocStore } from '../../stores/docStore'
import { listDocs, getDoc, uploadDoc, searchDocs, deleteDoc, saveDocEmbeddings, semanticSearchDocs } from '../../api/docs'
import type { DocDetail } from '../../api/docs'
import { useUIStore } from '../../stores/uiStore'

const isMobileViewport = () =>
 typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches

type MobileDocMode = 'list' | 'detail'

export function DocFullScreen() {
 const { docs, activeDocId, setActiveDoc, selectedChunkIds, toggleChunk, clearChunks, setDocs } =
 useDocStore()
 const [docDetail, setDocDetail] = useState<DocDetail | null>(null)
 const [loading, setLoading] = useState(false)
 const [listLoading, setListLoading] = useState(false)
 const [search, setSearch] = useState('')
 const [uploading, setUploading] = useState(false)
 const [searchMode, setSearchMode] = useState(false)
 const [searchResults, setSearchResults] = useState<typeof docs>([])
 const [embeddingStatus, setEmbeddingStatus] = useState('')
 const [loadError, setLoadError] = useState<string | null>(null)
 const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
 const [mobileMode, setMobileMode] = useState<MobileDocMode>(activeDocId ? 'detail' : 'list')
 const fileRef = useRef<HTMLInputElement>(null)
 const showToast = useUIStore((s) => s.showToast)

 useEffect(() => {
 if (docs.length > 0) return
 setListLoading(true)
 listDocs()
 .then((res) => {
 setLoadError(null)
 setDocs(Array.isArray(res) ? res : [])
 })
 .catch((err) => {
 setLoadError(err instanceof Error ? err.message : 'Failed to load documents')
 })
 .finally(() => setListLoading(false))
 }, [setDocs])

 useEffect(() => {
 if (!activeDocId) {
 setDocDetail(null)
 return
 }
 let ignore = false
 setLoading(true)
 getDoc(activeDocId)
 .then((doc) => { if (!ignore) setDocDetail(doc) })
 .catch(() => { if (!ignore) setDocDetail(null) })
 .finally(() => { if (!ignore) setLoading(false) })
 return () => { ignore = true }
 }, [activeDocId])

 useEffect(() => {
 if (activeDocId && isMobileViewport()) {
 setMobileMode('detail')
 }
 }, [activeDocId])

 const handleSearch = async (q: string) => {
 setSearch(q)
 if (!q.trim()) {
 setSearchMode(false)
 setSearchResults([])
 return
 }
 setSearchMode(true)
 try {
 const { embedText } = await import('../../lib/embeddingClient')
 const queryVector = await embedText(q)
 const semanticResults = await semanticSearchDocs(queryVector)
 const byDoc = new Map<string, typeof docs[number]>()
 for (const result of semanticResults) {
 const existing = docs.find((doc) => doc.doc_id === result.doc_id)
 byDoc.set(result.doc_id, {
 doc_id: result.doc_id,
 name: result.doc_name,
 chunk_count: existing?.chunk_count ?? 0,
 uploaded_at: existing?.uploaded_at,
 tags: existing?.tags ?? result.tags,
 })
 }
 setSearchResults(Array.from(byDoc.values()))
 } catch {
 try {
 const res = await searchDocs(q)
 setSearchResults(Array.isArray(res) ? res : [])
 } catch {
 setSearchResults([])
 }
 }
 }

 const rebuildDocIndex = async (docId: string, name: string) => {
 setEmbeddingStatus('Rebuilding semantic index')
 try {
 const detail = await getDoc(docId)
 const { embedTexts, getEmbeddingMetadata } = await import('../../lib/embeddingClient')
 const vectors = await embedTexts(detail.chunks.map((chunk) => chunk.text))
 const metadata = await getEmbeddingMetadata()
 await saveDocEmbeddings(detail.doc_id, detail.chunks, vectors, metadata)
 showToast(`Semantic index rebuilt for ${name}`)
 } catch (err) {
 showToast(`Semantic reindex failed: ${err instanceof Error ? err.message : 'unknown'}`)
 } finally {
 setEmbeddingStatus('')
 }
 }

 const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
 const file = e.target.files?.[0]
 if (!file) return
 setUploading(true)
 try {
 const uploaded = await uploadDoc(file)
 showToast(`Uploaded ${file.name}`)
 if (uploaded.chunks.length > 0) {
 setEmbeddingStatus('Embedding document')
 try {
 const { embedTexts, getEmbeddingMetadata } = await import('../../lib/embeddingClient')
 const vectors = await embedTexts(uploaded.chunks.map((chunk) => chunk.text))
 const metadata = await getEmbeddingMetadata()
 await saveDocEmbeddings(uploaded.doc_id, uploaded.chunks, vectors, metadata)
 showToast(`Semantic index ready for ${file.name}`)
 } catch (err) {
 showToast(`Uploaded, but semantic indexing failed: ${err instanceof Error ? err.message : 'unknown'}`)
 } finally {
 setEmbeddingStatus('')
 }
 }
 const res = await listDocs()
 setDocs(Array.isArray(res) ? res : [])
 } catch (err) {
 showToast(`Upload failed: ${err instanceof Error ? err.message : 'unknown'}`)
 }
 setUploading(false)
 if (fileRef.current) fileRef.current.value = ''
 }

 const handleDelete = async (docId: string) => {
 const previousDocs = docs
 const previousResults = searchResults
 const previousDetail = docDetail
 const nextDocs = docs.filter((doc) => doc.doc_id !== docId)
 setDocs(nextDocs)
 setSearchResults((results) => results.filter((doc) => doc.doc_id !== docId))
 if (activeDocId === docId) {
 setActiveDoc(null)
 setDocDetail(null)
 }
 try {
 await deleteDoc(docId)
 const res = await listDocs()
 setDocs(Array.isArray(res) ? res : [])
 showToast('Document moved to bin')
 } catch {
 setDocs(previousDocs)
 setSearchResults(previousResults)
 if (activeDocId === docId) {
 setActiveDoc(docId)
 setDocDetail(previousDetail)
 }
 showToast('Failed to delete document')
 }
 }

 const displayDocs = searchMode ? searchResults : docs

 return (
 <div className="relative flex h-full overflow-hidden">
 <input
 ref={fileRef}
 type="file"
 className="hidden"
 accept=".pdf,.txt,.md,.csv"
 onChange={handleUpload}
 />

  {/* Left: doc list */}
  {!sidebarCollapsed && (
  <div
  className={`h-full w-full flex-col bg-surface-light md:relative md:flex md:w-[300px] md:flex-shrink-0 ${
  mobileMode === 'list' ? 'flex' : 'hidden'
  }`}
  >
  {/* Search + Upload */}
  <div className="p-3 space-y-2">
  <div className="flex items-center gap-1.5">
  <div className="flex items-center gap-2 bg-surface rounded-lg px-3 py-2 flex-1">
  <span className="material-symbols-outlined text-accent-muted text-base">search</span>
  <input
  type="text"
  placeholder="Search documents..."
  className="bg-transparent border-none text-sm text-text-primary outline-none w-full placeholder-accent-muted/50"
  value={search}
  onChange={(e) => handleSearch(e.target.value)}
  />
  {search && (
  <button
  onClick={() => handleSearch('')}
  className="text-accent-muted/50 hover:text-accent-muted"
  >
  <span className="material-symbols-outlined text-sm">close</span>
  </button>
  )}
  </div>
  <button
  onClick={() => setSidebarCollapsed(true)}
  className="hidden w-6 h-6 md:flex items-center justify-center rounded text-accent-muted/40 hover:text-accent-muted hover:bg-surface-card transition-all flex-shrink-0"
  title="Collapse sidebar"
  >
  <span className="material-symbols-outlined text-sm">chevron_left</span>
  </button>
  </div>
 <button
 onClick={() => fileRef.current?.click()}
 disabled={uploading || Boolean(embeddingStatus)}
 className="w-full flex items-center justify-center gap-2 py-2 bg-accent/10 text-accent text-xs font-mono uppercase tracking-wider rounded-lg hover:bg-accent/20 transition-all disabled:opacity-50"
 >
 <span className="material-symbols-outlined text-sm">
 {uploading || embeddingStatus ? 'hourglass_empty' : 'upload_file'}
 </span>
 {embeddingStatus || (uploading ? 'Uploading...' : 'Upload Document')}
 </button>
 </div>

 {/* Selected chunks indicator */}
 {selectedChunkIds.size > 0 && (
 <div className="px-3 py-2 bg-accent/5 flex items-center justify-between">
 <span className="text-[10px] text-accent font-mono">
 {selectedChunkIds.size} chunk{selectedChunkIds.size !== 1 ? 's' : ''} selected
 </span>
 <button
 onClick={clearChunks}
 className="text-[10px] text-accent-muted hover:text-accent font-mono"
 >
 Clear
 </button>
 </div>
 )}

 {/* Doc list */}
 <div className="flex-1 overflow-y-auto custom-scrollbar p-2 space-y-0.5">
 {listLoading && docs.length === 0 && (
 <div className="space-y-1 px-1 py-1">
 {[0, 1, 2, 3, 4, 5].map((item) => (
 <div key={item} className="h-12 rounded-lg bg-surface-card/45 animate-pulse" />
 ))}
 </div>
 )}
 {loadError && (
 <div className="m-2 rounded-lg border border-danger/20 bg-danger/10 px-3 py-2 text-xs text-danger break-words">
 {loadError}
 </div>
 )}
 {displayDocs.map((doc) => (
 <div
 key={doc.doc_id}
 className={`relative group flex items-center rounded-lg transition-all ${
 activeDocId === doc.doc_id
 ? 'bg-surface-card text-text-primary'
 : 'text-accent-muted hover:bg-surface-card/50 hover:text-text-primary'
 }`}
 >
  <button
  onClick={() => {
  setActiveDoc(doc.doc_id)
  setSearch('')
  setSearchMode(false)
  if (isMobileViewport()) setMobileMode('detail')
  }}
  className="flex-1 text-left px-3 py-2.5 min-w-0 pr-8"
  >
  <div className="flex items-center gap-2 min-w-0">
  <span className="material-symbols-outlined text-accent text-sm flex-shrink-0">
  description
  </span>
  <span className="text-sm truncate flex-1 min-w-0" title={doc.name}>{doc.name}</span>
  </div>
 </button>
 <button
 onClick={(e) => {
 e.stopPropagation()
 handleDelete(doc.doc_id)
 }}
 className="opacity-0 group-hover:opacity-100 absolute right-2 p-1 rounded text-danger hover:bg-danger/10 transition-all"
 title="Delete"
 >
 <span className="material-symbols-outlined text-sm">delete</span>
 </button>
 </div>
 ))}
 {displayDocs.length === 0 && !searchMode && !listLoading && (
 <div className="px-3 py-12 text-center">
 <span className="material-symbols-outlined text-accent-muted/20 text-4xl block mb-2">
 folder_open
 </span>
 <p className="text-accent-muted/40 text-xs">No documents uploaded yet</p>
 </div>
 )}
 {searchMode && displayDocs.length === 0 && (
 <div className="px-3 py-8 text-center text-accent-muted/40 text-xs space-y-3">
 <p>No results for "{search}"</p>
 <button
 onClick={() => handleSearch('')}
 className="rounded-lg bg-surface-card/70 px-3 py-1.5 text-[10px] font-mono uppercase tracking-wider text-accent-muted transition-all hover:bg-surface-lighter hover:text-text-primary"
 >
 Clear search
 </button>
 </div>
 )}
 </div>

 {/* Footer stats */}
 <div className="p-3 text-[10px] text-accent-muted/40 font-mono flex justify-between">
 <span>{docs.length} document{docs.length !== 1 ? 's' : ''}</span>
 <span>
 {docs.reduce((sum, d) => sum + d.chunk_count, 0)} chunks total
 </span>
 </div>
  </div>
  )}

{sidebarCollapsed && (
 <button
 onClick={() => setSidebarCollapsed(false)}
 className="hidden flex-shrink-0 w-8 bg-surface-light md:flex items-center justify-center text-accent-muted/40 hover:text-accent-muted hover:bg-surface-lighter transition-all"
 title="Expand sidebar"
 >
 <span className="material-symbols-outlined text-sm">chevron_right</span>
 </button>
 )}

 {/* Right: doc viewer */}
 <div
 className={`min-w-0 flex-1 overflow-y-auto custom-scrollbar bg-surface md:block ${
 mobileMode === 'detail' ? 'block' : 'hidden'
 }`}
 >
 {loading && (
 <div className="h-full flex items-center justify-center">
 <div className="flex items-center gap-3 text-accent-muted text-sm">
 <div className="w-4 h-4 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
 Loading document...
 </div>
 </div>
 )}

 {!loading && !docDetail && (
 <div className="h-full flex items-center justify-center text-center">
 <div className="space-y-3">
 <span className="material-symbols-outlined text-accent-muted/15 text-7xl block">
 menu_book
 </span>
 <p className="text-accent-muted/30 text-sm">Select a document to read</p>
 <p className="text-accent-muted/15 text-[10px] font-mono">
 Hover or click passages to mark them
 </p>
 <button
 onClick={() => fileRef.current?.click()}
 disabled={uploading || Boolean(embeddingStatus)}
 className="inline-flex items-center gap-2 rounded-xl bg-surface-card/80 px-3 py-2 text-xs text-text-primary shadow-sm transition-all hover:bg-surface-lighter disabled:opacity-50"
 >
 <span className="material-symbols-outlined text-sm text-accent">
 {uploading ? 'hourglass_empty' : 'upload_file'}
 </span>
 {embeddingStatus || (uploading ? 'Uploading...' : 'Upload document')}
 </button>
 </div>
 </div>
 )}

 {!loading && docDetail && (
 <div className="max-w-3xl mx-auto px-4 py-5 space-y-6 md:px-8 md:py-8 md:space-y-8">
 <div className="sticky top-3 z-10 rounded-2xl bg-surface/90 px-3 py-3 shadow-[0_8px_24px_rgb(0_0_0/0.06)] backdrop-blur md:top-4 md:px-4">
 <div className="flex flex-wrap items-start justify-between gap-2 md:gap-4">
 <button
 onClick={() => setMobileMode('list')}
 className="mr-1 flex h-8 w-8 items-center justify-center rounded-lg bg-surface-card text-accent-muted hover:text-text-primary md:hidden"
 title="Open documents"
 aria-label="Open documents"
 >
 <span className="material-symbols-outlined text-base">list</span>
 </button>
 <div className="min-w-0">
 <h1 className="truncate text-base font-semibold text-text-primary">{docDetail.name}</h1>
 <p className="mt-0.5 text-[10px] text-accent-muted/50 font-mono">
 {docDetail.chunk_count} chunks
 {selectedChunkIds.size > 0 ? ` - ${selectedChunkIds.size} marked` : ''}
 </p>
 </div>
 {selectedChunkIds.size > 0 && (
 <button
 onClick={clearChunks}
 className="rounded-lg px-2 py-1 text-[10px] font-mono uppercase tracking-wider text-accent-muted transition-colors hover:bg-surface-lighter hover:text-text-primary"
 >
 Clear
 </button>
 )}
 <button
 onClick={() => rebuildDocIndex(docDetail.doc_id, docDetail.name)}
 disabled={Boolean(embeddingStatus)}
 className="rounded-lg px-2 py-1 text-[10px] font-mono uppercase tracking-wider text-accent-muted transition-colors hover:bg-surface-lighter hover:text-text-primary disabled:opacity-50"
 >
 Rebuild index
 </button>
 </div>
 </div>

 {selectedChunkIds.size > 0 && (
 <div className="flex items-center gap-2 rounded-xl bg-accent/5 px-4 py-2">
 <span className="material-symbols-outlined text-accent text-sm">check_circle</span>
 <span className="text-xs text-accent">
 {selectedChunkIds.size} passage{selectedChunkIds.size !== 1 ? 's' : ''} marked
 </span>
 </div>
 )}

 <div className="sr-only">
 <h1>{docDetail.name}</h1>
 <p>{docDetail.chunk_count} chunks</p>
 </div>

 <div className="relative">
 {docDetail.chunks?.map((chunk) => {
 const isSelected = selectedChunkIds.has(chunk.chunk_id)
 return (
 <div
 key={chunk.chunk_id}
 onClick={() => toggleChunk(chunk.chunk_id)}
 className={`group relative cursor-pointer transition-all py-1 ${
 isSelected
 ? 'bg-accent/10 -mx-3 px-3 rounded-lg shadow-[inset_3px_0_0_rgb(var(--color-accent)/0.65)]'
 : 'hover:bg-surface-light/50 hover:-mx-3 hover:px-3 hover:rounded-lg'
 }`}
 >
 <div
 className={`absolute -left-2 top-2 w-5 h-5 rounded-full border flex items-center justify-center flex-shrink-0 transition-all md:-left-8 ${
 isSelected
 ? 'bg-accent border-accent opacity-100'
 : 'border-border/10 bg-surface-card/80 opacity-0 group-hover:opacity-100'
 }`}
 >
 <span className={`material-symbols-outlined ${isSelected ? 'text-surface text-[10px]' : 'text-accent-muted text-[11px]'}`}>
 {isSelected ? 'check' : 'add'}
 </span>
 </div>

 <div className="prose-custom text-sm text-text-secondary leading-relaxed">
 <ReactMarkdown remarkPlugins={[remarkMath]} rehypePlugins={[rehypeKatex]}>
 {chunk.text}
 </ReactMarkdown>
 </div>
 </div>
 )
 })}
 </div>
 </div>
 )}
 </div>
 </div>
 )
}
