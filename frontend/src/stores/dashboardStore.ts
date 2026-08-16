import { create } from 'zustand'
import { getDashboard, type DashboardData } from '../api/rewards'

// The dashboard is shared by the full-screen view and the profile shortcut.
// Keeping the fetch state here prevents duplicate requests when both mount
// during the same navigation and gives the UI one consistent error state.

interface DashboardState {
  data: DashboardData | null
  loading: boolean
  error: string | null
  lastFetchedAt: number | null
  refreshDashboard: (limit?: number) => Promise<DashboardData | null>
}

export const useDashboardStore = create<DashboardState>((set) => ({
  data: null,
  loading: false,
  error: null,
  lastFetchedAt: null,
  refreshDashboard: async (limit = 50) => {
    set({ loading: true, error: null })
    try {
      const data = await getDashboard(limit)
      set({ data, loading: false, lastFetchedAt: Date.now() })
      return data
    } catch (err) {
      set({
        loading: false,
        error: err instanceof Error ? err.message : 'Failed to refresh dashboard',
      })
      return null
    }
  },
}))
