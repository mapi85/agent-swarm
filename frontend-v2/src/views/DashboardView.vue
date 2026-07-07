<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { api, stream } from '../api.js'
import { fmtTokens, fmtDate } from '../utils.js'
import Markdown from '../components/Markdown.vue'

const router = useRouter()
const ov = ref({ agents: 0, open_tasks: 0, running_sessions: 0, planned_sessions: 0, running_missions: 0, open_notifications: 0 })
const questions = ref([])
const replies = ref({})
const agents = ref([])
const tok = ref(null)
const tl = ref(null)
const period = ref('all')
let stop = null

const kpis = [
  { key: 'agents', label: 'Agents', icon: '🤖', to: '/agents' },
  { key: 'open_tasks', label: 'Tâches ouvertes', icon: '📋', to: '/tasks' },
  { key: 'running_sessions', label: 'Sessions en cours', icon: '▶️', to: '/tasks' },
  { key: 'planned_sessions', label: 'Sessions planifiées', icon: '⏱️', to: '/tasks' },
  { key: 'running_missions', label: 'Missions actives', icon: '🎯', to: '/missions' },
  { key: 'open_notifications', label: 'Notifications', icon: '🔔', to: '/' },
]

// --- Regroupement des agents par état ---
function stateOf(a) {
  if (a.running_tasks > 0) return 'running'
  if (a.paused) return 'paused'
  if (a.open_tasks > 0) return 'waiting'
  return 'idle'
}
const STATES = [
  { key: 'running', label: '▶ En cours', cls: 'blue' },
  { key: 'waiting', label: '⏱ Tâches en attente', cls: 'amber' },
  { key: 'paused', label: '⏸ En pause', cls: 'gray' },
  { key: 'idle', label: '💤 Inactifs', cls: 'gray' },
]
const grouped = computed(() => {
  const g = { running: [], waiting: [], paused: [], idle: [] }
  for (const a of agents.value) g[stateOf(a)].push(a)
  return g
})

// --- Timeline ---
const tlSpan = computed(() => tl.value ? (new Date(tl.value.end) - new Date(tl.value.start)) : 1)
const nowPct = computed(() => tl.value ? (new Date(tl.value.now) - new Date(tl.value.start)) / tlSpan.value * 100 : 0)
const tlAgents = computed(() => {
  if (!tl.value) return []
  const by = {}
  for (const s of tl.value.sessions) (by[s.agent] ||= []).push(s)
  return Object.entries(by).map(([agent, sessions]) => ({ agent, sessions }))
})
function block(s) {
  const start = new Date(s.started_at || s.scheduled_at)
  let endT = s.ended_at ? new Date(s.ended_at) : (s.status === 'running' ? new Date(tl.value.now) : new Date(start.getTime() + 30 * 60000))
  const left = Math.max(0, (start - new Date(tl.value.start)) / tlSpan.value * 100)
  const width = Math.max(1.2, (endT - start) / tlSpan.value * 100)
  const color = { completed: 'var(--green)', running: 'var(--primary)', failed: 'var(--red)', interrupted: 'var(--amber)', planned: 'var(--violet)' }[s.status] || 'var(--muted)'
  return { left: left + '%', width: Math.min(width, 100 - left) + '%', background: color, opacity: s.status === 'planned' ? 0.5 : 1 }
}

// --- Histogramme tokens ---
const maxBar = computed(() => tok.value ? Math.max(1, ...tok.value.by_time.map((b) => b.t)) : 1)

async function loadQuestions() {
  try { questions.value = await api.get('/api/notifications?status=open&type=question') } catch { /* */ }
}
async function answer(n) {
  const response = (replies.value[n.id] || '').trim()
  if (!response) return
  await api.post(`/api/notifications/${n.id}/answer`, { response })
  await loadQuestions()
}
async function loadTokens() { tok.value = await api.get('/api/stats/tokens?period=' + period.value) }

onMounted(async () => {
  try { ov.value = await api.get('/api/overview') } catch { /* */ }
  try { agents.value = await api.get('/api/agents') } catch { /* */ }
  try { tl.value = await api.get('/api/stats/timeline') } catch { /* */ }
  loadTokens(); loadQuestions()
  stop = stream('/api/stream/overview', (ev, data) => {
    if (ev === 'overview') {
      ov.value.open_tasks = data.open_tasks
      ov.value.running_sessions = data.running_sessions
      ov.value.open_notifications = data.open_notifications
    }
  })
})
onUnmounted(() => { if (stop) stop() })
</script>

