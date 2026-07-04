<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'
import { fmtTokens } from '../utils.js'
import Modal from '../components/Modal.vue'

const tab = ref('users')
const users = ref([])
const providers = ref([])
const smtp = ref({ host: '', port: 587, user: '', from_addr: '', password: '', password_set: false })

const provForm = ref(null) // null = fermé
const emptyProv = () => ({ id: null, name: '', ptype: 'anthropic', base_url: '', api_key: '',
  default_model: '', models: '', native_features: true, is_default: false,
  limit_short_tokens: 0, limit_short_hours: 0, limit_long_tokens: 0, limit_long_days: 0 })

async function loadUsers() { users.value = await api.get('/api/users') }
async function loadProviders() { providers.value = await api.get('/api/providers') }
async function loadSmtp() { smtp.value = { ...(await api.get('/api/settings/smtp')), password: '' } }

onMounted(() => { loadUsers(); loadProviders(); loadSmtp() })

// --- users ---
async function approve(u) { await api.post(`/api/users/${u.id}/approve`); await loadUsers() }
async function disable(u) { await api.post(`/api/users/${u.id}/disable`); await loadUsers() }
async function enable(u) { await api.post(`/api/users/${u.id}/enable`); await loadUsers() }
async function saveQuota(u) {
  await api.patch(`/api/users/${u.id}`, {
    quota_short_tokens: u.quota_short_tokens, quota_short_hours: u.quota_short_hours,
    quota_long_tokens: u.quota_long_tokens, quota_long_days: u.quota_long_days,
  })
  alert('Quota enregistré.')
}

// --- providers ---
function editProvider(p) {
  provForm.value = p
    ? { ...emptyProv(), ...p, api_key: '', models: (p.models || []).join('\n') }
    : emptyProv()
}
async function saveProvider() {
  const f = provForm.value
  const body = { name: f.name, ptype: f.ptype, base_url: f.base_url, default_model: f.default_model,
    models: f.models.split('\n').map((s) => s.trim()).filter(Boolean), native_features: f.native_features,
    limit_short_tokens: f.limit_short_tokens, limit_short_hours: f.limit_short_hours,
    limit_long_tokens: f.limit_long_tokens, limit_long_days: f.limit_long_days }
  if (f.api_key) body.api_key = f.api_key
  try {
    if (f.id) await api.patch(`/api/providers/${f.id}`, body)
    else { body.is_default = f.is_default; await api.post('/api/providers', body) }
    provForm.value = null
    await loadProviders()
  } catch (e) { alert(e.message) }
}
async function setDefault(p) { await api.post(`/api/providers/${p.id}/default`); await loadProviders() }
async function removeProvider(p) {
  if (!confirm('Supprimer ce provider ?')) return
  try { await api.del(`/api/providers/${p.id}`); await loadProviders() } catch (e) { alert(e.message) }
}
async function fetchModels() {
  try {
    const r = await api.post('/api/providers/fetch-models', {
      provider_id: provForm.value.id, ptype: provForm.value.ptype,
      base_url: provForm.value.base_url, api_key: provForm.value.api_key })
    provForm.value.models = r.models.join('\n')
  } catch (e) { alert(e.message) }
}

async function saveSmtp() {
  const body = { host: smtp.value.host, port: smtp.value.port, user: smtp.value.user, from_addr: smtp.value.from_addr }
  if (smtp.value.password) body.password = smtp.value.password
  smtp.value = { ...(await api.put('/api/settings/smtp', body)), password: '' }
  alert('SMTP enregistré.')
}
</script>

