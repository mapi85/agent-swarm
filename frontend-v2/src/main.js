import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router.js'
import { useAuth } from './store.js'
import { api } from './api.js'
import './style.css'

const app = createApp(App)
app.use(createPinia())

// Vérifie la session avant de monter (recharge du profil si un token existe).
const auth = useAuth()
async function boot() {
  if (auth.token) {
    try { auth.user = await api.get('/api/auth/me') } catch { auth.logout() }
  }
  auth.ready = true
  app.use(router)
  app.mount('#app')
}
boot()
