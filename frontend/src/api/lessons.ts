import { api } from './client'
import type { QuizAnswer, QuizGradeResponse } from './quiz'

export interface LessonSummary {
  lesson_id: string
  title: string
  topic: string
  status: string
  current_checkpoint: number
  total_checkpoints: number
  checkpoints: {
    title: string
    difficulty: string
    num_questions: number
    pass_threshold_percent: number
    question_mode: string
    markdown?: string | null
    quiz?: unknown[]
    sources?: string[]
  }[]
}

interface BackendLesson {
  id: string
  title: string
  objective: string
  content_blocks: string[]
  checkpoints: LessonSummary['checkpoints']
  status: string
  current_checkpoint: number
  total_checkpoints: number
  tags?: string[]
}

function mapLesson(l: BackendLesson): LessonSummary {
  return {
    lesson_id: l.id,
    title: l.title,
    topic: l.objective,
    status: l.status,
    current_checkpoint: l.current_checkpoint,
    total_checkpoints: l.total_checkpoints,
    checkpoints: l.checkpoints || [],
  }
}

export async function listLessons(source?: 'account' | 'curriculum') {
  const qs = source ? `?source=${source}` : ''
  const res = await api.get<BackendLesson[]>(`/lessons${qs}`)
  return res.map(mapLesson)
}

export async function startLesson(topic: string, title?: string) {
  const res = await api.post<{
    lesson_id: string
    title: string
    topic: string
    checkpoints: LessonSummary['checkpoints']
    current_checkpoint_index: number
  }>('/lessons', { topic, title })
  return {
    lesson_id: res.lesson_id,
    title: res.title,
    topic: res.topic,
    status: 'in_progress',
    current_checkpoint: res.current_checkpoint_index,
    total_checkpoints: res.checkpoints?.length ?? 3,
    checkpoints: res.checkpoints || [],
  }
}

export async function restartLesson(lessonId: string) {
  await api.post<{ status: string; lesson_id: string }>(`/lessons/${lessonId}/restart`)
}

export async function deleteLesson(lessonId: string) {
  return api.del<{ status: string; lesson_id: string }>(`/lessons/${lessonId}`)
}

export async function attemptLesson(
  lessonId: string,
  checkpointIndex: number,
  quizId: string,
  scorePercent: number,
  passed: boolean,
) {
  return api.post<{ lesson_id: string; current_checkpoint_index: number; completed: boolean }>(
    `/lessons/${lessonId}/attempt`,
    {
      checkpoint_index: checkpointIndex,
      quiz_id: quizId,
      score_percent: scorePercent,
      passed,
    },
  )
}

export interface LessonGenerationJob {
  job_id: string
  type: 'lesson_generation'
  status:
    | 'queued'
    | 'planning'
    | 'generating'
    | 'reviewing'
    | 'saving'
    | 'succeeded'
    | 'failed'
    | 'rejected'
    | 'canceled'
  phase: string
  progress: number
  created_at: string
  updated_at: string
  error?: string | null
  rejection_reason?: string | null
  result?: unknown
  request?: Record<string, unknown>
  subtasks?: Array<{
    name: string
    label?: string
    status: string
    phase?: string
    progress?: number
    error?: string | null
  }>
}

export async function generateLesson(topic: string, title?: string, useOnline = false) {
  return api.post<LessonGenerationJob>('/lessons/generate', {
    topic,
    title,
    use_online: useOnline,
  })
}

export async function getLessonGenerationJob(jobId: string) {
  return api.get<LessonGenerationJob>(`/lessons/jobs/${jobId}`)
}

export async function cancelLessonGenerationJob(jobId: string) {
  return api.post<LessonGenerationJob>(`/lessons/jobs/${jobId}/cancel`)
}

export async function listLessonGenerationJobs(limit = 10) {
  return api.get<LessonGenerationJob[]>(`/lessons/jobs?limit=${limit}`)
}

export interface LessonGradeResponse extends QuizGradeResponse {
  passed: boolean
}

export async function gradeLessonCheckpoint(
  lessonId: string,
  checkpointIndex: number,
  answers: QuizAnswer[],
) {
  return api.post<LessonGradeResponse>(
    `/lessons/${lessonId}/checkpoints/${checkpointIndex}/grade`,
    { answers },
  )
}
