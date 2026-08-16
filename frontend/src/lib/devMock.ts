// Dev-only "no-auth" mock layer.
//
// When VITE_NO_AUTH=1 (see .env.development.local), the app skips the login
// gate and serves mock data so the entire UI renders with NO backend running.
// This is gated behind an env flag and is completely inert in production
// builds, so it is safe to commit.
//
// Toggle: set VITE_NO_AUTH=1 in frontend/.env.development.local, then `npm run dev`.

export const DEV_NO_AUTH = import.meta.env.VITE_NO_AUTH === '1'

const nowIso = () => new Date().toISOString()

const SAMPLE_SESSIONS = [
  { id: 'demo-1', title: 'Photosynthesis basics', created_at: nowIso(), updated_at: nowIso() },
  { id: 'demo-2', title: 'Linear algebra warm-up', created_at: nowIso(), updated_at: nowIso() },
]

const SAMPLE_MESSAGES: Record<string, { role: string; text: string }[]> = {
  'demo-1': [
    { role: 'user', text: 'Can you explain photosynthesis simply?' },
    {
      role: 'assistant',
      text: 'Sure! Photosynthesis is how plants turn **sunlight**, water, and CO₂ into glucose (energy) and oxygen. It happens mainly in the *chloroplasts*.',
    },
  ],
}

const USAGE = {
  tokens_used: 0,
  doc_count: 0,
  lessons_count: 0,
  storage_bytes: 0,
  max_tokens: 100_000,
  max_docs: 50,
  max_lessons: 100,
  max_storage_bytes: 50 * 1024 * 1024,
}

const DASHBOARD = {
  summary: {
    total_points: 0,
    total_quizzes: 0,
    accuracy_percent: 0,
    total_correct: 0,
    total_answered: 0,
  },
  trend: [],
  session_logs: [],
}

const LEADERBOARD = {
  entries: [
    { rank: 1, account_id: 'ada', display_name: 'ada', total_points: 1280, total_quizzes: 24, accuracy_percent: 91.5 },
    { rank: 2, account_id: 'grace', display_name: 'grace', total_points: 940, total_quizzes: 18, accuracy_percent: 88.0 },
    { rank: 3, account_id: 'demo', display_name: 'demo', total_points: 610, total_quizzes: 12, accuracy_percent: 80.0 },
    { rank: 4, account_id: 'linus', display_name: 'linus', total_points: 300, total_quizzes: 6, accuracy_percent: 72.0 },
  ],
  total_users: 4,
  me: { rank: 3, account_id: 'demo', display_name: 'demo', total_points: 610, total_quizzes: 12, accuracy_percent: 80.0 },
}

const WORKSPACE = {
  code: { js: '', python: '' },
  tasks: {},
  sessions: [],
}

const SHOP_TIERS = [
  { id: 'silver', label: 'Silver', difficulty: 'easy', num_questions: 3, cost: 50 },
  { id: 'gold', label: 'Gold', difficulty: 'medium', num_questions: 5, cost: 150 },
  { id: 'platinum', label: 'Platinum', difficulty: 'hard', num_questions: 7, cost: 300 },
]
const TIER_META: Record<string, { difficulty: string; n: number; cost: number }> = {
  silver: { difficulty: 'easy', n: 3, cost: 50 },
  gold: { difficulty: 'medium', n: 5, cost: 150 },
  platinum: { difficulty: 'hard', n: 7, cost: 300 },
}

function mockQuiz(n: number, difficulty: string) {
  return {
    quiz_id: `mock-${Date.now()}`,
    topic_filter: null,
    exclude_topic_filter: null,
    difficulty_filter: difficulty,
    build_indicator: 'ok',
    failure_reason: 'none',
    warnings: [],
    questions: Array.from({ length: n }, (_, i) => ({
      question_id: `q${i}`,
      topic_id: 't1',
      topic_title: 'Photosynthesis',
      question: `(${difficulty}) Sample question ${i + 1} drawn from your documents?`,
      expected_answer: 'Chlorophyll',
      difficulty,
      source_context: 'From your uploaded notes.',
      question_mode: 'mcq',
      options: ['Chlorophyll', 'Mitochondria', 'Ribosome', 'Nucleus'],
      correct_option_index: 0,
    })),
  }
}

function mockPack(tier: string) {
  const meta = TIER_META[tier] ?? TIER_META.silver
  return {
    id: `pack-${Date.now()}`,
    tier,
    difficulty: meta.difficulty,
    cost: meta.cost,
    created_at: nowIso(),
    opened: false,
    rewarded_at: null as string | null,
    best_score_percent: null as number | null,
    quiz: mockQuiz(meta.n, meta.difficulty),
  }
}

// Stateful dev shop so the buy -> open -> take-quiz flow works without a backend.
const SHOP_EARNED = 600
let shopSpent = 150
const shopPacks: ReturnType<typeof mockPack>[] = [{ ...mockPack('gold'), id: 'pack-demo', opened: true }]

function shopState() {
  return {
    tiers: SHOP_TIERS,
    earned_xp: SHOP_EARNED,
    spent_points: shopSpent,
    available_xp: Math.max(0, SHOP_EARNED - shopSpent),
    packs: shopPacks,
  }
}

