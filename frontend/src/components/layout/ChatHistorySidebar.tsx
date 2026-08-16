import { useEffect, useState } from 'react'
import { useChatStore } from '../../stores/chatStore'
import { useUIStore } from '../../stores/uiStore'
import { listSessions, getSession } from '../../api/chat'

export function ChatHistorySidebar() {
 const sessions = useChatStore((s) => s.sessions)
 const activeSessionId = useChatStore((s) => s.activeSessionId)
 const setActiveSession = useChatStore((s) => s.setActiveSession)
 const addSession = useChatStore((s) => s.addSession)
 const setSessions = useChatStore((s) => s.setSessions)
 const setMessages = useChatStore((s) => s.setMessages)
 const [search, setSearch] = useState('')
 const [sessionsLoading, setSessionsLoading] = useState(true)
 const sidebarCollapsed = useUIStore((s) => s.sidebarCollapsed)
 const toggleSidebar = useUIStore((s) => s.toggleSidebar)

  useEffect(() => {
    const ensureDraftSession = () => {
      if (useChatStore.getState().activeSessionId) return
      const tempSessionId = `pending_${Date.now()}`
      addSession({ session_id: tempSessionId, title: 'New chat' })
      setMessages(tempSessionId, [])
      setActiveSession(tempSessionId)
      return tempSessionId
    }

    ensureDraftSession()

    listSessions()
      .then((s) => {
        setSessions(s)
        const currentActive = useChatStore.getState().activeSessionId
        if (currentActive) return
        const firstSession = s[0]
        if (firstSession) {
          setActiveSession(firstSession.session_id)
          getSession(firstSession.session_id)
            .then((res) => setMessages(firstSession.session_id, res.messages))
            .catch((e) => console.error('Failed to load session messages:', e))
          return
        }
        ensureDraftSession()
      })
      .catch((e) => {
        console.error('Failed to load sessions:', e)
        ensureDraftSession()
      })
      .finally(() => setSessionsLoading(false))
  }, [addSession, setActiveSession, setMessages, setSessions])

 const filtered = sessions.filter(
 (s) =>
 !search ||
 (s.title || '').toLowerCase().includes(search.toLowerCase()) ||
 (s.session_id || '').toLowerCase().includes(search.toLowerCase()),
 )

 const handleNewChat = () => {
 const tempSessionId = `pending_${Date.now()}`
 addSession({ session_id: tempSessionId, title: 'New chat' })
 setMessages(tempSessionId, [])
 setActiveSession(tempSessionId)
 }

 const handleSelectSession = async (sessionId: string) => {
 setActiveSession(sessionId)
 const existing = useChatStore.getState().messages[sessionId]
 if (existing && existing.length > 0) return
 try {
 const res = await getSession(sessionId)
 setMessages(sessionId, res.messages)
 } catch (e) {
 console.error('Failed to load session messages:', e)
 }
 }

 if (sidebarCollapsed) {
 return (
 <aside className="hidden w-[52px] flex-shrink-0 bg-surface-light/70 h-full flex-col items-center py-3 gap-2 shadow-[inset_-1px_0_0_rgb(var(--color-border)/0.04)] transition-[width,opacity] duration-200 ease-out md:flex">
 <button
 onClick={toggleSidebar}
 className="w-9 h-9 rounded-xl bg-surface-card flex items-center justify-center text-accent-muted hover:text-text-primary hover:bg-surface-lighter transition-all"
 title="Open sidebar"
 >
 <span className="material-symbols-outlined text-lg">left_panel_open</span>
 </button>
 <button
 onClick={handleNewChat}
 className="w-9 h-9 rounded-xl flex items-center justify-center text-accent-muted hover:text-text-primary hover:bg-surface-card transition-all"
 title="New chat"
 >
 <span className="material-symbols-outlined text-lg">edit</span>
 </button>
 <div className="flex-1" />
 </aside>
 )
 }

 return (
 <aside className="hidden w-[260px] flex-shrink-0 bg-surface-light/70 h-full flex-col shadow-[inset_-1px_0_0_rgb(var(--color-border)/0.04)] transition-[width,opacity] duration-200 ease-out md:flex">
 {/* Header */}
 <div className="p-3 flex items-center justify-between">
 <div className="flex items-center gap-2">
 <div className="w-7 h-7 rounded-lg bg-surface-card flex items-center justify-center text-accent">
 <span className="material-symbols-outlined text-base font-bold">school</span>
 </div>
 <span className="text-sm font-bold text-text-primary">Obital</span>
 </div>
 <button
 onClick={toggleSidebar}
 className="w-8 h-8 rounded-lg bg-surface-card flex items-center justify-center text-accent-muted hover:text-text-primary hover:bg-surface-lighter transition-all"
 title="Close sidebar"
 >
 <span className="material-symbols-outlined text-lg">left_panel_close</span>
 </button>
 </div>

 <div className="px-3 pb-2">
 <button
 onClick={handleNewChat}
 className="w-full flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-accent-muted hover:text-text-primary hover:bg-surface-card/70 transition-all"
 >
 <span className="material-symbols-outlined text-base">edit</span>
 New chat
 </button>
 </div>

 {/* Search */}
 <div className="px-3 pb-2">
 <div className="flex items-center gap-2 bg-surface rounded-lg px-3 py-2">
 <span className="material-symbols-outlined text-accent-muted text-base">search</span>
 <input
 type="text"
 placeholder="Search chats"
 className="bg-transparent border-none text-sm text-text-primary outline-none placeholder-accent-muted/50 w-full"
 value={search}
 onChange={(e) => setSearch(e.target.value)}
 />
 </div>
 </div>

 {/* Sessions list */}
 <div
 className="flex-1 overflow-y-auto px-2"
 style={{ scrollbarWidth: 'none' }}
 >
 <style>{`
 .sessions-list::-webkit-scrollbar { display: none; }
 `}</style>
 <div className="sessions-list">
 <div className="text-[10px] uppercase tracking-wider text-accent-muted/50 font-mono font-bold px-2 py-2">
 Recents
 </div>
 {sessionsLoading && sessions.length === 0 && (
 <div className="space-y-1 px-1 py-1">
 {[0, 1, 2, 3, 4].map((item) => (
 <div key={item} className="h-9 rounded-lg bg-surface-card/45 animate-pulse" />
 ))}
 </div>
 )}
 {filtered.map((session) => (
 <button
 key={session.session_id}
 onClick={() => handleSelectSession(session.session_id)}
 className={`w-full text-left px-3 py-2.5 rounded-lg text-sm transition-all mb-0.5 ${
 activeSessionId === session.session_id
 ? 'bg-surface-card text-text-primary'
 : 'text-accent-muted hover:bg-surface-card/50 hover:text-text-primary'
 }`}
 >
 <div className="truncate text-[13px]">
 {session.title || `Chat ${session.session_id.slice(0, 8)}`}
 </div>
 </button>
 ))}
 {filtered.length === 0 && (
 <div className="px-3 py-8 text-center text-accent-muted/40 text-xs">
 No chats yet. Start a new one.
 </div>
 )}
 </div>
 </div>

 </aside>
 )
}

