import { useState } from 'react'
import type { MouseEvent } from 'react'
import { confirmAction } from '../../api/chat'
import type { ChatActivityItem } from '../../api/chat'
import { listLessons } from '../../api/lessons'
import { useChatStore } from '../../stores/chatStore'
import { useLessonStore } from '../../stores/lessonStore'
import { useUIStore } from '../../stores/uiStore'

interface Props {
 content?: string
 item?: ChatActivityItem
}

export function ToolCallCard({ content = '', item }: Props) {
 const [expanded, setExpanded] = useState(false)
 const [isSubmitting, setIsSubmitting] = useState(false)

 const detail = item?.detail || content
 const title = item?.title || content.replace('Executed tool: ', '').split(' ')[0] || 'Tool call'
 const kind = item?.kind || 'tool'
 const status = item?.status || (content.startsWith('Executed tool:') ? 'done' : undefined)
 const icon =
 kind === 'thinking'
 ? 'psychology'
 : kind === 'confirmation'
 ? 'rule'
 : kind === 'error'
 ? 'error'
 : kind === 'plan'
 ? 'checklist'
 : kind === 'subagent'
 ? 'groups'
 : kind === 'artifact'
 ? 'auto_stories'
 : 'terminal'
 const statusLabel =
 status === 'running' ? 'Running' : status === 'waiting' ? 'Waiting' : status === 'error' ? 'Error' : 'Done'
 const kindLabel =
 kind === 'thinking'
 ? 'Thinking'
 : kind === 'tool'
 ? 'Tool'
 : kind === 'plan'
 ? 'Plan'
 : kind === 'subagent'
 ? 'Agent'
 : kind === 'artifact'
 ? 'Artifact'
 : statusLabel

 const handleOpenArtifact = (event: MouseEvent) => {
 event.stopPropagation()
 if (item?.artifact?.kind !== 'lesson' || !item.artifact.lesson_id) return
 useLessonStore.getState().setActiveLesson(item.artifact.lesson_id)
 useUIStore.getState().openFullScreen('lesson')
 listLessons()
 .then((lessons) => {
 useLessonStore.getState().setLessons(Array.isArray(lessons) ? lessons : [])
 })
 .catch(() => {})
 }

 const handleConfirm = async (event: MouseEvent, decision: 'approve' | 'cancel') => {
 event.stopPropagation()
 if (!item?.confirmation || isSubmitting || item.status !== 'waiting') return
 setIsSubmitting(true)
 const { appendMessage, updateActivityItem } = useChatStore.getState()
 try {
 const result = await confirmAction(
 item.confirmation.session_id,
 item.confirmation.action_id,
 decision,
 )
 const nextStatus = result.status === 'completed' || result.status === 'canceled' ? 'done' : 'error'
 updateActivityItem(item.confirmation.session_id, item.id, {
 status: nextStatus,
 detail: `${detail}\n\n${result.reply}`.trim(),
 })
 appendMessage(item.confirmation.session_id, { role: 'assistant', content: result.reply })
 } catch (err) {
 const message = err instanceof Error ? err.message : 'Confirmation failed'
 updateActivityItem(item.confirmation.session_id, item.id, {
 status: 'error',
 detail: `${detail}\n\n${message}`.trim(),
 })
 useUIStore.getState().showToast('Could not confirm action')
 } finally {
 setIsSubmitting(false)
 }
 }

 const showConfirmationControls = Boolean(item?.confirmation && item.status === 'waiting')

 return (
 <div className="bg-surface/50 rounded-lg px-4 py-2.5 text-xs">
 <div
 className="flex items-center gap-2 cursor-pointer"
 onClick={() => setExpanded(!expanded)}
 >
 <span className="material-symbols-outlined text-[16px] text-accent">
 {icon}
 </span>
 <span className="text-accent-muted font-mono truncate">
 <span className="text-accent">{kindLabel}</span>{' '}
 {title}
 </span>
 {status === 'running' && <div className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse-dot" />}
 {item?.artifact?.kind === 'lesson' && item.artifact.lesson_id && (
 <button
 onClick={handleOpenArtifact}
 className="ml-auto px-2 py-1 rounded-md bg-accent/10 text-accent hover:bg-accent/20 transition-colors"
 >
 Open
 </button>
 )}
 <span className="material-symbols-outlined text-accent-muted/30 text-sm ml-auto">
 {expanded ? 'expand_less' : 'expand_more'}
 </span>
 </div>
 {showConfirmationControls && (
 <div className="mt-3 flex items-center gap-2">
 <button
 onClick={(event) => handleConfirm(event, 'approve')}
 disabled={isSubmitting}
 className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-black hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-60"
 >
 <span className="material-symbols-outlined text-[15px]">check</span>
 Approve
 </button>
 <button
 onClick={(event) => handleConfirm(event, 'cancel')}
 disabled={isSubmitting}
 className="inline-flex items-center gap-1.5 rounded-md bg-surface-hover px-3 py-1.5 text-xs font-medium text-accent-muted hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-60"
 >
 <span className="material-symbols-outlined text-[15px]">close</span>
 Reject
 </button>
 </div>
 )}
 {expanded && detail && (
 <div className="mt-2 pt-2 text-accent-muted/60 font-mono text-[11px] whitespace-pre-wrap">
 {detail}
 </div>
 )}
 </div>
 )
}
