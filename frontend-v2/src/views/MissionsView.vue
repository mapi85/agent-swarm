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
const expanded = ref({})   // mission_id -> bool
const progress = ref({})   // mission_id -> {progress, tasks}

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

// Suivi d'avancement : tâches réelles (plan + sous-tâches déléguées) + progression.
async function toggleProgress(m) {
  if (expanded.value[m.id]) { expanded.value[m.id] = false; return }
  if (!progress.value[m.id]) {
    try { progress.value[m.id] = await api.get(`/api/missions/${m.id}/tasks`) }
    catch (e) { alert(e.message); return }
  }
  expanded.value[m.id] = true
}
function pct(m) {
  const p = progress.value[m.id]?.progress
  return p && p.total ? Math.round((p.done / p.total) * 100) : 0
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
          <button class="sm" @click="toggleProgress(m)">{{ expanded[m.id] ? 'Masquer le suivi' : 'Voir l\'avancement' }}</button>
          <button v-if="m.status !== 'archived'" class="sm" @click="archive(m)">Archiver</button>
          <button class="danger sm" @click="remove(m)">Supprimer</button>
        </template>
      </div>

      <!-- Suivi d'avancement : tâches réelles et leur statut -->
      <div v-if="expanded[m.id] && progress[m.id]" style="margin-top: .8rem">
        <div class="row spread" style="align-items: center; margin-bottom: .4rem">
          <div class="muted" style="font-size: .82rem">
            {{ progress[m.id].progress.done }}/{{ progress[m.id].progress.total }} terminées
            · {{ progress[m.id].progress.in_progress }} en cours
            <span v-if="progress[m.id].progress.waiting"> · {{ progress[m.id].progress.waiting }} en attente</span>
            <span v-if="progress[m.id].progress.failed"> · {{ progress[m.id].progress.failed }} échec</span>
          </div>
          <strong style="font-size: .85rem">{{ pct(m) }}%</strong>
        </div>
        <div style="height: 8px; background: var(--primary-weak); border-radius: 4px; overflow: hidden; margin-bottom: .5rem">
          <div :style="{ width: pct(m) + '%' }" style="height: 100%; background: var(--primary)"></div>
        </div>
        <div v-for="t in progress[m.id].tasks" :key="t.id" class="navlink row spread"
          style="padding: .3rem .4rem" @click="router.push('/tasks/' + t.id)">
          <div class="row" style="gap: .4rem; align-items: center">
            <StatusBadge :status="t.status" />
            <span>#{{ t.id }} {{ t.title }}</span>
          </div>
          <span class="muted" style="font-size: .78rem">{{ t.agent_name }}</span>
        </div>
        <div v-if="!progress[m.id].tasks.length" class="muted" style="font-size: .82rem">
          Aucune tâche rattachée (la mission n'a pas encore été materialisée ou n'a pas de sous-tâches).
        </div>
      </div>
    </div>
  </div>
</template>