<template>
  <h1>Tableau de bord</h1>

  <!-- Questions en attente (en haut ; masqué s'il n'y en a pas) -->
  <template v-if="questions.length">
    <h2>❓ Questions en attente</h2>
    <div class="stack" style="margin-bottom: 1.2rem">
      <div v-for="n in questions" :key="n.id" class="card pad">
        <div class="muted" style="font-size: .8rem">Tâche #{{ n.task_id }}</div>
        <Markdown :text="n.content" style="margin: .3rem 0" />
        <textarea v-model="replies[n.id]" placeholder="Ta réponse… (Ctrl+Entrée)" rows="2"
          @keydown.ctrl.enter="answer(n)"></textarea>
        <button class="primary sm" style="margin-top: .4rem" @click="answer(n)">Répondre</button>
      </div>
    </div>
  </template>

  <!-- KPIs -->
  <div class="grid" style="grid-template-columns: repeat(auto-fit, minmax(150px, 1fr))">
    <div v-for="k in kpis" :key="k.key" class="card kpi" style="cursor: pointer" @click="router.push(k.to)">
      <div class="l">{{ k.icon }} {{ k.label }}</div>
      <div class="n">{{ ov[k.key] }}</div>
    </div>
  </div>

  <!-- Timeline -->
  <h2 style="margin-top: 1.5rem">⏱ Activité (−12h / +6h)</h2>
  <div class="card pad">
    <div v-if="!tlAgents.length" class="empty">Aucune session sur la fenêtre.</div>
    <div v-else>
      <div v-for="row in tlAgents" :key="row.agent" class="row" style="gap: .5rem; margin-bottom: .3rem">
        <div style="width: 140px; font-size: .8rem" class="muted">{{ row.agent }}</div>
        <div style="position: relative; flex: 1; height: 20px; background: #f2f4f7; border-radius: 4px">
          <div v-for="s in row.sessions" :key="s.id" :style="block(s)"
            style="position: absolute; top: 2px; height: 16px; border-radius: 3px"
            :title="`${s.agent} · ${s.status} · ${(s.objective||'').slice(0,60)}`"></div>
        </div>
      </div>
      <div style="position: relative; margin-left: 148px; height: 14px">
        <div :style="{ left: nowPct + '%' }" style="position: absolute; top: 0; width: 2px; height: 14px; background: var(--red)"></div>
        <span :style="{ left: nowPct + '%' }" style="position: absolute; top: 0; font-size: .68rem; color: var(--red); transform: translateX(-50%)">maintenant</span>
      </div>
    </div>
  </div>

  <!-- Agents par état -->
  <h2 style="margin-top: 1.5rem">État des agents</h2>
  <div class="grid" style="grid-template-columns: repeat(auto-fit, minmax(200px, 1fr))">
    <div v-for="st in STATES" :key="st.key" class="card pad">
      <div class="row spread">
        <strong>{{ st.label }}</strong>
        <span class="badge" :class="st.cls">{{ grouped[st.key].length }}</span>
      </div>
      <div v-for="a in grouped[st.key]" :key="a.id" class="navlink" style="padding: .25rem .4rem"
        @click="router.push('/agents/' + a.id)">
        {{ a.name }}
      </div>
    </div>
  </div>

  <!-- Consommation de tokens -->
  <div class="row spread" style="margin-top: 1.5rem; align-items: flex-end">
    <h2 style="margin: 0">Consommation de tokens</h2>
    <div class="row">
      <button v-for="p in ['all','7d','24h']" :key="p" class="sm" :class="{ primary: period === p }"
        @click="period = p; loadTokens()">{{ p === 'all' ? 'Tout' : p === '7d' ? '7 jours' : '24h' }}</button>
    </div>
  </div>
  <template v-if="tok">
    <div class="grid" style="grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); margin-top: .6rem">
      <div class="card kpi"><div class="l">Total (in+out)</div><div class="n">{{ fmtTokens(tok.total) }}</div></div>
      <div class="card kpi"><div class="l">Sessions facturées</div><div class="n">{{ tok.sessions }}</div></div>
      <div class="card kpi"><div class="l">Moyenne / session</div><div class="n">{{ fmtTokens(tok.avg_per_session) }}</div></div>
      <div class="card kpi"><div class="l">Session la plus lourde</div><div class="n">{{ fmtTokens(tok.heaviest_session) }}</div></div>
      <div class="card kpi" title="Part des tokens d'entrée relue depuis le cache de préfixe (facturée à prix réduit)">
        <div class="l">Cache (préfixe relu)</div><div class="n">{{ tok.cache_hit_rate ?? 0 }}%</div></div>
    </div>

    <div v-if="tok.by_time.length" class="card pad" style="margin-top: 1rem">
      <div style="display: flex; align-items: flex-end; gap: 3px; height: 120px; overflow-x: auto">
        <div v-for="b in tok.by_time" :key="b.label" style="display: flex; flex-direction: column; align-items: center; min-width: 22px">
          <div class="muted" style="font-size: .65rem">{{ fmtTokens(b.t) }}</div>
          <div :style="{ height: (b.t / maxBar * 90 + 4) + 'px' }" :title="`${b.label} · ${fmtTokens(b.t)} · ${b.s} sessions`"
            style="width: 18px; background: var(--primary); border-radius: 3px 3px 0 0"></div>
          <div class="muted" style="font-size: .6rem; transform: rotate(-45deg); white-space: nowrap; margin-top: .3rem">{{ b.label.slice(5) }}</div>
        </div>
      </div>
    </div>

    <div class="grid" style="grid-template-columns: 1fr 1fr; margin-top: 1rem">
      <div class="card pad">
        <h3>Par agent</h3>
        <table><thead><tr><th>Agent</th><th>In</th><th>Out</th></tr></thead>
          <tbody><tr v-for="r in tok.by_agent" :key="r.name"><td>{{ r.name }}</td><td class="muted">{{ fmtTokens(r.i) }}</td><td class="muted">{{ fmtTokens(r.o) }}</td></tr>
            <tr v-if="!tok.by_agent.length"><td colspan="3" class="muted">Aucune consommation.</td></tr></tbody></table>
      </div>
      <div class="card pad">
        <h3>Par provider</h3>
        <table><thead><tr><th>Provider</th><th>In</th><th>Out</th></tr></thead>
          <tbody><tr v-for="r in tok.by_provider" :key="r.name"><td>{{ r.name }}</td><td class="muted">{{ fmtTokens(r.i) }}</td><td class="muted">{{ fmtTokens(r.o) }}</td></tr>
            <tr v-if="!tok.by_provider.length"><td colspan="3" class="muted">Aucune consommation.</td></tr></tbody></table>
      </div>
    </div>
  </template>

</template>
