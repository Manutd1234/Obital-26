export function parseQuizData(text: string): unknown | null {
  const match = text.match(/\[QUIZ_DATA_START\](.*?)\[QUIZ_DATA_END\]/s)
  if (!match) return null
  try {
    return JSON.parse(match[1])
  } catch {
    return null
  }
}

export function stripQuizTags(text: string): string {
  // Strip everything from the quiz header through the data tags
  // Pattern: === Quiz: ... === ... [QUIZ_DATA_START]...[QUIZ_DATA_END]
  return text.replace(/===\s*Quiz:.*?\[QUIZ_DATA_END\]/gs, '').trim()
}

/**
 * remark-math uses dollar delimiters by default, while LLMs commonly emit
 * LaTeX's equivalent \(...\) and \[...\] delimiters. Normalize only those
 * escaped delimiters so ordinary square-bracketed prose is left untouched.
 */
export function normalizeLatexDelimiters(text: string): string {
  return text
    .replace(/\\\[([\s\S]*?)\\\]/g, (_, formula: string) => `$$\n${formula.trim()}\n$$`)
    .replace(/\\\(([\s\S]*?)\\\)/g, (_, formula: string) => `$${formula.trim()}$`)
}

export function parseToolEvents(text: string): { type: string; content: string }[] {
  const events: { type: string; content: string }[] = []
  const regex = /\[tool\]\s*(.*)/g
  let match
  while ((match = regex.exec(text)) !== null) {
    events.push({ type: 'tool', content: match[1] })
  }
  return events
}

export function formatTimestamp(ts: string): string {
  const d = new Date(ts)
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMins / 60)
  const diffDays = Math.floor(diffHours / 24)

  if (diffMins < 1) return 'Just now'
  if (diffMins < 60) return `${diffMins}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  if (diffDays < 7) return `${diffDays}d ago`
  return d.toLocaleDateString()
}
