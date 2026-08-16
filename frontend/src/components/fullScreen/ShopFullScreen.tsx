import { useEffect, useState } from 'react'
import { useShopStore } from '../../stores/shopStore'
import { useDocStore } from '../../stores/docStore'
import { useUIStore } from '../../stores/uiStore'
import { QuizWidget } from '../chat/QuizWidget'
import { gradePack } from '../../api/shop'
import { listDocs } from '../../api/docs'
import type { OwnedPack, PackTier, PackTierId } from '../../api/shop'

const DEFAULT_TIERS: PackTier[] = [
  { id: 'silver', label: 'Silver', difficulty: 'easy', num_questions: 3, cost: 50 },
  { id: 'gold', label: 'Gold', difficulty: 'medium', num_questions: 5, cost: 150 },
  { id: 'platinum', label: 'Platinum', difficulty: 'hard', num_questions: 7, cost: 300 },
]

const TIER_VISUALS: Record<PackTierId, { emoji: string; gradient: string; label: string }> = {
  silver: { emoji: '🥈', gradient: 'from-slate-300 via-slate-100 to-slate-400', label: 'text-slate-700' },
  gold: { emoji: '🥇', gradient: 'from-amber-300 via-yellow-100 to-amber-400', label: 'text-amber-800' },
  platinum: { emoji: '💎', gradient: 'from-violet-300 via-cyan-100 to-cyan-300', label: 'text-violet-800' },
}

function TierPack({
  tier,
  available,
  noDocs,
  purchasing,
  onBuy,
}: {
  tier: PackTier
  available: number
  noDocs: boolean
  purchasing: boolean
  onBuy: () => void
}) {
  const v = TIER_VISUALS[tier.id]
  const cantAfford = available < tier.cost
  const disabled = noDocs || cantAfford || purchasing
  const reason = noDocs ? 'Upload a document first' : cantAfford ? `Need ${tier.cost} XP` : ''

  return (
    <div className="flex flex-col overflow-hidden rounded-2xl border border-border/10 bg-surface-light">
      {/* Foil header */}
      <div className={`relative h-32 bg-gradient-to-br ${v.gradient} flex items-center justify-center`}>
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-tr from-white/0 via-white/40 to-white/0 opacity-60" />
        <span className="text-5xl drop-shadow">{v.emoji}</span>
      </div>

      <div className="flex flex-1 flex-col p-5">
        <div className="flex items-center justify-between">
          <h3 className="font-mono text-base font-bold uppercase tracking-wider text-text-primary">{tier.label}</h3>
          <span className="rounded-full bg-accent/15 px-3 py-1 font-mono text-sm font-bold text-accent">
            {tier.cost} XP
          </span>
        </div>
        <div className="mt-2 space-y-1 text-xs text-accent-muted">
          <div className="flex items-center gap-1.5">
            <span className="material-symbols-outlined text-sm">trending_up</span>
            {tier.difficulty} difficulty
          </div>
          <div className="flex items-center gap-1.5">
            <span className="material-symbols-outlined text-sm">style</span>
            {tier.num_questions} quiz cards from your docs
          </div>
        </div>

        <div className="flex-1" />

        <button
          onClick={onBuy}
          disabled={disabled}
          title={reason}
          className="mt-4 flex items-center justify-center gap-1.5 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-surface transition-colors hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-50"
        >
          {purchasing ? (
            'Opening…'
          ) : (
            <>
              <span className="material-symbols-outlined text-base">shopping_cart</span>
              Buy pack
            </>
          )}
        </button>
        {disabled && reason && !purchasing && (
          <p className="mt-1 text-center text-[10px] text-accent-muted/60">{reason}</p>
        )}
      </div>
    </div>
  )
}

