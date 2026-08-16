import { useEffect, useCallback, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import remarkMath from 'remark-math'
import { useLessonStore, type Lesson } from '../../stores/lessonStore'
import { useDocStore } from '../../stores/docStore'
import { gradeLessonCheckpoint, listLessons, restartLesson, deleteLesson } from '../../api/lessons'
import { useUIStore } from '../../stores/uiStore'
import { useDashboardStore } from '../../stores/dashboardStore'
import type { QuizGradeResponse, QuizGradedQuestion, QuizQuestion } from '../../api/quiz'
import { normalizeLatexDelimiters } from '../../lib/utils'

const isMobileViewport = () =>
 typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches

type MobileLessonMode = 'list' | 'detail'
type LessonQuizDraft = {
 currentIdx: number
 answers: Record<string, string>
}

export function LessonFullScreen() {
 const {
 lessons,
 activeLessonId,
 checkpointIndex,
 setActiveLesson,
 setCheckpointIndex,
 nextCheckpoint,
 prevCheckpoint,
 setLessons,
 } = useLessonStore()
 const selectedChunkIds = useDocStore((s) => s.selectedChunkIds)
 const showToast = useUIStore((s) => s.showToast)
 const refreshDashboard = useDashboardStore((s) => s.refreshDashboard)
 const [showQuizPrompt, setShowQuizPrompt] = useState(false)
 const [quizDrafts, setQuizDrafts] = useState<Record<string, LessonQuizDraft>>({})
 const [loadError, setLoadError] = useState<string | null>(null)
 const [lessonsLoading, setLessonsLoading] = useState(false)
 const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
 const [mobileMode, setMobileMode] = useState<MobileLessonMode>(activeLessonId ? 'detail' : 'list')

 useEffect(() => {
 if (lessons.length > 0) return
 setLessonsLoading(true)
 listLessons()
 .then((res) => {
 setLoadError(null)
 setLessons(Array.isArray(res) ? res : [])
 })
 .catch((err) => {
 setLoadError(err instanceof Error ? err.message : 'Failed to load lessons')
 })
 .finally(() => setLessonsLoading(false))
 }, [lessons.length, setLessons])

 // Arrow key navigation
 const handleKeyDown = useCallback(
 (e: KeyboardEvent) => {
 // Don't navigate if typing in an input
 if ((e.target as HTMLElement).tagName === 'INPUT' || (e.target as HTMLElement).tagName === 'TEXTAREA') return
 if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
 e.preventDefault()
 nextCheckpoint()
 } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
 e.preventDefault()
 prevCheckpoint()
 } else if (e.key === 'Escape') {
 if (activeLessonId) {
 setActiveLesson(null)
 }
 }
 },
 [nextCheckpoint, prevCheckpoint, activeLessonId, setActiveLesson],
 )

 useEffect(() => {
 window.addEventListener('keydown', handleKeyDown)
 return () => window.removeEventListener('keydown', handleKeyDown)
 }, [handleKeyDown])

 useEffect(() => {
 setShowQuizPrompt(false)
 }, [activeLessonId, checkpointIndex])

 const quizDraftKey = activeLessonId ? `${activeLessonId}:${checkpointIndex}` : null
 const clearQuizDraft = useCallback(() => {
  if (!quizDraftKey) return
  setQuizDrafts((drafts) => {
   const next = { ...drafts }
   delete next[quizDraftKey]
   return next
  })
 }, [quizDraftKey])

 useEffect(() => {
 if (!activeLessonId) {
 setMobileMode('list')
 return
 }
 if (isMobileViewport()) {
 setMobileMode('detail')
 }
 }, [activeLessonId])

 const handleRestart = async (lessonId: string) => {
 clearQuizDraft()
 try {
 await restartLesson(lessonId)
 const res = await listLessons()
 setLessons(Array.isArray(res) ? res : [])
 setCheckpointIndex(0)
 showToast('Lesson restarted')
 } catch {
 showToast('Failed to restart lesson')
 }
 }

 const handleDelete = async (lessonId: string) => {
 const previousLessons = lessons
 const previousActiveLessonId = activeLessonId
 setLessons(lessons.filter((lesson) => lesson.lesson_id !== lessonId))
 if (activeLessonId === lessonId) {
 setActiveLesson(null)
 }
 try {
 await deleteLesson(lessonId)
 const res = await listLessons()
 setLessons(Array.isArray(res) ? res : [])
 showToast('Lesson moved to bin')
 } catch {
 setLessons(previousLessons)
 if (previousActiveLessonId === lessonId) {
 setActiveLesson(lessonId)
 }
 showToast('Failed to delete lesson')
 }
 }

 const activeLesson = lessons.find((l) => l.lesson_id === activeLessonId)

 // No lesson selected — show lesson list
 if (!activeLesson) {
 return (
 <div className="flex h-full overflow-hidden">
 <div className="flex w-full flex-col bg-surface-light md:w-[300px] md:flex-shrink-0">
 <div className="p-4">
 <h3 className="text-sm font-bold text-text-primary uppercase tracking-wider font-mono flex items-center gap-2">
 <span className="material-symbols-outlined text-accent text-base">school</span>
 Lessons
 </h3>
 </div>
 <div className="flex-1 overflow-y-auto custom-scrollbar p-2 space-y-1">
 {loadError && (
 <div className="m-2 rounded-lg border border-danger/20 bg-danger/10 px-3 py-2 text-xs text-danger break-words">
 {loadError}
 </div>
 )}
 {lessonsLoading && lessons.length === 0 && (
 <div className="space-y-1 px-1 py-1">
 {[0, 1, 2, 3, 4].map((item) => (
 <div key={item} className="h-16 rounded-lg bg-surface-card/45 animate-pulse" />
 ))}
 </div>
 )}
 {lessons.map((lesson) => (
 <LessonListItem
 key={lesson.lesson_id}
 lesson={lesson}
 isActive={false}
 onClick={() => {
 setActiveLesson(lesson.lesson_id)
 if (isMobileViewport()) setMobileMode('detail')
 }}
 onRestart={() => handleRestart(lesson.lesson_id)}
 onDelete={() => handleDelete(lesson.lesson_id)}
 />
 ))}
 {lessons.length === 0 && !lessonsLoading && (
 <div className="px-3 py-16 text-center">
 <span className="material-symbols-outlined text-accent-muted/15 text-5xl block mb-3">
 school
 </span>
 <p className="text-accent-muted/40 text-xs">No lessons yet</p>
 <p className="text-accent-muted/20 text-[10px] mt-1 font-mono">
 Start one from the chat with "start lesson on &lt;topic&gt;"
 </p>
 <button
 onClick={() => useUIStore.getState().closeFullScreen()}
 className="mt-4 inline-flex items-center gap-2 rounded-xl bg-surface-card/80 px-3 py-2 text-xs text-text-primary shadow-sm transition-all hover:bg-surface-lighter"
 >
 <span className="material-symbols-outlined text-sm text-accent">chat</span>
 Open chat
 </button>
 </div>
 )}
 </div>
 </div>

 {/* Empty state right panel */}
 <div className="hidden flex-1 items-center justify-center md:flex">
 <div className="text-center space-y-4 max-w-sm">
 <span className="material-symbols-outlined text-accent-muted/10 text-8xl block">
 auto_stories
 </span>
 <h2 className="text-lg font-bold text-text-primary/50">Select a Lesson</h2>
 <p className="text-accent-muted/30 text-xs leading-relaxed">
 Each lesson has checkpoints with topic material and quizzes.
 </p>
 </div>
 </div>
 </div>
 )
 }

 // Active lesson — checkpoint slides
 const totalCheckpoints = activeLesson.total_checkpoints
 const checkpoints = activeLesson.checkpoints || []
 const currentCp = checkpoints[checkpointIndex]
 const isCompleted = activeLesson.status === 'completed'
 const progressPercent = Math.round(((checkpointIndex + 1) / totalCheckpoints) * 100)
 const materialMarkdown = currentCp?.markdown || ''

 return (
 <div className="relative flex h-full overflow-hidden">
 {/* Left: lesson list sidebar */}
 {!sidebarCollapsed && (
 <div
 className={`h-full w-full flex-col bg-surface-light md:relative md:flex md:w-[300px] md:flex-shrink-0 ${
 mobileMode === 'list' ? 'flex' : 'hidden'
 }`}
 >
 <div className="p-3 flex items-center justify-between">
 <button
 onClick={() => setActiveLesson(null)}
 className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-accent-muted hover:text-text-primary hover:bg-surface-card/50 text-xs transition-colors"
 >
 <span className="material-symbols-outlined text-sm">format_list_bulleted</span>
 Lessons
 </button>
 <button
 onClick={() => setSidebarCollapsed(true)}
 className="hidden w-6 h-6 md:flex items-center justify-center rounded text-accent-muted/35 hover:text-accent-muted hover:bg-surface-card transition-all"
 title="Collapse sidebar"
 aria-label="Collapse lesson sidebar"
 >
 <span className="material-symbols-outlined text-sm">left_panel_close</span>
 </button>
 </div>
 <div className="px-3 pb-3">
 <div className="rounded-2xl bg-surface/70 px-3 py-3 shadow-[0_1px_2px_rgb(0_0_0/0.03)]">
 <div className="flex items-center justify-between gap-3">
 <div className="min-w-0">
 <p className="truncate text-xs font-semibold text-text-primary">{activeLesson.title}</p>
 <p className="mt-0.5 text-[10px] text-accent-muted/50 font-mono">
 Checkpoint {checkpointIndex + 1} of {totalCheckpoints}
 </p>
 </div>
 <span className="text-[10px] text-accent-muted/50 font-mono">{progressPercent}%</span>
 </div>
 <div className="mt-2 h-1.5 bg-surface-card rounded-full overflow-hidden">
 <div
 className={`h-full rounded-full transition-all duration-500 ${
 isCompleted ? 'bg-success' : 'bg-accent'
 }`}
 style={{ width: `${progressPercent}%` }}
 />
 </div>
 </div>
 </div>
 <div className="flex-1 overflow-y-auto custom-scrollbar p-2 space-y-0.5">
 <div className="px-1 pb-3">
 <p className="px-2 pb-2 text-[10px] font-mono uppercase tracking-wider text-accent-muted/40">
 Checkpoints
 </p>
 <div className="space-y-1">
 {checkpoints.map((cp, i) => {
 const isPast = i < activeLesson.current_checkpoint
 const isCurrent = i === checkpointIndex
 return (
 <button
 key={`${cp.title}-${i}`}
 onClick={() => {
 setCheckpointIndex(i)
 if (isMobileViewport()) setMobileMode('detail')
 }}
 className={`w-full rounded-xl px-3 py-2 text-left transition-all ${
 isCurrent
 ? 'bg-surface-card/90 text-text-primary shadow-sm'
 : 'text-accent-muted hover:bg-surface-card/50 hover:text-text-primary'
 }`}
 >
 <div className="flex items-start gap-2">
 <span
 className={`material-symbols-outlined mt-0.5 text-sm ${
 isPast
 ? 'text-success'
 : isCurrent
 ? 'text-accent'
 : 'text-accent-muted/35'
 }`}
 >
 {isPast ? 'check_circle' : isCurrent ? 'radio_button_checked' : 'radio_button_unchecked'}
 </span>
 <div className="min-w-0 flex-1">
 <p className="truncate text-xs">{cp.title}</p>
 <p className="mt-0.5 text-[9px] font-mono uppercase tracking-wider text-accent-muted/40">
 {cp.difficulty} - {cp.num_questions} q
 </p>
 </div>
 </div>
 </button>
 )
 })}
 </div>
 </div>
 <div className="px-1 pt-2">
 <p className="px-2 pb-2 text-[10px] font-mono uppercase tracking-wider text-accent-muted/40">
 Other lessons
 </p>
 {lessons.map((lesson) => (
 <LessonListItem
 key={lesson.lesson_id}
 lesson={lesson}
 isActive={lesson.lesson_id === activeLessonId}
 onClick={() => {
 setActiveLesson(lesson.lesson_id)
 setCheckpointIndex(0)
 if (isMobileViewport()) setMobileMode('detail')
 }}
 onRestart={() => handleRestart(lesson.lesson_id)}
 onDelete={() => handleDelete(lesson.lesson_id)}
 />
 ))}
 </div>
  </div>
  </div>
  )}

 {/* Collapse toggle when sidebar is hidden */}
{sidebarCollapsed && (
 <button
 onClick={() => setSidebarCollapsed(false)}
 className="hidden flex-shrink-0 w-8 bg-surface-light md:flex items-center justify-center text-accent-muted/40 hover:text-accent-muted hover:bg-surface-lighter transition-all"
 title="Expand sidebar"
 >
 <span className="material-symbols-outlined text-sm">chevron_right</span>
 </button>
 )}

 {/* Right: checkpoint slide */}
 <div
 className={`min-w-0 flex-1 flex-col overflow-hidden md:flex ${
 mobileMode === 'detail' ? 'flex' : 'hidden'
 }`}
 >
 {/* Top bar */}
 <div className="h-10 flex items-center px-3 md:px-4 bg-surface-light/60 flex-shrink-0 gap-2 md:gap-3 shadow-[inset_0_-1px_0_rgb(var(--color-border)/0.035)]">
 <button
 onClick={() => setMobileMode('list')}
 className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg text-accent-muted hover:bg-surface-card/70 hover:text-text-primary md:hidden"
 title="Open lessons"
 aria-label="Open lessons"
 >
 <span className="material-symbols-outlined text-base">format_list_bulleted</span>
 </button>
 <h2 className="text-xs font-medium text-accent-muted truncate flex-1 min-w-0">
 {activeLesson.title}
 </h2>
 {isCompleted && (
 <span className="text-[9px] font-mono uppercase tracking-wider px-2 py-0.5 rounded bg-success/10 text-success/80 flex-shrink-0">
 Completed
 </span>
 )}
 <div className="flex items-center gap-0.5 flex-shrink-0">
 <button
 onClick={prevCheckpoint}
 disabled={checkpointIndex === 0}
 className="w-7 h-7 rounded-lg flex items-center justify-center text-accent-muted hover:text-text-primary hover:bg-surface-card/70 disabled:opacity-20 disabled:cursor-default transition-all"
 title="Previous checkpoint"
 >
 <span className="material-symbols-outlined text-base">chevron_left</span>
 </button>
 <button
 onClick={nextCheckpoint}
 disabled={checkpointIndex >= totalCheckpoints - 1}
 className="w-7 h-7 rounded-lg flex items-center justify-center text-accent-muted hover:text-text-primary hover:bg-surface-card/70 disabled:opacity-20 disabled:cursor-default transition-all"
 title="Next checkpoint"
 >
 <span className="material-symbols-outlined text-base">chevron_right</span>
 </button>
 </div>
 </div>

 {/* Checkpoint content */}
 <div className="flex-1 overflow-y-auto custom-scrollbar">
 {currentCp ? (
 <div className="max-w-4xl mx-auto px-4 py-5 space-y-6 animate-fade-in md:px-8 md:py-8 md:space-y-8" key={checkpointIndex}>
 {/* Checkpoint header */}
 <div className="space-y-3">
 <div className="flex flex-wrap items-center gap-2 md:gap-3">
 <span
 className={`text-[10px] font-mono uppercase tracking-wider px-2.5 py-1 rounded-md font-medium ${
 currentCp.difficulty === 'easy'
 ? 'bg-success/15 text-success border border-success/20'
 : currentCp.difficulty === 'hard'
 ? 'bg-danger/15 text-danger border border-danger/20'
 : 'bg-accent/15 text-accent'
 }`}
 >
 {currentCp.difficulty}
 </span>
 <span className="text-[10px] text-accent-muted/40 font-mono">
 {currentCp.num_questions} questions · Pass {currentCp.pass_threshold_percent}% · Mode: {currentCp.question_mode}
 </span>
 </div>
 <h1 className="text-2xl font-bold text-text-primary leading-tight md:text-3xl">
 {currentCp.title}
 </h1>
 </div>

 {/* Topic material card */}
 <div className="bg-surface-light/70 rounded-xl overflow-hidden shadow-[0_1px_2px_rgb(0_0_0/0.03)]">
 <div className="px-4 py-4 flex items-center gap-2 md:px-6">
 <span className="material-symbols-outlined text-accent text-base">auto_stories</span>
 <span className="text-xs font-mono uppercase tracking-wider text-accent-muted font-medium">
 Study Material
 </span>
 </div>
 <div className="px-4 py-5 space-y-4 md:px-6 md:py-6">
 {materialMarkdown ? (
 <div className="prose-custom text-sm text-text-secondary leading-relaxed">
 <ReactMarkdown remarkPlugins={[remarkMath]} rehypePlugins={[rehypeKatex]}>
 {normalizeLatexDelimiters(materialMarkdown)}
 </ReactMarkdown>
 </div>
 ) : (
 <>
 <p className="text-sm text-text-secondary leading-relaxed">
 This checkpoint covers the fundamentals of <strong className="text-text-primary">{currentCp.title}</strong>.
 Review the concepts below, then test your understanding with the quiz.
 </p>

 {/* Key concepts */}
 <div className="grid grid-cols-1 gap-3 mt-4 md:grid-cols-2">
 {getConceptsForCheckpoint(currentCp.title).map((concept, i) => (
 <div key={i} className="bg-surface/50 rounded-xl px-4 py-3">
 <div className="flex items-center gap-2 mb-1">
 <span className="material-symbols-outlined text-accent/60 text-sm">{concept.icon}</span>
 <span className="text-[10px] font-mono text-accent-muted/50 uppercase">{concept.label}</span>
 </div>
 <p className="text-xs text-text-secondary/70 leading-relaxed">{concept.text}</p>
 </div>
 ))}
 </div>
 </>
 )}

 {/* Difficulty indicator */}
 <div className="flex items-center gap-4 pt-2 text-[10px] font-mono text-accent-muted/40">
 <span>Questions: {currentCp.num_questions}</span>
 <span>·</span>
 <span>Pass: {currentCp.pass_threshold_percent}%</span>
 <span>·</span>
 <span>Mode: {currentCp.question_mode.toUpperCase()}</span>
 </div>
 </div>
 </div>

 {/* Selected chunks context */}
 {selectedChunkIds.size > 0 && (
 <div className="bg-accent/5 rounded-xl px-5 py-3 flex items-center gap-2">
 <span className="material-symbols-outlined text-accent text-sm">link</span>
 <span className="text-xs text-accent">
 {selectedChunkIds.size} document chunk{selectedChunkIds.size !== 1 ? 's' : ''} selected for quiz context
 </span>
 </div>
 )}

 {/* Actions */}
 <div className="sticky bottom-3 z-10 flex flex-col gap-2 rounded-2xl bg-surface/90 p-2 shadow-[0_8px_24px_rgb(0_0_0/0.08)] backdrop-blur md:bottom-4 md:flex-row md:gap-3">
 <button
 onClick={() => {
 if (!Array.isArray(currentCp.quiz) || currentCp.quiz.length === 0) {
 showToast('This checkpoint has no embedded quiz yet')
 return
 }
 setShowQuizPrompt((value) => !value)
 }}
 className="flex-1 py-3.5 bg-accent text-surface font-bold text-xs uppercase tracking-wider rounded-xl hover:bg-accent-hover transition-all flex items-center justify-center gap-2"
 >
 <span className="material-symbols-outlined text-base">quiz</span>
 {showQuizPrompt ? 'Hide Checkpoint Quiz' : 'Start Checkpoint Quiz'}
 </button>
 {checkpointIndex < totalCheckpoints - 1 && (
 <button
 onClick={nextCheckpoint}
 className="justify-center px-6 py-3.5 text-text-primary text-xs uppercase tracking-wider rounded-xl hover:bg-surface-card transition-all flex items-center gap-2"
 >
 Next checkpoint
 <span className="material-symbols-outlined text-sm">arrow_forward</span>
 </button>
 )}
 </div>

 {showQuizPrompt && (
 <LessonCheckpointQuiz
 lessonId={activeLesson.lesson_id}
 checkpointIndex={checkpointIndex}
 checkpointTitle={currentCp.title}
 passThreshold={currentCp.pass_threshold_percent}
 questions={(currentCp.quiz || []) as QuizQuestion[]}
 initialDraft={quizDraftKey ? quizDrafts[quizDraftKey] : undefined}
 onDraftChange={(draft) => {
  if (!quizDraftKey) return
  setQuizDrafts((drafts) => {
   if (!draft) {
    const next = { ...drafts }
    delete next[quizDraftKey]
    return next
   }
   return { ...drafts, [quizDraftKey]: draft }
  })
 }}
 onClose={() => setShowQuizPrompt(false)}
 onGraded={async (passed) => {
 const res = await listLessons()
 setLessons(Array.isArray(res) ? res : [])
 await refreshDashboard(50)
 showToast(passed ? 'Checkpoint passed' : 'Checkpoint needs another try')
 }}
 />
 )}

 {/* Quiz prompt modal */}
 {false && showQuizPrompt && (
 <div className="fixed inset-0 z-[110] bg-black/60 flex items-center justify-center p-6">
 <div className="bg-surface-lighter rounded-2xl w-full max-w-[480px] p-6 shadow-2xl glass-panel space-y-4">
 <div className="flex items-center gap-3">
 <span className="material-symbols-outlined text-accent text-xl">quiz</span>
 <div>
 <h3 className="text-sm font-bold text-text-primary">Checkpoint Quiz</h3>
 <p className="text-[10px] text-accent-muted font-mono">
 {currentCp.num_questions} questions · {currentCp.difficulty} · Pass {currentCp.pass_threshold_percent}%
 </p>
 </div>
 </div>
 <p className="text-xs text-text-secondary leading-relaxed">
 This will start a quiz on <strong className="text-text-primary">{currentCp.title}</strong>.
 The quiz will appear in your chat session. You can answer by typing A/B/C/D or your response.
 </p>
 <div className="flex gap-2 pt-2">
 <button
 onClick={() => setShowQuizPrompt(false)}
 className="flex-1 py-2.5 text-text-primary text-xs rounded-xl hover:bg-surface-card transition-all"
 >
 Cancel
 </button>
 <button
 onClick={() => {
 setShowQuizPrompt(false)
 // Navigate to chat and send /lesson quiz command
 useUIStore.getState().closeFullScreen()
 useUIStore.getState().setView('chat')
 }}
 className="flex-1 py-2.5 bg-accent text-surface font-bold text-xs uppercase tracking-wider rounded-xl hover:bg-accent-hover transition-all"
 >
 Go to Chat & Start Quiz
 </button>
 </div>
 </div>
 </div>
 )}
 </div>
 ) : (
 <div className="h-full flex items-center justify-center">
 <div className="text-center space-y-3">
 <span className="material-symbols-outlined text-success/30 text-6xl block">
 celebration
 </span>
 <p className="text-text-primary font-bold">All Checkpoints Complete!</p>
 <p className="text-accent-muted/40 text-xs">
 You've finished all {totalCheckpoints} checkpoints for this lesson.
 </p>
 <button
 onClick={() => handleRestart(activeLesson.lesson_id)}
 className="px-4 py-2 text-accent-muted text-xs rounded-lg hover:bg-surface-card transition-all"
 >
 Restart Lesson
 </button>
 </div>
 </div>
 )}
 </div>
 </div>
 </div>
 )
}

