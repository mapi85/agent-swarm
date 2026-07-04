<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'
import Markdown from './Markdown.vue'

const props = defineProps({ sessionId: Number })
const events = ref([])

function display(e) {
  if (e.type === 'tool_use' || e.type === 'tool_result') {
    try {
      const d = JSON.parse(e.content)
      if (e.type === 'tool_use') return `🔧 ${d.name}` + (d.input ? ' ' + JSON.stringify(d.input).slice(0, 200) : '')
      return `${d.is_error ? '❌' : '✓'} ${d.name} → ${String(d.output || '').slice(0, 300)}`
    } catch { return e.content }
  }
  return e.content
}

onMounted(async () => {
  try { events.value = await api.get(`/api/sessions/${props.sessionId}/events`) } catch { events.value = [] }
})
</script>

<template>
  <div style="max-height: 340px; overflow-y: auto; background: #fafbfc; border: 1px solid var(--border); border-radius: 8px; padding: .5rem">
    <div v-if="!events.length" class="muted">Aucun événement.</div>
    <div v-for="(e, i) in events" :key="i" class="event" :class="e.type">
      <Markdown v-if="e.type === 'text'" :text="e.content" />
      <template v-else>{{ display(e) }}</template>
    </div>
  </div>
</template>
