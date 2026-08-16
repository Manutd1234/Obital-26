import { useMemo, useState } from 'react'
import { useWorkspaceStore } from '../../stores/workspaceStore'
import { StudyGoals } from './StudyGoals'
import {
  MONTH_NAMES,
  WEEKDAY_LABELS,
  buildMonthGrid,
  dateKey,
  formatLongDate,
  todayKey,
} from '../../lib/dateUtils'

export function WorkspaceCalendar() {
  const tasks = useWorkspaceStore((s) => s.data.tasks)
  const today = new Date()
  const [cursor, setCursor] = useState({ year: today.getFullYear(), month: today.getMonth() })
  const [selected, setSelected] = useState(todayKey())

  const grid = useMemo(() => buildMonthGrid(cursor.year, cursor.month), [cursor])
  const todayK = todayKey()

  const moveMonth = (delta: number) => {
    setCursor((c) => {
      const d = new Date(c.year, c.month + delta, 1)
      return { year: d.getFullYear(), month: d.getMonth() }
    })
  }

  const selectedDate = new Date(`${selected}T00:00:00`)
  const selectedSubtitle = selected === todayK ? `Today • ${formatLongDate(selectedDate)}` : formatLongDate(selectedDate)

  return (
    <div className="custom-scrollbar h-full overflow-y-auto p-6">
      <div className="mx-auto grid max-w-[980px] grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Calendar */}
        <div className="bg-surface-light border border-border/5 rounded-xl p-5">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="font-mono text-sm font-semibold uppercase tracking-wider text-text-primary">
              {MONTH_NAMES[cursor.month]} {cursor.year}
            </h3>
            <div className="flex gap-1">
              <button
                onClick={() => moveMonth(-1)}
                aria-label="Previous month"
                className="flex h-7 w-7 items-center justify-center rounded-lg text-accent-muted hover:bg-surface-card hover:text-text-primary"
              >
                <span className="material-symbols-outlined text-base">chevron_left</span>
              </button>
              <button
                onClick={() => setCursor({ year: today.getFullYear(), month: today.getMonth() })}
                className="rounded-lg px-2 font-mono text-[11px] text-accent-muted hover:bg-surface-card hover:text-text-primary"
              >
                today
              </button>
              <button
                onClick={() => moveMonth(1)}
                aria-label="Next month"
                className="flex h-7 w-7 items-center justify-center rounded-lg text-accent-muted hover:bg-surface-card hover:text-text-primary"
              >
                <span className="material-symbols-outlined text-base">chevron_right</span>
              </button>
            </div>
          </div>

          <div className="mb-1 grid grid-cols-7 gap-1">
            {WEEKDAY_LABELS.map((w) => (
              <div key={w} className="py-1 text-center font-mono text-[10px] uppercase text-accent-muted/50">
                {w}
              </div>
            ))}
          </div>

          <div className="grid grid-cols-7 gap-1">
            {grid.map((cell) => {
              const dayTasks = tasks[cell.key] ?? []
              const count = dayTasks.length
              const allDone = count > 0 && dayTasks.every((t) => t.done)
              const isSelected = cell.key === selected
              const isToday = cell.key === todayK
              return (
                <button
                  key={cell.key}
                  onClick={() => setSelected(cell.key)}
                  className={`relative flex h-12 flex-col items-center justify-center rounded-lg border text-sm transition-colors ${
                    isSelected
                      ? 'border-accent bg-accent/10 text-text-primary'
                      : 'border-transparent hover:bg-surface-card'
                  } ${cell.inMonth ? 'text-text-primary' : 'text-accent-muted/30'}`}
                >
                  <span className={isToday ? 'font-bold text-accent' : ''}>{cell.date.getDate()}</span>
                  {count > 0 && (
                    <span
                      className={`absolute bottom-1 h-1.5 w-1.5 rounded-full ${
                        allDone ? 'bg-success' : 'bg-accent'
                      }`}
                      title={`${count} goal${count !== 1 ? 's' : ''}`}
                    />
                  )}
                </button>
              )
            })}
          </div>
        </div>

        {/* Goals for the selected day */}
        <StudyGoals dateKey={selected} subtitle={selectedSubtitle} compact />
      </div>
    </div>
  )
}
