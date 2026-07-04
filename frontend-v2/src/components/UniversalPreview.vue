<script setup>
import { ref, onMounted, computed } from 'vue'
import { useAuth } from '../store.js'
import Modal from './Modal.vue'
import Markdown from './Markdown.vue'

const props = defineProps({ resource: Object })
const emit = defineEmits(['close'])
const auth = useAuth()
const text = ref('')
const imgUrl = ref('')
const showSource = ref(false)
const loading = ref(true)

const isImage = computed(() => /\.(png|jpe?g|gif|webp|svg)$/i.test(props.resource.name))
const isMarkdown = computed(() =>
  /\.(md|markdown)$/i.test(props.resource.name) ||
  (props.resource.kind === 'note' && (props.resource.content || '').trimStart().startsWith('#')))

onMounted(async () => {
  const r = props.resource
  if (r.kind === 'link') { text.value = r.content; loading.value = false; return }
  if (r.kind === 'note') { text.value = r.content || ''; loading.value = false; return }
  // fichier : télécharge via l'API (auth par en-tête, pas de token en URL)
  try {
    const res = await fetch(`/api/resources/${r.id}/content`, {
      headers: auth.token ? { Authorization: `Bearer ${auth.token}` } : {},
    })
    if (isImage.value) {
      imgUrl.value = URL.createObjectURL(await res.blob())
    } else {
      text.value = await res.text()
    }
  } catch { text.value = '(aperçu indisponible)' } finally { loading.value = false }
})

function download() {
  const r = props.resource
  fetch(`/api/resources/${r.id}/content`, {
    headers: auth.token ? { Authorization: `Bearer ${auth.token}` } : {},
  }).then((res) => res.blob()).then((b) => {
    const a = document.createElement('a')
    a.href = URL.createObjectURL(b); a.download = r.name; a.click()
  })
}
</script>

<template>
  <Modal :title="resource.name" wide @close="emit('close')">
    <div v-if="loading" class="empty"><span class="spinner"></span></div>
    <template v-else>
      <div v-if="resource.kind === 'link'">
        <a :href="resource.content" target="_blank" rel="noopener">{{ resource.content }}</a>
      </div>
      <img v-else-if="isImage && imgUrl" :src="imgUrl" style="max-width: 100%; border-radius: 8px" />
      <div v-else-if="isMarkdown && !showSource"><Markdown :text="text" /></div>
      <pre v-else style="white-space: pre-wrap; max-height: 60vh; overflow: auto">{{ text }}</pre>

      <div class="row" style="justify-content: flex-end; margin-top: 1rem">
        <button v-if="isMarkdown" class="ghost sm" @click="showSource = !showSource">
          {{ showSource ? 'Voir le rendu' : 'Voir la source' }}
        </button>
        <button v-if="resource.kind === 'file'" class="sm" @click="download">⬇ Télécharger</button>
      </div>
    </template>
  </Modal>
</template>
