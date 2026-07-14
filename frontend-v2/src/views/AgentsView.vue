<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'
import { fmtDate } from '../utils.js'
import AgentForm from '../components/AgentForm.vue'

const router = useRouter()
const agents = ref([])
const search = ref('')
const theme = ref('')   // filtre par thème (catégorie) ; '' = tous
const showForm = ref(false)
const viewMode = ref(localStorage.getItem('agentsView') || 'tile')  // tile | list
function setViewMode(m) { viewMode.value = m; localStorage.setItem('agentsView', m) }

async function load() { agents.value = await api.get('/api/agents') }
onMounted(load)

const themes = computed(() =>
  [...new Set(agents.value.map((a) => a.category).filter(Boolean))].sort())

const filtered = computed(() => {
  const q = search.value.toLowerCase()
  return agents.value
    .filter((a) =>
      (!theme.value || a.category === theme.value) &&
      (!q || a.name.toLowerCase().includes(q) || (a.description || '').toLowerCase().includes(q) ||
        (a.category || '').toLowerCase().includes(q)))
    // Tri par prochaine exécution : échéance la plus proche d'abord, sans session à la fin
    .sort((a, b) => {
      if (a.next_session_at && b.next_session_at) return a.next_session_at.localeCompare(b.next_session_at)
      if (a.next_session_at) return -1
      if (b.next_session_at) return 1
      return a.name.localeCompare(b.name)
    })
})

async function onSaved() { showForm.value = false; await load() }
</script>

<template>
  <div class="row spread">
    <h1>Agents</h1>
    <div class="row" style="gap: .4rem">
      <div class="row" style="gap: .2rem">
        <button class="sm" :class="{ primary: viewMode === 'tile' }" @click="setViewMode('tile')" title="Affichage tuiles">▦</button>
        <button class="sm" :class="{ primary: viewMode === 'list' }" @click="setViewMode('list')" title="Affichage liste">☰</button>
      </div>
      <button class="primary" @click="showForm = true">+ Nouvel agent</button>
    </div>
  </div>
  <input v-model="search" placeholder="Rechercher un agent…" style="max-width: 320px; margin-bottom: .6rem" />

  <!-- Filtres par thème (tags des agents) -->
  <div v-if="themes.length" class="row wrap" style="gap: .35rem; margin-bottom: 1rem">
    <button class="sm themebtn" :class="{ on: !theme }" @click="theme = ''">Tous</button>
    <button v-for="t in themes" :key="t" class="sm themebtn" :class="{ on: theme === t }" @click="theme = t">
      🏷 {{ t }}
    </button>
  </div>

  <div v-if="!filtered.length" class="card pad empty">Aucun agent{{ theme ? ' pour ce thème' : '' }}.</div>

  <!-- Mode liste compact -->
  <table v-if="viewMode === 'list' && filtered.length" class="card" style="overflow: hidden">
    <thead><tr><th>Agent</th><th>Thème</th><th>État</th><th>Activité</th><th>Prochaine session</th><th>Modèle</th></tr></thead>
    <tbody>
      <tr v-for="a in filtered" :key="a.id" style="cursor: pointer" @click="router.push('/agents/' + a.id)">
        <td><strong>{{ a.name }}</strong><div class="muted" style="font-size: .76rem; max-width: 360px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">{{ a.description || '—' }}</div></td>
        <td><span v-if="a.category" class="badge teal" style="font-size: .72rem">🏷 {{ a.category }}</span></td>
        <td>
          <span v-if="a.owner_user_id === null" class="badge violet">système</span>
          <span v-else-if="a.paused" class="badge amber">en pause</span>
          <span v-else-if="a.running_tasks" class="badge blue">{{ a.running_tasks }} en cours</span>
          <span v-else class="badge gray">inactif</span>
        </td>
        <td class="muted" style="font-size: .82rem">
          <span v-if="a.open_tasks">{{ a.open_tasks }} en attente</span><span v-else>—</span>
        </td>
        <td class="muted" style="font-size: .82rem">{{ a.next_session_at ? '⏱ ' + fmtDate(a.next_session_at) : (a.paused ? '⏸ pause' : '—') }}</td>
        <td class="muted" style="font-size: .82rem">{{ a.model }}</td>
      </tr>
    </tbody>
  </table>

  <!-- Mode tuile -->
  <div v-if="viewMode === 'tile'" class="grid" style="grid-template-columns: repeat(auto-fill, minmax(280px, 1fr))">
    <div v-for="a in filtered" :key="a.id" class="card pad" style="cursor: pointer" @click="router.push('/agents/' + a.id)">
      <div class="row spread">
        <strong>{{ a.name }}</strong>
        <span v-if="a.owner_user_id === null" class="badge violet">système</span>
        <span v-else-if="a.paused" class="badge amber">en pause</span>
      </div>
      <div class="muted" style="font-size: .85rem; margin: .3rem 0; min-height: 2.4em">{{ a.description || '—' }}</div>
      <div class="row wrap" style="gap: .4rem; font-size: .78rem">
        <span v-if="a.category" class="badge teal">🏷 {{ a.category }}</span>
        <span v-if="a.running_tasks" class="badge blue">{{ a.running_tasks }} en cours</span>
        <span v-if="a.open_tasks" class="badge gray">{{ a.open_tasks }} en attente</span>
        <span class="muted">{{ a.model }}</span>
      </div>
      <div class="muted" style="font-size: .76rem; margin-top: .3rem">
        {{ a.next_session_at ? '⏱ Prochaine session : ' + fmtDate(a.next_session_at) : (a.paused ? '⏸ En pause' : '— aucune session planifiée') }}
      </div>
    </div>
  </div>

  <AgentForm v-if="showForm" @close="showForm = false" @saved="onSaved" />
</template>
