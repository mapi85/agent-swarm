<script setup>
import { ref, onMounted, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api.js'
import { fmtDate, fmtTokens } from '../utils.js'
import StatusBadge from '../components/StatusBadge.vue'
import Markdown from '../components/Markdown.vue'
import SessionStream from '../components/SessionStream.vue'
import StaticEvents from '../components/StaticEvents.vue'
import TaskResources from '../components/TaskResources.vue'
import ArtifactBrowser from '../components/ArtifactBrowser.vue'
import TaskForm from '../components/TaskForm.vue'
import Modal from '../components/Modal.vue'

const route = useRoute()
const router = useRouter()
const task = ref(null)
const sessions = ref([])
const selectedSession = ref(null)
const creatingFollowup = ref(false)
const relanceOpen = ref(false)
const relanceNote = ref('')
const redirectOpen = ref(false)
const redirectDesc = ref('')

async function load() {
  const id = route.params.id
  task.value = await api.get(`/api/tasks/${id}`)
  sessions.value = await api.get(`/api/sessions?task_id=${id}`)
  if (sessions.value.length) {
    const cur = selectedSession.value && sessions.value.find((s) => s.id === selectedSession.value.id)
    selectedSession.value = cur || sessions.value[0]
  }
}
onMounted(load)
// Naviguer vers une tâche liée reste sur ce composant : recharger quand l'id change.
watch(() => route.params.id, () => { selectedSession.value = null; chainOpen.value = false; load() })

const selectedRunning = computed(() => selectedSession.value?.status === 'running')
// Tâches récurrentes : des dizaines de sessions → boutons pour les plus récentes
// seulement, la liste complète dans un sélecteur.
const recentSessions = computed(() => sessions.value.slice(0, 8))
const manySessions = computed(() => sessions.value.length > 8)
const selectedIdx = computed(() => sessions.value.findIndex((s) => s.id === selectedSession.value?.id))
function selectPrev() { const i = selectedIdx.value; if (i < sessions.value.length - 1) selectedSession.value = sessions.value[i + 1] }
function selectNext() { const i = selectedIdx.value; if (i > 0) selectedSession.value = sessions.value[i - 1] }
function onPick(e) {
  const s = sessions.value.find((x) => x.id === Number(e.target.value))
  if (s) selectedSession.value = s
}
const STATUS_ICON = { planned: '🕐', running: '▶', completed: '✓', failed: '✗', interrupted: '⏸' }

// --- Chaîne des tâches liées (arborescence en modale) ---
const chainOpen = ref(false)
const chain = ref(null)   // {root, nodes, edges, truncated}
async function openChain() {
  chain.value = await api.get(`/api/tasks/${route.params.id}/chain`)
  chainOpen.value = true
}
// Niveaux chronologiques : profondeur = plus longue chaîne d'antécédents.
// Niveau 0 = origines ; la tâche courante et l'aval suivent naturellement.
const chainLevels = computed(() => {
  if (!chain.value) return []
  const { nodes, edges } = chain.value
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]))
  const preds = {}   // id -> antécédents
  for (const e of edges) (preds[e.to] ||= []).push(e.from)
  const depth = {}
  function d(id, guard = 0) {
    if (depth[id] != null) return depth[id]
    if (guard > 80) return 0
    const ps = (preds[id] || []).filter((p) => byId[p])
    return (depth[id] = ps.length ? 1 + Math.max(...ps.map((p) => d(p, guard + 1))) : 0)
  }
  nodes.forEach((n) => d(n.id))
  const levels = []
  for (const n of nodes) (levels[depth[n.id]] ||= []).push(n)
  return levels.map((l) => l.sort((a, b) => a.id - b.id)).filter(Boolean)
})
function edgeKinds(nodeId) {
  // libellé du lien qui mène à ce nœud (depends_on / follow_up)
  const kinds = (chain.value?.edges || []).filter((e) => e.to === nodeId).map((e) => e.kind)
  return kinds.includes('depends_on') ? 'dépend de' : (kinds.length ? 'fait suite à' : '')
}
function gotoTask(id) { chainOpen.value = false; router.push('/tasks/' + id) }
// Une tâche non terminée peut être relancée / réorientée manuellement.
const actionable = computed(() => task.value && !['done', 'cancelled'].includes(task.value.status))

async function runNow(s) { await api.post(`/api/sessions/${s.id}/run-now`, {}); await load() }
async function interrupt(s) { await api.post(`/api/sessions/${s.id}/interrupt`); await load() }
async function retry(s) { await api.post(`/api/sessions/${s.id}/retry`); await load() }
async function onFollowup() { creatingFollowup.value = false; await load() }

