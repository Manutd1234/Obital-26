import { useState, useCallback } from 'react'
import { gradeQuiz } from '../../api/quiz'
import type { QuizAnswer, QuizBuildResponse, QuizGradeResponse, QuizGradedQuestion } from '../../api/quiz'
import { api } from '../../api/client'
import { listLessons } from '../../api/lessons'
import { useChatStore } from '../../stores/chatStore'
import { useDashboardStore } from '../../stores/dashboardStore'
import { useLessonStore } from '../../stores/lessonStore'

interface Props {
 data: unknown
 onGrade?: (quiz: QuizBuildResponse, answers: QuizAnswer[]) => Promise<QuizGradeResponse>
}

type Phase = 'quiz' | 'grading' | 'results'

export function QuizWidget({ data, onGrade }: Props) {
 const quiz = data as QuizBuildResponse
 const [currentIdx, setCurrentIdx] = useState(0)
 const [answers, setAnswers] = useState<Record<string, string>>({})
 const [phase, setPhase] = useState<Phase>('quiz')
 const [results, setResults] = useState<QuizGradeResponse | null>(null)
 const [openInput, setOpenInput] = useState('')

 const questions = quiz.questions || []
 const question = questions[currentIdx]
 if (!question) return null

 const total = questions.length
 const isMCQ = question.question_mode === 'mcq' && question.options
 const selectedAnswer = answers[question.question_id]

 const handleSelect = (optionText: string) => {
 if (phase !== 'quiz') return
 setAnswers((prev) => ({ ...prev, [question.question_id]: optionText }))
 }

 const handleOpenSubmit = () => {
 if (!openInput.trim()) return
 setAnswers((prev) => ({ ...prev, [question.question_id]: openInput.trim() }))
 setOpenInput('')
 }

 const handlePrev = () => {
 if (currentIdx > 0) setCurrentIdx(currentIdx - 1)
 }

 const handleNext = () => {
 if (currentIdx < total - 1) setCurrentIdx(currentIdx + 1)
 }

  const handleSubmitQuiz = async () => {
    setPhase('grading')
    try {
      const answerList = Object.entries(answers).map(([question_id, answer]) => ({
        question_id,
        answer,
      }))
      const res = await (onGrade ? onGrade(quiz, answerList) : gradeQuiz(quiz, answerList))
      setResults(res)
      setPhase('results')
      // Refresh lesson store so dashboard picks up updated state
      listLessons()
        .then((lessons) => { useLessonStore.getState().setLessons(Array.isArray(lessons) ? lessons : []) })
        .catch(() => {})
      useDashboardStore.getState().refreshDashboard(50).catch(() => {})
      // Clear backend's active quiz state so next chat message isn't treated as quiz answer
      const sessionId = useChatStore.getState().activeSessionId
      if (sessionId) {
        api.post(`/chat/sessions/${sessionId}/messages`, { message: '/exit' }).catch(() => {})
      }
    } catch (err) {
      console.error('Grade quiz failed:', err)
      setPhase('quiz')
    }
  }

 const getResult = (questionId: string): QuizGradedQuestion | undefined =>
 results?.results.find((r) => r.question_id === questionId)

 const getOptionClass = (optionText: string) => {
 if (phase === 'quiz') {
 return selectedAnswer === optionText
 ? 'border-transparent bg-accent/10 font-medium'
 : 'border-transparent bg-surface/50 hover:bg-accent/5'
 }
 if (phase === 'results') {
 const result = getResult(question.question_id)
 if (result?.is_correct && optionText === selectedAnswer) {
 return 'border-success bg-success/10'
 }
 if (!result?.is_correct && optionText === selectedAnswer) {
 return 'border-danger bg-danger/10'
 }
 if (!result?.is_correct && optionText === result?.expected_answer) {
 return 'border-success bg-success/5 opacity-70'
 }
 return 'border-transparent opacity-40'
 }
 return 'border-transparent opacity-50'
 }

 // Results screen
 if (phase === 'results' && results) {
 const pct = Math.round(results.score_percent)
 return (
 <div className="bg-surface-light rounded-xl p-5 space-y-4">
 {/* Score header */}
 <div className="flex items-center gap-3">
 <div className={`w-8 h-8 rounded-full flex items-center justify-center text-[11px] font-bold ${
 pct >= 70 ? 'bg-success/20 text-success' : 'bg-danger/20 text-danger'
 }`}>
 {pct}%
 </div>
 <div>
 <div className="text-sm font-semibold text-text-primary">
 {results.correct_answers}/{results.total_questions} correct
 </div>
 <div className="text-[10px] text-accent-muted font-mono">
 {results.answered_questions} answered
 </div>
 </div>
 </div>

 {/* Per-question review */}
 <div className="space-y-3">
 {questions.map((q, i) => {
 const r = getResult(q.question_id)
 return (
 <div key={q.question_id} className=" rounded-lg p-3 space-y-2">
 <div className="flex items-start gap-2">
 <span className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold flex-shrink-0 mt-0.5 ${
 r?.is_correct ? 'bg-success/20 text-success' : 'bg-danger/20 text-danger'
 }`}>
 {r?.is_correct ? '✓' : '✗'}
 </span>
 <div className="min-w-0">
 <div className="text-xs text-accent-muted font-mono">
 Q{i + 1} · {q.difficulty}
 </div>
 <p className="text-sm text-text-primary mt-1">{q.question}</p>
 </div>
 </div>

 {/* Show options with correct/incorrect marking */}
 {q.options && (
 <div className="ml-7 space-y-1">
 {q.options.map((opt, j) => {
 const label = String.fromCharCode(65 + j)
 const isYourAnswer = r?.submitted_answer === opt || r?.submitted_answer === label
 const isCorrect = r?.expected_answer === opt || r?.expected_answer === label
 return (
 <div
 key={j}
 className={`text-xs px-3 py-1.5 rounded border ${
 isYourAnswer && r?.is_correct
 ? 'border-success bg-success/10 text-success'
 : isYourAnswer && !r?.is_correct
 ? 'border-danger bg-danger/10 text-danger'
 : isCorrect && !r?.is_correct
 ? 'border-success/50 bg-success/5 text-success/70'
 : 'border-transparent text-accent-muted/50'
 }`}
 >
 <span className="font-mono mr-1.5">{label})</span>
 {opt}
 {isYourAnswer && !r?.is_correct && (
 <span className="ml-1.5 text-[10px]">(your answer)</span>
 )}
 {isCorrect && !r?.is_correct && (
 <span className="ml-1.5 text-[10px]">(correct)</span>
 )}
 </div>
 )
 })}
 </div>
 )}

 {/* Open-ended: show expected vs submitted */}
 {!q.options && r && (
 <div className="ml-7 space-y-1 text-xs">
 <div>
 <span className="text-accent-muted">Your answer:</span>{' '}
 <span className={r.is_correct ? 'text-success' : 'text-danger'}>
 {r.submitted_answer || '(empty)'}
 </span>
 </div>
 {!r.is_correct && (
 <div>
 <span className="text-accent-muted">Expected:</span>{' '}
 <span className="text-success">{r.expected_answer}</span>
 </div>
 )}
 </div>
 )}
 </div>
 )
 })}
 </div>
 </div>
 )
 }

 // Grading spinner
 if (phase === 'grading') {
 return (
 <div className="bg-surface-light rounded-xl p-5 flex items-center gap-3">
 <div className="w-4 h-4 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
 <span className="text-xs text-accent-muted font-mono">Grading answers...</span>
 </div>
 )
 }

 // Quiz phase
 return (
 <div className="bg-surface-light rounded-xl p-5 space-y-4">
 {/* Header */}
 <div className="flex items-center justify-between">
 <div className="flex items-center gap-2">
 <span className="material-symbols-outlined text-accent text-base">quiz</span>
 <span className="text-[10px] text-accent-muted font-mono uppercase tracking-wider">
 Question {currentIdx + 1}/{total}
 {question.difficulty && ` · ${question.difficulty}`}
 </span>
 </div>
 {quiz.topic_filter && (
 <span className="text-[10px] text-accent-muted/50 font-mono">{quiz.topic_filter}</span>
 )}
 </div>

 {/* Question */}
 <p className="text-sm text-text-primary leading-relaxed">{question.question}</p>

 {/* MCQ Options */}
 {isMCQ && question.options && (
 <div className="space-y-2">
 {question.options.map((opt, i) => {
 const label = String.fromCharCode(65 + i)
 return (
 <button
 key={i}
 onClick={() => handleSelect(opt)}
 className={`w-full text-left px-4 py-3 rounded-lg border text-sm transition-all ${getOptionClass(opt)}`}
 >
 <span className="font-mono text-accent-muted mr-2">{label})</span>
 {opt}
 </button>
 )
 })}
 </div>
 )}

 {/* Open-ended input */}
 {!isMCQ && (
 <div className="space-y-2">
 <input
 type="text"
 value={selectedAnswer || openInput}
 onChange={(e) => {
 setOpenInput(e.target.value)
 setAnswers((prev) => ({ ...prev, [question.question_id]: e.target.value }))
 }}
 onKeyDown={(e) => {
 if (e.key === 'Enter' && openInput.trim()) handleOpenSubmit()
 }}
 placeholder="Type your answer..."
 className="w-full px-4 py-3 rounded-lg bg-surface text-sm text-text-primary placeholder-accent-muted/40 focus:border-accent focus:outline-none transition-all"
 />
 </div>
 )}

 {/* Navigation */}
 <div className="flex gap-2 pt-2">
 {currentIdx > 0 && (
 <button
 onClick={handlePrev}
 className="px-4 py-2 bg-surface-card text-text-secondary text-xs rounded-lg hover:bg-surface-lighter transition-all"
 >
 ← Prev
 </button>
 )}

 <div className="flex-1" />

 {currentIdx < total - 1 ? (
 <button
 onClick={handleNext}
 className="px-4 py-2 bg-surface-card text-text-primary text-xs rounded-lg hover:bg-surface-lighter transition-all"
 >
 Next →
 </button>
 ) : (
 <button
 onClick={handleSubmitQuiz}
 disabled={Object.keys(answers).length < total}
 className="px-6 py-2 bg-accent text-surface font-bold text-xs uppercase tracking-wider rounded-lg disabled:opacity-30 disabled:cursor-default hover:bg-accent-hover transition-all"
 >
 Submit Quiz ({Object.keys(answers).length}/{total})
 </button>
 )}
 </div>
 </div>
 )
}
