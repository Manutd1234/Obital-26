import { create } from 'zustand'
import {
  emptyWorkspace,
  getWorkspace,
  putWorkspace,
  type WorkspaceData,
  type WorkspaceSession,
} from '../api/workspace'

type CodeLang = 'js' | 'python'

interface WorkspaceState {
  data: WorkspaceData
  loading: boolean
  loaded: boolean
  saving: boolean
  error: string | null
  load: () => Promise<void>
  setCode: (lang: CodeLang, text: string) => void
  addTask: (dateKey: string, text: string) => void
  toggleTask: (dateKey: string, id: string) => void
  removeTask: (dateKey: string, id: string) => void
  addSession: (session: WorkspaceSession) => void
}

function newId(): string {
  try {
    return crypto.randomUUID()
  } catch {
    return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  }
}

let saveTimer: ReturnType<typeof setTimeout> | null = null

export const useWorkspaceStore = create<WorkspaceState>((set, get) => {
  // Debounced full-document save. Errors are surfaced but non-fatal (local state is source of truth).
  const scheduleSave = () => {
    if (saveTimer) clearTimeout(saveTimer)
    saveTimer = setTimeout(async () => {
      saveTimer = null
      set({ saving: true })
      try {
        await putWorkspace(get().data)
        set({ saving: false, error: null })
      } catch (err) {
        set({ saving: false, error: err instanceof Error ? err.message : 'Failed to save workspace' })
      }
    }, 800)
  }

  return {
    data: emptyWorkspace(),
    loading: false,
    loaded: false,
    saving: false,
    error: null,

    load: async () => {
      if (get().loaded || get().loading) return
      set({ loading: true, error: null })
      try {
        const data = await getWorkspace()
        set({ data: { ...emptyWorkspace(), ...data }, loading: false, loaded: true })
      } catch (err) {
        set({
          loading: false,
          loaded: true, // allow local editing even if the fetch failed
          error: err instanceof Error ? err.message : 'Failed to load workspace',
        })
      }
    },

    setCode: (lang, text) => {
      set((s) => ({ data: { ...s.data, code: { ...s.data.code, [lang]: text } } }))
      scheduleSave()
    },

    addTask: (dateKey, text) => {
      const trimmed = text.trim()
      if (!trimmed) return
      set((s) => {
        const day = s.data.tasks[dateKey] ?? []
        return {
          data: {
            ...s.data,
            tasks: { ...s.data.tasks, [dateKey]: [...day, { id: newId(), text: trimmed, done: false }] },
          },
        }
      })
      scheduleSave()
    },

    toggleTask: (dateKey, id) => {
      set((s) => {
        const day = s.data.tasks[dateKey] ?? []
        return {
          data: {
            ...s.data,
            tasks: {
              ...s.data.tasks,
              [dateKey]: day.map((t) => (t.id === id ? { ...t, done: !t.done } : t)),
            },
          },
        }
      })
      scheduleSave()
    },

    removeTask: (dateKey, id) => {
      set((s) => {
        const day = s.data.tasks[dateKey] ?? []
        const next = day.filter((t) => t.id !== id)
        const tasks = { ...s.data.tasks }
        if (next.length) tasks[dateKey] = next
        else delete tasks[dateKey]
        return { data: { ...s.data, tasks } }
      })
      scheduleSave()
    },

    addSession: (session) => {
      set((s) => ({ data: { ...s.data, sessions: [...s.data.sessions, session] } }))
      scheduleSave()
    },
  }
})

export const makeSessionId = newId
