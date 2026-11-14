import { createRouter, createWebHistory } from 'vue-router'

const routes = [
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
    path: '/voices',
    name: 'voices',
    component: () => import('../views/VoicesPage.vue'),
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

export default router
