<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'
import { fmtTokens } from '../utils.js'
import StatusBadge from '../components/StatusBadge.vue'
import TaskForm from '../components/TaskForm.vue'

const router = useRouter()
const tasks = ref([])
const agents = ref({})
const statusFilter = ref('')
const creating = ref(false)

async function load() {
  const url = '/api/tasks' + (statusFilter.value ? `?status=${statusFilter.value}` : '')
  tasks.value = await api.get(url)
  const list = await api.get('/api/agents')
  agents.value = Object.fromEntries(list.map((a) => [a.id, a.name]))
}
onMounted(load)

const statuses = ['', 'pending', 'ready', 'in_progress', 'waiting_user', 'done', 'failed']
const statusLabel = { '': 'Tous', pending: 'En attente', ready: 'Prête', in_progress: 'En cours',
  waiting_user: 'Attend', done: 'Terminée', failed: 'Échec' }

async function onCreated() { creating.value = false; await load() }
</script>

<template>
  <div class="row spread">
    <h1>Tâches</h1>
    <button class="primary" @click="creating = true">+ Nouvelle tâche</button>
  </div>
  <div class="row wrap" style="margin-bottom: 1rem">
    <button v-for="s in statuses" :key="s" class="sm" :class="{ primary: statusFilter === s }"
      @click="statusFilter = s; load()">{{ statusLabel[s] }}</button>
  </div>

  <div v-if="!tasks.length" class="card pad empty">Aucune tâche.</div>
  <table v-else class="card" style="overflow: hidden">
    <thead><tr><th>#</th><th>Titre</th><th>Agent</th><th>Statut</th><th>Tokens</th></tr></thead>
    <tbody>
      <tr v-for="t in tasks" :key="t.id" style="cursor: pointer" @click="router.push('/tasks/' + t.id)">
        <td>{{ t.id }}</td>
        <td>{{ t.title || t.description.slice(0, 70) }}</td>
        <td class="muted">{{ agents[t.agent_id] || '#' + t.agent_id }}</td>
        <td><StatusBadge :status="t.status" /></td>
        <td class="muted">{{ fmtTokens(t.input_tokens + t.output_tokens) }}</td>
      </tr>
    </tbody>
  </table>

  <TaskForm v-if="creating" @close="creating = false" @saved="onCreated" />
</template>
