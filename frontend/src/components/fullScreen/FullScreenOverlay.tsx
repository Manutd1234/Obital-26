import { useUIStore } from '../../stores/uiStore'
import { DocFullScreen } from '../fullScreen/DocFullScreen'
import { LessonFullScreen } from '../fullScreen/LessonFullScreen'
import { DashboardFullScreen } from '../fullScreen/DashboardFullScreen'
import { PipelineFullScreen } from '../fullScreen/PipelineFullScreen'
import { BinFullScreen } from '../fullScreen/BinFullScreen'
import { LeaderboardFullScreen } from '../fullScreen/LeaderboardFullScreen'
import { WorkspaceFullScreen } from '../fullScreen/WorkspaceFullScreen'
import { ShopFullScreen } from '../fullScreen/ShopFullScreen'

const TITLES: Record<string, string> = {
  doc: 'Docs',
  lesson: 'Lessons',
  dashboard: 'Dashboard',
  pipeline: 'Pipeline',
  bin: 'Bin',
  leaderboard: 'Leaderboard',
  workspace: 'Workspace',
  shop: 'Pack Shop',
}

export function FullScreenOverlay() {
  const fullScreenView = useUIStore((s) => s.fullScreenView)
  const closeFullScreen = useUIStore((s) => s.closeFullScreen)

  if (!fullScreenView) return null

  return (
    <div className="absolute inset-x-0 bottom-16 top-0 z-[40] flex flex-col bg-surface animate-fade-in md:bottom-0">
      {/* Top bar with back + title */}
      <div className="flex h-9 flex-shrink-0 items-center gap-2 bg-surface-light pl-2 pr-4">
        <button
          onClick={closeFullScreen}
          title="Back to chat"
          className="flex h-7 w-7 items-center justify-center rounded-lg text-accent-muted transition-colors hover:bg-surface-lighter hover:text-text-primary"
        >
          <span className="material-symbols-outlined text-base">arrow_back</span>
        </button>
        <span className="font-mono text-[11px] uppercase tracking-[0.16em] text-accent-muted">
          {TITLES[fullScreenView] ?? fullScreenView}
        </span>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden">
        {fullScreenView === 'doc' && <DocFullScreen />}
        {fullScreenView === 'lesson' && <LessonFullScreen />}
        {fullScreenView === 'dashboard' && <DashboardFullScreen />}
        {fullScreenView === 'pipeline' && <PipelineFullScreen />}
        {fullScreenView === 'bin' && <BinFullScreen />}
        {fullScreenView === 'leaderboard' && <LeaderboardFullScreen />}
        {fullScreenView === 'workspace' && <WorkspaceFullScreen />}
        {fullScreenView === 'shop' && <ShopFullScreen />}
      </div>
    </div>
  )
}
