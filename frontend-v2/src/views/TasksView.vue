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
const theme = ref('')            // filtre par thème ; '' = tous
const phase = ref('prevues')     // prevues | encours | passees | toutes
const creating = ref(false)

// Phases dérivées du STATUT (combinaison phase × statut).
const PHASES = {
  prevues: { label: 'Prévues', cls: 'amber', statuses: ['pending', 'ready', 'waiting_user', 'stalled'] },
  encours: { label: 'En cours', cls: 'blue', statuses: ['in_progress'] },
  passees: { label: 'Passées', cls: 'gray', statuses: ['done', 'failed', 'cancelled'] },
}
const phaseList = ['prevues', 'encours', 'passees', 'toutes']

async function load() {
  tasks.value = await api.get('/api/tasks')
  const list = await api.get('/api/agents')
  agents.value = Object.fromEntries(list.map((a) => [a.id, a.name]))
  agentCat.value = Object.fromEntries(list.map((a) => [a.id, a.category || '']))
}
onMounted(load)

const hover = ref(null)   // { task, y } : aperçu au survol
function onEnter(e, t) { hover.value = { task: t, y: e.clientY } }
function onLeave() { hover.value = null }

// Thèmes présents parmi les agents des tâches affichées
const themes = computed(() =>
  [...new Set(tasks.value.map((t) => agentCat.value[t.agent_id]).filter(Boolean))].sort())
// Raison d'attente d'une tâche (pertinent pour la phase « Prévues »).
function waitInfo(t) {
  if (t.next_session_at) return { kind: 'session', label: '⏱ ' + fmtDate(t.next_session_at), cls: 'blue' }
  if (t.blocked_by && t.blocked_by.length) return { kind: 'dep', label: '⏳ attend #' + t.blocked_by[0].task_id, cls: 'amber' }
  if (t.status === 'waiting_user') return { kind: 'question', label: '⏸ question ouverte', cls: 'violet' }
  return null
}
const visibleTasks = computed(() => {
  let list = tasks.value
  if (theme.value) list = list.filter((t) => agentCat.value[t.agent_id] === theme.value)
  const statuses = PHASES[phase.value]?.statuses
  if (statuses) list = list.filter((t) => statuses.includes(t.status))
  // Tri : Prévues → par prochaine échéance puis bloquées ; Passées → plus récentes d'abord.
  return list.slice().sort((a, b) => {
    if (phase.value === 'passees') {
      const ka = a.completed_at || '', kb = b.completed_at || ''
      if (ka !== kb) return kb.localeCompare(ka)   // plus récent d'abord (completed_at est une chaîne ISO)
      return b.id - a.id                            // rupture : par id décroissant (number, pas localeCompare)
    }
    const wa = waitInfo(a), wb = waitInfo(b)
    const ra = wa?.kind === 'session' ? 0 : 1, rb = wb?.kind === 'session' ? 0 : 1
    if (ra !== rb) return ra - rb
    if (wa?.kind === 'session' && wb?.kind === 'session') return a.next_session_at.localeCompare(b.next_session_at)
    return String(a.title || a.id).localeCompare(String(b.title || b.id))
  })
})


async function onCreated() { creating.value = false; await load() }
</script>

<template>
  <div class="row spread">
    <h1>Tâches</h1>
    <button class="primary" @click="creating = true">+ Nouvelle tâche</button>
  </div>
  <!-- Phase (déduite du statut) : Prévues par défaut — cohérent avec les badges -->
  <div class="row wrap" style="gap: .35rem; margin-bottom: .6rem">
    <button v-for="p in phaseList" :key="p" class="sm"
      :class="{ primary: phase === p }"
      @click="phase = (p === 'toutes' ? 'toutes' : p)">
      {{ p === 'toutes' ? 'Toutes' : PHASES[p].label }}
    </button>
  </div>
  <!-- Filtres par thème : couleur dédiée (teal), distincte des filtres de phase -->
  <div v-if="themes.length" class="row wrap" style="gap: .35rem; margin-bottom: 1rem">
    <button class="sm themebtn" :class="{ on: !theme }" @click="theme = ''">Tous thèmes</button>
    <button v-for="t in themes" :key="t" class="sm themebtn" :class="{ on: theme === t }" @click="theme = t">
      🏷 {{ t }}
    </button>
  </div>

  <div v-if="!visibleTasks.length" class="card pad empty">
    Aucune tâche {{ phase === 'toutes' ? '' : (PHASES[phase]?.label.toLowerCase() + ' ') }}{{ theme ? 'pour ce thème' : '' }}.

  </div>
  <table v-else class="card" style="overflow: hidden">
    <thead><tr><th>#</th><th>Titre</th><th>Agent</th><th>Thème</th><th>Statut</th><th>Attente</th><th>Prochain objectif</th><th>Tokens</th></tr></thead>
    <tbody>
      <tr v-for="t in visibleTasks" :key="t.id" style="cursor: pointer"
        @click="router.push('/tasks/' + t.id)" @mouseenter="onEnter($event, t)" @mouseleave="onLeave">
        <td>{{ t.id }}</td>
        <td>{{ t.title || t.description.slice(0, 70) }}</td>
        <td class="muted">{{ agents[t.agent_id] || '#' + t.agent_id }}</td>
        <td><span v-if="agentCat[t.agent_id]" class="badge teal" style="font-size: .72rem">🏷 {{ agentCat[t.agent_id] }}</span></td>
        <td><StatusBadge :status="t.status" /></td>
        <td><span v-if="waitInfo(t)" class="badge" :class="waitInfo(t).cls" style="font-size: .72rem">{{ waitInfo(t).label }}</span><span v-else class="muted">—</span></td>
        <td class="muted" style="max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: .82rem"
          :title="t.next_objective || ''">{{ t.next_objective || '—' }}</td>
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
    <div v-if="hover.task.next_objective" style="margin-top: .5rem; font-size: .85rem">
      <strong style="font-size: .78rem; color: var(--primary)">▶ PROCHAIN OBJECTIF</strong>
      <div style="white-space: pre-wrap; max-height: 20vh; overflow: hidden">{{ hover.task.next_objective }}</div>
    </div>
    <div v-if="hover.task.result" style="margin-top: .5rem; font-size: .85rem">
      <strong style="font-size: .78rem; color: var(--muted)">RÉSULTAT</strong>
      <div class="muted" style="white-space: pre-wrap; max-height: 20vh; overflow: hidden">{{ hover.task.result.slice(0, 500) }}</div>
    </div>
  </div>

  <TaskForm v-if="creating" @close="creating = false" @saved="onCreated" />
</template>
