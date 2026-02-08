import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/login',
    name: 'login',
    component: () => import('../views/LoginPage.vue'),
    meta: { public: true },
  },
  {
    path: '/',
    redirect: '/create',
  },
  {
    path: '/create',
    name: 'create',
    component: () => import('../views/VideoWizard.vue'),
  },
  {
    path: '/videos',
    name: 'videos',
    component: () => import('../views/MyVideos.vue'),
  },
  {
    path: '/account',
    name: 'account',
    component: () => import('../views/AccountPage.vue'),
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// Navigation guard — redirect to login if not authenticated
router.beforeEach((to, from, next) => {
  const token = localStorage.getItem('auth_token')
  if (!to.meta.public && !token) {
    next('/login')
  } else if (to.path === '/login' && token) {
    next('/create')
  } else {
    next()
  }
})

export default router