function openRelance() { relanceNote.value = ''; relanceOpen.value = true }
async function confirmRelance() {
  try {
    await api.post(`/api/tasks/${route.params.id}/relance`, { note: relanceNote.value || null })
  } catch (e) {
    // Agent en pause : la session ne partirait jamais. Proposer de le réactiver.
    if (!String(e.message).includes('en pause')) { alert(e.message); return }
    if (!confirm(e.message + '\n\nRéactiver l\'agent et lancer la session maintenant ?')) return
    try {
      await api.post(`/api/tasks/${route.params.id}/relance`, { note: relanceNote.value || null, resume_agent: true })
    } catch (e2) { alert(e2.message); return }
  }
  relanceOpen.value = false
  await load()
}
function openRedirect() { redirectDesc.value = task.value?.description || ''; redirectOpen.value = true }
async function confirmRedirect() {
  await api.patch(`/api/tasks/${route.params.id}`, { description: redirectDesc.value })
  redirectOpen.value = false
  await load()
}
async function abandon() {
  if (!confirm('Abandonner cette tâche sans suite ? Elle passera en « Annulée ».'))
    return
  await api.post(`/api/tasks/${route.params.id}/cancel`)
  await load()
}
</script>

<template>
  <div v-if="task">
    <div class="row"><router-link to="/tasks" class="muted">← Tâches</router-link></div>
    <div class="row spread">
      <h1 style="margin: .3rem 0">#{{ task.id }} {{ task.title }}</h1>
      <div class="row" style="gap: .4rem">
        <StatusBadge :status="task.status" />
        <button v-if="actionable" class="sm" @click="openRedirect">↻ Réorienter</button>
        <button v-if="actionable" class="sm primary" @click="openRelance">▶ Relancer…</button>
        <button v-if="actionable" class="sm danger" @click="abandon">Abandonner</button>
        <button class="sm" @click="creatingFollowup = true">+ Tâche de suite</button>
      </div>
    </div>

    <div class="grid" style="grid-template-columns: 2fr 1fr; align-items: start">
      <div class="stack">
        <div class="card pad">
          <h3>Description</h3>
          <Markdown :text="task.description" />
          <template v-if="task.result">
            <h3 style="margin-top: 1rem">Résultat</h3>
            <Markdown :text="task.result" />
          </template>
        </div>

        <div class="card pad">
          <div class="row spread" style="align-items: center; margin-bottom: .6rem">
            <h3 style="margin: 0">Sessions <span class="muted" style="font-size: .8rem; font-weight: 400">({{ sessions.length }})</span></h3>
            <!-- Navigation compacte pour les tâches récurrentes (nombreuses sessions) -->
            <div v-if="manySessions && selectedSession" class="row" style="gap: .3rem; align-items: center">
              <button class="ghost sm" :disabled="selectedIdx >= sessions.length - 1" @click="selectPrev" title="Session précédente">←</button>
              <select :value="selectedSession.id" @change="onPick" style="width: auto; font-size: .82rem; padding: .25rem .4rem">
                <option v-for="s in sessions" :key="s.id" :value="s.id">
                  n°{{ s.number }} {{ STATUS_ICON[s.status] || '' }} — {{ fmtDate(s.started_at || s.scheduled_at) }}
                </option>
              </select>
              <button class="ghost sm" :disabled="selectedIdx <= 0" @click="selectNext" title="Session suivante">→</button>
            </div>
          </div>
          <div v-if="!sessions.length" class="empty">Aucune session pour l'instant.</div>
          <div v-else class="row wrap" style="margin-bottom: .8rem">
            <button v-for="s in recentSessions" :key="s.id" class="sm"
              :class="{ primary: selectedSession?.id === s.id }" @click="selectedSession = s">
              n°{{ s.number }} {{ STATUS_ICON[s.status] || s.status }}
            </button>
            <span v-if="manySessions" class="muted" style="font-size: .78rem; align-self: center">
              … {{ sessions.length - recentSessions.length }} plus anciennes via la liste ↑
            </span>
          </div>
          <template v-if="selectedSession">
            <div class="row spread" style="margin-bottom: .5rem">
              <div class="muted" style="font-size: .82rem">
                {{ fmtDate(selectedSession.started_at || selectedSession.scheduled_at) }} ·
                {{ fmtTokens(selectedSession.input_tokens + selectedSession.output_tokens) }} tokens
              </div>
              <div class="row">
                <button v-if="selectedSession.status === 'planned'" class="sm primary" @click="runNow(selectedSession)">▶ Lancer</button>
                <button v-if="selectedRunning" class="sm danger" @click="interrupt(selectedSession)">Stop</button>
                <button v-if="['failed','interrupted'].includes(selectedSession.status)" class="sm" @click="retry(selectedSession)">↺ Relancer</button>
              </div>
            </div>
            <SessionStream v-if="selectedRunning" :session-id="selectedSession.id" :key="'live'+selectedSession.id" />
            <div v-else>
              <StaticEvents :session-id="selectedSession.id" :key="'st'+selectedSession.id" />
              <template v-if="selectedSession.report">
                <h4 style="margin-top: .8rem">📋 Rapport</h4>
                <Markdown :text="selectedSession.report" />
              </template>
            </div>
          </template>
        </div>
      </div>

      <div class="stack">
        <div class="card pad">
          <div class="row spread" style="align-items: center">
            <h3 style="margin: 0">Tâches liées</h3>
            <button v-if="task.antecedents.length || task.dependents.length" class="ghost sm"
              @click="openChain" title="Voir toute la chaîne des tâches liées">🔗 Chaîne</button>
          </div>
          <div v-if="task.antecedents.length">
            <div class="muted" style="font-size: .8rem">Amont (ressources héritées)</div>
            <div v-for="a in task.antecedents" :key="'a'+a.task_id" class="navlink" @click="router.push('/tasks/' + a.task_id)">
              ↑ #{{ a.task_id }} {{ a.title }} <StatusBadge :status="a.status" />
            </div>
          </div>
          <div v-if="task.dependents.length" style="margin-top: .5rem">
            <div class="muted" style="font-size: .8rem">Aval</div>
            <div v-for="d in task.dependents" :key="'d'+d.task_id" class="navlink" @click="router.push('/tasks/' + d.task_id)">
              ↓ #{{ d.task_id }} {{ d.title }} <StatusBadge :status="d.status" />
            </div>
          </div>
          <div v-if="!task.antecedents.length && !task.dependents.length" class="muted">Aucun lien.</div>
        </div>
        <TaskResources :task-id="task.id" :key="task.id" />
        <ArtifactBrowser :base="'/api/tasks/' + task.id" :key="'a' + task.id" title="Artefacts de la tâche" />
      </div>
    </div>

    <TaskForm v-if="creatingFollowup" :agent-id="task.agent_id" :link-task-id="task.id"
      @close="creatingFollowup = false" @saved="onFollowup" />

    <!-- Chaîne des tâches liées : frise chronologique (origines → courante → à venir) -->
    <Modal v-if="chainOpen && chain" :title="`Chaîne de la tâche #${chain.root}`" wide @close="chainOpen = false">
      <p class="muted" style="font-size: .82rem; margin-top: 0">
        De l'origine (en haut) aux prochaines tâches (en bas). Clique une tâche pour l'ouvrir.
        <span v-if="chain.truncated"> ⚠ Chaîne longue : affichage tronqué.</span>
      </p>
      <div v-for="(level, i) in chainLevels" :key="i">
        <div v-if="i > 0" class="muted" style="text-align: center; font-size: .8rem; line-height: 1">↓</div>
        <div class="row wrap" style="gap: .4rem; justify-content: center; margin: .25rem 0">
          <div v-for="n in level" :key="n.id" class="card pad" @click="gotoTask(n.id)"
            style="padding: .45rem .6rem; cursor: pointer; max-width: 300px"
            :style="n.id === chain.root ? 'border: 2px solid var(--primary); box-shadow: 0 0 0 3px var(--primary-weak)' : ''">
            <div class="row" style="gap: .35rem; align-items: center">
              <strong style="font-size: .85rem">#{{ n.id }}</strong>
              <StatusBadge :status="n.status" />
              <span v-if="n.id === chain.root" class="badge blue" style="font-size: .68rem">courante</span>
            </div>
            <div style="font-size: .82rem; margin-top: .15rem; overflow: hidden; text-overflow: ellipsis;
              display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical">{{ n.title }}</div>
            <div class="muted" style="font-size: .72rem; margin-top: .15rem">
              {{ n.agent_name }}
              <template v-if="edgeKinds(n.id)"> · {{ edgeKinds(n.id) }}</template>
              <template v-if="n.next_session_at"> · ⏱ {{ fmtDate(n.next_session_at) }}</template>
            </div>
          </div>
        </div>
      </div>
    </Modal>

    <Modal v-if="relanceOpen" :title="`Relancer la tâche #${task.id}`" @close="relanceOpen = false">
      <p class="muted" style="font-size: .85rem">
        Une nouvelle session est créée immédiatement. Ton commentaire guidera l'agent pour les prochaines exécutions.
      </p>
      <textarea v-model="relanceNote" rows="4" placeholder="Ex. : reprendre la surveillance ; vérifier X d'abord…"></textarea>
      <div class="row" style="justify-content: flex-end; margin-top: 1rem; gap: .4rem">
        <button class="ghost" @click="relanceOpen = false">Annuler</button>
        <button class="primary" @click="confirmRelance">Lancer la session</button>
      </div>
    </Modal>

    <Modal v-if="redirectOpen" :title="`Réorienter la tâche #${task.id}`" @close="redirectOpen = false">
      <p class="muted" style="font-size: .85rem">
        Modifie la spécification de la tâche. L'agent prendra en compte la nouvelle description à sa prochaine session.
      </p>
      <label>Description / objectif de la tâche</label>
      <textarea v-model="redirectDesc" rows="5"></textarea>
      <div class="row" style="justify-content: flex-end; margin-top: 1rem; gap: .4rem">
        <button class="ghost" @click="redirectOpen = false">Annuler</button>
        <button class="primary" @click="confirmRedirect">Enregistrer</button>
      </div>
    </Modal>
  </div>
</template>
