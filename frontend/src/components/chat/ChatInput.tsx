import { useEffect, useState, useRef, useCallback, useMemo } from 'react'
import { useChatStream } from '../../hooks/useChatStream'
import { useChatStore } from '../../stores/chatStore'
import { useDocStore } from '../../stores/docStore'
import { useUIStore } from '../../stores/uiStore'
import { getDoc } from '../../api/docs'
import {
 cancelLessonGenerationJob,
 listLessonGenerationJobs,
 listLessons,
 type LessonGenerationJob,
} from '../../api/lessons'
import { useLessonStore } from '../../stores/lessonStore'

interface Props {
 sessionId: string
}

const ACTIVE_LESSON_JOB_STATUSES = ['queued', 'planning', 'generating', 'reviewing', 'saving']
const FINISHED_LESSON_JOB_STATUSES = ['succeeded', 'failed', 'rejected', 'canceled']

function lessonArtifactFromJob(job: LessonGenerationJob) {
 const result = job.result && typeof job.result === 'object'
 ? (job.result as Record<string, unknown>)
 : {}
 const lessonId = typeof result.lesson_id === 'string' ? result.lesson_id : undefined
 if (!lessonId) return null
 return {
 kind: 'lesson' as const,
 action: 'open',
 lesson_id: lessonId,
 title: typeof result.title === 'string' ? result.title : undefined,
 job_id: job.job_id,
 }
}

