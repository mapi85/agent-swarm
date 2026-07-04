<script setup>
defineProps({ title: String, wide: Boolean })
const emit = defineEmits(['close'])
</script>

<template>
  <div class="overlay" @click.self="emit('close')">
    <div class="card pad modal" :class="{ wide }">
      <div class="row spread" style="margin-bottom: .8rem">
        <h2 style="margin: 0">{{ title }}</h2>
        <button class="ghost" @click="emit('close')">✕</button>
      </div>
      <slot />
    </div>
  </div>
</template>
