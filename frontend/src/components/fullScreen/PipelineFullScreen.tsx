import { useEffect, useState } from 'react'
import { getDoc, listDocs, type DocSummary } from '../../api/docs'
import { generateCurriculum, generateTopicQA, savePipeline } from '../../api/pipeline'
import type { Curriculum, TopicQASet } from '../../api/pipeline'
import { useUIStore } from '../../stores/uiStore'
import { useDocStore } from '../../stores/docStore'

type Phase = 'idle' | 'generating-curriculum' | 'generating-qa' | 'saving' | 'done' | 'error'

export function PipelineFullScreen() {
 const [docs, setDocs] = useState<DocSummary[]>([])
 const [loading, setLoading] = useState(true)
 const globalDocs = useDocStore((s) => s.docs)
 const [phase, setPhase] = useState<Phase>('idle')
 const [curriculum, setCurriculum] = useState<Curriculum | null>(null)
 const [topicQASets, setTopicQASets] = useState<TopicQASet[]>([])
 const [currentTopicIdx, setCurrentTopicIdx] = useState(0)
 const [error, setError] = useState<string | null>(null)
 const showToast = useUIStore((s) => s.showToast)

 useEffect(() => {
 if (globalDocs.length > 0) {
 setDocs(globalDocs.map(d => ({ doc_id: d.doc_id, name: d.name, chunk_count: d.chunk_count, uploaded_at: d.uploaded_at } as DocSummary)))
 setLoading(false)
 return
 }
 listDocs()
 .then((res) => setDocs(Array.isArray(res) ? res : []))
 .catch(() => {})
 .finally(() => setLoading(false))
 }, [])

 const handleSynthesize = async () => {
 const details = await Promise.all(docs.map((doc) => getDoc(doc.doc_id).catch(() => null)))
 const allChunks = details.flatMap((doc) => doc?.chunks.map((chunk) => chunk.text) ?? [])

 if (allChunks.length === 0) {
 setError('No chunks available. Upload documents first.')
 return
 }

 setError(null)
 setTopicQASets([])
 setCurrentTopicIdx(0)

 // Step 1: Generate curriculum
 setPhase('generating-curriculum')
 let curr: Curriculum
 try {
 curr = await generateCurriculum(allChunks.join('\n\n'))
 setCurriculum(curr)
 } catch (err) {
 setPhase('error')
 setError(`Curriculum generation failed: ${err instanceof Error ? err.message : 'unknown'}`)
 return
 }

 // Step 2: Generate QA for each topic
 setPhase('generating-qa')
 const contextChunks = allChunks.slice(0, 5)
 const qaSets: TopicQASet[] = []
 for (let i = 0; i < curr.topics.length; i++) {
 const topic = curr.topics[i]
 setCurrentTopicIdx(i)
 try {
 const qaSet = await generateTopicQA(topic.id, topic.title, topic.summary, contextChunks)
 qaSets.push(qaSet)
 setTopicQASets([...qaSets])
 } catch (err) {
 setPhase('error')
 setError(`QA generation failed for "${topic.title}": ${err instanceof Error ? err.message : 'unknown'}`)
 return
 }
 }

 // Step 3: Save
 setPhase('saving')
 try {
 await savePipeline({
 curriculum: curr,
 qa_sets: qaSets,
 metadata: {
 generated_at: new Date().toISOString(),
 device_used: 'web-ui',
 },
 })
 setPhase('done')
 showToast(`Generated ${curr.topics.length} topics, ${qaSets.reduce((n, q) => n + q.questions.length, 0)} QA pairs`)
 } catch (err) {
 setPhase('error')
 setError(`Save failed: ${err instanceof Error ? err.message : 'unknown'}`)
 }
 }

 const totalTopics = curriculum?.topics.length ?? 0
 const doneTopics = phase === 'generating-qa' ? currentTopicIdx : topicQASets.length

 return (
 <div className="h-full overflow-y-auto custom-scrollbar p-4 md:p-6">
 <div className="max-w-2xl mx-auto space-y-6">
 {/* Header */}
 <div>
 <h1 className="text-xl font-bold text-text-primary flex items-center gap-2">
 <span className="material-symbols-outlined text-accent">auto_awesome</span>
 Curriculum Pipeline
 </h1>
 <p className="text-xs text-accent-muted mt-1">
 Generate curriculum and QA sets from your uploaded documents.
 </p>
 </div>

 {/* Docs summary */}
 <div className="bg-surface-light rounded-xl p-4">
 <div className="flex items-center justify-between mb-2">
 <span className="text-xs font-mono text-accent-muted uppercase tracking-wider">Documents</span>
 <span className="text-xs text-accent-muted">
 {docs.length} docs, {docs.reduce((sum, doc) => sum + doc.chunk_count, 0)} chunks
 </span>
 </div>
 {loading ? (
 <div className="flex items-center gap-2 text-accent-muted text-xs">
 <div className="w-3 h-3 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
 Loading docs...
 </div>
 ) : docs.length === 0 ? (
 <p className="text-xs text-accent-muted/60">No documents found. Upload documents first.</p>
 ) : (
 <div className="space-y-1">
 {docs.map((doc) => (
  <div key={doc.doc_id} className="flex items-center gap-2 text-xs text-text-secondary min-w-0">
  <span className="material-symbols-outlined text-accent-muted text-sm flex-shrink-0">description</span>
  <span className="truncate flex-1 min-w-0" title={doc.name}>{doc.name}</span>
  <span className="text-accent-muted/50 flex-shrink-0">{doc.chunk_count} chunks</span>
 </div>
 ))}
 </div>
 )}
 </div>

 {/* Error */}
 {error && (
 <div className="bg-danger/10 border border-danger/20 rounded-xl p-4 text-xs text-danger">
 {error}
 </div>
 )}

 {/* Progress */}
 {phase !== 'idle' && phase !== 'done' && phase !== 'error' && (
 <div className="bg-surface-light rounded-xl p-4 space-y-3">
 <div className="flex items-center gap-2">
 <div className="w-4 h-4 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
 <span className="text-xs font-mono text-accent-muted uppercase tracking-wider">
 {phase === 'generating-curriculum' && 'Step 1/3: Generating curriculum...'}
 {phase === 'generating-qa' && `Step 2/3: Topic ${doneTopics + 1}/${totalTopics} — ${curriculum?.topics[currentTopicIdx]?.title ?? ''}`}
 {phase === 'saving' && 'Step 3/3: Saving results...'}
 </span>
 </div>
 {phase === 'generating-qa' && totalTopics > 0 && (
 <div className="w-full bg-surface rounded-full h-1.5">
 <div
 className="bg-accent h-1.5 rounded-full transition-all duration-500"
 style={{ width: `${((doneTopics) / totalTopics) * 100}%` }}
 />
 </div>
 )}
 </div>
 )}

 {/* Done */}
 {phase === 'done' && (
 <div className="bg-success/10 border border-success/20 rounded-xl p-4 text-xs text-success">
 Pipeline complete. {totalTopics} topics, {topicQASets.reduce((n, q) => n + q.questions.length, 0)} QA pairs generated.
 </div>
 )}

 {/* Topics preview */}
 {curriculum && (
 <div className="bg-surface-light rounded-xl p-4 space-y-3">
 <div className="flex items-center justify-between">
 <span className="text-xs font-mono text-accent-muted uppercase tracking-wider">
 {curriculum.title || 'Curriculum'}
 </span>
 <span className="text-[10px] text-accent-muted/50">{curriculum.topics.length} topics</span>
 </div>
 {curriculum.description && (
 <p className="text-xs text-text-secondary">{curriculum.description}</p>
 )}
 <div className="space-y-2">
 {curriculum.topics.map((topic, i) => {
 const qaSet = topicQASets.find((q) => q.topic_id === topic.id)
 const isCurrentPhase2 = phase === 'generating-qa' && i === currentTopicIdx
 return (
 <div key={topic.id} className=" rounded-lg p-3">
 <div className="flex items-center gap-2">
 {isCurrentPhase2 ? (
 <div className="w-4 h-4 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
 ) : qaSet ? (
 <span className="material-symbols-outlined text-success text-sm">check_circle</span>
 ) : (
 <span className="w-4 h-4 rounded-full" />
 )}
 <span className="text-sm text-text-primary font-medium">{topic.title}</span>
 {qaSet && (
 <span className="text-[10px] text-accent-muted/50 ml-auto">
 {qaSet.questions.length} QA
 </span>
 )}
 </div>
 {topic.summary && (
 <p className="text-[11px] text-accent-muted mt-1 ml-6">{topic.summary}</p>
 )}
 </div>
 )
 })}
 </div>
 </div>
 )}

 {/* Synthesize button */}
 <button
 onClick={handleSynthesize}
 disabled={phase !== 'idle' && phase !== 'done' && phase !== 'error' || docs.length === 0}
 className="w-full py-3 bg-accent text-surface font-bold text-xs uppercase tracking-wider rounded-xl disabled:opacity-30 disabled:cursor-default hover:bg-accent-hover transition-all flex items-center justify-center gap-2"
 >
 <span className="material-symbols-outlined text-base">auto_awesome</span>
 {phase === 'idle' || phase === 'done' || phase === 'error' ? 'Synthesize Curriculum' : 'Processing...'}
 </button>
 </div>
 </div>
 )
}
