<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'
import AgentForm from '../components/AgentForm.vue'

const router = useRouter()
const agents = ref([])
const search = ref('')
const theme = ref('')   // filtre par thème (catégorie) ; '' = tous
const showForm = ref(false)

async function load() { agents.value = await api.get('/api/agents') }
onMounted(load)

const themes = computed(() =>
  [...new Set(agents.value.map((a) => a.category).filter(Boolean))].sort())

const filtered = computed(() => {
  const q = search.value.toLowerCase()
  return agents.value.filter((a) =>
    (!theme.value || a.category === theme.value) &&
    (!q || a.name.toLowerCase().includes(q) || (a.description || '').toLowerCase().includes(q) ||
      (a.category || '').toLowerCase().includes(q)))
})

async function onSaved() { showForm.value = false; await load() }
</script>

<template>
  <div class="row spread">
    <h1>Agents</h1>
    <button class="primary" @click="showForm = true">+ Nouvel agent</button>
  </div>
  <input v-model="search" placeholder="Rechercher un agent…" style="max-width: 320px; margin-bottom: .6rem" />

  <!-- Filtres par thème (tags des agents) -->
  <div v-if="themes.length" class="row wrap" style="gap: .35rem; margin-bottom: 1rem">
    <button class="sm" :class="{ primary: !theme }" @click="theme = ''">Tous</button>
    <button v-for="t in themes" :key="t" class="sm" :class="{ primary: theme === t }" @click="theme = t">
      🏷 {{ t }}
    </button>
  </div>

  <div v-if="!filtered.length" class="card pad empty">Aucun agent{{ theme ? ' pour ce thème' : '' }}.</div>
  <div class="grid" style="grid-template-columns: repeat(auto-fill, minmax(280px, 1fr))">
    <div v-for="a in filtered" :key="a.id" class="card pad" style="cursor: pointer" @click="router.push('/agents/' + a.id)">
      <div class="row spread">
        <strong>{{ a.name }}</strong>
        <span v-if="a.owner_user_id === null" class="badge violet">système</span>
        <span v-else-if="a.paused" class="badge amber">en pause</span>
      </div>
      <div class="muted" style="font-size: .85rem; margin: .3rem 0; min-height: 2.4em">{{ a.description || '—' }}</div>
      <div class="row wrap" style="gap: .4rem; font-size: .78rem">
        <span v-if="a.category" class="badge gray">🏷 {{ a.category }}</span>
        <span v-if="a.running_tasks" class="badge blue">{{ a.running_tasks }} en cours</span>
        <span v-if="a.open_tasks" class="badge gray">{{ a.open_tasks }} en attente</span>
        <span class="muted">{{ a.model }}</span>
      </div>
    </div>
  </div>

  <AgentForm v-if="showForm" @close="showForm = false" @saved="onSaved" />
</template>
