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
    name: 'home',
    component: () => import('../views/HomePage.vue'),
  },
  {
    path: '/create/shorts',
    name: 'create-shorts',
    component: () => import('../views/VideoWizard.vue'),
    props: { mode: 'shorts' },
  },
  {
    path: '/create/longform',
    name: 'create-longform',
    component: () => import('../views/VideoWizard.vue'),
    props: { mode: 'longform' },
  },
  {
    path: '/create',
    redirect: '/',
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
  {
    path: '/editor/session/:sessionId',
    name: 'editor-session',
    component: () => import('../views/VideoEditorPage.vue'),
    props: true,
  },
  {
    path: '/editor/video/:videoId',
    name: 'editor-video',
    component: () => import('../views/VideoEditorPage.vue'),
    props: true,
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
    next('/')
  } else {
    next()
  }
})

export default router
