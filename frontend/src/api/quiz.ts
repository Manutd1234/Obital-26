import { api } from './client'

export interface QuizQuestion {
  question_id: string
  topic_id: string
  topic_title: string
  question: string
  expected_answer: string
  difficulty: string
  source_context: string | null
  question_mode: 'open' | 'mcq'
  options: string[] | null
  correct_option_index: number | null
}

export interface QuizBuildResponse {
  quiz_id: string
  topic_filter: string | null
  exclude_topic_filter: string | null
  difficulty_filter: string | null
  build_indicator: string
  failure_reason: string
  questions: QuizQuestion[]
  warnings: string[]
}

export interface QuizAnswer {
  question_id: string
  answer: string
}

export interface QuizGradedQuestion {
  question_id: string
  is_correct: boolean
  expected_answer: string
  submitted_answer: string
  score: number
}

export interface QuizGradeResponse {
  quiz_id: string
  total_questions: number
  answered_questions: number
  correct_answers: number
  score_percent: number
  results: QuizGradedQuestion[]
}

export async function buildQuiz(
  topic: string,
  numQuestions?: number,
  questionMode?: string,
  difficulty?: string,
) {
  return api.post<QuizBuildResponse>('/quiz/build', {
    topic,
    num_questions: numQuestions ?? 5,
    question_mode: questionMode ?? 'mcq',
    difficulty,
  })
}

export async function gradeQuiz(quiz: QuizBuildResponse, answers: QuizAnswer[]) {
  return api.post<QuizGradeResponse>('/quiz/grade', { quiz, answers })
}
