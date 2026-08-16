// Small date helpers shared by the workspace calendar, study goals, and dashboard.
// Mirrors the local-day logic already used privately in DashboardFullScreen.tsx.

export function startOfLocalDay(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate())
}

/** Local 'YYYY-MM-DD' key (not UTC) so a day matches the user's calendar. */
export function dateKey(date: Date): string {
  const local = startOfLocalDay(date)
  const year = local.getFullYear()
  const month = String(local.getMonth() + 1).padStart(2, '0')
  const day = String(local.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

export function todayKey(): string {
  return dateKey(new Date())
}

export function formatShortDate(value: string | Date): string {
  const date = typeof value === 'string' ? new Date(value) : value
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

export function formatLongDate(date: Date): string {
  return date.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
}

export const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

export const WEEKDAY_LABELS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa']

export interface CalendarCell {
  date: Date
  key: string
  inMonth: boolean
}

/**
 * Build a 6-row (42-cell) month grid starting on Sunday, including leading/trailing
 * days from adjacent months so the grid is always rectangular.
 */
export function buildMonthGrid(year: number, month: number): CalendarCell[] {
  const first = new Date(year, month, 1)
  const start = new Date(first)
  start.setDate(first.getDate() - first.getDay()) // back up to Sunday
  const cells: CalendarCell[] = []
  for (let i = 0; i < 42; i += 1) {
    const date = new Date(start)
    date.setDate(start.getDate() + i)
    cells.push({ date, key: dateKey(date), inMonth: date.getMonth() === month })
  }
  return cells
}