function OwnedPackCard({
  pack,
  revealing,
  onOpen,
  onTake,
}: {
  pack: OwnedPack
  revealing: boolean
  onOpen: () => void
  onTake: () => void
}) {
  const v = TIER_VISUALS[pack.tier]
  const cardCount = pack.quiz?.questions?.length ?? 0

  if (!pack.opened) {
    return (
      <button
        onClick={onOpen}
        className={`group relative flex h-44 flex-col items-center justify-center overflow-hidden rounded-2xl border border-border/10 bg-gradient-to-br ${v.gradient} transition-transform duration-300 ${
          revealing ? 'scale-110 animate-pulse' : 'hover:-translate-y-1'
        }`}
      >
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-tr from-white/0 via-white/50 to-white/0 opacity-60" />
        <span className="text-5xl drop-shadow">{v.emoji}</span>
        <span className="mt-2 font-mono text-xs font-bold uppercase tracking-wider text-slate-800/80">
          {revealing ? 'Opening…' : 'Tap to open'}
        </span>
      </button>
    )
  }

  return (
    <div className="flex h-44 flex-col rounded-2xl border border-border/10 bg-surface-light p-4">
      <div className="flex items-center gap-2">
        <span className="text-2xl">{v.emoji}</span>
        <div>
          <div className="font-mono text-sm font-bold uppercase tracking-wider text-text-primary">{pack.tier}</div>
          <div className="text-[10px] uppercase tracking-wider text-accent-muted">{pack.difficulty}</div>
        </div>
      </div>
      <div className="mt-2 flex-1 text-xs text-accent-muted">
        {cardCount} card{cardCount !== 1 ? 's' : ''} ready to play
        {pack.rewarded_at && (
          <div className="mt-1 font-mono text-[10px] uppercase tracking-wider text-accent-muted/60">
            XP claimed
          </div>
        )}
      </div>
      <button
        onClick={onTake}
        className="flex items-center justify-center gap-1.5 rounded-lg bg-surface-card px-3 py-2 text-sm text-text-primary transition-colors hover:bg-surface-lighter"
      >
        <span className="material-symbols-outlined text-base">play_arrow</span>
        Take quiz
      </button>
    </div>
  )
}

