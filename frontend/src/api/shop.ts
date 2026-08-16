import { api } from './client'
import type { QuizAnswer, QuizBuildResponse, QuizGradeResponse } from './quiz'

export type PackTierId = 'silver' | 'gold' | 'platinum'

export interface PackTier {
  id: PackTierId
  label: string
  difficulty: string
  num_questions: number
  cost: number
}

export interface OwnedPack {
  id: string
  tier: PackTierId
  difficulty: string
  cost: number
  created_at: string
  opened: boolean
  rewarded_at: string | null
  best_score_percent: number | null
  quiz: QuizBuildResponse
}

export interface ShopState {
  tiers: PackTier[]
  earned_xp: number
  spent_points: number
  available_xp: number
  packs: OwnedPack[]
}

export async function getShop() {
  return api.get<ShopState>('/shop')
}

export async function purchasePack(tier: PackTierId) {
  return api.post<OwnedPack>('/shop/purchase', { tier })
}

export async function openPack(id: string) {
  return api.post<OwnedPack>(`/shop/packs/${encodeURIComponent(id)}/open`)
}

export async function gradePack(id: string, answers: QuizAnswer[]) {
  return api.post<QuizGradeResponse>(`/shop/packs/${encodeURIComponent(id)}/grade`, { answers })
}
