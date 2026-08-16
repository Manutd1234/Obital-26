import { useState, useEffect } from 'react'
import { useUIStore } from '../../stores/uiStore'
import { useAuthStore } from '../../stores/authStore'
import { getSettings, updateSettings } from '../../api/settings'
import { api } from '../../api/client'

export function SettingsModal() {
 const closeSettings = useUIStore((s) => s.closeSettings)
 const settingsTab = useUIStore((s) => s.settingsTab)
 const showToast = useUIStore((s) => s.showToast)
 const theme = useUIStore((s) => s.theme)
 const toggleTheme = useUIStore((s) => s.toggleTheme)
 const { username, lock: storeLock, unlockToken } = useAuthStore()
 const [prompt, setPrompt] = useState('')
 const [loading, setLoading] = useState(true)
 const [saving, setSaving] = useState(false)
 const [syncing, setSyncing] = useState<'push' | 'pull' | null>(null)
 const [activeTab, setActiveTab] = useState<'prompt' | 'account' | 'sync'>(settingsTab)

 useEffect(() => {
 getSettings()
 .then((res) => setPrompt(res.chat?.system_prompt || ''))
 .catch(() => {})
 .finally(() => setLoading(false))
 }, [])

 const handleSavePrompt = async () => {
 setSaving(true)
 try {
 await updateSettings({ chat: { system_prompt: prompt } })
 showToast('System prompt saved')
 closeSettings()
 } catch (e) {
 showToast(`Failed to save: ${e instanceof Error ? e.message : 'unknown'}`)
 }
 setSaving(false)
 }

 const handleSync = async (direction: 'push' | 'pull') => {
 setSyncing(direction)
 try {
 await api.post(`/sync/${direction}`)
 showToast(`${direction === 'push' ? 'Pushed' : 'Pulled'} data successfully`)
 } catch (e) {
 showToast(`Sync failed: ${e instanceof Error ? e.message : 'unknown'}`)
 }
 setSyncing(null)
 }

 const handleLock = async () => {
 try {
 await api.post('/auth/lock')
 } catch {
 // ignore
 }
 storeLock()
 closeSettings()
 }

 return (
 <div className="fixed inset-0 z-[100] bg-black/80 flex items-center justify-center p-6" onClick={closeSettings}>
 <div
 className="bg-surface-lighter rounded-2xl w-full max-w-[680px] max-h-[85vh] overflow-hidden flex flex-col shadow-2xl glass-panel"
 onClick={(e) => e.stopPropagation()}
 >
 {/* Header */}
 <div className="h-12 bg-surface-card flex items-center justify-between px-6 flex-shrink-0">
 <span className="text-sm font-semibold text-text-primary font-mono uppercase tracking-wider">
 {activeTab === 'account' ? 'Profile' : 'Settings'}
 </span>
 <button
 onClick={closeSettings}
 className="text-accent-muted hover:text-danger transition-colors p-1"
 >
 <span className="material-symbols-outlined text-lg">close</span>
 </button>
 </div>

 {/* Tabs */}
 <div className="flex flex-shrink-0">
 {[
 { id: 'prompt' as const, label: 'System Prompt', icon: 'smart_toy' },
 { id: 'account' as const, label: 'Account', icon: 'account_circle' },
 { id: 'sync' as const, label: 'Sync & Storage', icon: 'cloud_sync' },
 ].map((tab) => (
 <button
 key={tab.id}
 onClick={() => setActiveTab(tab.id)}
 className={`flex-1 flex items-center justify-center gap-2 py-3 text-xs font-mono uppercase tracking-wider transition-all border-b-2 ${
 activeTab === tab.id
 ? 'text-accent border-accent'
 : 'text-accent-muted/50 border-transparent hover:text-accent-muted'
 }`}
 >
 <span className="material-symbols-outlined text-sm">{tab.icon}</span>
 {tab.label}
 </button>
 ))}
 </div>

 {/* Content */}
 <div className="flex-1 overflow-y-auto p-6 custom-scrollbar">
 {activeTab === 'prompt' && (
 <div className="space-y-4">
 <div>
 <label className="block text-[10px] text-accent-muted uppercase tracking-widest font-mono mb-2">
 System Prompt
 </label>
 <p className="text-[10px] text-accent-muted/40 mb-3">
 Controls how the AI tutor behaves. This is sent with every message.
 </p>
 {loading ? (
 <div className="h-48 bg-surface rounded-lg flex items-center justify-center">
 <div className="w-4 h-4 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
 </div>
 ) : (
 <textarea
 className="w-full min-h-[250px] bg-surface text-text-primary text-sm p-4 rounded-xl outline-none focus:border-accent transition-colors font-mono resize-y leading-relaxed"
 value={prompt}
 onChange={(e) => setPrompt(e.target.value)}
 placeholder="You are a helpful study tutor..."
 />
 )}
 </div>
 </div>
 )}

 {activeTab === 'account' && (
 <div className="space-y-6">
 {/* Profile card */}
 <div className="bg-surface rounded-xl p-5 space-y-4">
 <div className="flex items-center gap-4">
 <div className="w-14 h-14 rounded-full bg-surface-card flex items-center justify-center text-accent-muted">
 <span className="material-symbols-outlined text-2xl">account_circle</span>
 </div>
 <div>
 <h3 className="text-base font-bold text-text-primary">{username || 'Guest'}</h3>
 <p className="text-[10px] text-accent-muted/50 font-mono uppercase tracking-wider">
 {unlockToken ? 'Vault Unlocked' : 'Locked'}
 </p>
 </div>
 </div>
 <div className="flex items-center justify-between pt-4">
 <div>
 <p className="text-xs font-mono uppercase tracking-wider text-accent-muted/70">
 Appearance
 </p>
 <p className="text-[10px] text-accent-muted/40 mt-1">
 Keep the workspace light, or switch to a softer dark mode.
 </p>
 </div>
 <button
 onClick={toggleTheme}
 className="flex items-center gap-2 rounded-xl px-3 py-2 text-xs text-text-primary hover:bg-surface-card transition-all"
 >
 <span className="material-symbols-outlined text-sm">
 {theme === 'dark' ? 'dark_mode' : 'light_mode'}
 </span>
 {theme === 'dark' ? 'Dark' : 'Light'}
 </button>
 </div>
 </div>

 {/* Danger zone */}
 <div className="border border-danger/20 rounded-xl p-5 space-y-3">
 <h4 className="text-xs font-mono uppercase tracking-wider text-danger/70">
 Security
 </h4>
 <button
 onClick={handleLock}
 className="w-full flex items-center justify-center gap-2 py-2.5 border border-danger/30 text-danger text-xs rounded-xl hover:bg-danger/5 transition-all"
 >
 <span className="material-symbols-outlined text-sm">lock</span>
 Lock Vault
 </button>
 </div>
 </div>
 )}

 {activeTab === 'sync' && (
 <div className="space-y-6">
 {/* Sync status */}
 <div className="bg-surface rounded-xl p-5 space-y-4">
 <div className="flex items-center gap-2">
 <span className="material-symbols-outlined text-accent text-base">cloud_sync</span>
 <h4 className="text-xs font-mono uppercase tracking-wider text-accent-muted font-medium">
 Vault Sync
 </h4>
 <span className="ml-auto text-[10px] font-mono text-success">Enabled</span>
 </div>
 <p className="text-[10px] text-accent-muted/40">
 Sync your chat sessions, quiz history, and documents between local and cloud storage.
 </p>

 <div className="flex gap-3">
 <button
 onClick={() => handleSync('push')}
 disabled={syncing !== null}
 className="flex-1 py-3 bg-accent/10 text-accent text-xs font-mono uppercase tracking-wider rounded-xl hover:bg-accent/20 transition-all disabled:opacity-50 flex items-center justify-center gap-2"
 >
 {syncing === 'push' ? (
 <div className="w-3 h-3 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
 ) : (
 <span className="material-symbols-outlined text-sm">cloud_upload</span>
 )}
 Push
 </button>
 <button
 onClick={() => handleSync('pull')}
 disabled={syncing !== null}
 className="flex-1 py-3 bg-accent/10 text-accent text-xs font-mono uppercase tracking-wider rounded-xl hover:bg-accent/20 transition-all disabled:opacity-50 flex items-center justify-center gap-2"
 >
 {syncing === 'pull' ? (
 <div className="w-3 h-3 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
 ) : (
 <span className="material-symbols-outlined text-sm">cloud_download</span>
 )}
 Pull
 </button>
 </div>
 </div>

 {/* Storage info */}
 <div className="bg-surface rounded-xl p-5 space-y-3">
 <div className="flex items-center gap-2">
 <span className="material-symbols-outlined text-accent-muted text-base">database</span>
 <h4 className="text-xs font-mono uppercase tracking-wider text-accent-muted font-medium">
 Local Storage
 </h4>
 </div>
 <div className="space-y-2">
 <div className="flex justify-between text-xs">
 <span className="text-accent-muted/60">Data encrypted with AES-GCM</span>
 <span className="text-accent-muted/40 font-mono">
 <span className="material-symbols-outlined text-xs text-success align-middle">verified_user</span>
 </span>
 </div>
 <div className="flex justify-between text-xs">
 <span className="text-accent-muted/60">Key derivation</span>
 <span className="text-accent-muted/40 font-mono">Argon2id</span>
 </div>
 </div>
 </div>
 </div>
 )}
 </div>

 {/* Footer (prompt tab only) */}
 {activeTab === 'prompt' && (
 <div className="p-4 flex justify-end gap-3 flex-shrink-0">
 <button
 onClick={closeSettings}
 className="px-4 py-2 text-text-primary text-xs rounded-xl hover:bg-surface-card transition-all"
 >
 Cancel
 </button>
 <button
 onClick={handleSavePrompt}
 disabled={saving || loading}
 className="px-5 py-2 bg-accent text-surface font-bold text-xs uppercase tracking-wider rounded-xl hover:bg-accent-hover transition-all disabled:opacity-50 flex items-center gap-2"
 >
 {saving && (
 <div className="w-3 h-3 border-2 border-surface/30 border-t-surface rounded-full animate-spin" />
 )}
 {saving ? 'Saving...' : 'Save'}
 </button>
 </div>
 )}
 </div>
 </div>
 )
}
