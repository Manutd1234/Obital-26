import { create } from 'zustand'
import {
  listFriends,
  addFriend as apiAddFriend,
  removeFriend as apiRemoveFriend,
  searchFriend as apiSearchFriend,
  getFriendsPresence,
  type FriendInfo,
  type FriendSearchResult,
  type FriendPresence,
} from '../api/friends'

interface FriendState {
  friends: FriendInfo[]
  onlineFriendIds: Set<string>
  presence: FriendPresence[]
  loading: boolean
  error: string | null

  fetchFriends: () => Promise<void>
  searchFriend: (q: string) => Promise<FriendSearchResult>
  addFriend: (friendId: string) => Promise<void>
  removeFriend: (friendId: string) => Promise<void>
  fetchPresence: () => Promise<void>
  isFriendOnline: (friendId: string) => boolean
}

export const useFriendStore = create<FriendState>((set, get) => ({
  friends: [],
  onlineFriendIds: new Set(),
  presence: [],
  loading: false,
  error: null,

  fetchFriends: async () => {
    set({ loading: true, error: null })
    try {
      const friends = await listFriends()
      set({ friends, loading: false })
    } catch (err) {
      set({
        loading: false,
        error: err instanceof Error ? err.message : 'Failed to fetch friends',
      })
    }
  },

  searchFriend: async (q) => {
    return apiSearchFriend(q)
  },

  addFriend: async (friendId) => {
    const entry = await apiAddFriend(friendId)
    set((state) => ({ friends: [...state.friends, entry] }))
  },

  removeFriend: async (friendId) => {
    await apiRemoveFriend(friendId)
    set((state) => ({
      friends: state.friends.filter((f) => f.account_id !== friendId),
      onlineFriendIds: new Set(
        [...state.onlineFriendIds].filter((id) => id !== friendId),
      ),
    }))
  },

  fetchPresence: async () => {
    try {
      const presence = await getFriendsPresence()
      const heartbeatOnline = presence.filter((p) => p.online).map((p) => p.account_id)
      set({ presence, onlineFriendIds: new Set(heartbeatOnline) })
    } catch {
      // heartbeat fallback may fail silently
    }
  },

  isFriendOnline: (friendId) => get().onlineFriendIds.has(friendId),
}))
