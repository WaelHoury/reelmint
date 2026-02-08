<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { googleLogin } from '../api'
import { setAuth } from '../stores/auth'

const router = useRouter()
const loading = ref(false)

// Load Google Identity Services
function initGoogleSignIn() {
  if (window.google) {
    window.google.accounts.id.initialize({
      client_id: import.meta.env.VITE_GOOGLE_CLIENT_ID,
      callback: handleCredentialResponse,
    })
    window.google.accounts.id.renderButton(
      document.getElementById('google-signin-btn'),
      { theme: 'outline', size: 'large', width: 320, text: 'signin_with' }
    )
  }
}

async function handleCredentialResponse(response) {
  loading.value = true
  try {
    const { data } = await googleLogin(response.credential)
    setAuth(data.access_token, data.user)
    router.push('/create')
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || 'Login failed')
  } finally {
    loading.value = false
  }
}

// Load the Google script and initialize
function loadGoogleScript() {
  if (document.getElementById('google-gsi-script')) {
    initGoogleSignIn()
    return
  }
  const script = document.createElement('script')
  script.id = 'google-gsi-script'
  script.src = 'https://accounts.google.com/gsi/client'
  script.async = true
  script.defer = true
  script.onload = initGoogleSignIn
  document.head.appendChild(script)
}

import { onMounted } from 'vue'
onMounted(loadGoogleScript)
</script>

<template>
  <div class="login-page">
    <el-card class="login-card" shadow="always">
      <div class="login-header">
        <el-icon :size="48" color="#409eff"><VideoCamera /></el-icon>
        <h1>AI Video Maker</h1>
        <p>Create stunning videos with AI</p>
      </div>
      <div class="login-body">
        <div id="google-signin-btn" v-show="!loading"></div>
        <el-button v-if="loading" loading size="large" style="width: 320px">
          Signing in...
        </el-button>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}

.login-card {
  width: 420px;
  padding: 20px;
  border-radius: 12px;
}

.login-header {
  text-align: center;
  margin-bottom: 32px;
}

.login-header h1 {
  margin: 12px 0 4px;
  font-size: 24px;
  color: #303133;
}

.login-header p {
  color: #909399;
  font-size: 14px;
}

.login-body {
  display: flex;
  justify-content: center;
}
</style>
