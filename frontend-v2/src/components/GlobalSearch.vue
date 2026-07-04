<script setup>
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'

const router = useRouter()
const q = ref('')
const res = ref(null)
const open = ref(false)
let t = null

watch(q, (val) => {
  clearTimeout(t)
  if (val.trim().length < 2) { res.value = null; open.value = false; return }
  t = setTimeout(async () => {
    try { res.value = await api.get('/api/search?q=' + encodeURIComponent(val)); open.value = true } catch { /* */ }
  }, 250)
})

function go(path) { open.value = false; q.value = ''; router.push(path) }
</script>

<template>
  <div style="position: relative; flex: 1; max-width: 420px">
    <input v-model="q" placeholder="🔍 Rechercher tâches, missions, ressources…"
      @focus="res && (open = true)" @blur="setTimeout(() => open = false, 200)" />
    <div v-if="open && res" class="card pad popover" style="left: 0; right: auto; width: 420px">
      <template v-if="res.tasks.length">
        <div class="muted" style="font-size: .78rem; font-weight: 700">TÂCHES</div>
        <div v-for="t in res.tasks" :key="'t'+t.id" class="navlink" @mousedown="go('/tasks/' + t.id)">
          #{{ t.id }} {{ t.title }}
        </div>
      </template>
      <template v-if="res.missions.length">
        <div class="muted" style="font-size: .78rem; font-weight: 700; margin-top: .4rem">MISSIONS</div>
        <div v-for="m in res.missions" :key="'m'+m.id" class="navlink" @mousedown="go('/missions')">
          {{ m.title }}
        </div>
      </template>
      <template v-if="res.resources.length">
        <div class="muted" style="font-size: .78rem; font-weight: 700; margin-top: .4rem">RESSOURCES</div>
        <div v-for="r in res.resources" :key="'r'+r.id" class="navlink"
          @mousedown="r.task_id ? go('/tasks/' + r.task_id) : null">
          {{ r.name }} <span class="muted">({{ r.kind }})</span>
        </div>
      </template>
      <div v-if="!res.tasks.length && !res.missions.length && !res.resources.length" class="muted">Aucun résultat.</div>
    </div>
  </div>
</template>
