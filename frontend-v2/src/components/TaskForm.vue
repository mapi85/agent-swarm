<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'
import Modal from './Modal.vue'

const props = defineProps({ agentId: Number, linkTaskId: Number })
const emit = defineEmits(['close', 'saved'])

const agents = ref([])
const form = ref({ agent_id: props.agentId || null, title: '', description: '', links: [] })
const error = ref('')
const busy = ref(false)

onMounted(async () => {
  agents.value = await api.get('/api/agents')
  if (!form.value.agent_id && agents.value.length) form.value.agent_id = agents.value[0].id
  if (props.linkTaskId) form.value.links = [{ task_id: props.linkTaskId, kind: 'follow_up' }]
})

async function save() {
  error.value = ''; busy.value = true
  try {
    await api.post('/api/tasks', form.value)
    emit('saved')
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
</script>

<template>
  <Modal title="Nouvelle tâche" @close="emit('close')">
    <label>Agent</label>
    <select v-model="form.agent_id">
      <option v-for="a in agents" :key="a.id" :value="a.id">{{ a.name }}</option>
    </select>
    <label>Titre</label>
    <input v-model="form.title" placeholder="Titre court" />
    <label>Description (complète et autonome)</label>
    <textarea v-model="form.description" rows="5" placeholder="Contexte, attendu, critères de réussite…"></textarea>
    <p v-if="linkTaskId" class="muted" style="font-size: .82rem">↳ Liée à la tâche #{{ linkTaskId }} (porosité : accès à ses ressources et artefacts).</p>
    <p v-if="error" class="badge red" style="display: block; padding: .5rem; margin-top: .6rem">{{ error }}</p>
    <div class="row" style="justify-content: flex-end; margin-top: 1rem">
      <button class="ghost" @click="emit('close')">Annuler</button>
      <button class="primary" :disabled="busy || !form.description" @click="save">Créer la tâche</button>
    </div>
  </Modal>
</template>
