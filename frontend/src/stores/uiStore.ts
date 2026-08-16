import { create } from 'zustand'

export type ViewMode = 'chat' | 'doc' | 'lesson' | 'dashboard' | 'pipeline' | 'bin' | 'leaderboard' | 'workspace' | 'shop'
export type ThemeMode = 'light' | 'dark'
export type SettingsTab = 'prompt' | 'account' | 'sync'

interface UIState {
  view: ViewMode
  theme: ThemeMode
  sideNavCollapsed: boolean
  sidebarCollapsed: boolean
  fullScreenView: ViewMode | null
  settingsOpen: boolean
  settingsTab: SettingsTab
  friendsOpen: boolean
  toastMessage: string | null
  setView: (v: ViewMode) => void
  setTheme: (theme: ThemeMode) => void
  toggleTheme: () => void
  toggleSideNav: () => void
  toggleSidebar: () => void
  openFullScreen: (v: ViewMode) => void
  closeFullScreen: () => void
  openSettings: (tab?: SettingsTab) => void
  closeSettings: () => void
  openFriends: () => void
  closeFriends: () => void
  showToast: (msg: string) => void
  clearToast: () => void
}

const savedTheme = (localStorage.getItem('obital_theme') as ThemeMode | null) || 'light'
document.documentElement.classList.toggle('dark', savedTheme === 'dark')

export const useUIStore = create<UIState>((set) => ({
  view: 'chat',
  theme: savedTheme,
  sideNavCollapsed: false,
  sidebarCollapsed: false,
  fullScreenView: null,
  settingsOpen: false,
  settingsTab: 'prompt',
  friendsOpen: false,
  toastMessage: null,
  setView: (v) => set({ view: v }),
  setTheme: (theme) => {
    localStorage.setItem('obital_theme', theme)
    document.documentElement.classList.toggle('dark', theme === 'dark')
    set({ theme })
  },
  toggleTheme: () =>
    set((s) => {
      const theme = s.theme === 'dark' ? 'light' : 'dark'
      localStorage.setItem('obital_theme', theme)
      document.documentElement.classList.toggle('dark', theme === 'dark')
      return { theme }
    }),
  toggleSideNav: () => set((s) => ({ sideNavCollapsed: !s.sideNavCollapsed })),
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  openFullScreen: (v) => set({ fullScreenView: v }),
  closeFullScreen: () => set({ fullScreenView: null }),
  openSettings: (tab = 'prompt') => set({ settingsOpen: true, settingsTab: tab }),
  closeSettings: () => set({ settingsOpen: false }),
  openFriends: () => set({ friendsOpen: true }),
  closeFriends: () => set({ friendsOpen: false }),
  showToast: (msg) => {
    set({ toastMessage: msg })
    setTimeout(() => set({ toastMessage: null }), 3000)
  },
  clearToast: () => set({ toastMessage: null }),
}))