type LessonQuizPhase = 'quiz' | 'grading' | 'results'

function LessonCheckpointQuiz({
 lessonId,
 checkpointIndex,
 checkpointTitle,
 passThreshold,
 questions,
 initialDraft,
 onDraftChange,
 onClose,
 onGraded,
}: {
 lessonId: string
 checkpointIndex: number
 checkpointTitle: string
 passThreshold: number
 questions: QuizQuestion[]
 initialDraft?: LessonQuizDraft
 onDraftChange: (draft: LessonQuizDraft | null) => void
 onClose: () => void
 onGraded: (passed: boolean) => Promise<void>
}) {
 const [currentIdx, setCurrentIdx] = useState(initialDraft?.currentIdx ?? 0)
 const [answers, setAnswers] = useState<Record<string, string>>(initialDraft?.answers ?? {})
 const [phase, setPhase] = useState<LessonQuizPhase>('quiz')
 const [results, setResults] = useState<(QuizGradeResponse & { passed: boolean }) | null>(null)
 const [error, setError] = useState<string | null>(null)

 const question = questions[currentIdx]
 if (!question) return null

 const total = questions.length
 const selectedAnswer = answers[question.question_id] || ''
 const isMCQ = question.question_mode === 'mcq' && Array.isArray(question.options) && question.options.length > 0
 const answeredCount = Object.values(answers).filter((answer) => answer.trim()).length

 const updateAnswers = (nextAnswers: Record<string, string>) => {
  setAnswers(nextAnswers)
  onDraftChange({ currentIdx, answers: nextAnswers })
 }

 const resetQuiz = () => {
  setCurrentIdx(0)
  setAnswers({})
  setPhase('quiz')
  setResults(null)
  setError(null)
  onDraftChange(null)
 }

 const handleSubmit = async () => {
 setPhase('grading')
 setError(null)
 try {
 const answerList = Object.entries(answers)
 .filter(([, answer]) => answer.trim())
 .map(([question_id, answer]) => ({ question_id, answer }))
 const graded = await gradeLessonCheckpoint(lessonId, checkpointIndex, answerList)
 setResults(graded)
 setPhase('results')
 await onGraded(graded.passed)
 } catch (err) {
 setError(err instanceof Error ? err.message : 'Failed to grade checkpoint quiz')
 setPhase('quiz')
 }
 }

 const resultFor = (questionId: string): QuizGradedQuestion | undefined =>
 results?.results.find((result) => result.question_id === questionId)

 if (phase === 'results' && results) {
 return (
 <div className="bg-surface-light rounded-2xl p-6 space-y-5">
 <div className="flex items-center justify-between gap-4">
 <div className="flex items-center gap-3">
 <div
 className={`w-10 h-10 rounded-full flex items-center justify-center text-xs font-bold ${
 results.passed ? 'bg-success/20 text-success' : 'bg-danger/20 text-danger'
 }`}
 >
 {Math.round(results.score_percent)}%
 </div>
 <div>
 <h3 className="text-sm font-bold text-text-primary">
 {results.passed ? 'Checkpoint passed' : 'Try this checkpoint again'}
 </h3>
 <p className="text-[10px] text-accent-muted font-mono">
 {results.correct_answers}/{results.total_questions} correct - pass mark {passThreshold}%
 </p>
 </div>
 </div>
 </div>

 <div className="flex gap-2">
 {(!results.passed) && (
 <button
  onClick={resetQuiz}
  className="px-4 py-2 rounded-xl bg-accent text-surface text-xs font-bold uppercase tracking-wider hover:bg-accent-hover transition-all"
 >
 Retry Quiz
 </button>
 )}
 <button
 onClick={onClose}
 className="px-4 py-2 rounded-xl text-text-secondary text-xs hover:bg-surface-card transition-all"
 >
 Close
 </button>
 </div>

 <div className="space-y-3">
 {questions.map((q, index) => {
 const result = resultFor(q.question_id)
 return (
 <div key={q.question_id} className=" rounded-xl p-4 space-y-2">
 <div className="flex items-start gap-2">
 <span
 className={`w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold flex-shrink-0 ${
 result?.is_correct ? 'bg-success/20 text-success' : 'bg-danger/20 text-danger'
 }`}
 >
 {result?.is_correct ? 'OK' : 'NO'}
 </span>
 <div className="min-w-0">
 <p className="text-[10px] text-accent-muted font-mono">Q{index + 1}</p>
 <p className="text-sm text-text-primary leading-relaxed break-words">{q.question}</p>
 </div>
 </div>
 <div className="pl-8 text-xs text-accent-muted space-y-1 break-words">
 <p>Your answer: <span className="text-text-secondary">{result?.submitted_answer || '(empty)'}</span></p>
 {!result?.is_correct && (
 <p>Expected: <span className="text-success">{result?.expected_answer}</span></p>
 )}
 </div>
 </div>
 )
 })}
 </div>
 </div>
 )
 }

 return (
 <div className="bg-surface-light rounded-2xl p-6 space-y-5">
 <div className="flex items-center justify-between gap-4">
 <div className="flex items-center gap-2 min-w-0">
 <span className="material-symbols-outlined text-accent text-base">quiz</span>
 <div className="min-w-0">
 <h3 className="text-sm font-bold text-text-primary break-words">{checkpointTitle}</h3>
 <p className="text-[10px] text-accent-muted font-mono">
 Question {currentIdx + 1}/{total} - pass mark {passThreshold}%
 </p>
 </div>
 </div>
 {phase === 'grading' && (
 <div className="w-4 h-4 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
 )}
 </div>

 <div className="space-y-3">
 <p className="text-sm text-text-primary leading-relaxed break-words">{question.question}</p>
 {isMCQ ? (
 <div className="space-y-2">
 {question.options?.map((option, index) => {
 const selected = selectedAnswer === option
 return (
 <button
 key={option}
 onClick={() => updateAnswers({ ...answers, [question.question_id]: option })}
 disabled={phase === 'grading'}
 className={`w-full text-left px-4 py-3 rounded-xl border text-sm transition-all whitespace-normal break-words ${
 selected
 ? 'border-transparent bg-accent/10 text-text-primary'
 : 'border-transparent bg-surface/50 text-text-secondary hover:bg-accent/5'
 }`}
 >
 <span className="font-mono text-accent-muted mr-2">{String.fromCharCode(65 + index)})</span>
 {option}
 </button>
 )
 })}
 </div>
 ) : (
 <textarea
 value={selectedAnswer}
 onChange={(event) => updateAnswers({ ...answers, [question.question_id]: event.target.value })}
 disabled={phase === 'grading'}
 rows={4}
 placeholder="Type your answer..."
 className="w-full px-4 py-3 rounded-xl bg-surface text-sm text-text-primary placeholder-accent-muted/40 focus:border-accent focus:outline-none transition-all resize-y min-h-28"
 />
 )}
 </div>

 {error && <p className="text-xs text-danger">{error}</p>}

 <div className="flex items-center gap-2">
 <button
 onClick={() => {
 const nextIdx = Math.max(0, currentIdx - 1)
 setCurrentIdx(nextIdx)
 onDraftChange({ currentIdx: nextIdx, answers })
 }}
 disabled={currentIdx === 0 || phase === 'grading'}
 className="px-4 py-2 text-text-secondary text-xs rounded-xl hover:bg-surface-card disabled:opacity-30 disabled:cursor-default transition-all"
 >
 Prev
 </button>
 <button
 onClick={() => {
 const nextIdx = Math.min(total - 1, currentIdx + 1)
 setCurrentIdx(nextIdx)
 onDraftChange({ currentIdx: nextIdx, answers })
 }}
 disabled={currentIdx >= total - 1 || phase === 'grading'}
 className="px-4 py-2 text-text-secondary text-xs rounded-xl hover:bg-surface-card disabled:opacity-30 disabled:cursor-default transition-all"
 >
 Next
 </button>
 <div className="flex-1" />
 <button
 onClick={onClose}
 disabled={phase === 'grading'}
 className="px-4 py-2 text-text-secondary text-xs rounded-xl hover:bg-surface-card disabled:opacity-30 transition-all"
 >
 Save & Close
 </button>
 <button
 onClick={resetQuiz}
 disabled={phase === 'grading'}
 className="px-4 py-2 text-danger/80 text-xs rounded-xl hover:bg-danger/10 disabled:opacity-30 transition-all"
 >
 Reset
 </button>
 <button
 onClick={handleSubmit}
 disabled={answeredCount < total || phase === 'grading'}
 className="px-5 py-2 bg-accent text-surface font-bold text-xs uppercase tracking-wider rounded-xl hover:bg-accent-hover disabled:opacity-30 disabled:cursor-default transition-all whitespace-nowrap"
 >
 Submit ({answeredCount}/{total})
 </button>
 </div>
 </div>
 )
}

