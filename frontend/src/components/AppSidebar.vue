<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { currentUser } from '../stores/auth'

const props = defineProps({
  collapsed: { type: Boolean, default: false },
})

const emit = defineEmits(['update:collapsed'])

const route = useRoute()
const router = useRouter()

const menuItems = [
  { path: '/', icon: 'Plus', label: 'Create', exact: true, also: ['/create/'] },
  { path: '/videos', icon: 'Film', label: 'My Videos' },
  { path: '/autopilot', icon: 'Timer', label: 'Autopilot' },
  { path: '/voices', icon: 'Microphone', label: 'My Voices' },
  { path: '/account', icon: 'User', label: 'Account' },
]

function isActive(item) {
  if (item.also?.some((prefix) => route.path.startsWith(prefix))) return true
  if (item.exact) {
    return route.path === item.path
  }
  return route.path === item.path || route.path.startsWith(item.path + '/')
}

function navigate(path) {
  router.push(path)
}

function toggleSidebar() {
  emit('update:collapsed', !props.collapsed)
}
</script>

<template>
  <aside class="sidebar" :class="{ collapsed }">
    <!-- Logo -->
    <div class="sidebar-logo" @click="navigate('/')">
      <img src="/favicon.png" alt="" class="logo-img" width="28" height="28" />
      <transition name="sidebar-text">
        <span v-show="!collapsed" class="logo-text">Reel<span class="logo-dot">mint</span></span>
      </transition>
    </div>

    <!-- Toggle -->
    <button class="sidebar-toggle" @click="toggleSidebar" :title="collapsed ? 'Expand sidebar' : 'Collapse sidebar'">
      <el-icon :size="16">
        <DArrowLeft v-if="!collapsed" />
        <DArrowRight v-else />
      </el-icon>
    </button>

    <!-- Navigation -->
    <nav class="sidebar-nav">
      <button
        v-for="item in menuItems"
        :key="item.path"
        class="nav-item"
        :class="{ active: isActive(item) }"
        @click="navigate(item.path)"
        :title="collapsed ? item.label : ''"
      >
        <el-icon :size="20"><component :is="item.icon" /></el-icon>
        <transition name="sidebar-text">
          <span v-show="!collapsed" class="nav-label">{{ item.label }}</span>
        </transition>
      </button>
    </nav>

    <!-- Footer -->
    <div class="sidebar-footer">
      <div class="user-section" v-if="currentUser">
        <el-avatar :size="collapsed ? 32 : 28" :src="currentUser.picture_url">
          <el-icon><User /></el-icon>
        </el-avatar>
        <transition name="sidebar-text">
          <span v-show="!collapsed" class="user-name">{{ currentUser.name }}</span>
        </transition>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  position: fixed;
  left: 0;
  top: 0;
  height: 100vh;
  width: var(--ce-sidebar-expanded);
  background: var(--ce-bg-sidebar);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border-right: 1px solid var(--ce-border-subtle);
  display: flex;
  flex-direction: column;
  z-index: 100;
  transition: width var(--ce-transition-normal);
  /* not overflow:hidden: the collapse toggle sits on the right border */
}

.sidebar-logo,
.sidebar-nav,
.sidebar-footer {
  overflow: hidden;
}

.sidebar.collapsed {
  width: var(--ce-sidebar-collapsed);
}

/* Logo */
.sidebar-logo {
  height: 60px;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 20px;
  border-bottom: 1px solid var(--ce-border-subtle);
  cursor: pointer;
  flex-shrink: 0;
}

.logo-img {
  width: 28px;
  height: 28px;
  border-radius: 7px;
  flex-shrink: 0;
}

.logo-text {
  font-size: 16px;
  font-weight: 700;
  letter-spacing: 0.5px;
  color: var(--ce-text-primary);
  white-space: nowrap;
}

.logo-dot {
  color: var(--ce-accent-light);
}

/* Toggle */
.sidebar-toggle {
  position: absolute;
  top: 20px;
  right: -12px;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: #111;
  border: 1px solid var(--ce-border-input);
  color: var(--ce-text-muted);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: color var(--ce-transition-fast), background var(--ce-transition-fast);
  z-index: 10;
}

.sidebar-toggle:hover {
  color: var(--ce-accent-light);
  background: #1a1a1a;
  border-color: var(--ce-accent-light);
}

/* Navigation */
.sidebar-nav {
  flex: 1;
  padding: 16px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
  border-radius: var(--ce-radius-xs);
  background: transparent;
  border: none;
  color: var(--ce-text-secondary);
  cursor: pointer;
  width: 100%;
  text-align: left;
  font-family: var(--ce-font-family);
  font-size: 14px;
  font-weight: 500;
  transition: background var(--ce-transition-fast), color var(--ce-transition-fast);
  white-space: nowrap;
  overflow: hidden;
  position: relative; /* anchors the active indicator */
}

.nav-item:hover {
  background: var(--ce-bg-hover);
  color: var(--ce-text-primary);
}

.nav-item.active {
  background: rgba(62, 230, 168, 0.1);
  color: var(--ce-accent-light);
  font-weight: 600;
}

.nav-item:focus-visible {
  outline: 2px solid rgba(62, 230, 168, 0.6);
  outline-offset: -2px;
}

.nav-item.active::before {
  content: '';
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 20px;
  background: var(--ce-accent-light);
  border-radius: 0 3px 3px 0;
}

.nav-label {
  white-space: nowrap;
}

/* Footer */
.sidebar-footer {
  padding: 12px;
  border-top: 1px solid var(--ce-border-subtle);
  flex-shrink: 0;
}

.user-section {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  margin-bottom: 8px;
  overflow: hidden;
}

.user-name {
  font-size: 13px;
  color: var(--ce-text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Sidebar text transition */
.sidebar-text-enter-active,
.sidebar-text-leave-active {
  transition: opacity 0.15s ease;
}

.sidebar-text-enter-from,
.sidebar-text-leave-to {
  opacity: 0;
}

/* Collapsed state centering */
.collapsed .sidebar-logo {
  justify-content: center;
  padding: 0;
}

.collapsed .nav-item {
  justify-content: center;
  padding: 12px;
}

.collapsed .user-section {
  justify-content: center;
  padding: 8px 0;
}

.collapsed .sidebar-toggle {
  right: -12px;
}

/* Phones / small tablets: always the compact icon rail */
@media (max-width: 768px) {
  .sidebar,
  .sidebar.collapsed {
    width: var(--ce-sidebar-collapsed);
  }

  .sidebar-toggle,
  .logo-text,
  .nav-label,
  .user-name {
    display: none !important;
  }

  .sidebar-logo {
    justify-content: center;
    padding: 0;
  }

  .nav-item {
    justify-content: center;
  }

  .user-section {
    justify-content: center;
    padding: 8px 0;
  }
}
</style>
