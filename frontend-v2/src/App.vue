<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuth } from './store.js'
import { api } from './api.js'
import NotificationBell from './components/NotificationBell.vue'
import GlobalSearch from './components/GlobalSearch.vue'

const auth = useAuth()
const route = useRoute()
const router = useRouter()
const showShell = computed(() => auth.isAuthenticated && route.path !== '/login')

const nav = computed(() => {
  const items = [
    { to: '/', label: 'Tableau de bord', icon: '📊' },
    { to: '/tasks', label: 'Tâches', icon: '📋' },
    { to: '/agents', label: 'Agents', icon: '🤖' },
    { to: '/missions', label: 'Missions', icon: '🎯' },
    { to: '/settings', label: 'Réglages', icon: '⚙️' },
  ]
  if (auth.isAdmin) items.push({ to: '/admin', label: 'Administration', icon: '🛡️' })
  return items
})

async function logout() {
  try { await api.post('/api/auth/logout') } catch { /* */ }
  auth.logout()
  router.push('/login')
}
</script>

<template>
  <div v-if="!auth.ready" class="empty" style="margin-top: 30vh"><span class="spinner"></span></div>
  <div v-else-if="showShell" class="shell">
    <aside class="sidebar">
      <div class="brand"><span class="logo">LE</span> L'Essaim</div>
      <router-link v-for="n in nav" :key="n.to" :to="n.to" class="navlink"
        :class="{ active: route.path === n.to || (n.to !== '/' && route.path.startsWith(n.to)) }">
        <span>{{ n.icon }}</span> {{ n.label }}
      </router-link>
    </aside>
    <div class="main">
      <div class="topbar">
        <GlobalSearch />
        <div class="grow"></div>
        <NotificationBell />
        <div class="row" style="gap: .4rem">
          <span class="badge" :class="auth.isAdmin ? 'violet' : 'gray'">{{ auth.user.display_name }}</span>
          <button class="ghost sm" @click="logout">Déconnexion</button>
        </div>
      </div>
      <div class="content">
        <router-view />
      </div>
    </div>
  </div>
  <router-view v-else />
</template>
