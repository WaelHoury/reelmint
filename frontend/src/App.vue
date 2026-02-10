<script setup>
import { ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { isAuthenticated, currentUser, logout } from './stores/auth'
import AppSidebar from './components/AppSidebar.vue'

const route = useRoute()
const router = useRouter()

const sidebarCollapsed = ref(localStorage.getItem('sidebar_collapsed') === 'true')

watch(sidebarCollapsed, (val) => {
  localStorage.setItem('sidebar_collapsed', String(val))
})

const isLoginPage = computed(() => route.path === '/login')
const isEditorPage = computed(() => route.path.startsWith('/editor/'))
const showSidebar = computed(() => !isLoginPage.value && !isEditorPage.value)

const mainStyle = computed(() => ({
  marginLeft: sidebarCollapsed.value
    ? 'var(--ce-sidebar-collapsed)'
    : 'var(--ce-sidebar-expanded)',
}))

function handleLogout() {
  logout()
  router.push('/login')
}
</script>

<template>
  <el-config-provider>
    <!-- Login page: full-screen, no sidebar -->
    <router-view v-if="isLoginPage" />

    <!-- Editor page: full-screen, no sidebar -->
    <router-view v-else-if="isEditorPage" />

    <!-- Authenticated layout with sidebar -->
    <div v-else class="app-layout">
      <AppSidebar
        v-if="showSidebar"
        v-model:collapsed="sidebarCollapsed"
        @logout="handleLogout"
      />
      <main class="app-main" :style="mainStyle">
        <transition name="fade" mode="out-in">
          <router-view />
        </transition>
      </main>
    </div>
  </el-config-provider>
</template>

<style>
/* Minimal global styles — theme.css handles base + Element Plus overrides */
.app-layout {
  min-height: 100vh;
}

.app-main {
  padding: 32px 40px;
  transition: margin-left var(--ce-transition-normal);
  min-height: 100vh;
}

@media (max-width: 768px) {
  .app-main {
    padding: 20px 16px;
  }
}
</style>
