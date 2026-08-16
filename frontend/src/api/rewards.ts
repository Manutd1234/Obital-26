import { api } from './client'

export interface DashboardData {
  summary: {
    total_points: number
    total_quizzes: number
    accuracy_percent: number
    total_correct: number
    total_answered: number
  }
  trend: {
    x: number
    quiz_id: string
    score_percent: number
    points_earned: number
    cumulative_points: number
    submitted_at: string
  }[]
  session_logs: {
    session_id: string
    started_at: string
    last_event_at: string
    points_delta: number
    quizzes_delta: number
    events: number
  }[]
}

export async function getDashboard(limit = 50) {
  return api.get<DashboardData>(`/rewards/dashboard?limit=${limit}`)
}
