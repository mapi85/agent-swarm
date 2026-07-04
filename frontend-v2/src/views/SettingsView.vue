<script setup>
import { ref, onMounted, computed } from 'vue'
import { api } from '../api.js'
import { useAuth } from '../store.js'
import { fmtTokens } from '../utils.js'

const auth = useAuth()
const usage = ref(null)
const channels = ref([])
const showForm = ref(false)
const form = ref({ name: '', type: 'email', to: '', bot_token: '', chat_id: '', use_for_alerts: true, use_for_questions: false })
const testResult = ref({})

async function load() {
  usage.value = await api.get('/api/usage')
  channels.value = await api.get('/api/channels')
}
onMounted(load)

function pct(used, limit) { return limit ? Math.min(100, Math.round((used / limit) * 100)) : 0 }
function gaugeCls(p) { return p >= 90 ? 'crit' : p >= 70 ? 'warn' : '' }

async function createChannel() {
  const body = { name: form.value.name, type: form.value.type,
    use_for_alerts: form.value.use_for_alerts, use_for_questions: form.value.use_for_questions }
  if (form.value.type === 'email') body.to = form.value.to
  else { body.bot_token = form.value.bot_token; body.chat_id = form.value.chat_id }
  try {
    await api.post('/api/channels', body)
    showForm.value = false
    form.value = { name: '', type: 'email', to: '', bot_token: '', chat_id: '', use_for_alerts: true, use_for_questions: false }
    await load()
  } catch (e) { alert(e.message) }
}
async function testChannel(c) {
  testResult.value = { ...testResult.value, [c.id]: '…' }
  const r = await api.post(`/api/channels/${c.id}/test`)
  testResult.value = { ...testResult.value, [c.id]: r.result }
}
async function removeChannel(c) {
  if (!confirm('Supprimer ce canal ?')) return
  await api.del(`/api/channels/${c.id}`); await load()
}
async function toggle(c, field) {
  await api.patch(`/api/channels/${c.id}`, { [field]: !c[field] }); await load()
}

const hasQuota = computed(() => usage.value && (usage.value.short_limit || usage.value.long_limit))
</script>

<template>
  <h1>Réglages</h1>

  <div class="card pad" style="margin-bottom: 1rem">
    <h3>Mon compte</h3>
    <div class="muted">{{ auth.user.email }} · {{ auth.user.role === 'admin' ? 'Administrateur' : 'Utilisateur' }}</div>
  </div>

  <div class="card pad" style="margin-bottom: 1rem">
    <h3>Ma consommation de tokens</h3>
    <div v-if="!hasQuota" class="muted">Aucun quota fixé (illimité).</div>
    <template v-else>
      <div v-if="usage.short_limit" style="margin: .5rem 0">
        <div class="row spread" style="font-size: .85rem">
          <span>Court terme ({{ usage.short_hours }} h)</span>
          <span>{{ fmtTokens(usage.short_used) }} / {{ fmtTokens(usage.short_limit) }}</span>
        </div>
        <div class="gauge"><span :class="gaugeCls(pct(usage.short_used, usage.short_limit))"
          :style="{ width: pct(usage.short_used, usage.short_limit) + '%' }"></span></div>
      </div>
      <div v-if="usage.long_limit" style="margin: .5rem 0">
        <div class="row spread" style="font-size: .85rem">
          <span>Long terme ({{ usage.long_days }} j)</span>
          <span>{{ fmtTokens(usage.long_used) }} / {{ fmtTokens(usage.long_limit) }}</span>
        </div>
        <div class="gauge"><span :class="gaugeCls(pct(usage.long_used, usage.long_limit))"
          :style="{ width: pct(usage.long_used, usage.long_limit) + '%' }"></span></div>
      </div>
    </template>
  </div>

  <div class="card pad">
    <div class="row spread">
      <h3 style="margin: 0">Mes canaux de notification</h3>
      <button class="sm" @click="showForm = !showForm">+ Canal</button>
    </div>

    <div v-if="showForm" class="card pad" style="margin: .8rem 0; background: var(--bg)">
      <div class="grid" style="grid-template-columns: 1fr 1fr">
        <div><label>Nom</label><input v-model="form.name" /></div>
        <div><label>Type</label>
          <select v-model="form.type"><option value="email">Email</option><option value="telegram">Telegram</option></select>
        </div>
      </div>
      <template v-if="form.type === 'email'">
        <label>Adresse email</label><input v-model="form.to" type="email" />
      </template>
      <template v-else>
        <label>Token du bot (@BotFather)</label><input v-model="form.bot_token" />
        <label>Chat ID (@userinfobot)</label><input v-model="form.chat_id" />
      </template>
      <div class="row" style="margin-top: .6rem; gap: 1rem">
        <label class="row" style="width: auto; gap: .3rem"><input type="checkbox" v-model="form.use_for_alerts" style="width: auto" /> Alertes</label>
        <label class="row" style="width: auto; gap: .3rem"><input type="checkbox" v-model="form.use_for_questions" style="width: auto" :disabled="form.type === 'email'" /> Questions <span class="muted">(Telegram)</span></label>
      </div>
      <button class="primary sm" style="margin-top: .6rem" @click="createChannel">Créer</button>
    </div>

    <div v-if="!channels.length" class="muted" style="margin-top: .5rem">Aucun canal. Par défaut, les alertes restent visibles dans la cloche.</div>
    <table v-else style="margin-top: .5rem">
      <thead><tr><th>Nom</th><th>Type</th><th>Destinataire</th><th>Alertes</th><th>Questions</th><th></th></tr></thead>
      <tbody>
        <tr v-for="c in channels" :key="c.id">
          <td>{{ c.name }}</td>
          <td><span class="badge gray">{{ c.type === 'email' ? '📧' : '✈️' }} {{ c.type }}</span></td>
          <td class="muted">{{ c.to || c.chat_id || '—' }}</td>
          <td><input type="checkbox" :checked="c.use_for_alerts" style="width: auto" @change="toggle(c, 'use_for_alerts')" /></td>
          <td><input type="checkbox" :checked="c.use_for_questions" style="width: auto" @change="toggle(c, 'use_for_questions')" :disabled="c.type === 'email'" /></td>
          <td class="row" style="gap: .3rem">
            <button class="ghost sm" @click="testChannel(c)">Tester</button>
            <button class="ghost sm danger" @click="removeChannel(c)">×</button>
            <span v-if="testResult[c.id]" class="muted" style="font-size: .75rem">{{ testResult[c.id] }}</span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
