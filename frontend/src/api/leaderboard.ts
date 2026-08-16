import { api } from './client'

export interface LeaderboardEntry {
  rank: number
  account_id: string
  display_name: string
  total_points: number
  total_quizzes: number
  accuracy_percent: number
}

export interface LeaderboardResponse {
  entries: LeaderboardEntry[]
  total_users: number
  me: LeaderboardEntry | null
}

// account_id is attached automatically by the client wrapper — do not add it here.
export async function getLeaderboard(limit = 50) {
  return api.get<LeaderboardResponse>(`/leaderboard?limit=${limit}`)
}
