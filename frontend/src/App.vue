<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { isAuthenticated, currentUser, logout } from './stores/auth'

const route = useRoute()
const router = useRouter()

const isLoginPage = computed(() => route.path === '/login')

function handleLogout() {
  logout()
  router.push('/login')
}
</script>

<template>
  <el-config-provider>
    <!-- Login page: full-screen, no sidebar -->
    <router-view v-if="isLoginPage" />

    <!-- Authenticated layout with sidebar -->
    <el-container v-else class="app-container">
      <el-aside width="220px" class="app-aside">
        <div class="sidebar-logo">
          <el-icon :size="24" color="#409eff"><VideoCamera /></el-icon>
          <span>AI Video Maker</span>
        </div>

        <el-menu
          :default-active="route.path"
          router
          class="sidebar-menu"
        >
          <el-menu-item index="/create">
            <el-icon><Plus /></el-icon>
            <span>Create Video</span>
          </el-menu-item>
          <el-menu-item index="/videos">
            <el-icon><Film /></el-icon>
            <span>My Videos</span>
          </el-menu-item>
          <el-menu-item index="/account">
            <el-icon><User /></el-icon>
            <span>Account</span>
          </el-menu-item>
        </el-menu>

        <div class="sidebar-footer">
          <el-button text type="danger" @click="handleLogout" style="width: 100%">
            <el-icon><SwitchButton /></el-icon>
            Logout
          </el-button>
        </div>
      </el-aside>

      <el-container>
        <el-header class="app-header">
          <div class="header-user" v-if="currentUser">
            <el-avatar :size="32" :src="currentUser.picture_url">
              <el-icon><User /></el-icon>
            </el-avatar>
            <span class="header-name">{{ currentUser.name }}</span>
          </div>
        </el-header>
        <el-main class="app-main">
          <router-view />
        </el-main>
      </el-container>
    </el-container>
  </el-config-provider>
</template>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: 'Helvetica Neue', Helvetica, 'PingFang SC', Arial, sans-serif;
  background-color: #f5f7fa;
  color: #303133;
}

.app-container {
  min-height: 100vh;
}

.app-aside {
  background: #fff;
  border-right: 1px solid #e4e7ed;
  display: flex;
  flex-direction: column;
  height: 100vh;
  position: fixed;
  left: 0;
  top: 0;
  z-index: 100;
}

.sidebar-logo {
  height: 60px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 20px;
  font-size: 16px;
  font-weight: 600;
  color: #303133;
  border-bottom: 1px solid #e4e7ed;
}

.sidebar-menu {
  flex: 1;
  border-right: none;
}

.sidebar-footer {
  padding: 12px 16px;
  border-top: 1px solid #e4e7ed;
}

/* Offset main content for fixed sidebar */
.app-container > .el-container {
  margin-left: 220px;
}

.app-header {
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  height: 60px;
  padding: 0 24px;
}

.header-user {
  display: flex;
  align-items: center;
  gap: 10px;
}

.header-name {
  font-size: 14px;
  color: #606266;
}

.app-main {
  padding: 24px;
  max-width: 1200px;
}
</style>
