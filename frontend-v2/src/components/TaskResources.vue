<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'
import { useAuth } from '../store.js'
import UniversalPreview from './UniversalPreview.vue'

const props = defineProps({ taskId: Number })
const auth = useAuth()
const resources = ref([])
const preview = ref(null)
const fileInput = ref(null)

async function load() {
  try { resources.value = await api.get(`/api/resources?task_id=${props.taskId}`) } catch { resources.value = [] }
}
onMounted(load)

async function upload(e) {
  const file = e.target.files[0]
  if (!file) return
  const fd = new FormData()
  fd.append('file', file); fd.append('scope', 'task'); fd.append('task_id', props.taskId)
  await fetch('/api/resources/upload', {
    method: 'POST',
    headers: auth.token ? { Authorization: `Bearer ${auth.token}` } : {},
    body: fd,
  })
  e.target.value = ''
  await load()
}

const icon = (k) => (k === 'file' ? '📄' : k === 'link' ? '🔗' : '📝')
</script>

<template>
  <div class="card pad">
    <div class="row spread">
      <h3 style="margin: 0">Ressources & artefacts</h3>
      <button class="ghost sm" @click="fileInput.click()">📎 Ajouter</button>
      <input ref="fileInput" type="file" style="display: none" @change="upload" />
    </div>
    <div v-if="!resources.length" class="muted" style="margin-top: .5rem">Aucune ressource pour cette tâche.</div>
    <div v-for="r in resources" :key="r.id" class="navlink" @click="preview = r">
      {{ icon(r.kind) }} {{ r.name }}
    </div>
    <UniversalPreview v-if="preview" :resource="preview" @close="preview = null" />
  </div>
</template>
