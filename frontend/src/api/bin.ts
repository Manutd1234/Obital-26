import { api } from './client'

export interface BinItem {
  id: string
  item_type: string
  item_id: string
  name: string
  deleted_at: string
  expires_at: string
  days_remaining: number
}

export async function listBin() {
  return api.get<BinItem[]>('/bin')
}

export async function restoreBinItem(binId: string) {
  return api.post<{ status: string; item_type: string; item_id: string }>(`/bin/${binId}/restore`)
}

export async function permanentDeleteBinItem(binId: string) {
  return api.del<{ status: string; bin_id: string }>(`/bin/${binId}`)
}
