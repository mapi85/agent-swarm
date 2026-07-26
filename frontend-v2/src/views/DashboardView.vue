<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { api, stream } from '../api.js'
import { fmtTokens, fmtDate } from '../utils.js'
import Markdown from '../components/Markdown.vue'
import Modal from '../components/Modal.vue'

const router = useRouter()
const ov = ref({ agents: 0, open_tasks: 0, running_sessions: 0, planned_sessions: 0, running_missions: 0, open_notifications: 0 })
const attention = ref({ questions: [], stalled: [] })
const replies = ref({})        // réponses inline aux questions (key = notif_id)
const relanceTarget = ref(null) // tâche stalled en cours de relance (pour le modal)
const relanceNote = ref('')
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
// Graduations horaires sur la fenêtre -12h / +6h (toutes les 3h).
const tlTicks = computed(() => {
  const ticks = []
  for (let h = -12; h <= 6; h += 3) {
    ticks.push({ pct: ((h + 12) / 18) * 100, label: h === 0 ? '▶' : (h + 'h') })
  }
  return ticks
})
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

async function loadAttention() {
  try { attention.value = await api.get('/api/tasks/attention') } catch { /* */ }
}
const attentionCount = computed(() => attention.value.questions.length + attention.value.stalled.length)

async function answer(q) {
  const response = (replies.value[q.notif_id] || '').trim()
  if (!response) return
  await api.post(`/api/notifications/${q.notif_id}/answer`, { response })
  await loadAttention()
}
function openRelance(item) { relanceTarget.value = item; relanceNote.value = '' }
async function confirmRelance() {
  if (!relanceTarget.value) return
  const id = relanceTarget.value.task_id
  try {
    await api.post(`/api/tasks/${id}/relance`, { note: relanceNote.value || null })
  } catch (e) {
    // Agent en pause : la session ne partirait jamais. Proposer de le réactiver.
    if (!String(e.message).includes('en pause')) { alert(e.message); return }
    if (!confirm(e.message + '\n\nRéactiver l\'agent et lancer la session maintenant ?')) return
    try { await api.post(`/api/tasks/${id}/relance`, { note: relanceNote.value || null, resume_agent: true }) }
    catch (e2) { alert(e2.message); return }
  }
  relanceTarget.value = null
  relanceNote.value = ''
  await loadAttention()
}
async function abandon(item) {
  await api.post(`/api/tasks/${item.task_id}/cancel`)
  await loadAttention()
}
async function loadTokens() { tok.value = await api.get('/api/stats/tokens?period=' + period.value) }

