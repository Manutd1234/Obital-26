import { useEffect, useRef, useState } from 'react'
import { useWorkspaceStore, makeSessionId } from '../../stores/workspaceStore'
import { todayKey } from '../../lib/dateUtils'

const PRESETS = [
  { label: '25 min', h: 0, m: 25, s: 0 },
  { label: '5 min', h: 0, m: 5, s: 0 },
  { label: '50 min', h: 0, m: 50, s: 0 },
]

function fmt(total: number): string {
  const hh = Math.floor(total / 3600)
  const mm = Math.floor((total % 3600) / 60)
  const ss = total % 60
  const p = (n: number) => String(n).padStart(2, '0')
  return hh > 0 ? `${p(hh)}:${p(mm)}:${p(ss)}` : `${p(mm)}:${p(ss)}`
}

export function PomodoroTimer() {
  const addSession = useWorkspaceStore((s) => s.addSession)
  const sessions = useWorkspaceStore((s) => s.data.sessions)

  const [h, setH] = useState(0)
  const [m, setM] = useState(25)
  const [s, setS] = useState(0)
  const [remaining, setRemaining] = useState(25 * 60)
  const [running, setRunning] = useState(false)
  const [label, setLabel] = useState('')

  const endRef = useRef<number | null>(null)
  const segmentStartRemainingRef = useRef(0)

  const configured = h * 3600 + m * 60 + s

  // Reset only when the configured duration changes. Pausing should preserve
  // the remaining time so the session can be resumed.
  useEffect(() => {
    setRemaining(configured)
    endRef.current = null
    setRunning(false)
  }, [configured])

  // Tick against a target timestamp so it stays accurate under tab throttling.
  useEffect(() => {
    if (!running) return
    const tick = () => {
      if (endRef.current == null) return
      const left = Math.max(0, Math.round((endRef.current - Date.now()) / 1000))
      setRemaining(left)
      if (left <= 0) {
        endRef.current = null
        setRunning(false)
        const elapsed = segmentStartRemainingRef.current
        if (elapsed > 0) {
          addSession({
            id: makeSessionId(),
            date: todayKey(),
            duration_seconds: elapsed,
            completed_at: new Date().toISOString(),
            label: label.trim() || undefined,
          })
        }
      }
    }
    const iv = setInterval(tick, 250)
    tick()
    return () => clearInterval(iv)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running])

  const start = () => {
    if (configured <= 0) return
    const startFrom = remaining > 0 ? remaining : configured
    segmentStartRemainingRef.current = startFrom
    endRef.current = Date.now() + startFrom * 1000
    setRemaining(startFrom)
    setRunning(true)
  }
  const pause = () => {
    if (endRef.current == null) return
    const left = Math.max(0, Math.ceil((endRef.current - Date.now()) / 1000))
    const elapsed = Math.max(0, segmentStartRemainingRef.current - left)
    endRef.current = null
    setRemaining(left)
    setRunning(false)
    if (elapsed > 0) {
      addSession({
        id: makeSessionId(),
        date: todayKey(),
        duration_seconds: elapsed,
        completed_at: new Date().toISOString(),
        label: label.trim() || undefined,
      })
    }
  }
  const reset = () => {
    endRef.current = null
    setRunning(false)
    setRemaining(configured)
  }

  const applyPreset = (p: (typeof PRESETS)[number]) => {
    setH(p.h)
    setM(p.m)
    setS(p.s)
    setRunning(false)
    endRef.current = null
    setRemaining(p.h * 3600 + p.m * 60 + p.s)
  }

  const todays = sessions.filter((x) => x.date === todayKey())
  const focusSeconds = todays.reduce((acc, x) => acc + x.duration_seconds, 0)
  const focusDuration = focusSeconds < 60 ? `${focusSeconds}s` : `${Math.round(focusSeconds / 60)}m`

  const NumberField = ({
    value,
    onChange,
    max,
    unit,
  }: {
    value: number
    onChange: (n: number) => void
    max: number
    unit: string
  }) => (
    <div className="flex flex-col items-center">
      <input
        type="number"
        min={0}
        max={max}
        value={value}
        disabled={running}
        onChange={(e) => {
          const n = Math.max(0, Math.min(max, parseInt(e.target.value || '0', 10)))
          onChange(Number.isNaN(n) ? 0 : n)
        }}
        className="w-16 rounded-lg border border-border/10 bg-surface px-2 py-1.5 text-center font-mono text-lg text-text-primary focus:border-accent/40 focus:outline-none disabled:opacity-50"
      />
      <span className="mt-1 font-mono text-[10px] uppercase tracking-wider text-accent-muted/60">{unit}</span>
    </div>
  )

  return (
    <div className="custom-scrollbar h-full overflow-y-auto p-6">
      <div className="mx-auto flex max-w-[480px] flex-col items-center gap-6">
        {/* Countdown */}
        <div className="flex w-full flex-col items-center rounded-2xl bg-surface-light border border-border/5 p-8">
          <div className="font-mono text-6xl font-bold tabular-nums text-text-primary">{fmt(remaining)}</div>

          <input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="What are you focusing on? (optional)"
            className="mt-5 w-full rounded-lg border border-border/10 bg-surface px-3 py-2 text-center text-sm text-text-primary placeholder:text-accent-muted/40 focus:border-accent/40 focus:outline-none"
          />

          <div className="mt-5 flex gap-2">
            {!running ? (
              <button
                onClick={start}
                disabled={configured <= 0}
                className="flex items-center gap-1.5 rounded-lg bg-accent px-6 py-2 text-sm font-medium text-surface transition-colors hover:bg-accent-hover disabled:opacity-50"
              >
                <span className="material-symbols-outlined text-base">play_arrow</span>
                Start
              </button>
            ) : (
              <button
                onClick={pause}
                className="flex items-center gap-1.5 rounded-lg bg-surface-card px-6 py-2 text-sm font-medium text-text-primary transition-colors hover:bg-surface-lighter"
              >
                <span className="material-symbols-outlined text-base">pause</span>
                Pause
              </button>
            )}
            <button
              onClick={reset}
              className="flex items-center gap-1.5 rounded-lg bg-surface-card px-4 py-2 text-sm text-accent-muted transition-colors hover:text-text-primary"
            >
              <span className="material-symbols-outlined text-base">restart_alt</span>
              Reset
            </button>
          </div>
        </div>

        {/* Duration picker */}
        <div className="w-full rounded-xl bg-surface-light border border-border/5 p-5">
          <div className="mb-3 font-mono text-[10px] uppercase tracking-wider text-accent-muted">Set duration</div>
          <div className="flex items-end justify-center gap-3">
            <NumberField value={h} onChange={setH} max={23} unit="hours" />
            <NumberField value={m} onChange={setM} max={59} unit="min" />
            <NumberField value={s} onChange={setS} max={59} unit="sec" />
          </div>
          <div className="mt-4 flex justify-center gap-2">
            {PRESETS.map((p) => (
              <button
                key={p.label}
                onClick={() => applyPreset(p)}
                className="rounded-lg bg-surface px-3 py-1 font-mono text-xs text-accent-muted transition-colors hover:bg-surface-card hover:text-text-primary"
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>

        {/* Today's focus */}
        <div className="flex w-full items-center justify-between rounded-xl bg-surface-light border border-border/5 px-5 py-4">
          <div>
            <div className="font-mono text-[10px] uppercase tracking-wider text-accent-muted">Today's focus</div>
            <div className="text-xs text-accent-muted/60">syncs with your day</div>
          </div>
          <div className="text-right">
            <div className="font-mono text-2xl font-bold text-text-primary">{focusDuration}</div>
            <div className="font-mono text-[10px] uppercase tracking-wider text-accent-muted">
              {todays.length} session{todays.length !== 1 ? 's' : ''}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