export function ChatInput({ sessionId }: Props) {
 const [input, setInput] = useState('')
 const [lessonJobs, setLessonJobs] = useState<LessonGenerationJob[]>([])
  const [mentionOpen, setMentionOpen] = useState(false)
  const [mentionQuery, setMentionQuery] = useState('')
  const [mentionIndex, setMentionIndex] = useState(-1)
 const [attachedDocs, setAttachedDocs] = useState<{ name: string; docId: string }[]>([])
 const [attachedChunkIds, setAttachedChunkIds] = useState<string[]>([])
 const { sendMessage, abort } = useChatStream()
 const { appendMessage } = useChatStore.getState()
 const isStreaming = useChatStore((s) => s.isStreaming)
 const docs = useDocStore((s) => s.docs)
 const selectedChunkIds = useDocStore((s) => s.selectedChunkIds)
 const textareaRef = useRef<HTMLTextAreaElement>(null)
 const announcedLessonJobsRef = useRef<Set<string>>(new Set())
 const initializedLessonJobsRef = useRef(false)

 useEffect(() => {
 let disposed = false

 const refreshJobs = async () => {
 try {
 const jobs = await listLessonGenerationJobs(5)
 if (disposed) return
 setLessonJobs(Array.isArray(jobs) ? jobs : [])
 const succeededJobs = jobs.filter((job) => job.status === 'succeeded')
 if (succeededJobs.length > 0) {
 const lessons = await listLessons()
 if (!disposed) {
 useLessonStore.getState().setLessons(Array.isArray(lessons) ? lessons : [])
 }
 }
 if (!initializedLessonJobsRef.current) {
 for (const job of succeededJobs) {
 announcedLessonJobsRef.current.add(job.job_id)
 }
 initializedLessonJobsRef.current = true
 return
 }
 for (const job of succeededJobs) {
 if (disposed || announcedLessonJobsRef.current.has(job.job_id)) continue
 const artifact = lessonArtifactFromJob(job)
 if (!artifact) continue
 announcedLessonJobsRef.current.add(job.job_id)
 appendMessage(sessionId, {
 role: 'assistant',
 content: '',
 activity: [
 {
 id: `artifact-${artifact.job_id || artifact.lesson_id}`,
 kind: 'artifact',
 title: artifact.title ? `Lesson ready: ${artifact.title}` : 'Lesson ready',
 status: 'done',
 detail: artifact.lesson_id,
 artifact,
 },
 ],
 })
 }
 } catch {
 if (!disposed) setLessonJobs([])
 }
 }

 refreshJobs()
 const interval = window.setInterval(refreshJobs, 3000)
 return () => {
 disposed = true
 window.clearInterval(interval)
 }
 }, [appendMessage, sessionId])

 const activeLessonJob = lessonJobs.find((job) => ACTIVE_LESSON_JOB_STATUSES.includes(job.status))
 const latestFinishedLessonJob = lessonJobs.find((job) => FINISHED_LESSON_JOB_STATUSES.includes(job.status))
 const visibleLessonJob = activeLessonJob || latestFinishedLessonJob
 const visibleLessonIsCanceled = visibleLessonJob?.status === 'canceled'
 const visibleLessonIsFailed = visibleLessonJob?.status === 'failed' || visibleLessonJob?.status === 'rejected'
 const visibleLessonTopic =
 visibleLessonJob?.request && typeof visibleLessonJob.request === 'object' && 'topic' in visibleLessonJob.request
 ? String((visibleLessonJob.request as { topic?: unknown }).topic || 'lesson')
 : 'lesson'
 const visibleLessonPhase = visibleLessonJob?.phase?.replace(/_/g, ' ') || visibleLessonJob?.status || ''
 const visibleSubtasks = (visibleLessonJob?.subtasks || [])
 .filter((subtask) => ['running', 'retrying', 'fallback', 'succeeded', 'failed', 'canceled'].includes(subtask.status))
 .map((subtask) => `${subtask.label || subtask.name}: ${subtask.status}`)
 .join('\n')
  const mentionMatches = useMemo(
    () =>
      docs
        .filter((doc) => doc.name.toLowerCase().includes(mentionQuery.toLowerCase()))
        .slice(0, 6),
    [docs, mentionQuery],
  )

 const handleSend = useCallback(async () => {
 const text = input.trim()
 if (!text || isStreaming) return
 setInput('')

 // Intercept /pipeline command to open pipeline modal
 if (text.toLowerCase().startsWith('/pipeline')) {
 useUIStore.getState().openFullScreen('pipeline')
 return
 }

 const chunkIds = Array.from(new Set([...Array.from(selectedChunkIds), ...attachedChunkIds]))
 // Do not initialize the in-browser transformer for an ordinary chat message.
 // It is a large model and used to make the browser appear frozen before the
 // request was even sent. Document-grounded chat remains available by marking
 // passages in Docs or attaching a document with @.
 await sendMessage(sessionId, text, chunkIds)
 setAttachedDocs([])
 setAttachedChunkIds([])
 if (textareaRef.current) {
 textareaRef.current.style.height = 'auto'
 }
 }, [attachedChunkIds, input, isStreaming, sessionId, sendMessage, selectedChunkIds])

  const handleAttachDoc = useCallback(async (docId: string, name: string) => {
    setMentionOpen(false)
    setMentionQuery('')
    setMentionIndex(-1)
    setInput((current) => {
      const replaced = current.replace(/@([^\s@]*)$/, `@"${name}" `)
      return replaced === current ? `${current} @"${name}" ` : replaced
    })
    textareaRef.current?.focus()

    try {
      const detail = await getDoc(docId)
      const chunkIds = detail.chunks.map((chunk) => chunk.chunk_id)
      useDocStore.getState().addSelectedChunks(chunkIds)
      setAttachedChunkIds((ids) => Array.from(new Set([...ids, ...chunkIds])))
      setAttachedDocs((docs) =>
        docs.some((d) => d.docId === docId) ? docs : [...docs, { name, docId }],
      )
    } catch {
      useUIStore.getState().showToast('Could not attach document')
    }
  }, [])

 const handleDetachDoc = useCallback(async (docId: string, name: string) => {
 try {
 const detail = await getDoc(docId)
 const chunkIds = detail.chunks.map((chunk) => chunk.chunk_id)
 useDocStore.getState().removeChunkIds(chunkIds)
 setAttachedChunkIds((ids) => ids.filter((id) => !chunkIds.includes(id)))
 setAttachedDocs((docs) => docs.filter((d) => d.docId !== docId))
 setInput((current) => current.replace(new RegExp(`@"${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}"\\s?`), ''))
 textareaRef.current?.focus()
 } catch {
 useUIStore.getState().showToast('Could not detach document')
 }
 }, [])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (mentionOpen && mentionMatches.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setMentionIndex((i) => (i + 1) % mentionMatches.length)
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setMentionIndex((i) => (i - 1 + mentionMatches.length) % mentionMatches.length)
        return
      }
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        const doc = mentionMatches[mentionIndex >= 0 ? mentionIndex : 0]
        if (doc) handleAttachDoc(doc.doc_id, doc.name)
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        setMentionOpen(false)
        setMentionQuery('')
        setMentionIndex(-1)
        return
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (isStreaming) return
      handleSend()
    }
  }

 const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
 const value = e.target.value
 setInput(value)
 const match = value.match(/@([^\s@]*)$/)
    setMentionOpen(Boolean(match))
    setMentionQuery(match?.[1] || '')
    setMentionIndex(0)
 // Auto-resize
 const el = e.target
 el.style.height = 'auto'
 el.style.height = Math.min(el.scrollHeight, 200) + 'px'
 }

 const handleStopGeneration = useCallback(async () => {
 const jobId = activeLessonJob?.job_id
 abort()
 if (!jobId) return
 setLessonJobs((jobs) =>
 jobs.map((job) =>
 job.job_id === jobId
 ? { ...job, status: 'canceled', phase: 'canceled', error: 'Canceled by user.' }
 : job,
 ),
 )
 try {
 await cancelLessonGenerationJob(jobId)
 const jobs = await listLessonGenerationJobs(5)
 setLessonJobs(Array.isArray(jobs) ? jobs : [])
 } catch {
 useUIStore.getState().showToast('Could not stop lesson generation')
 }
 }, [abort, activeLessonJob?.job_id])

 return (
 <div className="px-3 pb-3 pt-2 md:px-4 md:pb-4">
 <div className="flex items-center justify-between mb-2 px-0.5">
 <div className="flex items-center gap-2">
 <span
 className={`w-1.5 h-1.5 rounded-full ${
 isStreaming ? 'bg-accent animate-pulse-dot' : 'bg-success'
 }`}
 />
 <span className="text-[10px] font-mono uppercase tracking-wider text-accent-muted/60">
 {isStreaming ? 'Assistant working' : 'Ready'}
 </span>
 {visibleLessonJob && (
 <span
 className={`flex items-center gap-1 rounded-full px-2 py-0.5 text-[9px] font-mono uppercase tracking-wider ${
 activeLessonJob
 ? 'bg-accent/10 text-accent'
 : visibleLessonJob.status === 'succeeded'
 ? 'bg-success/10 text-success'
 : visibleLessonIsCanceled
 ? 'bg-surface-card/70 text-accent-muted'
 : 'bg-danger/10 text-danger'
 }`}
 title={`${visibleLessonJob.job_id} - ${visibleLessonPhase}${visibleSubtasks ? `\n${visibleSubtasks}` : ''}`}
 >
 <span className="material-symbols-outlined text-[12px]">
 {activeLessonJob
 ? 'autorenew'
 : visibleLessonJob.status === 'succeeded'
 ? 'check_circle'
 : visibleLessonIsCanceled
 ? 'block'
 : 'error'}
 </span>
 {activeLessonJob
 ? `Creating ${visibleLessonTopic} ${visibleLessonJob.progress}%`
 : visibleLessonJob.status === 'succeeded'
 ? `${visibleLessonTopic} saved`
 : visibleLessonIsCanceled
 ? `${visibleLessonTopic} canceled`
 : visibleLessonIsFailed
 ? 'Lesson failed'
 : visibleLessonJob.status}
 </span>
 )}
  </div>
  </div>

  {/* Input */}
 <div className="relative">
 {mentionOpen && mentionMatches.length > 0 && !isStreaming && (
 <div className="mention-menu absolute bottom-full left-0 right-0 mb-2 max-h-56 overflow-y-auto rounded-lg bg-surface-card shadow-xl">
  {mentionMatches.map((doc, idx) => (
            <button
              key={doc.doc_id}
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onMouseEnter={() => setMentionIndex(idx)}
              onClick={() => handleAttachDoc(doc.doc_id, doc.name)}
              className={`mention-option flex w-full items-center gap-2 px-3 py-2 text-left text-xs transition-all duration-150 ${
                idx === mentionIndex
                  ? 'bg-accent/15 text-text-primary shadow-[inset_2px_0_0_rgb(var(--color-accent)/0.45)]'
                  : 'text-text-secondary hover:bg-surface-lighter hover:text-text-primary'
              }`}
 >
 <span className="material-symbols-outlined text-[16px] text-accent">description</span>
 <span className="min-w-0 flex-1 truncate">{doc.name}</span>
 </button>
 ))}
 </div>
 )}
 {attachedDocs.length > 0 && (
 <div className="mb-2 flex flex-wrap gap-1">
 {attachedDocs.map((doc) => (
 <span
 key={doc.docId}
 className="inline-flex items-center gap-1 rounded-full bg-accent/10 px-2 py-0.5 text-[10px] font-mono text-accent"
 >
 <span className="material-symbols-outlined text-[12px]">attach_file</span>
 {doc.name}
 <button
 type="button"
 onClick={() => handleDetachDoc(doc.docId, doc.name)}
 className="ml-0.5 rounded-full p-px text-accent/50 hover:text-accent hover:bg-accent/20 transition-colors"
 title={`Remove ${doc.name}`}
 >
 <span className="material-symbols-outlined text-[10px]">close</span>
 </button>
 </span>
 ))}
 </div>
 )}
 <div
 className={`flex items-end gap-2 rounded-2xl bg-surface-card px-4 py-3 shadow-sm transition-all md:gap-3 md:px-5 ${
 isStreaming ? 'opacity-70' : 'focus-within:bg-surface-lighter'
 }`}
 >
 <span className="text-accent font-mono text-sm select-none mt-0.5">&gt;</span>
 <textarea
 ref={textareaRef}
 className="flex-1 bg-transparent border-none text-text-primary text-sm outline-none font-mono resize-none min-h-[24px] max-h-[200px] leading-relaxed placeholder-accent-muted/40"
 placeholder={isStreaming ? 'Waiting for assistant to finish...' : 'Ask anything...'}
 value={input}
 onChange={handleInput}
 onKeyDown={handleKeyDown}
 disabled={isStreaming}
 aria-busy={isStreaming}
 rows={1}
 />
  <button
  type="button"
  onClick={isStreaming || activeLessonJob ? handleStopGeneration : handleSend}
  disabled={!isStreaming && !activeLessonJob && !input.trim()}
  className={
  isStreaming || activeLessonJob
  ? 'text-danger hover:text-red-400 transition-colors'
  : 'text-accent hover:text-accent-hover disabled:text-accent-muted/30 disabled:cursor-not-allowed transition-colors'
  }
  title={
  isStreaming
  ? 'Stop assistant response'
  : activeLessonJob
  ? 'Stop lesson generation'
  : 'Send'
  }
  >
  <span className="material-symbols-outlined text-xl">
  {isStreaming || activeLessonJob ? 'stop_circle' : 'send'}
  </span>
  </button>
 </div>
 </div>
     
 </div>
 )
}
