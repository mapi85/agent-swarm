<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'
import { fmtTokens } from '../utils.js'
import StatusBadge from '../components/StatusBadge.vue'
import TaskForm from '../components/TaskForm.vue'

const router = useRouter()
const tasks = ref([])
const agents = ref({})     // id -> nom
const agentCat = ref({})   // id -> thème (catégorie)
const statusFilter = ref('')
const theme = ref('')      // filtre par thème ; '' = tous
const creating = ref(false)

async function load() {
  const url = '/api/tasks' + (statusFilter.value ? `?status=${statusFilter.value}` : '')
  tasks.value = await api.get(url)
  const list = await api.get('/api/agents')
  agents.value = Object.fromEntries(list.map((a) => [a.id, a.name]))
  agentCat.value = Object.fromEntries(list.map((a) => [a.id, a.category || '']))
}
onMounted(load)

const statuses = ['', 'pending', 'ready', 'in_progress', 'waiting_user', 'done', 'failed']
const statusLabel = { '': 'Tous', pending: 'En attente', ready: 'Prête', in_progress: 'En cours',
  waiting_user: 'Attend', done: 'Terminée', failed: 'Échec' }

// Thèmes présents parmi les agents des tâches affichées
const themes = computed(() =>
  [...new Set(tasks.value.map((t) => agentCat.value[t.agent_id]).filter(Boolean))].sort())
const visibleTasks = computed(() =>
  theme.value ? tasks.value.filter((t) => agentCat.value[t.agent_id] === theme.value) : tasks.value)

async function onCreated() { creating.value = false; await load() }
</script>

<template>
  <div class="row spread">
    <h1>Tâches</h1>
    <button class="primary" @click="creating = true">+ Nouvelle tâche</button>
  </div>
  <div class="row wrap" style="margin-bottom: .6rem">
    <button v-for="s in statuses" :key="s" class="sm" :class="{ primary: statusFilter === s }"
      @click="statusFilter = s; load()">{{ statusLabel[s] }}</button>
  </div>
  <!-- Filtres par thème (tag de l'agent porteur de la tâche) -->
  <div v-if="themes.length" class="row wrap" style="gap: .35rem; margin-bottom: 1rem">
    <button class="sm" :class="{ primary: !theme }" @click="theme = ''">Tous thèmes</button>
    <button v-for="t in themes" :key="t" class="sm" :class="{ primary: theme === t }" @click="theme = t">
      🏷 {{ t }}
    </button>
  </div>

  <div v-if="!visibleTasks.length" class="card pad empty">Aucune tâche{{ theme ? ' pour ce thème' : '' }}.</div>
  <table v-else class="card" style="overflow: hidden">
    <thead><tr><th>#</th><th>Titre</th><th>Agent</th><th>Thème</th><th>Statut</th><th>Tokens</th></tr></thead>
    <tbody>
      <tr v-for="t in visibleTasks" :key="t.id" style="cursor: pointer" @click="router.push('/tasks/' + t.id)">
        <td>{{ t.id }}</td>
        <td>{{ t.title || t.description.slice(0, 70) }}</td>
        <td class="muted">{{ agents[t.agent_id] || '#' + t.agent_id }}</td>
        <td><span v-if="agentCat[t.agent_id]" class="badge gray" style="font-size: .72rem">🏷 {{ agentCat[t.agent_id] }}</span></td>
        <td><StatusBadge :status="t.status" /></td>
        <td class="muted">{{ fmtTokens(t.input_tokens + t.output_tokens) }}</td>
      </tr>
    </tbody>
  </table>

  <TaskForm v-if="creating" @close="creating = false" @saved="onCreated" />
</template>
