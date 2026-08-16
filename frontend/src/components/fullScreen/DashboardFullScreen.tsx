import { useEffect } from 'react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { DashboardData } from '../../api/rewards'
import { useDocStore } from '../../stores/docStore'
import { useDashboardStore } from '../../stores/dashboardStore'
import { useLessonStore, type Lesson } from '../../stores/lessonStore'
import { useUIStore } from '../../stores/uiStore'
import { useWorkspaceStore } from '../../stores/workspaceStore'
import { StudyGoals } from '../workspace/StudyGoals'
import { todayKey } from '../../lib/dateUtils'

export function DashboardFullScreen() {
  const dashData = useDashboardStore((s) => s.data)
  const refreshDashboard = useDashboardStore((s) => s.refreshDashboard)
  const lessons = useLessonStore((s) => s.lessons)
  const setActiveLesson = useLessonStore((s) => s.setActiveLesson)
  const docs = useDocStore((s) => s.docs)
  const openFullScreen = useUIStore((s) => s.openFullScreen)
  const loadWorkspace = useWorkspaceStore((s) => s.load)
  const sessions = useWorkspaceStore((s) => s.data.sessions)

  useEffect(() => {
    refreshDashboard(50)
    loadWorkspace()
  }, [refreshDashboard, loadWorkspace])

  const summary = dashData?.summary
  const trend = dashData?.trend || []
  const focusMinutesToday = Math.round(
    sessions.filter((x) => x.date === todayKey()).reduce((acc, x) => acc + x.duration_seconds, 0) / 60,
  )
  const activeLessons = lessons.filter((l) => l.status === 'active' || l.status === 'in_progress')
  const completedLessons = lessons.filter((l) => l.status === 'completed')

  return (
    <div className="h-full overflow-y-auto custom-scrollbar p-4 md:p-8">
      <div className="max-w-[1200px] mx-auto space-y-8">
        {/* Header */}
        <div className="flex justify-between items-end gap-4 border-b border-border/10 pb-4">
          <div>
            <h2 className="text-2xl font-bold text-text-primary">Dashboard</h2>
            <p className="text-sm text-accent-muted mt-1">
              Review your curriculum, browse your vault, or track your progress.
            </p>
          </div>
        </div>

        {/* Progress Overview */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 bg-surface-light border border-border/5 rounded-xl p-6">
            <div className="flex items-center justify-between gap-4 mb-4">
              <h3 className="text-sm font-semibold text-text-primary uppercase tracking-wider font-mono">
                Points Progression
              </h3>
              {trend.length > 0 && (
                <span className="text-[10px] text-accent-muted/40 font-mono">
                  {trend.length} recent attempt{trend.length !== 1 ? 's' : ''}
                </span>
              )}
            </div>
            {trend.length > 0 ? (
              <PointsProgressionChart points={trend} />
            ) : (
              <EmptyMetricState icon="bar_chart" label="No points history yet" />
            )}
          </div>

          <StreakHeatmap logs={dashData?.session_logs || []} />
        </div>

        {/* Stats + Score Trend */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Stats cards */}
          <div className="lg:col-span-1 grid grid-cols-2 gap-3">
            <StatCard
              icon="emoji_events"
              label="Points"
              value={summary?.total_points || 0}
              color="text-accent"
            />
            <StatCard
              icon="quiz"
              label="Quizzes"
              value={summary?.total_quizzes || 0}
              color="text-accent"
            />
            <StatCard
              icon="target"
              label="Accuracy"
              value={`${summary?.accuracy_percent || 0}%`}
              color="text-success"
            />
            <StatCard
              icon="check_circle"
              label="Correct"
              value={`${summary?.total_correct || 0}/${summary?.total_answered || 0}`}
              color="text-accent"
            />
          </div>

          {/* Recent score trend */}
          <div className="lg:col-span-2 bg-surface-light rounded-xl p-6">
            <div className="flex items-center justify-between gap-4 mb-4">
              <h3 className="text-sm font-semibold text-text-primary uppercase tracking-wider font-mono">
                Recent Quiz Scores
              </h3>
              {trend.length > 0 && (
                <span className="text-[10px] text-accent-muted/40 font-mono">
                  {trend.length} recent attempt{trend.length !== 1 ? 's' : ''}
                </span>
              )}
            </div>
            {trend.length > 0 ? (
              <div className="space-y-5">
                {trend.slice(0, 8).map((item, i) => {
                  const score = item.score_percent
                  return (
                    <div key={i} className="flex items-center gap-3">
                      <span className="text-[10px] text-accent-muted/40 font-mono w-16 truncate">
                        {item.quiz_id.replace('quiz_', '').slice(0, 8)}
                      </span>
                      <div className="flex-1 h-2 bg-surface rounded-full overflow-hidden">
                        <div
                          className={`h-full rounded-full transition-all duration-700 ${
                            score >= 70 ? 'bg-success' : score >= 50 ? 'bg-accent' : 'bg-danger'
                          }`}
                          style={{ width: `${Math.min(100, score)}%` }}
                        />
                      </div>
                      <span className="text-xs text-text-primary font-mono w-10 text-right">
                        {score.toFixed(0)}%
                      </span>
                      <span className="text-[10px] text-success/60 font-mono w-10 text-right">
                        +{item.points_earned}
                      </span>
                    </div>
                  )
                })}
              </div>
            ) : (
              <EmptyMetricState icon="bar_chart" label="No quiz history yet" />
            )}
          </div>
        </div>

        {/* Study Goals + Focus (synced with Workspace) */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2">
            <StudyGoals subtitle="Today's goals · synced with Workspace" compact />
          </div>
          <button
            onClick={() => openFullScreen('workspace')}
            className="bg-surface-light border border-border/5 rounded-xl p-6 text-left hover:border-border/15 hover:bg-surface-lighter transition-all flex flex-col justify-between"
          >
            <div className="flex items-center gap-2">
              <span className="material-symbols-outlined text-accent text-base">timer</span>
              <span className="text-sm font-semibold text-text-primary uppercase tracking-wider font-mono">
                Focus Today
              </span>
            </div>
            <div className="mt-4">
              <div className="text-3xl font-bold text-text-primary">{focusMinutesToday}m</div>
              <div className="text-[10px] text-accent-muted/40 font-mono uppercase tracking-wider mt-1">
                Open Workspace →
              </div>
            </div>
          </button>
        </div>

        {/* Bento Grid */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          {/* Left: Curriculum / Lessons (col 7) */}
          <div className="lg:col-span-7 space-y-4">
            <div className="flex items-center justify-between border-b border-border/10 pb-2">
              <h3 className="text-sm font-semibold text-text-primary flex items-center gap-2 uppercase tracking-wider font-mono">
                <span className="material-symbols-outlined text-accent text-base">school</span>
                Curriculum
              </h3>
              <span className="text-xs text-accent-muted/50 font-mono">
                {lessons.length} Module{lessons.length !== 1 ? 's' : ''}
              </span>
            </div>

            {/* Active lessons */}
            {activeLessons.length > 0 && (
              <div className="space-y-2">
                <div className="text-[10px] text-accent font-mono uppercase tracking-wider px-1">
                  In Progress
                </div>
                {activeLessons.map((lesson) => (
                  <LessonCard
                    key={lesson.lesson_id}
                    lesson={lesson}
                    onClick={() => {
                      setActiveLesson(lesson.lesson_id)
                      openFullScreen('lesson')
                    }}
                  />
                ))}
              </div>
            )}

            {completedLessons.length > 0 && (
              <div className="space-y-2">
                <div className="text-[10px] text-success/60 font-mono uppercase tracking-wider px-1">
                  Completed
                </div>
                {completedLessons.map((lesson) => (
                  <LessonCard
                    key={lesson.lesson_id}
                    lesson={lesson}
                    onClick={() => {
                      setActiveLesson(lesson.lesson_id)
                      openFullScreen('lesson')
                    }}
                  />
                ))}
              </div>
            )}

            {lessons.length === 0 && (
              <div className="bg-surface-light border border-border/5 rounded-xl p-10 text-center">
                <span className="material-symbols-outlined text-accent-muted/15 text-5xl block mb-3">
                  school
                </span>
                <p className="text-accent-muted/40 text-sm">No lessons yet</p>
                <p className="text-accent-muted/20 text-xs mt-1">
                  Start one from the chat
                </p>
              </div>
            )}
          </div>

          {/* Right: Vault Library (col 5) */}
          <div className="lg:col-span-5 space-y-4">
            <div className="flex items-center justify-between border-b border-border/10 pb-2">
              <h3 className="text-sm font-semibold text-text-primary flex items-center gap-2 uppercase tracking-wider font-mono">
                <span className="material-symbols-outlined text-accent text-base">inventory_2</span>
                Vault Library
              </h3>
              <span className="text-xs text-accent-muted/50 font-mono">
                {docs.length} File{docs.length !== 1 ? 's' : ''}
              </span>
            </div>

            {docs.map((doc) => (
              <button
                key={doc.doc_id}
                onClick={() => openFullScreen('doc')}
                className="w-full text-left bg-surface-light border border-border/5 rounded-xl p-4 hover:border-border/15 hover:bg-surface-lighter transition-all group"
              >
                <div className="flex items-center gap-3">
                  <div className="w-9 h-9 rounded-lg bg-surface flex items-center justify-center text-accent flex-shrink-0">
                    <span className="material-symbols-outlined text-lg">description</span>
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-sm text-text-primary truncate group-hover:text-accent transition-colors">
                      {doc.name}
                    </div>
                  </div>
                  <span className="material-symbols-outlined text-accent-muted/20 text-sm group-hover:text-accent-muted/50 transition-colors">
                    arrow_forward
                  </span>
                </div>
              </button>
            ))}

            {docs.length === 0 && (
              <div className="bg-surface-light border border-border/5 rounded-xl p-10 text-center">
                <span className="material-symbols-outlined text-accent-muted/15 text-5xl block mb-3">
                  folder_open
                </span>
                <p className="text-accent-muted/40 text-sm">No documents uploaded</p>
                <p className="text-accent-muted/20 text-xs mt-1">
                  Upload PDFs, text files, or markdown
                </p>
              </div>
            )}
          </div>
        </div>

      </div>
    </div>
  )
}

function PointsProgressionChart({
  points,
}: {
  points: NonNullable<DashboardData['trend']>
}) {
  const last = points[points.length - 1]
  const chartData = points.map((point, index) => ({
    ...point,
    label: point.submitted_at ? formatShortDate(point.submitted_at) : `#${index + 1}`,
  }))

  return (
    <div className="rounded-xl bg-surface/50 px-4 py-3">
      <div className="flex items-start justify-between gap-4 mb-2">
        <div>
          <div className="text-[10px] text-accent-muted/40 font-mono uppercase tracking-wider">
            Total points
          </div>
          <div className="text-2xl font-bold text-text-primary">{last.cumulative_points}</div>
        </div>
        <div className="text-right">
          <div className="text-[10px] text-accent-muted/40 font-mono uppercase tracking-wider">
            Latest
          </div>
          <div className="text-sm font-mono text-success">+{last.points_earned}</div>
        </div>
      </div>
      <div className="h-40">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData} margin={{ top: 8, right: 8, left: -24, bottom: 0 }}>
            <defs>
              <linearGradient id="pointsProgressionFill" x1="0" x2="0" y1="0" y2="1">
                <stop offset="5%" stopColor="rgb(var(--color-accent))" stopOpacity={0.22} />
                <stop offset="95%" stopColor="rgb(var(--color-accent))" stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="rgb(var(--color-border) / 0.12)" vertical={false} />
            <XAxis
              dataKey="label"
              axisLine={false}
              tickLine={false}
              minTickGap={28}
              tick={{ fill: 'rgb(var(--color-accent-muted) / 0.55)', fontSize: 10 }}
            />
            <YAxis
              axisLine={false}
              tickLine={false}
              width={34}
              tick={{ fill: 'rgb(var(--color-accent-muted) / 0.45)', fontSize: 10 }}
            />
            <Tooltip
              cursor={{ stroke: 'rgb(var(--color-accent-muted) / 0.25)' }}
              contentStyle={{
                background: 'rgb(var(--color-surface-card))',
                border: '1px solid rgb(var(--color-border) / 0.12)',
                borderRadius: 8,
                color: 'rgb(var(--color-text-primary))',
                fontSize: 12,
              }}
              labelStyle={{ color: 'rgb(var(--color-accent-muted))' }}
              formatter={(value) => [Array.isArray(value) ? value[0] : value ?? 0, 'Points']}
            />
            <Area
              type="monotone"
              dataKey="cumulative_points"
              stroke="rgb(var(--color-accent))"
              strokeWidth={2.5}
              fill="url(#pointsProgressionFill)"
              dot={{ r: 3, strokeWidth: 2, fill: 'rgb(var(--color-surface-light))' }}
              activeDot={{ r: 5 }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

function StreakHeatmap({ logs }: { logs: NonNullable<DashboardData['session_logs']> }) {
  const days = buildHeatmapDays(logs)
  const activeDays = days.filter((day) => day.score > 0).length
  const currentStreak = getCurrentStreak(days)

  return (
    <div className="bg-surface-light border border-border/5 rounded-xl p-6 min-w-0">
      <div className="flex items-start justify-between gap-4 mb-4">
        <div>
          <h3 className="text-sm font-semibold text-text-primary uppercase tracking-wider font-mono">
            Study Streak
          </h3>
          <p className="text-[10px] text-accent-muted/40 font-mono mt-1">
            Account activity from reward logs
          </p>
        </div>
        <div className="text-right">
          <div className="text-2xl font-bold text-text-primary">{currentStreak}</div>
          <div className="text-[10px] text-accent-muted/40 font-mono uppercase tracking-wider">
            Day streak
          </div>
        </div>
      </div>
      <div className="overflow-x-auto pb-1 custom-scrollbar">
        <div className="grid grid-flow-col grid-rows-7 gap-1 w-max">
          {days.map((day) => (
            <div
              key={day.key}
              title={`${day.label}: ${day.events} event${day.events === 1 ? '' : 's'}, +${day.points} points, ${day.quizzes} quiz${day.quizzes === 1 ? '' : 'zes'}`}
              className={`h-3 w-3 rounded-sm ${heatmapClass(day.score)}`}
            />
          ))}
        </div>
      </div>
      <div className="mt-4 flex items-center justify-between gap-3 text-[10px] text-accent-muted/45 font-mono">
        <span>{activeDays} active day{activeDays === 1 ? '' : 's'}</span>
        <div className="flex items-center gap-1">
          <span>Less</span>
          {[0, 1, 2, 3, 4].map((score) => (
            <span key={score} className={`h-2.5 w-2.5 rounded-sm ${heatmapClass(score)}`} />
          ))}
          <span>More</span>
        </div>
      </div>
    </div>
  )
}

function EmptyMetricState({ icon, label }: { icon: string; label: string }) {
  return (
    <div className="py-8 text-center">
      <span className="material-symbols-outlined text-accent-muted/15 text-4xl block mb-2">
        {icon}
      </span>
      <p className="text-accent-muted/30 text-xs">{label}</p>
    </div>
  )
}

type HeatmapDay = {
  key: string
  label: string
  points: number
  quizzes: number
  events: number
  score: number
}

function buildHeatmapDays(logs: NonNullable<DashboardData['session_logs']>): HeatmapDay[] {
  const today = startOfLocalDay(new Date())
  const start = new Date(today)
  start.setDate(today.getDate() - 83)
  const byDay = new Map<string, { points: number; quizzes: number; events: number }>()

  for (const log of logs) {
    const date = new Date(log.last_event_at || log.started_at)
    if (Number.isNaN(date.getTime())) continue
    const key = dateKey(date)
    const existing = byDay.get(key) || { points: 0, quizzes: 0, events: 0 }
    byDay.set(key, {
      points: existing.points + Math.max(0, log.points_delta),
      quizzes: existing.quizzes + Math.max(0, log.quizzes_delta),
      events: existing.events + Math.max(1, log.events || 0),
    })
  }

  const days: HeatmapDay[] = []
  for (let offset = 0; offset < 84; offset += 1) {
    const date = new Date(start)
    date.setDate(start.getDate() + offset)
    const key = dateKey(date)
    const activity = byDay.get(key) || { points: 0, quizzes: 0, events: 0 }
    const rawScore = activity.points + activity.quizzes * 3 + activity.events
    days.push({
      key,
      label: date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
      points: activity.points,
      quizzes: activity.quizzes,
      events: activity.events,
      score: rawScore === 0 ? 0 : rawScore >= 18 ? 4 : rawScore >= 10 ? 3 : rawScore >= 4 ? 2 : 1,
    })
  }
  return days
}

function getCurrentStreak(days: HeatmapDay[]) {
  let streak = 0
  for (let index = days.length - 1; index >= 0; index -= 1) {
    if (days[index].score === 0) break
    streak += 1
  }
  return streak
}

function heatmapClass(score: number) {
  if (score >= 4) return 'bg-success'
  if (score === 3) return 'bg-success/70'
  if (score === 2) return 'bg-accent/45'
  if (score === 1) return 'bg-accent/20'
  return 'bg-surface border border-border/10'
}

function startOfLocalDay(date: Date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate())
}

function dateKey(date: Date) {
  const local = startOfLocalDay(date)
  const year = local.getFullYear()
  const month = String(local.getMonth() + 1).padStart(2, '0')
  const day = String(local.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function formatShortDate(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function StatCard({
  icon,
  label,
  value,
  color,
}: {
  icon: string
  label: string
  value: number | string
  color: string
}) {
  return (
    <div className="bg-surface-light border border-border/5 rounded-xl p-4 space-y-1.5">
      <div className="flex items-center gap-1.5">
        <span className={`material-symbols-outlined text-sm ${color}`}>{icon}</span>
        <span className="text-[9px] text-accent-muted/50 font-mono uppercase tracking-wider">
          {label}
        </span>
      </div>
      <div className="text-xl font-bold text-text-primary">{value}</div>
    </div>
  )
}

function LessonCard({
  lesson,
  onClick,
}: {
  lesson: Lesson
  onClick: () => void
}) {
  const progress = lesson.total_checkpoints > 0
    ? Math.round((lesson.current_checkpoint / lesson.total_checkpoints) * 100)
    : 0
  const isCompleted = lesson.status === 'completed'

  return (
    <button
      onClick={onClick}
      className="w-full text-left bg-surface-light border border-border/5 rounded-xl p-4 hover:border-border/15 hover:bg-surface-lighter transition-all group"
    >
      <div className="flex items-start gap-3">
        <div className="w-9 h-9 rounded-lg bg-surface flex items-center justify-center text-accent flex-shrink-0">
          <span className="material-symbols-outlined text-lg">
            {isCompleted ? 'check_circle' : 'school'}
          </span>
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between">
            <span className="text-sm text-text-primary truncate group-hover:text-accent transition-colors">
              {lesson.title}
            </span>
            <span
              className={`text-[9px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded flex-shrink-0 ml-2 ${
                isCompleted
                  ? 'bg-success/20 text-success'
                  : 'bg-accent/20 text-accent'
              }`}
            >
              {lesson.status}
            </span>
          </div>
          <div className="flex items-center gap-3 mt-1.5">
            <span className="text-[10px] text-accent-muted/40 font-mono">
              {lesson.current_checkpoint + 1}/{lesson.total_checkpoints} checkpoints
            </span>
          </div>
          <div className="mt-2 h-1 bg-surface rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${
                isCompleted ? 'bg-success' : 'bg-accent/60'
              }`}
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>
      </div>
    </button>
  )
}
