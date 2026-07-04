<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { api } from '../api.js'
import Markdown from './Markdown.vue'
import { fmtDate } from '../utils.js'

const open = ref(false)
const items = ref([])
const count = ref(0)
const replies = ref({})
let timer = null

async function load() {
  try {
    items.value = await api.get('/api/notifications?status=open')
    count.value = items.value.length
  } catch { /* ignore */ }
}
async function answer(n) {
  const response = (replies.value[n.id] || '').trim()
  if (!response) return
  await api.post(`/api/notifications/${n.id}/answer`, { response })
  replies.value[n.id] = ''
  await load()
}
async function dismiss(n) {
  await api.post(`/api/notifications/${n.id}/dismiss`)
  await load()
}

onMounted(() => { load(); timer = setInterval(load, 8000) })
onUnmounted(() => clearInterval(timer))
defineExpose({ load })
</script>

<template>
  <div style="position: relative">
    <button class="iconbtn" @click="open = !open" title="Notifications">
      🔔<span v-if="count" class="dot">{{ count }}</span>
    </button>
    <div v-if="open" class="card pad popover" @click.stop>
      <div class="row spread" style="margin-bottom: .5rem">
        <strong>Notifications</strong>
        <button class="ghost sm" @click="open = false">✕</button>
      </div>
      <div v-if="!items.length" class="empty">Rien à traiter 🎉</div>
      <div v-for="n in items" :key="n.id" class="card pad" style="margin-bottom: .5rem">
        <div class="row spread">
          <span class="badge" :class="n.type === 'question' ? 'violet' : 'amber'">
            {{ n.type === 'question' ? '❓ Question' : '🔔 Alerte' }}
          </span>
          <span class="muted" style="font-size: .78rem">{{ fmtDate(n.created_at) }}</span>
        </div>
        <Markdown :text="n.content" style="margin: .4rem 0" />
        <template v-if="n.type === 'question'">
          <textarea v-model="replies[n.id]" placeholder="Ta réponse… (Ctrl+Entrée)"
            @keydown.ctrl.enter="answer(n)" rows="2"></textarea>
          <div class="row" style="margin-top: .4rem">
            <button class="primary sm" @click="answer(n)">Répondre</button>
            <button class="ghost sm" @click="dismiss(n)">Ignorer</button>
          </div>
        </template>
        <button v-else class="ghost sm" @click="dismiss(n)" style="margin-top: .4rem">Marquer comme lu</button>
      </div>
    </div>
  </div>
</template>
