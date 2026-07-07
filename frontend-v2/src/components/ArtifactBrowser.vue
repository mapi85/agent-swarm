<script setup>
import { ref, onMounted, watch, computed } from 'vue'
import { api } from '../api.js'
import { useAuth } from '../store.js'
import { fmtDate } from '../utils.js'
import Modal from './Modal.vue'
import Markdown from './Markdown.vue'

const props = defineProps({ base: String, title: { type: String, default: 'Artefacts' } })
const auth = useAuth()
const files = ref([])
const search = ref('')
const preview = ref(null) // { path } en cours d'aperçu
const text = ref('')
const imgUrl = ref('')
const loading = ref(false)

function fmtSize(n) {
  if (n >= 1e6) return (n / 1e6).toFixed(1) + ' Mo'
  if (n >= 1e3) return (n / 1e3).toFixed(1) + ' Ko'
  return n + ' o'
}
function icon(p) {
  if (/\.(png|jpe?g|gif|webp|svg)$/i.test(p)) return '🖼'
  if (/\.(md|txt|json|ya?ml|py|js|sh|csv|log|html?)$/i.test(p)) return '📝'
  return '📄'
}
const isImg = computed(() => preview.value && /\.(png|jpe?g|gif|webp|svg)$/i.test(preview.value.path))
const isMd = computed(() => preview.value && /\.(md|markdown)$/i.test(preview.value.path))
const showSource = ref(false)

async function load() {
  try { files.value = (await api.get(props.base + '/artifacts')).files } catch { files.value = [] }
}
onMounted(load)
watch(() => props.base, load)

const filtered = computed(() => {
  const q = search.value.toLowerCase()
  return files.value.filter((f) => !q || f.path.toLowerCase().includes(q))
})

async function open(f) {
  preview.value = f; text.value = ''; imgUrl.value = ''; showSource.value = false; loading.value = true
  try {
    const res = await fetch(`${props.base}/artifact?path=${encodeURIComponent(f.path)}`,
      { headers: auth.token ? { Authorization: `Bearer ${auth.token}` } : {} })
    if (isImg.value) imgUrl.value = URL.createObjectURL(await res.blob())
    else text.value = await res.text()
  } catch { text.value = '(aperçu indisponible)' } finally { loading.value = false }
}
function download(f) {
  fetch(`${props.base}/artifact?path=${encodeURIComponent(f.path)}`,
    { headers: auth.token ? { Authorization: `Bearer ${auth.token}` } : {} })
    .then((r) => r.blob()).then((b) => {
      const a = document.createElement('a')
      a.href = URL.createObjectURL(b); a.download = f.path.split('/').pop(); a.click()
    })
}
</script>

<template>
  <div class="card pad">
    <div class="row spread">
      <h3 style="margin: 0">{{ title }}</h3>
      <span class="muted" style="font-size: .8rem">{{ files.length }} fichier(s)</span>
    </div>
    <input v-if="files.length > 6" v-model="search" placeholder="Filtrer…" style="margin: .5rem 0" />
    <div v-if="!files.length" class="muted" style="margin-top: .4rem">Aucun artefact.</div>
    <div v-for="f in filtered" :key="f.path" class="row spread navlink" style="padding: .3rem .4rem" @click="open(f)">
      <span>{{ icon(f.path) }} {{ f.path }}</span>
      <span class="muted" style="font-size: .75rem; white-space: nowrap">{{ fmtSize(f.size) }} · {{ fmtDate(f.mtime) }}</span>
    </div>

    <Modal v-if="preview" :title="preview.path" wide @close="preview = null">
      <div v-if="loading" class="empty"><span class="spinner"></span></div>
      <template v-else>
        <img v-if="isImg && imgUrl" :src="imgUrl" style="max-width: 100%; border-radius: 8px" />
        <div v-else-if="isMd && !showSource"><Markdown :text="text" /></div>
        <pre v-else style="white-space: pre-wrap; max-height: 60vh; overflow: auto">{{ text }}</pre>
        <div class="row" style="justify-content: flex-end; margin-top: 1rem">
          <button v-if="isMd" class="ghost sm" @click="showSource = !showSource">{{ showSource ? 'Voir le rendu' : 'Voir la source' }}</button>
          <button class="sm" @click="download(preview)">⬇ Télécharger</button>
        </div>
      </template>
    </Modal>
  </div>
</template>
