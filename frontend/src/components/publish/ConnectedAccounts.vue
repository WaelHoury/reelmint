<script setup>
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getConnectedAccounts, startOAuth, disconnectAccount } from '../../api'

const route = useRoute()
const router = useRouter()

const accounts = ref([])
const loading = ref(false)
const connecting = ref(false)

async function fetchAccounts() {
  loading.value = true
  try {
    const { data } = await getConnectedAccounts()
    accounts.value = data.accounts
  } catch {
    // ignore — might not have any accounts yet
  } finally {
    loading.value = false
  }
}

async function connectYouTube() {
  connecting.value = true
  try {
    const { data } = await startOAuth('youtube', '/account')
    window.location.href = data.auth_url
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || 'Failed to start YouTube connection')
    connecting.value = false
  }
}

async function handleDisconnect(account) {
  try {
    await ElMessageBox.confirm(
      `Disconnect ${account.display_name || account.provider}? You won't be able to publish to this account until you reconnect.`,
      'Disconnect Account',
      { confirmButtonText: 'Disconnect', cancelButtonText: 'Cancel', type: 'warning' }
    )
    await disconnectAccount(account.id)
    ElMessage.success('Account disconnected')
    fetchAccounts()
  } catch {
    // cancelled or error
  }
}

async function handleReconnect(account) {
  connecting.value = true
  try {
    const { data } = await startOAuth(account.provider, '/account')
    window.location.href = data.auth_url
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || 'Failed to start reconnection')
    connecting.value = false
  }
}

function providerLabel(provider) {
  const labels = { youtube: 'YouTube', tiktok: 'TikTok', instagram: 'Instagram' }
  return labels[provider] || provider
}

function statusType(status) {
  if (status === 'active') return 'success'
  if (status === 'expired' || status === 'revoked') return 'danger'
  return 'info'
}

onMounted(() => {
  // Handle OAuth callback query params
  const oauthResult = route.query.oauth
  if (oauthResult === 'success') {
    ElMessage.success(`${providerLabel(route.query.provider || '')} account connected!`)
    router.replace({ query: {} })
  } else if (oauthResult === 'error') {
    ElMessage.error(route.query.message || 'Failed to connect account')
    router.replace({ query: {} })
  }

  fetchAccounts()
})
</script>

<template>
  <div class="connected-accounts">
    <h3 class="section-title">Connected Channels</h3>

    <div v-if="loading" class="loading-state">
      <el-skeleton :rows="2" animated />
    </div>

    <div v-else>
      <!-- Account list -->
      <div v-for="account in accounts" :key="account.id" class="account-item glass-card">
        <div class="account-info">
          <el-avatar :size="40" :src="account.avatar_url">
            <span>{{ (account.display_name || '?')[0] }}</span>
          </el-avatar>
          <div class="account-details">
            <span class="account-name">{{ account.display_name || account.username }}</span>
            <div class="account-meta">
              <el-tag size="small" :type="statusType(account.status)">
                {{ account.status }}
              </el-tag>
              <span class="provider-label">{{ providerLabel(account.provider) }}</span>
            </div>
          </div>
        </div>
        <div class="account-actions">
          <el-button
            v-if="account.status === 'expired' || account.status === 'revoked'"
            size="small"
            type="warning"
            plain
            @click="handleReconnect(account)"
          >
            Reconnect
          </el-button>
          <el-button
            size="small"
            type="danger"
            plain
            @click="handleDisconnect(account)"
          >
            Disconnect
          </el-button>
        </div>
      </div>

      <!-- Connect button -->
      <div class="connect-actions">
        <el-button
          type="primary"
          :loading="connecting"
          @click="connectYouTube"
        >
          <el-icon class="btn-icon"><Plus /></el-icon>
          Connect YouTube
        </el-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.connected-accounts {
  margin-top: 32px;
  max-width: 500px;
}

.section-title {
  font-size: 18px;
  font-weight: 700;
  color: var(--ce-text-primary);
  margin-bottom: 16px;
}

.account-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px;
  margin-bottom: 12px;
}

.account-info {
  display: flex;
  align-items: center;
  gap: 12px;
}

.account-details {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.account-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--ce-text-primary);
}

.account-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

.provider-label {
  font-size: 12px;
  color: var(--ce-text-muted);
}

.account-actions {
  display: flex;
  gap: 8px;
}

.connect-actions {
  margin-top: 16px;
}

.btn-icon {
  margin-right: 4px;
}

.loading-state {
  padding: 16px;
}
</style>
