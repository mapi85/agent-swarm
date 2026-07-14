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

// Suivi d'avancement : tâches réelles + sessions du superviseur (exécution solo).
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
function missionTaskId(m) { return progress.value[m.id]?.tasks?.[0]?.id }

// (L'ancien regroupement par vagues de dépendances est supprimé : le superviseur
// exécute désormais la mission lui-même, en suivant une simple feuille de route.)
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

      <div v-if="m.plan && m.plan.steps && m.plan.steps.length" style="margin: .6rem 0">
        <div class="muted" style="font-size: .8rem; font-weight: 600; margin-bottom: .3rem">
          Feuille de route (réalisée par le superviseur lui-même, sans délégation)
        </div>
        <div v-for="(s, i) in m.plan.steps" :key="i" class="card pad" style="padding: .5rem .7rem; margin: .2rem 0">
          <strong>Étape {{ i + 1 }} — {{ s.title }}</strong>
          <Markdown :text="s.description" style="font-size: .85rem" />
        </div>
      </div>
      <div v-else-if="m.plan && m.plan.tasks && m.plan.tasks.length" class="muted" style="font-size: .8rem; margin: .6rem 0">
        Plan ancien format (mission déléguée) — {{ m.plan.tasks.length }} tâches.
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
          Aucune tâche rattachée (la mission n'a pas encore été materialisée).
        </div>

        <!-- Activité : sessions du superviseur (le vrai suivi d'une mission solo) -->
        <div v-if="progress[m.id].sessions && progress[m.id].sessions.length" style="margin-top: .7rem">
          <div class="muted" style="font-size: .8rem; font-weight: 600; margin-bottom: .3rem">
            Activité du superviseur — {{ progress[m.id].progress.sessions }} session(s)
          </div>
          <div v-for="s in progress[m.id].sessions" :key="s.id" class="navlink row spread"
            style="padding: .3rem .4rem; align-items: flex-start"
            @click="missionTaskId(m) && router.push('/tasks/' + missionTaskId(m))">
            <div class="row" style="gap: .4rem; align-items: center; flex: 1; min-width: 0">
              <StatusBadge :status="s.status" kind="session" />
              <span class="muted" style="font-size: .76rem">n°{{ s.number }}</span>
              <span style="font-size: .82rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">{{ s.objective || '(sans objectif)' }}</span>
            </div>
          </div>
          <div v-if="progress[m.id].sessions[0] && progress[m.id].sessions[0].report" class="card pad"
            style="font-size: .8rem; margin-top: .4rem; max-height: 14em; overflow: auto">
            <strong style="font-size: .76rem; color: var(--muted)">DERNIER RAPPORT</strong>
            <div class="muted" style="white-space: pre-wrap; margin-top: .2rem">{{ progress[m.id].sessions[0].report }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