/** Mock REST responses, matched by HTTP method + path (query string stripped). */
export async function mockApi<T>(path: string, method = 'GET', _body?: BodyInit | null): Promise<T> {
  const p = path.split('?')[0]
  const m = method.toUpperCase()
  let result: unknown = {}

  if (p === '/chat/sessions' && m === 'GET') result = SAMPLE_SESSIONS
  else if (p === '/chat/sessions' && m === 'POST')
    result = { session_id: `demo-${Date.now()}`, title: 'New chat' }
  else if (/^\/chat\/sessions\/[^/]+$/.test(p) && m === 'GET') {
    const id = p.split('/')[3]
    result = { id, title: 'Chat', messages: SAMPLE_MESSAGES[id] ?? [] }
  } else if (p === '/docs/search') result = []
  else if (p === '/docs')
    result = [{ doc_id: 'demo-doc', name: 'photosynthesis-notes.pdf', chunk_count: 12, created_at: nowIso() }]
  else if (p === '/lessons/jobs' || p.startsWith('/lessons/jobs')) result = m === 'GET' ? [] : {}
  else if (p === '/lessons') result = []
  else if (p === '/bin') result = []
  else if (p === '/usage') result = USAGE
  else if (p === '/rewards/dashboard') result = DASHBOARD
  else if (p === '/leaderboard') result = LEADERBOARD
  else if (p === '/workspace') {
    if (m === 'PUT') {
      try {
        result = _body ? JSON.parse(String(_body)) : WORKSPACE
      } catch {
        result = WORKSPACE
      }
    } else {
      result = WORKSPACE
    }
  } else if (p === '/shop' && m === 'GET') result = shopState()
  else if (p === '/shop/purchase' && m === 'POST') {
    let tier = 'silver'
    try {
      tier = _body ? JSON.parse(String(_body)).tier || 'silver' : 'silver'
    } catch {
      tier = 'silver'
    }
    const pack = mockPack(tier)
    shopSpent += TIER_META[tier]?.cost ?? 50
    shopPacks.push(pack)
    result = pack
  } else if (/^\/shop\/packs\/[^/]+\/open$/.test(p) && m === 'POST') {
    const id = p.split('/')[3]
    const pack = shopPacks.find((x) => x.id === id)
    if (pack) pack.opened = true
    result = pack ?? { status: 'ok' }
  } else if (/^\/shop\/packs\/[^/]+\/grade$/.test(p) && m === 'POST') {
    const id = p.split('/')[3]
    const pack = shopPacks.find((x) => x.id === id)
    if (pack) {
      pack.rewarded_at = pack.rewarded_at || nowIso()
      pack.best_score_percent = 100
    }
    result = {
      quiz_id: pack?.quiz.quiz_id ?? `mock-${Date.now()}`,
      total_questions: pack?.quiz.questions.length ?? 0,
      answered_questions: pack?.quiz.questions.length ?? 0,
      correct_answers: pack?.quiz.questions.length ?? 0,
      score_percent: 100,
      results: (pack?.quiz.questions ?? []).map((q) => ({
        question_id: q.question_id,
        is_correct: true,
        expected_answer: q.expected_answer,
        submitted_answer: q.expected_answer,
        score: 1,
      })),
    }
  }
  else if (p === '/settings') result = { chat: { system_prompt: '' } }
  else if (p.startsWith('/sync/')) result = { status: 'ok', pushed: 0, pulled: 0 }
  else if (p.startsWith('/friends')) {
    if (p === '/friends' && m === 'GET') result = []
    else if (p.startsWith('/friends/search') && m === 'GET')
      result = { account_id: 'demo', display_name: 'demo', already_friend: false }
    else if (p.startsWith('/friends/presence') && m === 'GET') result = []
    else if (/^\/friends\/[^/]+$/.test(p) && m === 'POST')
      result = { account_id: p.split('/')[2], display_name: p.split('/')[2], added_at: nowIso() }
    else if (/^\/friends\/[^/]+$/.test(p) && m === 'DELETE') result = { status: 'ok' }
  } else if (p === '/presence/heartbeat' && m === 'POST') result = { status: 'ok' }
  else if (p === '/auth/lock') result = { status: 'ok' }
  else result = {}

  // tiny delay so loading states are exercised
  await new Promise((r) => setTimeout(r, 60))
  return result as T
}

/** Mock SSE chat stream: echoes a friendly reply token-by-token. */
export async function* mockSSE(body: unknown): AsyncGenerator<{ event: string; data: unknown }> {
  const userText =
    (body && typeof body === 'object' && 'message' in body && String((body as { message: unknown }).message)) ||
    'your message'
  const reply = `*(dev mock reply — no backend)*  You said: "${userText}". This is sample streamed text so you can style the chat view.`
  const words = reply.split(' ')
  let full = ''
  for (const w of words) {
    const chunk = (full ? ' ' : '') + w
    full += chunk
    yield { event: 'token', data: chunk }
    await new Promise((r) => setTimeout(r, 35))
  }
  yield { event: 'done', data: full }
}
