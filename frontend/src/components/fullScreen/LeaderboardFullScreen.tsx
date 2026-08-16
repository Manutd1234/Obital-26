import { useEffect } from 'react'
import { useLeaderboardStore } from '../../stores/leaderboardStore'
import type { LeaderboardEntry } from '../../api/leaderboard'

const MEDALS: Record<number, string> = { 1: '🥇', 2: '🥈', 3: '🥉' }

function rankLabel(rank: number): string {
  return MEDALS[rank] ?? `#${rank}`
}

function LeaderboardRow({ entry, isMe }: { entry: LeaderboardEntry; isMe: boolean }) {
  return (
    <div
      className={`flex items-center gap-4 rounded-xl border px-4 py-3 transition-colors ${
        isMe
          ? 'border-accent/40 bg-accent/10'
          : 'border-border/5 bg-surface-light hover:bg-surface-card/60'
      }`}
    >
      {/* Rank */}
      <div className="w-10 flex-shrink-0 text-center font-mono text-base font-bold text-accent-muted">
        {rankLabel(entry.rank)}
      </div>

      {/* Avatar */}
      <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full bg-surface-card font-mono text-sm font-bold uppercase text-accent">
        {entry.display_name.slice(0, 1)}
      </div>

      {/* Name */}
      <div className="min-w-0 flex-1">
        <div className="truncate font-mono text-sm text-text-primary">
          {entry.display_name}
          {isMe && <span className="ml-2 text-[10px] uppercase tracking-wider text-accent">you</span>}
        </div>
        <div className="text-[11px] text-accent-muted">
          {entry.total_quizzes} quiz{entry.total_quizzes !== 1 ? 'zes' : ''} · {entry.accuracy_percent}% accuracy
        </div>
      </div>

      {/* XP */}
      <div className="flex-shrink-0 text-right">
        <div className="font-mono text-base font-bold text-text-primary">
          {entry.total_points.toLocaleString()}
        </div>
        <div className="text-[10px] uppercase tracking-wider text-accent-muted">XP</div>
      </div>
    </div>
  )
}

export function LeaderboardFullScreen() {
  const data = useLeaderboardStore((s) => s.data)
  const loading = useLeaderboardStore((s) => s.loading)
  const error = useLeaderboardStore((s) => s.error)
  const fetchLeaderboard = useLeaderboardStore((s) => s.fetchLeaderboard)

  useEffect(() => {
    fetchLeaderboard(50)
  }, [fetchLeaderboard])

  const entries = data?.entries ?? []
  const meId = data?.me?.account_id ?? null

  return (
    <div className="h-full overflow-y-auto custom-scrollbar p-4 md:p-8">
      <div className="mx-auto max-w-[720px] space-y-6">
        {/* Header */}
        <div className="flex items-end justify-between border-b border-border/10 pb-4">
          <div>
            <h2 className="text-2xl font-bold text-text-primary">Leaderboard</h2>
            <p className="mt-1 text-sm text-accent-muted">
              Every learner ranked by XP. Earn points by taking quizzes.
            </p>
          </div>
          {data && (
            <span className="font-mono text-[11px] text-accent-muted/60">
              {data.total_users} learner{data.total_users !== 1 ? 's' : ''}
            </span>
          )}
        </div>

        {/* Your rank banner (when outside the visible window) */}
        {data?.me && !entries.some((e) => e.account_id === meId) && (
          <div className="rounded-xl border border-accent/30 bg-accent/5 px-4 py-3 text-sm text-text-primary">
            Your rank: <span className="font-mono font-bold">#{data.me.rank}</span> ·{' '}
            <span className="font-mono">{data.me.total_points.toLocaleString()} XP</span>
          </div>
        )}

        {/* States */}
        {loading && entries.length === 0 ? (
          <div className="space-y-2">
            {[1, 2, 3, 4, 5].map((i) => (
              <div key={i} className="h-[60px] animate-pulse rounded-xl bg-surface-light" />
            ))}
          </div>
        ) : error ? (
          <div className="rounded-xl border border-danger/30 bg-danger/5 px-4 py-6 text-center text-sm text-danger">
            {error}
          </div>
        ) : entries.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-16 text-accent-muted">
            <span className="material-symbols-outlined text-4xl">leaderboard</span>
            <p className="text-sm">No learners ranked yet. Take a quiz to get on the board.</p>
          </div>
        ) : (
          <div className="space-y-2">
            {entries.map((entry) => (
              <LeaderboardRow key={entry.account_id} entry={entry} isMe={entry.account_id === meId} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