export function MobileChatHistoryButton() {
 const [open, setOpen] = useState(false)
 const sessions = useChatStore((s) => s.sessions)
 const activeSessionId = useChatStore((s) => s.activeSessionId)
 const setActiveSession = useChatStore((s) => s.setActiveSession)
 const addSession = useChatStore((s) => s.addSession)
 const setMessages = useChatStore((s) => s.setMessages)

 const visibleSessions = sessions.slice(0, 12)

 const handleNewChat = () => {
 const tempSessionId = `pending_${Date.now()}`
 addSession({ session_id: tempSessionId, title: 'New chat' })
 setMessages(tempSessionId, [])
 setActiveSession(tempSessionId)
 setOpen(false)
 }

 const handleSelectSession = async (sessionId: string) => {
 setActiveSession(sessionId)
 setOpen(false)
 const existing = useChatStore.getState().messages[sessionId]
 if (existing && existing.length > 0) return
 try {
 const res = await getSession(sessionId)
 setMessages(sessionId, res.messages)
 } catch (e) {
 console.error('Failed to load session messages:', e)
 }
 }

 return (
 <>
 <button
 type="button"
 onClick={() => setOpen(true)}
 className="mr-2 flex h-8 w-8 items-center justify-center rounded-lg bg-surface-card text-accent-muted transition-colors hover:text-text-primary md:hidden"
 aria-label="Open chat history"
 >
 <span className="material-symbols-outlined text-[19px]">history</span>
 </button>
 {open && (
 <>
 <button
 type="button"
 className="fixed inset-0 z-[130] cursor-default bg-black/25 md:hidden"
 aria-label="Close chat history"
 onClick={() => setOpen(false)}
 />
 <div className="fixed left-2 right-2 top-2 z-[131] max-h-[72dvh] overflow-hidden rounded-2xl border border-border/10 bg-surface shadow-2xl md:hidden">
 <div className="flex items-center justify-between border-b border-border/10 px-3 py-2">
 <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-accent-muted">Chats</span>
 <button
 type="button"
 onClick={handleNewChat}
 className="flex h-8 items-center gap-1 rounded-lg bg-surface-card px-2 text-xs text-text-primary"
 >
 <span className="material-symbols-outlined text-[16px]">edit</span>
 New
 </button>
 </div>
 <div className="max-h-[calc(72dvh-49px)] overflow-y-auto p-2">
 {visibleSessions.map((session) => (
 <button
 key={session.session_id}
 type="button"
 onClick={() => handleSelectSession(session.session_id)}
 className={`mb-1 flex w-full items-center gap-2 rounded-xl px-3 py-3 text-left transition-colors ${
 activeSessionId === session.session_id
 ? 'bg-surface-card text-text-primary'
 : 'text-accent-muted hover:bg-surface-card/60 hover:text-text-primary'
 }`}
 >
 <span className="material-symbols-outlined text-[17px]">chat_bubble</span>
 <span className="min-w-0 flex-1 truncate text-sm">
 {session.title || (session.session_id.startsWith('pending_') ? 'New chat' : `Chat ${session.session_id.slice(0, 8)}`)}
 </span>
 </button>
 ))}
 {visibleSessions.length === 0 && (
 <div className="px-3 py-8 text-center text-xs text-accent-muted/50">
 No chats yet.
 </div>
 )}
 </div>
 </div>
 </>
 )}
 </>
 )
}
