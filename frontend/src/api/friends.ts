import { api } from './client'

export interface FriendInfo {
  account_id: string
  display_name: string
  added_at: string
}

export interface FriendSearchResult {
  account_id: string
  display_name: string
  already_friend: boolean
}

export interface FriendPresence {
  account_id: string
  display_name: string
  status: string
  online: boolean
  last_seen_at: string | null
}

export async function listFriends() {
  return api.get<FriendInfo[]>('/friends')
}

export async function searchFriend(q: string) {
  return api.get<FriendSearchResult>(`/friends/search?q=${encodeURIComponent(q)}`)
}

export async function addFriend(friendId: string) {
  return api.post<FriendInfo>(`/friends/${encodeURIComponent(friendId)}`)
}

export async function removeFriend(friendId: string) {
  return api.del<{ status: string }>(`/friends/${encodeURIComponent(friendId)}`)
}

export async function getFriendsPresence() {
  return api.get<FriendPresence[]>('/friends/presence')
}
