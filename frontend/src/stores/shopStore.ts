import { create } from 'zustand'
import { getShop, openPack, purchasePack, type PackTierId, type ShopState } from '../api/shop'

// API errors arrive as `API 400: {"detail":"..."}` — surface just the detail.
function cleanError(msg: string): string {
  const match = msg.match(/\{.*\}$/)
  if (match) {
    try {
      const parsed = JSON.parse(match[0])
      if (parsed?.detail) return String(parsed.detail)
    } catch {
      /* fall through */
    }
  }
  return msg
}

interface ShopStoreState {
  state: ShopState | null
  loading: boolean
  purchasing: PackTierId | null
  error: string | null
  load: () => Promise<void>
  purchase: (tier: PackTierId) => Promise<{ ok: boolean; error?: string }>
  open: (id: string) => Promise<void>
}

export const useShopStore = create<ShopStoreState>((set) => ({
  state: null,
  loading: false,
  purchasing: null,
  error: null,

  load: async () => {
    set({ loading: true, error: null })
    try {
      set({ state: await getShop(), loading: false })
    } catch (err) {
      set({ loading: false, error: err instanceof Error ? err.message : 'Failed to load shop' })
    }
  },

  purchase: async (tier) => {
    set({ purchasing: tier, error: null })
    try {
      await purchasePack(tier)
      const fresh = await getShop()
      set({ state: fresh, purchasing: null })
      return { ok: true }
    } catch (err) {
      const msg = cleanError(err instanceof Error ? err.message : 'Purchase failed')
      set({ purchasing: null, error: msg })
      return { ok: false, error: msg }
    }
  },

  open: async (id) => {
    // Optimistically flip locally; persist in the background.
    set((s) =>
      s.state
        ? { state: { ...s.state, packs: s.state.packs.map((p) => (p.id === id ? { ...p, opened: true } : p)) } }
        : s,
    )
    try {
      await openPack(id)
    } catch {
      /* non-fatal: local state already reflects opened */
    }
  },
}))
