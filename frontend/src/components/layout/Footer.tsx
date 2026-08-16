import { useEffect, useState } from 'react'
import { getUsage, type AccountUsage } from '../../api/usage'
import { useDocStore } from '../../stores/docStore'
import { useLessonStore } from '../../stores/lessonStore'

export function Footer() {
 const [usage, setUsage] = useState<AccountUsage | null>(null)
 const docCount = useDocStore((s) => s.docs.length)
 const lessonCount = useLessonStore((s) => s.lessons.length)

 useEffect(() => {
 const refreshUsage = () => {
 getUsage().then(setUsage).catch(() => {})
 }
 refreshUsage()
 const interval = window.setInterval(refreshUsage, 15000)
 return () => window.clearInterval(interval)
 }, [])

 if (!usage) return null

 const tokenPct = Math.round((usage.tokens_used / usage.max_tokens) * 100)
 const docPct = Math.round((docCount / usage.max_docs) * 100)
 const lessonPct = Math.round((lessonCount / usage.max_lessons) * 100)

 return (
 <div className="h-8 flex items-center px-4 bg-surface-light text-[9px] text-accent-muted/50 font-mono uppercase tracking-widest gap-6 flex-shrink-0">
 <span>Tokens {usage.tokens_used.toLocaleString()}/{usage.max_tokens.toLocaleString()} ({tokenPct}%)</span>
 <span>Docs {docCount}/{usage.max_docs} ({docPct}%)</span>
 <span>Lessons {lessonCount}/{usage.max_lessons} ({lessonPct}%)</span>
 </div>
 )
}
