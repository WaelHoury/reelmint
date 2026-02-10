<script setup>
import { ref, onMounted } from 'vue'
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
      { theme: 'filled_black', size: 'large', width: 320, text: 'signin_with' }
    )
  }
}

async function handleCredentialResponse(response) {
  loading.value = true
  try {
    const { data } = await googleLogin(response.credential)
    setAuth(data.access_token, data.user)
    router.push('/')
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

onMounted(loadGoogleScript)
</script>

<template>
  <div class="login-page">
    <div class="login-card glass-card">
      <div class="login-header">
        <div class="login-icon">
          <el-icon :size="40" color="var(--ce-purple-light)"><VideoCamera /></el-icon>
        </div>
        <h1 class="login-title">Clipra<span class="ce-gradient-text">.AI</span></h1>
        <p class="login-subtitle">Create stunning videos with AI</p>
      </div>
      <div class="login-body">
        <div id="google-signin-btn" v-show="!loading"></div>
        <el-button v-if="loading" loading size="large" style="width: 320px">
          Signing in...
        </el-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
}

/* Additional glow for login page */
.login-page::before {
  content: '';
  position: absolute;
  width: 600px;
  height: 600px;
  background: radial-gradient(circle, rgba(167, 139, 250, 0.12) 0%, transparent 70%);
  top: 50%;
  left: 50%;
  transform: translate(-50%, -60%);
  pointer-events: none;
}

.login-card {
  width: 420px;
  padding: 40px 36px;
  position: relative;
  z-index: 1;
}

.login-header {
  text-align: center;
  margin-bottom: 36px;
}

.login-icon {
  width: 72px;
  height: 72px;
  border-radius: 50%;
  background: rgba(167, 139, 250, 0.1);
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 16px;
}

.login-title {
  font-size: 28px;
  font-weight: 800;
  color: var(--ce-text-primary);
  margin: 0 0 8px;
  letter-spacing: -0.5px;
}

.login-subtitle {
  color: var(--ce-text-muted);
  font-size: 14px;
}

.login-body {
  display: flex;
  justify-content: center;
}
</style>
