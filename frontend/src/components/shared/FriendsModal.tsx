import { useState, useEffect, useCallback } from 'react'
import { useUIStore } from '../../stores/uiStore'
import { useFriendStore } from '../../stores/friendStore'
import { searchFriend } from '../../api/friends'

export function FriendsModal() {
  const closeFriends = useUIStore((s) => s.closeFriends)
  const showToast = useUIStore((s) => s.showToast)
  const {
    friends,
    onlineFriendIds,
    loading,
    fetchFriends,
    addFriend,
    removeFriend,
  } = useFriendStore()

  const [query, setQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [searchResult, setSearchResult] = useState<{
    account_id: string
    display_name: string
    already_friend: boolean
  } | null>(null)
  const [searchError, setSearchError] = useState<string | null>(null)

  useEffect(() => {
    fetchFriends()
  }, [fetchFriends])

  const handleSearch = useCallback(async () => {
    const q = query.trim().toLowerCase()
    if (!q || q.length < 2) {
      setSearchError('Type at least 2 characters')
      return
    }
    setSearching(true)
    setSearchError(null)
    setSearchResult(null)
    try {
      const result = await searchFriend(q)
      setSearchResult(result)
    } catch (err) {
      if (err instanceof Error && err.message.includes('404')) {
        setSearchError('User not found')
      } else if (err instanceof Error && err.message.includes('400')) {
        setSearchError('Cannot add yourself')
      } else {
        setSearchError(err instanceof Error ? err.message : 'Search failed')
      }
    } finally {
      setSearching(false)
    }
  }, [query])

  const handleAddFriend = useCallback(async (friendId: string) => {
    try {
      await addFriend(friendId)
      showToast(`Added ${friendId}`)
      setSearchResult(null)
      setQuery('')
      setSearchError(null)
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to add friend')
    }
  }, [addFriend, showToast])

  const handleRemoveFriend = useCallback(async (friendId: string) => {
    try {
      await removeFriend(friendId)
      showToast(`Removed ${friendId}`)
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to remove friend')
    }
  }, [removeFriend, showToast])

  return (
    <div
      className="fixed inset-0 z-[110] flex items-center justify-center bg-black/80 p-6"
      onClick={closeFriends}
    >
      <div
        className="flex w-full max-w-[420px] flex-col overflow-hidden rounded-2xl bg-surface-lighter shadow-2xl glass-panel"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex h-12 flex-shrink-0 items-center justify-between bg-surface-card px-6">
          <span className="font-mono text-sm font-semibold uppercase tracking-wider text-text-primary">
            Friends
          </span>
          <button
            onClick={closeFriends}
            className="text-accent-muted transition-colors hover:text-danger"
            aria-label="Close friends"
          >
            <span className="material-symbols-outlined text-lg">close</span>
          </button>
        </div>

        {/* Search bar */}
        <div className="flex-shrink-0 border-b border-border/10 px-6 py-4">
          <div className="flex gap-2">
            <div className="relative flex-1">
              <input
                type="text"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value)
                  setSearchError(null)
                  setSearchResult(null)
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleSearch()
                }}
                placeholder="Search by username..."
                className="w-full rounded-xl bg-surface px-3 py-2 pr-8 font-mono text-sm text-text-primary outline-none placeholder-accent-muted/50 focus:ring-1 focus:ring-accent/30"
              />
              {query && (
                <button
                  onClick={() => {
                    setQuery('')
                    setSearchResult(null)
                    setSearchError(null)
                  }}
                  className="absolute inset-y-0 right-2 flex items-center text-accent-muted/40 hover:text-accent-muted hover:!transform-none"
                  aria-label="Clear friend search"
                >
                  <span className="material-symbols-outlined text-sm">close</span>
                </button>
              )}
            </div>
            <button
              onClick={handleSearch}
              disabled={searching}
              className="flex h-[38px] w-[38px] flex-shrink-0 items-center justify-center rounded-xl bg-accent text-surface transition-colors hover:bg-accent-hover disabled:opacity-50"
            >
              {searching ? (
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-surface/30 border-t-surface" />
              ) : (
                <span className="material-symbols-outlined text-sm">search</span>
              )}
            </button>
          </div>

          {/* Search result */}
          {searchError && (
            <p className="mt-2 font-mono text-[11px] text-danger/80">{searchError}</p>
          )}

          {searchResult && (
            <div className="mt-2 flex items-center justify-between rounded-xl bg-surface-card px-3 py-2">
              <div className="flex items-center gap-2">
                <span className="flex h-7 w-7 items-center justify-center rounded-full bg-surface text-[11px] font-semibold text-accent-muted uppercase">
                  {searchResult.display_name[0]}
                </span>
                <span className="font-mono text-sm text-text-primary">{searchResult.display_name}</span>
              </div>
              {searchResult.already_friend ? (
                <span className="font-mono text-[10px] uppercase tracking-wider text-accent-muted/60">
                  Already added
                </span>
              ) : (
                <button
                  onClick={() => handleAddFriend(searchResult.account_id)}
                  className="rounded-lg bg-accent px-3 py-1 font-mono text-[10px] font-bold uppercase tracking-wider text-surface transition-colors hover:bg-accent-hover"
                >
                  Add
                </button>
              )}
            </div>
          )}
        </div>

        {/* Friend list */}
        <div className="flex-1 overflow-y-auto px-6 py-3 custom-scrollbar">
          {loading && friends.length === 0 ? (
            <div className="space-y-2 py-2">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-11 animate-pulse rounded-xl bg-surface-card/45" />
              ))}
            </div>
          ) : friends.length === 0 ? (
            <div className="py-8 text-center font-mono text-xs text-accent-muted/40">
              No friends yet — search above to find someone
            </div>
          ) : (
            <div className="space-y-1">
              {friends.map((f) => {
                const isOnline = onlineFriendIds.has(f.account_id)
                return (
                  <div
                    key={f.account_id}
                    className="flex items-center justify-between rounded-xl px-3 py-2.5 transition-colors hover:bg-surface-card/60"
                  >
                    <div className="flex items-center gap-2.5 min-w-0">
                      {/* Status dot */}
                      <span
                        className={`inline-block h-2.5 w-2.5 flex-shrink-0 rounded-full transition-colors ${
                          isOnline
                            ? 'bg-success shadow-[0_0_6px_rgba(22,163,74,0.5)]'
                            : 'bg-accent-muted/25'
                        }`}
                      />
                      {/* Avatar */}
                      <span className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-surface text-[11px] font-semibold text-accent-muted uppercase">
                        {f.display_name[0]}
                      </span>
                      {/* Name + status */}
                      <div className="min-w-0">
                        <span className="block truncate font-mono text-sm text-text-primary">
                          {f.display_name}
                        </span>
                        <span className="font-mono text-[10px] uppercase tracking-wider text-accent-muted/50">
                          {isOnline ? 'Online' : 'Offline'}
                        </span>
                      </div>
                    </div>
                    {/* Delete */}
                    <button
                      onClick={() => handleRemoveFriend(f.account_id)}
                      className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg text-accent-muted/30 transition-colors hover:bg-danger/10 hover:text-danger"
                      aria-label={`Remove ${f.display_name}`}
                      title="Remove friend"
                    >
                      <span className="material-symbols-outlined text-base">delete</span>
                    </button>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex-shrink-0 border-t border-border/10 px-6 py-3">
          <span className="font-mono text-[10px] text-accent-muted/40">
            {friends.length} friend{friends.length !== 1 ? 's' : ''} &middot;{' '}
            {onlineFriendIds.size} online
          </span>
        </div>
      </div>
    </div>
  )
}
