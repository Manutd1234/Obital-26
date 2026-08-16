import { ChatHistorySidebar, MobileChatHistoryButton } from './ChatHistorySidebar'
import { MobileNav, SideNav } from './SideNav'
import { ChatView } from '../chat/ChatView'
import { Footer } from './Footer'
import { useUIStore } from '../../stores/uiStore'
import { FullScreenOverlay } from '../fullScreen/FullScreenOverlay'
import { SettingsModal } from '../shared/SettingsModal'
import { FriendsModal } from '../shared/FriendsModal'
import { Toast } from '../shared/Toast'
import { useSocialPresence } from '../../hooks/useSocialPresence'

export function AppShell() {
  const fullScreenView = useUIStore((s) => s.fullScreenView)
  const settingsOpen = useUIStore((s) => s.settingsOpen)
  const friendsOpen = useUIStore((s) => s.friendsOpen)

  useSocialPresence()

  return (
    <div className="flex h-[100dvh] overflow-hidden bg-surface text-text-primary md:h-screen">
      {/* Persistent left navigation rail — always visible across all views */}
      <SideNav />

      {/* Content region — the section overlay covers only this area, not the rail */}
      <div className="relative flex min-w-0 flex-1 flex-col overflow-hidden md:flex-row">
        {/* Chat layout: history sidebar + main */}
        <ChatHistorySidebar />
        <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
          <div className="flex h-9 flex-shrink-0 items-center bg-surface-light pl-2 pr-3 md:pl-4">
            <MobileChatHistoryButton />
            <span className="font-mono text-[11px] uppercase tracking-[0.16em] text-accent-muted">
              Chat
            </span>
          </div>
          <ChatView />
          <div className="hidden md:block">
            <Footer />
          </div>
        </main>

        {/* Full screen section overlay (Dashboard / Lessons / Docs / Bin) */}
        {fullScreenView && <FullScreenOverlay />}
        <MobileNav />
      </div>

      {/* Settings modal */}
      {settingsOpen && <SettingsModal />}
      {friendsOpen && <FriendsModal />}

      {/* Toast notifications */}
      <Toast />
    </div>
  )
}
