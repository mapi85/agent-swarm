<script setup>
import { computed } from 'vue'
import { TASK_STATUS, SESSION_STATUS, MISSION_STATUS } from '../utils.js'
const props = defineProps({ status: String, kind: { type: String, default: 'task' } })
const map = { task: TASK_STATUS, session: SESSION_STATUS, mission: MISSION_STATUS }
const info = computed(() => (map[props.kind] || {})[props.status] || { label: props.status, cls: 'gray' })
</script>

<template>
  <span class="badge" :class="info.cls">{{ info.label }}</span>
</template>
