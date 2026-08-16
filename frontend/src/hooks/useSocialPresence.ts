import { useEffect, useRef, useCallback } from 'react'
import { useAuthStore } from '../stores/authStore'
import { useFriendStore } from '../stores/friendStore'
import { sendHeartbeat } from '../api/presence'
import { DEV_NO_AUTH } from '../lib/devMock'

const HEARTBEAT_INTERVAL_MS = 25_000
const PRESENCE_FALLBACK_INTERVAL_MS = 10_000

export function useSocialPresence() {
  const { isUnlocked, username } = useAuthStore()
  const normUsername = (username || '').trim().toLowerCase()
  const { fetchFriends, fetchPresence, friends } = useFriendStore()
  const heartbeatTimerRef = useRef<number | null>(null)
  const presenceFallbackTimerRef = useRef<number | null>(null)

  const startHeartbeat = useCallback(() => {
    if (heartbeatTimerRef.current != null) return
    sendHeartbeat('online').catch(() => {})
    heartbeatTimerRef.current = window.setInterval(() => {
      sendHeartbeat('online').catch(() => {})
    }, HEARTBEAT_INTERVAL_MS)
  }, [])

  const stopHeartbeat = useCallback(() => {
    if (heartbeatTimerRef.current != null) {
      window.clearInterval(heartbeatTimerRef.current)
      heartbeatTimerRef.current = null
    }
  }, [])

  useEffect(() => {
    if (DEV_NO_AUTH || !isUnlocked || !normUsername) return

    fetchFriends().then(() => fetchPresence()).catch(() => {})
    startHeartbeat()

    presenceFallbackTimerRef.current = window.setInterval(() => {
      fetchPresence()
    }, PRESENCE_FALLBACK_INTERVAL_MS)

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        sendHeartbeat('online').catch(() => {})
        fetchPresence()
      }
    }

    document.addEventListener('visibilitychange', handleVisibilityChange)

    return () => {
      stopHeartbeat()
      if (presenceFallbackTimerRef.current != null) {
        window.clearInterval(presenceFallbackTimerRef.current)
        presenceFallbackTimerRef.current = null
      }
      document.removeEventListener('visibilitychange', handleVisibilityChange)
      sendHeartbeat('offline').catch(() => {})
    }
  }, [isUnlocked, normUsername])

  const isFriendOnline = useCallback(
    (friendId: string) => useFriendStore.getState().onlineFriendIds.has(friendId),
    [],
  )

  return { friends, isFriendOnline }
}
