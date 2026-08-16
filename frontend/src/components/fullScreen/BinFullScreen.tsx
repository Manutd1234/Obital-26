import { useEffect, useState } from 'react'
import { listBin, restoreBinItem, permanentDeleteBinItem } from '../../api/bin'
import type { BinItem } from '../../api/bin'
import { useUIStore } from '../../stores/uiStore'

export function BinFullScreen() {
 const [items, setItems] = useState<BinItem[]>([])
 const [loading, setLoading] = useState(true)
 const showToast = useUIStore((s) => s.showToast)

 const fetchItems = async () => {
 setLoading(true)
 try {
 const data = await listBin()
 setItems(data)
 } catch {
 showToast('Failed to load bin')
 }
 setLoading(false)
 }

 useEffect(() => {
 fetchItems()
 }, [])

 const handleRestore = async (binId: string) => {
 const previousItems = items
 setItems((current) => current.filter((item) => item.id !== binId))
 try {
 await restoreBinItem(binId)
 showToast('Item restored')
 } catch {
 setItems(previousItems)
 showToast('Failed to restore item')
 }
 }

 const handlePermanentDelete = async (binId: string) => {
 const previousItems = items
 setItems((current) => current.filter((item) => item.id !== binId))
 try {
 await permanentDeleteBinItem(binId)
 showToast('Item permanently deleted')
 } catch {
 setItems(previousItems)
 showToast('Failed to delete item')
 }
 }

 return (
 <div className="flex h-full">
 <div className="flex-1 flex flex-col max-w-3xl mx-auto w-full">
 {/* Header */}
 <div className="p-6">
 <h2 className="text-xl font-semibold text-text-primary flex items-center gap-2">
 <span className="material-symbols-outlined">delete</span>
 Bin
 </h2>
 <p className="text-xs text-accent-muted/50 mt-1">
 Items are permanently deleted after 30 days
 </p>
 </div>

 {/* Content */}
 <div className="flex-1 overflow-y-auto custom-scrollbar p-4">
 {loading && (
 <div className="text-center py-16 text-accent-muted/40 text-sm">Loading...</div>
 )}

 {!loading && items.length === 0 && (
 <div className="text-center py-16">
 <span className="material-symbols-outlined text-accent-muted/15 text-5xl block mb-3">
 delete_sweep
 </span>
 <p className="text-accent-muted/40 text-xs">Bin is empty</p>
 </div>
 )}

 {!loading && items.length > 0 && (
 <div className="space-y-2">
 {items.map((item) => (
 <div
 key={item.id}
 className="flex items-center gap-4 p-3 rounded-lg bg-surface-card/50"
 >
 <span className="material-symbols-outlined text-accent-muted text-lg">
 {item.item_type === 'lesson' ? 'school' : 'description'}
 </span>
 <div className="flex-1 min-w-0">
 <p className="text-sm text-text-primary truncate">{item.name}</p>
 <p className="text-[10px] text-accent-muted/50 font-mono mt-0.5">
 {item.item_type} &middot; {Math.ceil(item.days_remaining)}d remaining
 </p>
 </div>
 <button
 onClick={() => handleRestore(item.id)}
 className="px-3 py-1.5 text-xs rounded-lg bg-accent/10 text-accent hover:bg-accent/20 transition-all"
 >
 Restore
 </button>
 <button
 onClick={() => handlePermanentDelete(item.id)}
 className="px-3 py-1.5 text-xs rounded-lg text-danger hover:bg-danger/10 transition-all"
 >
 Delete
 </button>
 </div>
 ))}
 </div>
 )}
 </div>
 </div>
 </div>
 )
}
