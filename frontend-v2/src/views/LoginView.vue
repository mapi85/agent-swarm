<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'
import { useAuth } from '../store.js'

const auth = useAuth()
const router = useRouter()
const mode = ref('login') // login | register
const email = ref('')
const password = ref('')
const displayName = ref('')
const error = ref('')
const info = ref('')
const busy = ref(false)

async function submit() {
  error.value = ''; info.value = ''; busy.value = true
  try {
    if (mode.value === 'login') {
      const { token, user } = await api.post('/api/auth/login', { email: email.value, password: password.value })
      auth.setSession(token, user)
      router.push('/')
    } else {
      const user = await api.post('/api/auth/register',
        { email: email.value, password: password.value, display_name: displayName.value })
      if (user.status === 'active') {
        info.value = 'Compte créé (administrateur). Tu peux te connecter.'
      } else {
        info.value = 'Inscription enregistrée. Un administrateur doit valider ton compte.'
      }
      mode.value = 'login'
    }
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
</script>

<template>
  <div style="min-height: 100vh; display: grid; place-items: center; background: var(--bg); padding: 1rem">
    <div class="card pad" style="width: min(400px, 96vw)">
      <div class="row" style="gap: .6rem; margin-bottom: 1rem">
        <span class="logo" style="width: 34px; height: 34px">LE</span>
        <div><h1 style="margin: 0; font-size: 1.25rem">L'Essaim</h1>
          <div class="muted" style="font-size: .82rem">Supervision d'agents autonomes</div></div>
      </div>

      <div class="tabs">
        <button class="tab" :class="{ active: mode === 'login' }" @click="mode = 'login'">Connexion</button>
        <button class="tab" :class="{ active: mode === 'register' }" @click="mode = 'register'">Inscription</button>
      </div>

      <form @submit.prevent="submit">
        <template v-if="mode === 'register'">
          <label>Nom affiché</label>
          <input v-model="displayName" required />
        </template>
        <label>Email</label>
        <input v-model="email" type="email" required />
        <label>Mot de passe</label>
        <input v-model="password" type="password" required minlength="8" />
        <button class="primary" style="width: 100%; margin-top: 1rem" :disabled="busy">
          {{ busy ? '…' : (mode === 'login' ? 'Se connecter' : "S'inscrire") }}
        </button>
      </form>
      <p v-if="error" class="badge red" style="margin-top: .8rem; display: block; padding: .5rem">{{ error }}</p>
      <p v-if="info" class="badge green" style="margin-top: .8rem; display: block; padding: .5rem">{{ info }}</p>
    </div>
  </div>
</template>
