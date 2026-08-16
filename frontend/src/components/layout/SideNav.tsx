import { useState } from 'react'
import { useUIStore, type ViewMode } from '../../stores/uiStore'
import { useAuthStore } from '../../stores/authStore'
import { api } from '../../api/client'

const navItems: { id: ViewMode; icon: string; label: string }[] = [
  { id: 'chat', icon: 'chat_bubble', label: 'Chat' },
  { id: 'dashboard', icon: 'dashboard', label: 'Dashboard' },
  { id: 'lesson', icon: 'deployed_code', label: 'Lessons' },
  { id: 'doc', icon: 'description', label: 'Docs' },
  { id: 'workspace', icon: 'code_blocks', label: 'Work' },
  { id: 'shop', icon: 'storefront', label: 'Shop' },
  { id: 'leaderboard', icon: 'leaderboard', label: 'Ranks' },
  { id: 'bin', icon: 'delete', label: 'Bin' },
]

function NavButton({
  icon,
  label,
  active,
  onClick,
  title,
  collapsed = false,
}: {
  icon: string
  label: string
  active: boolean
  onClick: () => void
  title?: string
  collapsed?: boolean
}) {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      aria-current={active ? 'page' : undefined}
      title={title ?? label}
      className={`group relative flex w-full flex-col items-center rounded-xl px-1 transition-all duration-150 ${
        collapsed ? 'gap-0 py-2' : 'gap-1 py-2.5'
      } ${
        active
          ? 'bg-surface-card text-text-primary shadow-sm'
          : 'text-accent-muted hover:bg-surface-card/60 hover:text-text-primary'
      }`}
    >
      {/* active indicator bar */}
      <span
        className={`absolute left-0 top-1/2 h-6 w-[3px] -translate-y-1/2 rounded-r-full bg-accent transition-opacity ${
          active ? 'opacity-100' : 'opacity-0'
        }`}
      />
      <span className="material-symbols-outlined text-[21px] leading-none">{icon}</span>
      <span
        className={`font-mono text-[9px] uppercase tracking-[0.12em] leading-none ${
          collapsed ? 'sr-only' : ''
        }`}
      >
        {label}
      </span>
    </button>
  )
}

export function SideNav() {
  const openFullScreen = useUIStore((s) => s.openFullScreen)
  const closeFullScreen = useUIStore((s) => s.closeFullScreen)
  const fullScreenView = useUIStore((s) => s.fullScreenView)
  const sideNavCollapsed = useUIStore((s) => s.sideNavCollapsed)
  const theme = useUIStore((s) => s.theme)
  const toggleTheme = useUIStore((s) => s.toggleTheme)
  const toggleSideNav = useUIStore((s) => s.toggleSideNav)
  const openSettings = useUIStore((s) => s.openSettings)
  const openFriends = useUIStore((s) => s.openFriends)
  const logout = useAuthStore((s) => s.logout)

  const handleLogout = async () => {
    try {
      await api.post('/auth/lock')
    } catch {
      // Clear local state even if the backend lock request fails.
    }
    logout()
  }

  const handleNavigate = (id: ViewMode) => {
    if (id === 'chat') {
      closeFullScreen()
    } else {
      openFullScreen(id)
    }
  }

  return (
    <nav
      className={`z-[120] hidden h-full flex-shrink-0 flex-col items-center bg-surface-light/80 py-3 shadow-[inset_-1px_0_0_rgb(var(--color-border)/0.06)] transition-[width] duration-200 md:flex ${
        sideNavCollapsed ? 'w-[48px]' : 'w-[82px]'
      }`}
    >
      {/* Brand mark */}
      <div className="mb-3 flex w-full items-center justify-center px-1">
        <button
          onClick={toggleSideNav}
          title={sideNavCollapsed ? 'Expand navigation' : 'Collapse navigation'}
          aria-label={sideNavCollapsed ? 'Expand navigation' : 'Collapse navigation'}
          className="flex h-9 w-9 items-center justify-center rounded-xl bg-surface-card text-accent hover:bg-surface-lighter"
        >
          <span className="material-symbols-outlined text-[20px] font-bold">
            {sideNavCollapsed ? 'keyboard_double_arrow_right' : 'keyboard_double_arrow_left'}
          </span>
        </button>
      </div>

      {/* Primary navigation */}
      <div className={`flex w-full flex-col gap-1 ${sideNavCollapsed ? 'px-1.5' : 'px-2'}`}>
        {navItems.map((item) => (
          <NavButton
            key={item.id}
            icon={item.icon}
            label={item.label}
            active={item.id === 'chat' ? !fullScreenView : fullScreenView === item.id}
            onClick={() => handleNavigate(item.id)}
            collapsed={sideNavCollapsed}
          />
        ))}
      </div>

      <div className="flex-1" />

      {/* Utilities */}
      <div className={`flex w-full flex-col gap-1 ${sideNavCollapsed ? 'px-1.5' : 'px-2'}`}>
        <NavButton
          icon={theme === 'dark' ? 'light_mode' : 'dark_mode'}
          label={theme === 'dark' ? 'Light' : 'Dark'}
          active={false}
          onClick={toggleTheme}
          title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
          collapsed={sideNavCollapsed}
        />
        <NavButton
          icon="group"
          label="Friends"
          active={false}
          onClick={openFriends}
          collapsed={sideNavCollapsed}
        />
        <NavButton
          icon="settings"
          label="Settings"
          active={false}
          onClick={() => openSettings()}
          collapsed={sideNavCollapsed}
        />
        <NavButton
          icon="logout"
          label="Logout"
          active={false}
          onClick={handleLogout}
          title="Log out"
          collapsed={sideNavCollapsed}
        />
      </div>
    </nav>
  )
}

