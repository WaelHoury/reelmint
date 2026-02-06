<script setup>
</script>

<template>
  <el-config-provider>
    <div class="app-layout">
      <el-header class="app-header">
        <div class="header-content">
          <h1 class="logo">
            <el-icon><VideoCamera /></el-icon>
            AI Video Maker
          </h1>
        </div>
      </el-header>
      <el-main class="app-main">
        <router-view />
      </el-main>
    </div>
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

.app-layout {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.app-header {
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
  display: flex;
  align-items: center;
  padding: 0 24px;
  height: 60px;
}

.header-content {
  max-width: 1200px;
  width: 100%;
  margin: 0 auto;
}

.logo {
  font-size: 20px;
  font-weight: 600;
  color: #409eff;
  display: flex;
  align-items: center;
  gap: 8px;
}

.app-main {
  max-width: 1200px;
  width: 100%;
  margin: 0 auto;
  padding: 24px;
  flex: 1;
}
</style>