onMounted(async () => {
  try { ov.value = await api.get('/api/overview') } catch { /* */ }
  try { agents.value = await api.get('/api/agents') } catch { /* */ }
  try { tl.value = await api.get('/api/stats/timeline') } catch { /* */ }
  loadTokens(); loadAttention()
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

  <!-- Bac « À traiter » : questions (réponse inline) + tâches bloquées (relance/abandon) -->
  <template v-if="attentionCount">
    <h2>🚨 À traiter ({{ attentionCount }})</h2>
    <div class="stack" style="margin-bottom: 1.2rem">
      <!-- Questions explicites des agents -->
      <div v-for="q in attention.questions" :key="'q'+q.notif_id" class="card pad">
        <div class="row spread">
          <div class="muted" style="font-size: .8rem">{{ q.agent_name }} · tâche #{{ q.task_id }}</div>
          <span class="badge violet" style="font-size: .72rem">question</span>
        </div>
        <Markdown :text="q.content" style="margin: .3rem 0" />
        <textarea v-model="replies[q.notif_id]" placeholder="Ta réponse… (Ctrl+Entrée)" rows="2"
          @keydown.ctrl.enter="answer(q)"></textarea>
        <div class="row" style="justify-content: flex-end; margin-top: .4rem">
          <button class="primary sm" @click="answer(q)">Répondre</button>
        </div>
      </div>
      <!-- Tâches bloquées (chaîne brisée / agent qui n'avance plus) -->
      <div v-for="s in attention.stalled" :key="'s'+s.task_id" class="card pad">
        <div class="row spread">
          <div>
            <strong>{{ s.agent_name }}</strong>
            <span class="muted" style="font-size: .82rem"> · tâche #{{ s.task_id }} — {{ s.title }}</span>
          </div>
          <div class="row" style="gap: .3rem">
            <span v-if="s.agent_paused" class="badge amber" style="font-size: .72rem">⏸ agent en pause</span>
            <span class="badge red" style="font-size: .72rem">bloquée ({{ s.consecutive_stalls }}×)</span>
          </div>
        </div>
        <div class="muted" style="font-size: .82rem; margin: .3rem 0">
          L'agent n'avance plus. Relance-le (avec un commentaire pour guider les prochaines sessions) ou abandonne.
        </div>
        <div class="row" style="justify-content: flex-end; gap: .4rem">
          <button class="ghost sm" @click="abandon(s)">Abandonner</button>
          <button class="primary sm" @click="openRelance(s)">Relancer…</button>
        </div>
      </div>
    </div>
  </template>

  <!-- Modal de relance avec commentaire -->
  <Modal v-if="relanceTarget" :title="`Relancer ${relanceTarget.agent_name} (#${relanceTarget.task_id})`" @close="relanceTarget = null">
    <p class="muted" style="font-size: .85rem">
      Ton commentaire sera transmis à l'agent comme note de session pour guider les prochaines exécutions.
    </p>
    <textarea v-model="relanceNote" rows="4" placeholder="Ex. : reprendre la surveillance 4h ; vérifier X d'abord…"></textarea>
    <div class="row" style="justify-content: flex-end; margin-top: 1rem; gap: .4rem">
      <button class="ghost" @click="relanceTarget = null">Annuler</button>
      <button class="primary" @click="confirmRelance()">Lancer la session</button>
    </div>
  </Modal>

  <!-- KPIs -->
  <div class="grid" style="grid-template-columns: repeat(auto-fit, minmax(150px, 1fr))">
    <div v-for="k in kpis" :key="k.key" class="card kpi" style="cursor: pointer" @click="router.push(k.to)">
      <div class="l">{{ k.icon }} {{ k.label }}</div>
      <div class="n">{{ ov[k.key] }}</div>
    </div>
  </div>

  <!-- Timeline -->
  <h2 style="margin-top: 1.5rem">⏱ Activité (−12h / +6h) — cliquez un bloc pour ouvrir la tâche</h2>
  <div class="card pad">
    <div v-if="!tlAgents.length" class="empty">Aucune session sur la fenêtre.</div>
    <div v-else>
      <div v-for="row in tlAgents" :key="row.agent" class="row" style="gap: .5rem; margin-bottom: .3rem">
        <div style="width: 140px; font-size: .8rem" class="muted">{{ row.agent }}</div>
        <div style="position: relative; flex: 1; height: 22px; background: #f2f4f7; border-radius: 4px">
          <div v-for="s in row.sessions" :key="s.id" :style="block(s)"
            style="position: absolute; top: 2px; height: 18px; border-radius: 3px; cursor: pointer"
            :title="`${s.agent} · ${s.status} · ${(s.objective||'').slice(0,80)}`"
            @click="s.task_id && router.push('/tasks/' + s.task_id)"></div>
        </div>
      </div>
      <!-- Axe temporel : graduations + repère « maintenant » -->
      <div style="position: relative; margin-left: 148px; height: 18px; margin-top: .2rem">
        <div v-for="tk in tlTicks" :key="tk.pct" :style="{ left: tk.pct + '%' }"
          style="position: absolute; top: 0; transform: translateX(-50%); font-size: .66rem"
          :class="tk.label === '▶' ? '' : 'muted'">
          <div style="width: 1px; height: 5px; background: var(--muted); margin: 0 auto"></div>
          {{ tk.label }}
        </div>
        <div :style="{ left: nowPct + '%' }" style="position: absolute; top: 0; width: 2px; height: 14px; background: var(--red)"></div>
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
      <div class="muted" style="font-size: .8rem; margin-bottom: .6rem">Consommation dans le temps (survolez une barre pour le détail, défilez horizontalement si besoin)</div>
      <div style="display: flex; align-items: flex-end; gap: 6px; height: 210px; overflow-x: auto; padding: .3rem .2rem 1.4rem">
        <div v-for="b in tok.by_time" :key="b.label"
          style="display: flex; flex-direction: column; align-items: center; min-width: 40px; height: 100%; justify-content: flex-end"
          :title="`${b.label} · ${fmtTokens(b.t)} · ${b.s} session(s)`">
          <div class="muted" style="font-size: .68rem; margin-bottom: 3px">{{ fmtTokens(b.t) }}</div>
          <div :style="{ height: (b.t / maxBar * 160 + 4) + 'px' }"
            style="width: 26px; background: var(--primary); border-radius: 4px 4px 0 0"></div>
          <div class="muted" style="font-size: .68rem; margin-top: .4rem; white-space: nowrap; transform: translateY(1.2rem)">{{ b.label }}</div>
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
