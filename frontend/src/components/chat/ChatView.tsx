import { useEffect, useRef, useState } from 'react'
import { useChatStore } from '../../stores/chatStore'
import { useLessonStore } from '../../stores/lessonStore'
import { useDocStore } from '../../stores/docStore'
import { listLessons } from '../../api/lessons'
import { getEmbeddingStatus, listDocs, type EmbeddingStatus } from '../../api/docs'
import { MessageBubble } from './MessageBubble'
import { ChatInput } from './ChatInput'
import { useUIStore } from '../../stores/uiStore'

export function ChatView() {
 const activeSessionId = useChatStore((s) => s.activeSessionId)
 const messages = useChatStore((s) => s.messages)
 const streamingContent = useChatStore((s) => s.streamingContent)
 const streamingActivity = useChatStore((s) => s.streamingActivity)
 const streamingSessionId = useChatStore((s) => s.streamingSessionId)
 const isStreaming = useChatStore((s) => s.isStreaming)
 const openFullScreen = useUIStore((s) => s.openFullScreen)
 const hasLessons = useLessonStore((s) => s.lessons.length > 0)
 const bottomRef = useRef<HTMLDivElement>(null)
 const [embeddingStatus, setEmbeddingStatus] = useState<EmbeddingStatus | null>(null)

 const sessionMessages = activeSessionId ? messages[activeSessionId] || [] : []
 const isActiveSessionStreaming = isStreaming && activeSessionId === streamingSessionId

  // Load lessons and docs on mount
  useEffect(() => {
    if (hasLessons) return
    listLessons()
      .then((res) => useLessonStore.getState().setLessons(Array.isArray(res) ? res : []))
      .catch(() => {})
    listDocs()
      .then((res) => {
        const docs = Array.isArray(res) ? res : []
        useDocStore.getState().setDocs(docs)
      })
      .catch(() => {})
  }, [hasLessons])

  useEffect(() => {
    let disposed = false
    let timer: number | undefined

    const poll = async () => {
      try {
        const result = await getEmbeddingStatus()
        if (disposed) return
        setEmbeddingStatus(result.status)
        if (result.status === 'loading' || result.status === 'idle') {
          timer = window.setTimeout(poll, 2000)
        }
      } catch {
        // Status is only a courtesy indicator; chat remains usable if it fails.
      }
    }

    poll()
    return () => {
      disposed = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [])

 // Auto-scroll
 useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: isActiveSessionStreaming ? 'auto' : 'smooth' })
 }, [sessionMessages.length, streamingContent, streamingActivity.length, isActiveSessionStreaming])

 return (
 <div className="flex-1 flex flex-col min-h-0 h-full overflow-hidden bg-surface text-text-primary">
 {/* Chat messages */}
 <div className="flex-1 overflow-y-auto custom-scrollbar">
 {sessionMessages.length === 0 && !isActiveSessionStreaming ? (
 // Empty state
 <div className="h-full flex flex-col items-center justify-center text-center px-8">
 <div className="w-16 h-16 rounded-2xl bg-surface-card flex items-center justify-center text-accent mb-6">
 <span className="material-symbols-outlined text-3xl">school</span>
 </div>
 <h2 className="text-2xl font-bold text-text-primary mb-2">
 What are you studying today?
 </h2>
 <p className="text-accent-muted text-sm max-w-md">
 Ask me anything about your study material, start a lesson, take a quiz,
 or upload documents to your vault.
 </p>
 <div className="mt-6 flex flex-wrap items-center justify-center gap-2">
 <button
 onClick={() => openFullScreen('doc')}
 className="inline-flex items-center gap-2 rounded-xl bg-surface-card/80 px-3 py-2 text-xs text-text-primary shadow-sm transition-all hover:bg-surface-lighter"
 >
 <span className="material-symbols-outlined text-sm text-accent">upload_file</span>
 Upload docs
 </button>
 <button
 onClick={() => openFullScreen('lesson')}
 className="inline-flex items-center gap-2 rounded-xl bg-surface-card/60 px-3 py-2 text-xs text-accent-muted transition-all hover:bg-surface-card hover:text-text-primary"
 >
 <span className="material-symbols-outlined text-sm">school</span>
 View lessons
 </button>
 <button
 onClick={() => openFullScreen('dashboard')}
 className="inline-flex items-center gap-2 rounded-xl bg-surface-card/60 px-3 py-2 text-xs text-accent-muted transition-all hover:bg-surface-card hover:text-text-primary"
 >
 <span className="material-symbols-outlined text-sm">space_dashboard</span>
 Dashboard
 </button>
 </div>
 </div>
 ) : (
 <div className="max-w-[900px] mx-auto px-4 py-6 space-y-6 md:px-6 md:py-8 md:space-y-7">
 {sessionMessages.map((msg, i) => (
 <MessageBubble key={i} message={msg} />
 ))}
 {isActiveSessionStreaming && (streamingContent || streamingActivity.length > 0) && (
 <MessageBubble
 message={{ role: 'assistant', content: streamingContent, activity: streamingActivity }}
 isStreaming
 />
 )}
 {isActiveSessionStreaming && !streamingContent && streamingActivity.length === 0 && (
          <div className="flex items-center gap-2 px-4">
              <div className="flex items-center gap-1">
                <div className="w-1.5 h-1.5 bg-accent rounded-full animate-pulse-dot" />
                <div className="w-1.5 h-1.5 bg-accent rounded-full animate-pulse-dot [animation-delay:0.2s]" />
                <div className="w-1.5 h-1.5 bg-accent rounded-full animate-pulse-dot [animation-delay:0.4s]" />
              </div>
              <span className="text-[10px] text-accent-muted font-mono uppercase">
                Thinking...
              </span>
            </div>
 )}
 <div ref={bottomRef} />
 </div>
 )}
 </div>

 {embeddingStatus && embeddingStatus !== 'ready' && embeddingStatus !== 'disabled' && (
   <div className={`flex items-center justify-center gap-2 border-t border-surface-lighter/40 px-4 py-2 text-[10px] font-mono ${
     embeddingStatus === 'unavailable' ? 'text-red-400' : 'text-accent-muted'
   }`}>
     <span className={`h-1.5 w-1.5 rounded-full ${
       embeddingStatus === 'unavailable' ? 'bg-red-400' : 'bg-accent animate-pulse'
     }`} />
     {embeddingStatus === 'loading' ? 'Loading embedding model…' : 'Embedding model unavailable'}
   </div>
 )}

 {/* Chat input */}
 {activeSessionId && <ChatInput sessionId={activeSessionId} />}
 </div>
 )
}
