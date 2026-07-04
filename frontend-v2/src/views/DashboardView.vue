<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { api, stream } from '../api.js'
import Markdown from '../components/Markdown.vue'

const router = useRouter()
const ov = ref({ agents: 0, open_tasks: 0, running_sessions: 0, planned_sessions: 0, running_missions: 0, open_notifications: 0 })
const questions = ref([])
const replies = ref({})
let stop = null

async function loadQuestions() {
  try { questions.value = await api.get('/api/notifications?status=open&type=question') } catch { /* */ }
}
async function answer(n) {
  const response = (replies.value[n.id] || '').trim()
  if (!response) return
  await api.post(`/api/notifications/${n.id}/answer`, { response })
  await loadQuestions()
}

const kpis = [
  { key: 'agents', label: 'Agents', icon: '🤖', to: '/agents' },
  { key: 'open_tasks', label: 'Tâches ouvertes', icon: '📋', to: '/tasks' },
  { key: 'running_sessions', label: 'Sessions en cours', icon: '▶️', to: '/tasks' },
  { key: 'planned_sessions', label: 'Sessions planifiées', icon: '⏱️', to: '/tasks' },
  { key: 'running_missions', label: 'Missions actives', icon: '🎯', to: '/missions' },
  { key: 'open_notifications', label: 'Notifications', icon: '🔔', to: '/' },
]

onMounted(async () => {
  try { ov.value = await api.get('/api/overview') } catch { /* */ }
  loadQuestions()
  // Flux temps réel des compteurs
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
  <div class="grid" style="grid-template-columns: repeat(auto-fit, minmax(160px, 1fr))">
    <div v-for="k in kpis" :key="k.key" class="card kpi" style="cursor: pointer" @click="router.push(k.to)">
      <div class="l">{{ k.icon }} {{ k.label }}</div>
      <div class="n">{{ ov[k.key] }}</div>
    </div>
  </div>

  <h2 style="margin-top: 1.5rem">❓ Questions en attente</h2>
  <div v-if="!questions.length" class="card pad empty">Aucune question en attente.</div>
  <div v-else class="stack">
    <div v-for="n in questions" :key="n.id" class="card pad">
      <div class="muted" style="font-size: .8rem">Tâche #{{ n.task_id }}</div>
      <Markdown :text="n.content" style="margin: .3rem 0" />
      <textarea v-model="replies[n.id]" placeholder="Ta réponse… (Ctrl+Entrée)" rows="2"
        @keydown.ctrl.enter="answer(n)"></textarea>
      <button class="primary sm" style="margin-top: .4rem" @click="answer(n)">Répondre</button>
    </div>
  </div>
</template>
