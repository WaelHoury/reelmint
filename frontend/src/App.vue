<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { loadCurrentUser } from './stores/auth'
import AppSidebar from './components/AppSidebar.vue'

const route = useRoute()

onMounted(loadCurrentUser)

const sidebarCollapsed = ref(localStorage.getItem('sidebar_collapsed') === 'true')

watch(sidebarCollapsed, (val) => {
  localStorage.setItem('sidebar_collapsed', String(val))
})

const isEditorPage = computed(() => route.path.startsWith('/editor/'))

const mainStyle = computed(() => ({
  marginLeft: sidebarCollapsed.value
    ? 'var(--ce-sidebar-collapsed)'
    : 'var(--ce-sidebar-expanded)',
}))
</script>

<template>
  <el-config-provider>
    <!-- Editor page: full-screen, no sidebar -->
    <router-view v-if="isEditorPage" />

    <!-- Main layout with sidebar -->
    <div v-else class="app-layout">
      <AppSidebar v-model:collapsed="sidebarCollapsed" />
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
