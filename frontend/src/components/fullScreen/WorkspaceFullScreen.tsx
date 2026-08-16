import { useEffect, useState } from 'react'
import { useWorkspaceStore } from '../../stores/workspaceStore'
import { CodeRunner } from '../workspace/CodeRunner'
import { WorkspaceCalendar } from '../workspace/WorkspaceCalendar'
import { PomodoroTimer } from '../workspace/PomodoroTimer'

type Tab = 'code' | 'calendar' | 'timer'

const TABS: { id: Tab; label: string; icon: string }[] = [
  { id: 'code', label: 'Code', icon: 'terminal' },
  { id: 'calendar', label: 'Calendar', icon: 'calendar_month' },
  { id: 'timer', label: 'Timer', icon: 'timer' },
]

export function WorkspaceFullScreen() {
  const [tab, setTab] = useState<Tab>('code')
  const load = useWorkspaceStore((s) => s.load)

  useEffect(() => {
    load()
  }, [load])

  return (
    <div className="flex h-full flex-col">
      {/* Tab bar */}
      <div className="flex flex-shrink-0 items-center gap-1 border-b border-border/10 px-4 py-2">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm transition-colors ${
              tab === t.id
                ? 'bg-surface-card text-text-primary shadow-sm'
                : 'text-accent-muted hover:bg-surface-card/60 hover:text-text-primary'
            }`}
          >
            <span className="material-symbols-outlined text-base">{t.icon}</span>
            {t.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="min-h-0 flex-1">
        {tab === 'code' && <CodeRunner />}
        {tab === 'calendar' && <WorkspaceCalendar />}
        {tab === 'timer' && <PomodoroTimer />}
      </div>
    </div>
  )
}
