<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api.js'
import { useAuth } from '../store.js'
import { fmtTokens } from '../utils.js'
import StatusBadge from '../components/StatusBadge.vue'
import AgentForm from '../components/AgentForm.vue'
import TaskForm from '../components/TaskForm.vue'

const route = useRoute()
const router = useRouter()
const auth = useAuth()
const agent = ref(null)
const tasks = ref([])
const editing = ref(false)
const creatingTask = ref(false)

const canManage = computed(() =>
  agent.value && (auth.isAdmin || agent.value.owner_user_id === auth.user.id))

async function load() {
  agent.value = await api.get(`/api/agents/${route.params.id}`)
  tasks.value = await api.get(`/api/tasks?agent_id=${route.params.id}`)
}
onMounted(load)

async function togglePause() {
  await api.post(`/api/agents/${agent.value.id}/${agent.value.paused ? 'resume' : 'pause'}`)
  await load()
}
async function remove() {
  if (!confirm('Supprimer cet agent ? (impossible s\'il a un historique de tâches)')) return
  try { await api.del(`/api/agents/${agent.value.id}`); router.push('/agents') }
  catch (e) { alert(e.message) }
}
async function onSaved() { editing.value = false; await load() }
async function onTaskCreated() { creatingTask.value = false; await load() }
</script>

<template>
  <div v-if="agent">
    <div class="row" style="margin-bottom: .3rem">
      <router-link to="/agents" class="muted">← Agents</router-link>
    </div>
    <div class="row spread">
      <h1 style="margin: 0">{{ agent.name }}
        <span v-if="agent.owner_user_id === null" class="badge violet">système</span>
        <span v-else-if="agent.paused" class="badge amber">en pause</span>
      </h1>
      <div class="row" v-if="canManage">
        <button class="sm" @click="creatingTask = true">+ Tâche</button>
        <button class="sm" @click="editing = true">✎ Modifier</button>
        <button class="sm" @click="togglePause">{{ agent.paused ? 'Reprendre' : 'Mettre en pause' }}</button>
        <button class="sm danger" @click="remove">🗑</button>
      </div>
    </div>

    <div class="card pad" style="margin: 1rem 0">
      <div class="row wrap" style="gap: 1rem; font-size: .85rem">
        <span v-if="agent.category" class="badge gray">🏷 {{ agent.category }}</span>
        <span class="muted">Modèle : <b>{{ agent.model }}</b></span>
        <span class="muted">Effort : {{ agent.effort }}</span>
        <span class="muted">Parallèle : {{ agent.max_parallel_tasks }}</span>
        <span class="muted">Budget/session : {{ agent.session_token_budget || '∞' }}</span>
      </div>
      <h3 style="margin-top: 1rem">Mission permanente</h3>
      <div class="muted" style="white-space: pre-wrap; font-size: .9rem">{{ agent.mission_prompt }}</div>
    </div>

    <h2>Tâches de cet agent</h2>
    <div v-if="!tasks.length" class="card pad empty">Aucune tâche.</div>
    <table v-else class="card" style="overflow: hidden">
      <thead><tr><th>#</th><th>Titre</th><th>Statut</th><th>Tokens</th></tr></thead>
      <tbody>
        <tr v-for="t in tasks" :key="t.id" style="cursor: pointer" @click="router.push('/tasks/' + t.id)">
          <td>{{ t.id }}</td>
          <td>{{ t.title || t.description.slice(0, 60) }}</td>
          <td><StatusBadge :status="t.status" /></td>
          <td class="muted">{{ fmtTokens(t.input_tokens + t.output_tokens) }}</td>
        </tr>
      </tbody>
    </table>

    <AgentForm v-if="editing" :agent="agent" @close="editing = false" @saved="onSaved" />
    <TaskForm v-if="creatingTask" :agent-id="agent.id" @close="creatingTask = false" @saved="onTaskCreated" />
  </div>
</template>
