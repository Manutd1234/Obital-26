import { api } from './client'

export interface AccountUsage {
  tokens_used: number
  doc_count: number
  lessons_count: number
  storage_bytes: number
  max_tokens: number
  max_docs: number
  max_lessons: number
  max_storage_bytes: number
}

export async function getUsage() {
  return api.get<AccountUsage>('/usage')
}

export async function recordTokens(tokens: number) {
  return api.post<{ status: string; tokens_used: number }>('/usage/tokens', { tokens })
}
