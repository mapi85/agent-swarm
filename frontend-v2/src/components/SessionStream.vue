<script setup>
import { ref, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { api, stream } from '../api.js'
import Markdown from './Markdown.vue'
import { fmtDate } from '../utils.js'

const props = defineProps({ sessionId: Number })
const events = ref([])
const box = ref(null)
const done = ref(false)
let stop = null

function pushEvent(e) {
  let display = e.content
  if (e.type === 'tool_use' || e.type === 'tool_result') {
    try {
      const d = JSON.parse(e.content)
      if (e.type === 'tool_use') display = `🔧 ${d.name}` + (d.input ? ' ' + JSON.stringify(d.input).slice(0, 300) : '')
      else display = `${d.is_error ? '❌' : '✓'} ${d.name} → ${String(d.output || '').slice(0, 400)}`
    } catch { /* keep raw */ }
  }
  events.value.push({ ...e, display })
  nextTick(() => { if (box.value) box.value.scrollTop = box.value.scrollHeight })
}

function start(id) {
  events.value = []; done.value = false
  if (stop) stop()
  stop = stream(`/api/stream/sessions/${id}/events`, (ev, data) => {
    if (ev === 'end') { done.value = true; return }
    pushEvent(data)
  })
}

watch(() => props.sessionId, (id) => { if (id) start(id) })
onMounted(() => { if (props.sessionId) start(props.sessionId) })
onUnmounted(() => { if (stop) stop() })
</script>

<template>
  <div>
    <div ref="box" style="max-height: 460px; overflow-y: auto; background: #fafbfc; border: 1px solid var(--border); border-radius: 8px; padding: .5rem">
      <div v-if="!events.length" class="empty">En attente d'événements…</div>
      <div v-for="(e, i) in events" :key="i" class="event" :class="e.type">
        <template v-if="e.type === 'text'"><Markdown :text="e.content" /></template>
        <template v-else>{{ e.display }}</template>
      </div>
    </div>
    <div class="muted" style="font-size: .8rem; margin-top: .3rem">
      {{ done ? '● Session terminée' : '○ Flux en direct…' }}
    </div>
  </div>
</template>
