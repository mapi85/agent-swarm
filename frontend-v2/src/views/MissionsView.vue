<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'
import { fmtTokens } from '../utils.js'
import StatusBadge from '../components/StatusBadge.vue'
import Markdown from '../components/Markdown.vue'

const router = useRouter()
const missions = ref([])
const newMission = ref('')
const creating = ref(false)
const error = ref('')
const includeArchived = ref(false)

async function load() {
  missions.value = await api.get('/api/missions?include_archived=' + includeArchived.value)
}
onMounted(load)

async function propose() {
  error.value = ''; creating.value = true
  try {
    await api.post('/api/missions', { mission: newMission.value })
    newMission.value = ''
    await load()
  } catch (e) { error.value = e.message } finally { creating.value = false }
}
async function approve(m) {
  try { await api.post(`/api/missions/${m.id}/approve`); await load() }
  catch (e) { alert(e.message) }
}
async function replan(m) { await api.post(`/api/missions/${m.id}/replan`); await load() }
async function archive(m) { await api.post(`/api/missions/${m.id}/archive`); await load() }
async function remove(m) {
  if (!confirm('Supprimer cette mission ?')) return
  try { await api.del(`/api/missions/${m.id}`); await load() } catch (e) { alert(e.message) }
}

// Regroupe les tâches d'un plan par vagues de dépendances (pour l'affichage).
function waves(plan) {
  if (!plan || !plan.tasks) return []
  const byRef = Object.fromEntries(plan.tasks.map((t) => [t.ref, t]))
  const level = {}
  function lvl(t) {
    if (level[t.ref] != null) return level[t.ref]
    const deps = (t.depends_on || []).map((r) => byRef[r]).filter(Boolean)
    return (level[t.ref] = deps.length ? 1 + Math.max(...deps.map(lvl)) : 0)
  }
  plan.tasks.forEach(lvl)
  const out = []
  plan.tasks.forEach((t) => { (out[level[t.ref]] ||= []).push(t) })
  return out
}
</script>

<template>
  <h1>Missions</h1>

  <div class="card pad" style="margin-bottom: 1rem">
    <h3>Nouvelle mission</h3>
    <textarea v-model="newMission" rows="3" placeholder="Décris le besoin ; le superviseur proposera un plan de tâches…"></textarea>
    <div class="row" style="margin-top: .5rem">
      <button class="primary" :disabled="creating || !newMission.trim()" @click="propose">
        {{ creating ? 'Le superviseur réfléchit…' : 'Proposer un plan' }}
      </button>
      <span v-if="error" class="badge red" style="padding: .4rem">{{ error }}</span>
    </div>
  </div>

  <label class="row" style="gap: .4rem; width: auto; margin-bottom: .8rem">
    <input type="checkbox" v-model="includeArchived" @change="load" style="width: auto" /> Inclure les archivées
  </label>

  <div v-if="!missions.length" class="card pad empty">Aucune mission.</div>
  <div class="stack">
    <div v-for="m in missions" :key="m.id" class="card pad">
      <div class="row spread">
        <strong>{{ m.title }}</strong>
        <div class="row">
          <StatusBadge :status="m.status" kind="mission" />
          <span class="muted" style="font-size: .8rem">{{ fmtTokens(m.input_tokens + m.output_tokens) }} tok</span>
        </div>
      </div>
      <p class="muted" style="margin: .4rem 0">{{ m.summary }}</p>

      <div v-if="m.plan" style="margin: .6rem 0">
        <div v-if="m.plan.new_agents && m.plan.new_agents.length" style="margin-bottom: .5rem">
          <div class="muted" style="font-size: .8rem; font-weight: 600">Nouveaux agents à créer</div>
          <span v-for="na in m.plan.new_agents" :key="na.name" class="badge blue" style="margin-right: .3rem">+ {{ na.name }}</span>
        </div>
        <div v-for="(wave, i) in waves(m.plan)" :key="i" style="margin-bottom: .4rem">
          <div class="muted" style="font-size: .78rem">Étape {{ i + 1 }} · {{ wave.length }} tâche(s)</div>
          <div v-for="t in wave" :key="t.ref" class="card pad" style="padding: .5rem .7rem; margin: .2rem 0">
            <strong>{{ t.title || t.ref }}</strong> → <span class="muted">{{ t.agent }}</span>
            <Markdown :text="t.description" style="font-size: .85rem" />
          </div>
        </div>
      </div>

      <div class="row wrap">
        <template v-if="m.status === 'proposed'">
          <button class="primary sm" @click="approve(m)">Valider et lancer</button>
          <button class="sm" @click="replan(m)">Régénérer</button>
          <button class="danger sm" @click="remove(m)">Supprimer</button>
        </template>
        <template v-else>
          <button v-if="m.status !== 'archived'" class="sm" @click="archive(m)">Archiver</button>
          <button class="danger sm" @click="remove(m)">Supprimer</button>
        </template>
      </div>
    </div>
  </div>
</template>