// Sub-component
function LessonListItem({
 lesson,
 isActive,
 onClick,
 onRestart,
 onDelete,
}: {
 lesson: Lesson
 isActive: boolean
 onClick: () => void
 onRestart: () => void
 onDelete: () => void
}) {
 const [showMenu, setShowMenu] = useState(false)
 const progress = lesson.total_checkpoints > 0
 ? Math.round((lesson.current_checkpoint / lesson.total_checkpoints) * 100)
 : 0

 return (
 <div className="relative group">
 <button
 onClick={onClick}
 className={`w-full text-left px-3 py-3 rounded-lg transition-all ${
 isActive
 ? 'bg-surface-card text-text-primary'
 : 'text-accent-muted hover:bg-surface-card/50 hover:text-text-primary'
 }`}
 >
 <div className="flex items-center gap-2">
 <span className="material-symbols-outlined text-accent text-sm">
 {lesson.status === 'completed' ? 'check_circle' : 'school'}
 </span>
 <span className="text-sm truncate flex-1">{lesson.title}</span>
 <button
 onClick={(e) => {
 e.stopPropagation()
 setShowMenu(!showMenu)
 }}
 className="opacity-0 group-hover:opacity-100 text-accent-muted/50 hover:text-accent-muted transition-all"
 >
 <span className="material-symbols-outlined text-sm">more_vert</span>
 </button>
 </div>
 <div className="flex items-center gap-2 mt-1.5 pl-6">
 <span
 className={`text-[9px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded ${
 lesson.status === 'completed'
 ? 'bg-success/20 text-success'
 : lesson.status === 'active' || lesson.status === 'in_progress'
 ? 'bg-accent/20 text-accent'
 : 'bg-surface-card/80 text-accent-muted/40'
 }`}
 >
 {lesson.status}
 </span>
 <span className="text-[10px] text-accent-muted/40 font-mono">
 {lesson.current_checkpoint + 1}/{lesson.total_checkpoints}
 </span>
 </div>
 {/* Mini progress bar */}
 <div className="mt-1.5 pl-6">
 <div className="h-1 bg-surface rounded-full overflow-hidden">
 <div
 className={`h-full rounded-full transition-all ${
 lesson.status === 'completed' ? 'bg-success' : 'bg-accent/60'
 }`}
 style={{ width: `${progress}%` }}
 />
 </div>
 </div>
 </button>

 {/* Context menu */}
 {showMenu && (
 <>
 <div className="fixed inset-0 z-40" onClick={() => setShowMenu(false)} />
 <div className="absolute right-0 top-full mt-1 z-50 bg-surface-lighter rounded-xl shadow-2xl py-1 w-36">
 <button
 onClick={(e) => {
 e.stopPropagation()
 setShowMenu(false)
 onRestart()
 }}
 className="w-full text-left px-3 py-2 text-xs text-accent-muted hover:bg-surface-card hover:text-text-primary transition-all flex items-center gap-2"
 >
 <span className="material-symbols-outlined text-sm">restart_alt</span>
 Restart
 </button>
 <button
 onClick={(e) => {
 e.stopPropagation()
 setShowMenu(false)
 onDelete()
 }}
 className="w-full text-left px-3 py-2 text-xs text-danger hover:bg-danger/10 transition-all flex items-center gap-2"
 >
 <span className="material-symbols-outlined text-sm">delete</span>
 Delete
 </button>
 </div>
 </>
 )}
 </div>
 )
}

// Helper for generating concept cards per checkpoint
function getConceptsForCheckpoint(title: string) {
 const concepts = [
 { icon: 'lightbulb', label: 'Core Concept', text: 'Understanding the fundamental principles and theoretical framework behind the topic.' },
 { icon: 'account_tree', label: 'Structure', text: 'How the components relate to each other and form a coherent system.' },
 { icon: 'psychology', label: 'Application', text: 'Practical use cases and real-world examples that demonstrate the concept.' },
 { icon: 'warning', label: 'Common Pitfalls', text: 'Frequently encountered mistakes and how to avoid them.' },
 ]
 return concepts
}
