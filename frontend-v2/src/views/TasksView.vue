<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'
import { fmtTokens, fmtDate, TASK_STATUS } from '../utils.js'
import StatusBadge from '../components/StatusBadge.vue'
import TaskForm from '../components/TaskForm.vue'

const router = useRouter()
const tasks = ref([])
const agents = ref({})     // id -> nom
const agentCat = ref({})   // id -> thème (catégorie)
const statusFilter = ref('')
const theme = ref('')            // filtre par thème ; '' = tous
const onlyUpcoming = ref(true)   // par défaut : uniquement les tâches avec une session à venir
const creating = ref(false)

async function load() {
  const url = '/api/tasks' + (statusFilter.value ? `?status=${statusFilter.value}` : '')
  tasks.value = await api.get(url)
  const list = await api.get('/api/agents')
  agents.value = Object.fromEntries(list.map((a) => [a.id, a.name]))
  agentCat.value = Object.fromEntries(list.map((a) => [a.id, a.category || '']))
}
onMounted(load)

// Statuts gérés (source unique : TASK_STATUS, partagée avec StatusBadge).
const statuses = ['', ...Object.keys(TASK_STATUS)]
function statusLabel(s) { return s === '' ? 'Tous' : (TASK_STATUS[s] ? TASK_STATUS[s].label : s) }

const hover = ref(null)   // { task, y } : aperçu au survol
function onEnter(e, t) { hover.value = { task: t, y: e.clientY } }
function onLeave() { hover.value = null }

// Thèmes présents parmi les agents des tâches affichées
const themes = computed(() =>
  [...new Set(tasks.value.map((t) => agentCat.value[t.agent_id]).filter(Boolean))].sort())
// Raison d'attente d'une tâche : prochaine session, dépendance, ou question ouverte.
function waitInfo(t) {
  if (t.next_session_at) return { kind: 'session', label: '⏱ ' + fmtDate(t.next_session_at), cls: 'blue' }
  if (t.blocked_by && t.blocked_by.length) return { kind: 'dep', label: '⏳ attend #' + t.blocked_by[0].task_id, cls: 'amber' }
  if (t.status === 'waiting_user') return { kind: 'question', label: '⏸ question ouverte', cls: 'violet' }
  return null
}
const visibleTasks = computed(() => {
  let list = tasks.value
  if (theme.value) list = list.filter((t) => agentCat.value[t.agent_id] === theme.value)
  if (onlyUpcoming.value) {
    // Tâches « à traiter » : ont une session à venir, OU attendent une dépendance,
    // OU attendent une réponse utilisateur — pas seulement celles avec une date.
    list = list
      .filter((t) => t.next_session_at || (t.blocked_by && t.blocked_by.length) || t.status === 'waiting_user')
      .slice()
      .sort((a, b) => {
        const wa = waitInfo(a), wb = waitInfo(b)
        const ra = wa?.kind === 'session' ? 0 : 1
        const rb = wb?.kind === 'session' ? 0 : 1
        if (ra !== rb) return ra - rb
        if (wa?.kind === 'session' && wb?.kind === 'session')
          return a.next_session_at.localeCompare(b.next_session_at)
        return (a.title || a.id).localeCompare(b.title || b.id)
      })
  }
  return list
})

async function onCreated() { creating.value = false; await load() }
</script>

<template>
  <div class="row spread">
    <h1>Tâches</h1>
    <button class="primary" @click="creating = true">+ Nouvelle tâche</button>
  </div>
  <div class="row wrap" style="margin-bottom: .6rem">
    <button v-for="s in statuses" :key="s" class="sm" :class="{ primary: statusFilter === s }"
      @click="statusFilter = s; load()">{{ statusLabel(s) }}</button>
  </div>
  <!-- Filtres par thème : couleur dédiée (teal), distincte des filtres de statut -->
  <div class="row wrap spread" style="gap: .35rem; margin-bottom: 1rem; align-items: center">
    <div class="row wrap" style="gap: .35rem">
      <template v-if="themes.length">
        <button class="sm themebtn" :class="{ on: !theme }" @click="theme = ''">Tous thèmes</button>
        <button v-for="t in themes" :key="t" class="sm themebtn" :class="{ on: theme === t }" @click="theme = t">
          🏷 {{ t }}
        </button>
      </template>
    </div>
    <label class="row" style="width: auto; gap: .4rem; font-size: .85rem; margin: 0">
      <input type="checkbox" v-model="onlyUpcoming" style="width: auto" />
      À venir seulement
    </label>
  </div>

  <div v-if="!visibleTasks.length" class="card pad empty">
    {{ onlyUpcoming ? 'Aucune tâche à traiter (à venir, bloquée ou en attente de réponse).' : 'Aucune tâche' + (theme ? ' pour ce thème' : '') + '.' }}
  </div>
  <table v-else class="card" style="overflow: hidden">
    <thead><tr><th>#</th><th>Titre</th><th>Agent</th><th>Thème</th><th>Statut</th><th>Attente</th><th>Tokens</th></tr></thead>
    <tbody>
      <tr v-for="t in visibleTasks" :key="t.id" style="cursor: pointer"
        @click="router.push('/tasks/' + t.id)" @mouseenter="onEnter($event, t)" @mouseleave="onLeave">
        <td>{{ t.id }}</td>
        <td>{{ t.title || t.description.slice(0, 70) }}</td>
        <td class="muted">{{ agents[t.agent_id] || '#' + t.agent_id }}</td>
        <td><span v-if="agentCat[t.agent_id]" class="badge teal" style="font-size: .72rem">🏷 {{ agentCat[t.agent_id] }}</span></td>
        <td><StatusBadge :status="t.status" /></td>
        <td><span v-if="waitInfo(t)" class="badge" :class="waitInfo(t).cls" style="font-size: .72rem">{{ waitInfo(t).label }}</span><span v-else class="muted">—</span></td>
        <td class="muted">{{ fmtTokens(t.input_tokens + t.output_tokens) }}</td>
      </tr>
    </tbody>
  </table>

  <!-- Aperçu au survol : titre + descriptif de la tâche, sans l'ouvrir -->
  <div v-if="hover" class="card pad" style="position: fixed; right: 1.5rem; max-width: 440px; z-index: 40;
    pointer-events: none; box-shadow: 0 6px 24px rgba(16,24,40,.18)"
    :style="{ top: Math.min(Math.max(hover.y - 40, 70), 400) + 'px' }">
    <div class="row" style="gap: .4rem; margin-bottom: .3rem">
      <strong>#{{ hover.task.id }} {{ hover.task.title || '(sans titre)' }}</strong>
      <StatusBadge :status="hover.task.status" />
    </div>
    <div class="muted" style="font-size: .85rem; white-space: pre-wrap; max-height: 40vh; overflow: hidden">{{ hover.task.description }}</div>
    <div v-if="hover.task.result" style="margin-top: .5rem; font-size: .85rem">
      <strong style="font-size: .78rem; color: var(--muted)">RÉSULTAT</strong>
      <div class="muted" style="white-space: pre-wrap; max-height: 20vh; overflow: hidden">{{ hover.task.result.slice(0, 500) }}</div>
    </div>
  </div>

  <TaskForm v-if="creating" @close="creating = false" @saved="onCreated" />
</template>