export function MobileNav() {
  const [moreOpen, setMoreOpen] = useState(false)
  const openFullScreen = useUIStore((s) => s.openFullScreen)
  const closeFullScreen = useUIStore((s) => s.closeFullScreen)
  const fullScreenView = useUIStore((s) => s.fullScreenView)
  const theme = useUIStore((s) => s.theme)
  const toggleTheme = useUIStore((s) => s.toggleTheme)
  const openSettings = useUIStore((s) => s.openSettings)
  const openFriends = useUIStore((s) => s.openFriends)
  const logout = useAuthStore((s) => s.logout)

  const mobileItems = navItems.filter((item) =>
    ['chat', 'dashboard', 'lesson', 'doc'].includes(item.id),
  )

  const handleNavigate = (id: ViewMode) => {
    setMoreOpen(false)
    if (id === 'chat') {
      closeFullScreen()
    } else {
      openFullScreen(id)
    }
  }

  const handleLogout = async () => {
    setMoreOpen(false)
    try {
      await api.post('/auth/lock')
    } catch {
      // Clear local state even if the backend lock request fails.
    }
    logout()
  }

  const moreItems: Array<{ icon: string; label: string; onClick: () => void; active?: boolean }> = [
    {
      icon: 'code_blocks',
      label: 'Workspace',
      onClick: () => handleNavigate('workspace'),
      active: fullScreenView === 'workspace',
    },
    {
      icon: 'storefront',
      label: 'Shop',
      onClick: () => handleNavigate('shop'),
      active: fullScreenView === 'shop',
    },
    {
      icon: 'leaderboard',
      label: 'Ranks',
      onClick: () => handleNavigate('leaderboard'),
      active: fullScreenView === 'leaderboard',
    },
    {
      icon: 'delete',
      label: 'Bin',
      onClick: () => handleNavigate('bin'),
      active: fullScreenView === 'bin',
    },
    {
      icon: 'group',
      label: 'Friends',
      onClick: () => {
        setMoreOpen(false)
        openFriends()
      },
    },
    {
      icon: theme === 'dark' ? 'light_mode' : 'dark_mode',
      label: theme === 'dark' ? 'Light' : 'Dark',
      onClick: () => {
        setMoreOpen(false)
        toggleTheme()
      },
    },
    {
      icon: 'settings',
      label: 'Settings',
      onClick: () => {
        setMoreOpen(false)
        openSettings()
      },
    },
    {
      icon: 'logout',
      label: 'Logout',
      onClick: handleLogout,
    },
  ]

  return (
    <>
      {moreOpen && (
        <>
          <button
            className="fixed inset-0 z-[118] cursor-default bg-black/20 md:hidden"
            aria-label="Close more menu"
            onClick={() => setMoreOpen(false)}
          />
          <div className="fixed bottom-20 left-2 right-2 z-[119] rounded-2xl border border-border/10 bg-surface p-2 shadow-2xl md:hidden">
            <div className="grid grid-cols-4 gap-1">
              {moreItems.map((item) => (
                <button
                  key={item.label}
                  onClick={item.onClick}
                  className={`flex min-h-16 flex-col items-center justify-center gap-1 rounded-xl px-1 py-2 ${
                    item.active
                      ? 'bg-surface-card text-text-primary'
                      : 'text-accent-muted hover:bg-surface-card/70 hover:text-text-primary'
                  }`}
                >
                  <span className="material-symbols-outlined text-[22px] leading-none">{item.icon}</span>
                  <span className="max-w-full truncate font-mono text-[8px] uppercase tracking-[0.08em]">
                    {item.label}
                  </span>
                </button>
              ))}
            </div>
          </div>
        </>
      )}

      <nav className="z-[120] flex h-16 flex-shrink-0 items-center justify-around border-t border-border/10 bg-surface-light px-1 pb-[env(safe-area-inset-bottom)] md:hidden">
        {mobileItems.map((item) => {
          const active = item.id === 'chat' ? !fullScreenView : fullScreenView === item.id
          return (
            <button
              key={item.id}
              onClick={() => handleNavigate(item.id)}
              aria-label={item.label}
              aria-current={active ? 'page' : undefined}
              className={`flex min-w-0 flex-1 flex-col items-center justify-center gap-0.5 rounded-lg px-1 py-1.5 ${
                active ? 'text-text-primary' : 'text-accent-muted'
              }`}
            >
              <span className="material-symbols-outlined text-[22px] leading-none">{item.icon}</span>
              <span className="max-w-full truncate font-mono text-[8px] uppercase tracking-[0.08em]">
                {item.label}
              </span>
            </button>
          )
        })}
        <button
          onClick={() => setMoreOpen((open) => !open)}
          aria-label="More"
          aria-expanded={moreOpen}
          className={`flex min-w-0 flex-1 flex-col items-center justify-center gap-0.5 rounded-lg px-1 py-1.5 ${
            moreOpen ? 'text-text-primary' : 'text-accent-muted'
          }`}
        >
          <span className="material-symbols-outlined text-[22px] leading-none">more_horiz</span>
          <span className="max-w-full truncate font-mono text-[8px] uppercase tracking-[0.08em]">
            More
          </span>
        </button>
      </nav>
    </>
  )
}
