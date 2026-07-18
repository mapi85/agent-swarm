<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'
import { useAuth } from '../store.js'
import Modal from './Modal.vue'

const props = defineProps({ agent: Object }) // null = création
const emit = defineEmits(['close', 'saved'])
const auth = useAuth()

const providers = ref([])
const models = ref([])
const form = ref({
  name: '', description: '', mission_prompt: '', category: '',
  provider_id: null, model: '', effort: 'high', max_iterations: 60,
  session_token_budget: 0, max_parallel_tasks: 1, heartbeat_minutes: 0, system: false,
})
const error = ref('')
const busy = ref(false)

function onProvider() {
  const p = providers.value.find((x) => x.id === form.value.provider_id)
  models.value = p ? p.models : []
  // Changement de provider : on repasse en mode défaut (modèle vide = suit le
  // default_model du provider) sauf si le modèle saisi existe chez le nouveau provider.
  if (p && form.value.model && !models.value.includes(form.value.model)) form.value.model = ''
}

onMounted(async () => {
  providers.value = await api.get('/api/providers')
  if (props.agent) {
    Object.assign(form.value, {
      name: props.agent.name, description: props.agent.description,
      mission_prompt: props.agent.mission_prompt, category: props.agent.category,
      provider_id: props.agent.provider_id, model: props.agent.model, effort: props.agent.effort,
      max_iterations: props.agent.max_iterations, session_token_budget: props.agent.session_token_budget,
      max_parallel_tasks: props.agent.max_parallel_tasks, heartbeat_minutes: props.agent.heartbeat_minutes,
      system: props.agent.owner_user_id === null,
    })
  }
  onProvider()
})

async function save() {
  error.value = ''; busy.value = true
  try {
    if (props.agent) {
      const patch = { ...form.value }
      delete patch.name; delete patch.system
      await api.patch(`/api/agents/${props.agent.id}`, patch)
    } else {
      await api.post('/api/agents', form.value)
    }
    emit('saved')
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
</script>

<template>
  <Modal :title="agent ? 'Modifier l\'agent' : 'Nouvel agent'" wide @close="emit('close')">
    <div class="grid" style="grid-template-columns: 1fr 1fr">
      <div>
        <label>Nom</label>
        <input v-model="form.name" :disabled="!!agent" />
      </div>
      <div>
        <label>Thème / catégorie</label>
        <input v-model="form.category" />
      </div>
    </div>
    <label>Description (visible des autres agents)</label>
    <input v-model="form.description" />
    <label>Mission permanente (prompt système)</label>
    <textarea v-model="form.mission_prompt" rows="4"></textarea>

    <div class="grid" style="grid-template-columns: 1fr 1fr">
      <div>
        <label>Provider</label>
        <select v-model="form.provider_id" @change="onProvider">
          <option :value="null">Provider par défaut</option>
          <option v-for="p in providers" :key="p.id" :value="p.id">{{ p.name }}</option>
        </select>
      </div>
      <div>
        <label>Modèle</label>
        <input v-model="form.model" list="modellist" placeholder="(vide = suivre le défaut du provider)" />
        <datalist id="modellist"><option v-for="m in models" :key="m" :value="m" /></datalist>
        <div class="muted" style="font-size: .74rem; margin-top: .2rem">
          Laisser vide pour suivre le paramétrage par défaut : changer le provider/modèle par défaut
          s'appliquera d'un coup à tous les agents en mode défaut.
        </div>
      </div>
    </div>

    <div class="grid" style="grid-template-columns: 1fr 1fr 1fr 1fr">
      <div>
        <label>Effort</label>
        <select v-model="form.effort">
          <option>low</option><option>medium</option><option>high</option><option>max</option>
        </select>
      </div>
      <div>
        <label>Itérations max</label>
        <input v-model.number="form.max_iterations" type="number" min="5" max="500" />
      </div>
      <div>
        <label>Budget tokens/session</label>
        <input v-model.number="form.session_token_budget" type="number" min="0" />
      </div>
      <div>
        <label>Tâches en parallèle</label>
        <input v-model.number="form.max_parallel_tasks" type="number" min="1" max="10" />
      </div>
    </div>

    <div>
      <label>Cadence de veille (minutes, 0 = aucune)</label>
      <input v-model.number="form.heartbeat_minutes" type="number" min="0" max="10080" />
      <div class="muted" style="font-size: .78rem; margin-top: .2rem">
        Pour les agents récurrents / événementiels : garantit une session au moins toutes les N minutes
        quand l'agent est inactif, même sans tâche en attente. Laisser à 0 pour les agents ponctuels.
      </div>
    </div>

    <label v-if="auth.isAdmin && !agent" class="row" style="margin-top: .8rem; gap: .5rem">
      <input type="checkbox" v-model="form.system" style="width: auto" />
      <span>Agent système (visible et utilisable par tous les profils)</span>
    </label>

    <p v-if="error" class="badge red" style="display: block; padding: .5rem; margin-top: .6rem">{{ error }}</p>
    <div class="row" style="justify-content: flex-end; margin-top: 1rem">
      <button class="ghost" @click="emit('close')">Annuler</button>
      <button class="primary" :disabled="busy" @click="save">Enregistrer</button>
    </div>
  </Modal>
</template>
