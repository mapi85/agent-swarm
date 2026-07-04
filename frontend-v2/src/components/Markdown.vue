<script setup>
import { computed } from 'vue'
import { md } from '../utils.js'
const props = defineProps({ text: String })
const html = computed(() => md(props.text))
</script>

<template>
  <div class="md" v-html="html"></div>
</template>