<template>
  <h1>Administration</h1>
  <div class="tabs">
    <button class="tab" :class="{ active: tab === 'users' }" @click="tab = 'users'">Utilisateurs</button>
    <button class="tab" :class="{ active: tab === 'providers' }" @click="tab = 'providers'">Providers</button>
    <button class="tab" :class="{ active: tab === 'smtp' }" @click="tab = 'smtp'">SMTP</button>
  </div>

  <!-- Utilisateurs -->
  <div v-if="tab === 'users'">
    <table class="card" style="overflow: hidden">
      <thead><tr><th>Nom</th><th>Email</th><th>Rôle</th><th>Statut</th><th>Quota court (tok / h)</th><th>Quota long (tok / j)</th><th></th></tr></thead>
      <tbody>
        <tr v-for="u in users" :key="u.id">
          <td>{{ u.display_name }}</td>
          <td class="muted">{{ u.email }}</td>
          <td><span class="badge" :class="u.role === 'admin' ? 'violet' : 'gray'">{{ u.role }}</span></td>
          <td><span class="badge" :class="u.status === 'active' ? 'green' : u.status === 'pending' ? 'amber' : 'red'">{{ u.status }}</span></td>
          <td class="row" style="gap: .2rem">
            <input v-model.number="u.quota_short_tokens" type="number" style="width: 90px" />
            <input v-model.number="u.quota_short_hours" type="number" style="width: 55px" />
          </td>
          <td class="row" style="gap: .2rem">
            <input v-model.number="u.quota_long_tokens" type="number" style="width: 90px" />
            <input v-model.number="u.quota_long_days" type="number" style="width: 55px" />
          </td>
          <td class="row" style="gap: .3rem">
            <button v-if="u.status === 'pending'" class="primary sm" @click="approve(u)">Valider</button>
            <button v-if="u.status === 'active'" class="ghost sm" @click="disable(u)">Désactiver</button>
            <button v-if="u.status === 'disabled'" class="ghost sm" @click="enable(u)">Réactiver</button>
            <button class="ghost sm" @click="saveQuota(u)">💾</button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>

  <!-- Providers -->
  <div v-if="tab === 'providers'">
    <div class="row spread" style="margin-bottom: .8rem">
      <p class="muted" style="margin: 0">L'ordre (priorité) définit la chaîne de secours.</p>
      <button class="primary sm" @click="editProvider(null)">+ Provider</button>
    </div>
    <div class="stack">
      <div v-for="p in providers" :key="p.id" class="card pad">
        <div class="row spread">
          <div class="row">
            <strong>{{ p.name }}</strong>
            <span class="badge gray">{{ p.ptype }}</span>
            <span v-if="p.is_default" class="badge green">défaut</span>
            <span class="muted" style="font-size: .8rem">{{ p.agent_count }} agent(s)</span>
          </div>
          <div class="row">
            <button v-if="!p.is_default" class="ghost sm" @click="setDefault(p)">Par défaut</button>
            <button class="ghost sm" @click="editProvider(p)">✎</button>
            <button class="ghost sm danger" @click="removeProvider(p)">×</button>
          </div>
        </div>
        <div class="muted" style="font-size: .82rem; margin-top: .3rem">
          {{ p.base_url || 'API officielle' }} · clé {{ p.api_key_set ? 'définie' : '⚠ absente' }} ·
          modèles : {{ (p.models || []).join(', ') || '—' }}
        </div>
      </div>
    </div>

    <Modal v-if="provForm" :title="provForm.id ? 'Modifier le provider' : 'Nouveau provider'" wide @close="provForm = null">
      <div class="grid" style="grid-template-columns: 1fr 1fr">
        <div><label>Nom</label><input v-model="provForm.name" /></div>
        <div><label>Type</label><select v-model="provForm.ptype"><option value="anthropic">Anthropic-compatible</option><option value="openai">OpenAI-compatible</option></select></div>
      </div>
      <label>Base URL (vide = API officielle)</label><input v-model="provForm.base_url" />
      <label>Clé API {{ provForm.id ? '(vide = inchangée)' : '' }}</label><input v-model="provForm.api_key" type="password" />
      <div class="row spread"><label>Modèles (un par ligne)</label><button class="ghost sm" @click="fetchModels">🔄 Récupérer</button></div>
      <textarea v-model="provForm.models" rows="3"></textarea>
      <label>Modèle par défaut</label><input v-model="provForm.default_model" />
      <label class="row" style="width: auto; gap: .4rem; margin-top: .5rem"><input type="checkbox" v-model="provForm.native_features" style="width: auto" /> Fonctionnalités natives Anthropic (thinking, effort, cache)</label>
      <div class="grid" style="grid-template-columns: 1fr 1fr 1fr 1fr; margin-top: .5rem">
        <div><label>Limite court (tok)</label><input v-model.number="provForm.limit_short_tokens" type="number" /></div>
        <div><label>sur (h)</label><input v-model.number="provForm.limit_short_hours" type="number" /></div>
        <div><label>Limite long (tok)</label><input v-model.number="provForm.limit_long_tokens" type="number" /></div>
        <div><label>sur (j)</label><input v-model.number="provForm.limit_long_days" type="number" /></div>
      </div>
      <label v-if="!provForm.id" class="row" style="width: auto; gap: .4rem; margin-top: .5rem"><input type="checkbox" v-model="provForm.is_default" style="width: auto" /> Provider par défaut</label>
      <div class="row" style="justify-content: flex-end; margin-top: 1rem">
        <button class="ghost" @click="provForm = null">Annuler</button>
        <button class="primary" @click="saveProvider">Enregistrer</button>
      </div>
    </Modal>
  </div>

  <!-- SMTP -->
  <div v-if="tab === 'smtp'" class="card pad" style="max-width: 480px">
    <h3>Serveur SMTP (envoi des emails de notification)</h3>
    <label>Hôte</label><input v-model="smtp.host" />
    <div class="grid" style="grid-template-columns: 1fr 1fr">
      <div><label>Port</label><input v-model.number="smtp.port" type="number" /></div>
      <div><label>Expéditeur</label><input v-model="smtp.from_addr" /></div>
    </div>
    <label>Utilisateur</label><input v-model="smtp.user" />
    <label>Mot de passe {{ smtp.password_set ? '(enregistré — vide = inchangé)' : '' }}</label>
    <input v-model="smtp.password" type="password" />
    <button class="primary" style="margin-top: 1rem" @click="saveSmtp">Enregistrer</button>
  </div>
</template>
