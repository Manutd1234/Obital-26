import { api } from './client'

export interface Settings {
  chat: { system_prompt: string }
}

export async function getSettings() {
  return api.get<Settings>('/settings')
}

export async function updateSettings(patch: { chat?: { system_prompt?: string } }) {
  return api.patch<Settings>('/settings', patch)
}
