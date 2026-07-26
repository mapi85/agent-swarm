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
const includeArchived = ref(true)   // les missions validées sont archivées : montrées par défaut
const expanded = ref({})   // mission_id -> bool
const progress = ref({})   // mission_id -> {progress, tasks}
const deployResult = ref({})  // mission_id -> résultat de matérialisation (agents créés, guide)

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
  try {
    const res = await api.post(`/api/missions/${m.id}/approve`)
    deployResult.value[m.id] = res  // {mode, created_agents, coordination, activation_guide, errors}
    await load()
  } catch (e) { alert(e.message) }
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
    <textarea v-model="newMission" rows="3" placeholder="Décris ton objectif ou la finalité des agents à programmer ; le superviseur proposera un plan de déploiement d'agents…"></textarea>
    <div class="row" style="margin-top: .5rem">
      <button class="primary" :disabled="creating || !newMission.trim()" @click="propose">
        {{ creating ? 'Le superviseur conçoit le dispositif…' : 'Proposer un plan' }}
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

      <!-- Plan de déploiement : agents à créer -->
      <div v-if="m.plan && m.plan.agents && m.plan.agents.length" style="margin: .6rem 0">
        <div class="muted" style="font-size: .8rem; font-weight: 600; margin-bottom: .3rem">
          Plan de déploiement — {{ m.plan.agents.length }} agent(s) à créer
        </div>
        <div v-for="(a, i) in m.plan.agents" :key="i" class="card pad" style="padding: .5rem .7rem; margin: .2rem 0">
          <div class="row wrap" style="gap: .4rem; align-items: baseline">
            <strong>{{ a.name }}</strong>
            <span v-if="a.category" class="badge teal" style="font-size: .7rem">🏷 {{ a.category }}</span>
            <span class="badge gray" style="font-size: .7rem">effort {{ a.effort }}</span>
            <span class="badge gray" style="font-size: .7rem">
              {{ a.heartbeat_minutes > 0 ? '⏱ toutes les ' + a.heartbeat_minutes + ' min' : '⚡ événementiel' }}
            </span>
          </div>
          <div class="muted" style="font-size: .84rem; margin-top: .2rem">{{ a.role }}</div>
          <div v-if="a.trigger" class="muted" style="font-size: .78rem; margin-top: .2rem">
            <strong>Déclenchement :</strong> {{ a.trigger }}
          </div>
        </div>
        <div v-if="m.plan.coordination" class="muted" style="font-size: .82rem; margin-top: .4rem">
          <strong>Coordination :</strong> {{ m.plan.coordination }}
        </div>
      </div>
      <!-- Plan one-shot : étapes réalisées par le superviseur lui-même -->
      <div v-else-if="m.plan && m.plan.one_shot && m.plan.steps && m.plan.steps.length" style="margin: .6rem 0">
        <div class="muted" style="font-size: .8rem; font-weight: 600; margin-bottom: .3rem">
          Réalisation directe (one-shot, aucun agent permanent nécessaire)
        </div>
        <div v-for="(s, i) in m.plan.steps" :key="i" class="card pad" style="padding: .5rem .7rem; margin: .2rem 0">
          <strong>Étape {{ i + 1 }} — {{ s.title }}</strong>
          <Markdown :text="s.description" style="font-size: .85rem" />
        </div>
      </div>

      <!-- Récapitulatif de déploiement après validation -->
      <div v-if="deployResult[m.id] && deployResult[m.id].mode === 'agents'" class="card pad"
        style="margin: .6rem 0; border-left: 3px solid var(--green)">
        <strong style="font-size: .82rem; color: var(--green)">✅ {{ deployResult[m.id].created_agents.length }} agent(s) créé(s) — en pause</strong>
        <div v-for="a in deployResult[m.id].created_agents" :key="a.id" class="navlink row spread"
          style="padding: .25rem .4rem" @click="router.push('/agents/' + a.id)">
          <span>{{ a.name }}</span>
          <span class="muted" style="font-size: .76rem">→ voir l'agent</span>
        </div>
        <div v-if="deployResult[m.id].activation_guide" style="font-size: .84rem; margin-top: .4rem; white-space: pre-wrap">
          <strong style="font-size: .78rem; color: var(--muted)">COMMENT LANCER LA MISSION</strong>
          <div class="muted" style="margin-top: .2rem">{{ deployResult[m.id].activation_guide }}</div>
        </div>
        <div v-if="deployResult[m.id].errors && deployResult[m.id].errors.length" class="badge amber"
          style="display: block; padding: .4rem; margin-top: .4rem; font-size: .78rem; white-space: pre-wrap">{{ deployResult[m.id].errors.join('\n') }}</div>
      </div>

      <div class="row wrap">
        <template v-if="m.status === 'proposed'">
          <button class="primary sm" @click="approve(m)">
            {{ m.plan && m.plan.one_shot ? 'Valider et lancer (one-shot)' : 'Valider et créer les agents' }}
          </button>
          <button class="sm" @click="replan(m)">Régénérer</button>
          <button class="danger sm" @click="remove(m)">Supprimer</button>
        </template>
        <template v-else>
          <!-- Le suivi n'a de sens que pour une mission one-shot (tâche du superviseur) -->
          <button v-if="m.status === 'running'" class="sm" @click="toggleProgress(m)">{{ expanded[m.id] ? 'Masquer le suivi' : 'Voir l\'avancement' }}</button>
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
