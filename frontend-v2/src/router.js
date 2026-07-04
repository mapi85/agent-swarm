import { createRouter, createWebHistory } from 'vue-router'
import { useAuth } from './store.js'

const routes = [
  { path: '/login', component: () => import('./views/LoginView.vue'), meta: { public: true } },
  { path: '/', component: () => import('./views/DashboardView.vue') },
  { path: '/agents', component: () => import('./views/AgentsView.vue') },
  { path: '/agents/:id', component: () => import('./views/AgentDetailView.vue') },
  { path: '/tasks', component: () => import('./views/TasksView.vue') },
  { path: '/tasks/:id', component: () => import('./views/TaskDetailView.vue') },
  { path: '/missions', component: () => import('./views/MissionsView.vue') },
  { path: '/settings', component: () => import('./views/SettingsView.vue') },
  { path: '/admin', component: () => import('./views/AdminView.vue'), meta: { admin: true } },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({ history: createWebHistory(), routes })

router.beforeEach((to) => {
  const auth = useAuth()
  if (to.meta.public) return true
  if (!auth.isAuthenticated) return '/login'
  if (to.meta.admin && !auth.isAdmin) return '/'
  return true
})

export default router