export function ShopFullScreen() {
  const shop = useShopStore((s) => s.state)
  const loading = useShopStore((s) => s.loading)
  const purchasing = useShopStore((s) => s.purchasing)
  const error = useShopStore((s) => s.error)
  const load = useShopStore((s) => s.load)
  const purchase = useShopStore((s) => s.purchase)
  const openStore = useShopStore((s) => s.open)
  const docs = useDocStore((s) => s.docs)
  const setDocs = useDocStore((s) => s.setDocs)
  const showToast = useUIStore((s) => s.showToast)

  const [revealingId, setRevealingId] = useState<string | null>(null)
  const [activeQuiz, setActiveQuiz] = useState<OwnedPack | null>(null)
  const [docsChecked, setDocsChecked] = useState(false)

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    let cancelled = false
    if (docs.length > 0) {
      setDocsChecked(true)
      return
    }
    listDocs()
      .then((fresh) => {
        if (!cancelled) setDocs(fresh)
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setDocsChecked(true)
      })
    return () => {
      cancelled = true
    }
  }, [docs.length, setDocs])

  const tiers = shop?.tiers?.length ? shop.tiers : DEFAULT_TIERS
  const noDocs = docsChecked && docs.length === 0
  const available = shop?.available_xp ?? 0
  const packs = shop?.packs ?? []

  const onBuy = async (tier: PackTier) => {
    if (noDocs) {
      showToast('Upload a document first — packs are made from your docs.')
      return
    }
    const res = await purchase(tier.id)
    showToast(res.ok ? `${tier.label} pack acquired! Open it below.` : res.error || 'Purchase failed')
  }

  const onOpen = (pack: OwnedPack) => {
    setRevealingId(pack.id)
    setTimeout(() => {
      openStore(pack.id)
      setRevealingId(null)
    }, 900)
  }

  const closeQuiz = () => {
    setActiveQuiz(null)
    load() // taking a quiz earns XP — refresh the wallet
  }

  return (
    <div className="custom-scrollbar h-full overflow-y-auto p-4 md:p-8">
      <div className="mx-auto max-w-[1100px] space-y-8">
        {/* Header */}
        <div className="flex flex-wrap items-end justify-between gap-4 border-b border-border/10 pb-4">
          <div>
            <h2 className="text-2xl font-bold text-text-primary">Pack Shop</h2>
            <p className="mt-1 text-sm text-accent-muted">
              Spend XP on quiz packs generated from your uploaded documents. Higher tiers = harder questions.
            </p>
          </div>
          <div className="rounded-xl bg-surface-light border border-border/5 px-5 py-3 text-right">
            <div className="font-mono text-[10px] uppercase tracking-wider text-accent-muted">Available XP</div>
            <div className="text-3xl font-bold text-accent">{available.toLocaleString()}</div>
            {shop && (
              <div className="font-mono text-[10px] text-accent-muted/60">
                {shop.earned_xp.toLocaleString()} earned · {shop.spent_points.toLocaleString()} spent
              </div>
            )}
          </div>
        </div>

        {noDocs && (
          <div className="rounded-xl border border-accent/30 bg-accent/5 px-4 py-3 text-sm text-text-primary">
            Upload documents first — packs draw their quiz cards from your own material.
          </div>
        )}

        {error && (
          <div className="rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-text-primary">
            Shop could not load: {error}
          </div>
        )}

        {/* Tiers */}
        <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
          {tiers.map((tier) => (
            <TierPack
              key={tier.id}
              tier={tier}
              available={available}
              noDocs={noDocs}
              purchasing={purchasing === tier.id}
              onBuy={() => onBuy(tier)}
            />
          ))}
          {loading && !shop && [1, 2, 3].map((i) => <div key={i} className="h-72 animate-pulse rounded-2xl bg-surface-light" />)}
        </div>

        {/* Owned packs */}
        <div>
          <h3 className="mb-3 font-mono text-sm font-semibold uppercase tracking-wider text-text-primary">
            Your Packs {packs.length > 0 && <span className="text-accent-muted/50">· {packs.length}</span>}
          </h3>
          {packs.length === 0 ? (
            <div className="rounded-xl border border-border/5 bg-surface-light p-10 text-center text-sm text-accent-muted/50">
              No packs yet. Buy one above to get quiz cards from your documents.
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
              {packs
                .slice()
                .reverse()
                .map((pack) => (
                  <OwnedPackCard
                    key={pack.id}
                    pack={pack}
                    revealing={revealingId === pack.id}
                    onOpen={() => onOpen(pack)}
                    onTake={() => setActiveQuiz(pack)}
                  />
                ))}
            </div>
          )}
        </div>
      </div>

      {/* Take-quiz modal (reuses the existing QuizWidget) */}
      {activeQuiz && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-black/80 p-4"
          onClick={closeQuiz}
        >
          <div
            className="max-h-[88vh] w-full max-w-[640px] overflow-y-auto custom-scrollbar rounded-2xl bg-surface p-5"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-3 flex items-center justify-between">
              <span className="font-mono text-xs uppercase tracking-wider text-accent-muted">
                {TIER_VISUALS[activeQuiz.tier].emoji} {activeQuiz.tier} pack · {activeQuiz.difficulty}
              </span>
              <button
                onClick={closeQuiz}
                className="flex h-7 w-7 items-center justify-center rounded-lg text-accent-muted hover:bg-surface-card hover:text-text-primary"
              >
                <span className="material-symbols-outlined text-base">close</span>
              </button>
            </div>
            {activeQuiz.rewarded_at && (
              <div className="mb-3 rounded-lg border border-border/10 bg-surface-card px-3 py-2 text-xs text-accent-muted">
                XP for this pack has already been claimed. Retakes are practice only.
              </div>
            )}
            <QuizWidget
              data={activeQuiz.quiz}
              onGrade={(_, answers) => gradePack(activeQuiz.id, answers)}
            />
          </div>
        </div>
      )}
    </div>
  )
}
