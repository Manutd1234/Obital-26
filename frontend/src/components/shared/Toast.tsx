import { useUIStore } from '../../stores/uiStore'

export function Toast() {
 const msg = useUIStore((s) => s.toastMessage)
 if (!msg) return null

 return (
 <div className="fixed top-4 right-4 z-50 animate-slide-up">
 <div className="bg-surface-card rounded-xl px-5 py-3 shadow-2xl glass-panel">
 <p className="text-sm text-text-primary">{msg}</p>
 </div>
 </div>
 )
}
