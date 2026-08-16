import { useState } from 'react'
import { useWorkspaceStore } from '../../stores/workspaceStore'
import { todayKey } from '../../lib/dateUtils'

interface StudyGoalsProps {
  dateKey?: string
  subtitle?: string
  compact?: boolean
}

export function StudyGoals({ dateKey, subtitle, compact = false }: StudyGoalsProps) {
  const key = dateKey ?? todayKey()
  const tasks = useWorkspaceStore((s) => s.data.tasks[key] ?? [])
  const addTask = useWorkspaceStore((s) => s.addTask)
  const toggleTask = useWorkspaceStore((s) => s.toggleTask)
  const removeTask = useWorkspaceStore((s) => s.removeTask)

  const [draft, setDraft] = useState('')
  const done = tasks.filter((t) => t.done).length
  const total = tasks.length
  const pct = total ? Math.round((done / total) * 100) : 0

  const submit = () => {
    addTask(key, draft)
    setDraft('')
  }

  return (
    <div className="bg-surface-light border border-border/5 rounded-xl p-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-sm font-semibold text-text-primary uppercase tracking-wider font-mono">
            Study Goals
          </h3>
          {subtitle && <p className="mt-1 text-[11px] text-accent-muted/60 font-mono">{subtitle}</p>}
        </div>
        <span className="flex-shrink-0 rounded-full bg-accent/15 px-3 py-1 font-mono text-sm font-bold text-accent">
          {done}/{total}
        </span>
      </div>

      {/* Progress bar */}
      <div className="mt-3 h-2 overflow-hidden rounded-full bg-surface">
        <div
          className="h-full rounded-full bg-accent transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>

      {/* Add input */}
      <div className="mt-4 flex gap-2">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') submit()
          }}
          placeholder="Add a study goal"
          className="flex-1 rounded-lg border border-border/10 bg-surface px-3 py-2 text-sm text-text-primary placeholder:text-accent-muted/40 focus:border-accent/40 focus:outline-none"
        />
        <button
          onClick={submit}
          aria-label="Add goal"
          className="flex h-9 w-10 items-center justify-center rounded-lg bg-success text-surface transition-colors hover:opacity-90"
        >
          <span className="material-symbols-outlined text-lg">add</span>
        </button>
      </div>

      {/* Task list */}
      <div className={`mt-3 space-y-2 ${compact ? 'max-h-[220px] overflow-y-auto custom-scrollbar' : ''}`}>
        {tasks.length === 0 && (
          <p className="py-4 text-center text-xs text-accent-muted/40">No goals yet for this day.</p>
        )}
        {tasks.map((task) => (
          <div
            key={task.id}
            className="group flex items-center gap-3 rounded-lg border border-border/10 bg-surface px-3 py-2"
          >
            <button
              onClick={() => toggleTask(key, task.id)}
              aria-label={task.done ? 'Mark incomplete' : 'Mark complete'}
              className={`flex h-5 w-5 flex-shrink-0 items-center justify-center rounded border transition-colors ${
                task.done ? 'border-success bg-success text-surface' : 'border-border/30 hover:border-accent'
              }`}
            >
              {task.done && <span className="material-symbols-outlined text-sm">check</span>}
            </button>
            <span
              className={`flex-1 text-sm ${
                task.done ? 'text-accent-muted/50 line-through' : 'text-text-primary'
              }`}
            >
              {task.text}
            </span>
            <button
              onClick={() => removeTask(key, task.id)}
              aria-label="Delete goal"
              className="flex-shrink-0 text-accent-muted/30 transition-colors hover:text-danger"
            >
              <span className="material-symbols-outlined text-base">delete</span>
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
