import { create } from 'zustand'
import { getLeaderboard, type LeaderboardResponse } from '../api/leaderboard'

interface LeaderboardState {
  data: LeaderboardResponse | null
  loading: boolean
  error: string | null
  lastFetchedAt: number | null
  fetchLeaderboard: (limit?: number) => Promise<LeaderboardResponse | null>
}

export const useLeaderboardStore = create<LeaderboardState>((set) => ({
  data: null,
  loading: false,
  error: null,
  lastFetchedAt: null,
  fetchLeaderboard: async (limit = 50) => {
    set({ loading: true, error: null })
    try {
      const data = await getLeaderboard(limit)
      set({ data, loading: false, lastFetchedAt: Date.now() })
      return data
    } catch (err) {
      set({
        loading: false,
        error: err instanceof Error ? err.message : 'Failed to load leaderboard',
      })
      return null
    }
  },
}))
