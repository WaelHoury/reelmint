<script setup>
import { ref, onMounted } from 'vue'
import { currentUser } from '../stores/auth'
import { getMyVideos } from '../api'
import ConnectedAccounts from '../components/publish/ConnectedAccounts.vue'

const videoCount = ref(0)

onMounted(async () => {
  try {
    const { data } = await getMyVideos(1, 1)
    videoCount.value = data.total
  } catch {
    // ignore
  }
})
</script>

<template>
  <div>
    <h2 class="page-title">Account</h2>

    <div v-if="currentUser" class="account-card glass-card">
      <div class="user-header">
        <el-avatar :size="72" :src="currentUser.picture_url">
          <el-icon :size="36"><User /></el-icon>
        </el-avatar>
        <div>
          <h3 class="user-name">{{ currentUser.name }}</h3>
          <span class="user-email">{{ currentUser.email }}</span>
        </div>
      </div>

      <div class="stats-grid">
        <div class="stat-item">
          <span class="stat-value ce-gradient-text">{{ videoCount }}</span>
          <span class="stat-label">Total Videos</span>
        </div>
      </div>

      <div class="details-list">
        <div class="detail-row">
          <span class="detail-label">Email</span>
          <span class="detail-value">{{ currentUser.email }}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">Total Videos</span>
          <span class="detail-value">{{ videoCount }}</span>
        </div>
      </div>
    </div>

    <ConnectedAccounts />
  </div>
</template>

<style scoped>
.page-title {
  font-size: 24px;
  font-weight: 700;
  color: var(--ce-text-primary);
  margin-bottom: 24px;
}

.account-card {
  max-width: 500px;
  padding: 32px;
}

.user-header {
  display: flex;
  align-items: center;
  gap: 20px;
  margin-bottom: 28px;
}

.user-name {
  margin: 0 0 4px;
  font-size: 20px;
  font-weight: 700;
  color: var(--ce-text-primary);
}

.user-email {
  color: var(--ce-text-muted);
  font-size: 14px;
}

.stats-grid {
  display: flex;
  gap: 24px;
  margin-bottom: 24px;
  padding: 20px;
  background: rgba(62, 230, 168, 0.05);
  border-radius: var(--ce-radius-sm);
  border: 1px solid var(--ce-border-subtle);
}

.stat-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}

.stat-value {
  font-size: 28px;
  font-weight: 800;
}

.stat-label {
  font-size: 12px;
  color: var(--ce-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.details-list {
  display: flex;
  flex-direction: column;
  gap: 0;
  border: 1px solid #222;
  border-radius: var(--ce-radius-sm);
  overflow: hidden;
}

.detail-row {
  display: flex;
  padding: 12px 16px;
  border-bottom: 1px solid #222;
}

.detail-row:last-child {
  border-bottom: none;
}

.detail-label {
  width: 140px;
  flex-shrink: 0;
  font-size: 13px;
  color: var(--ce-text-muted);
  font-weight: 500;
}

.detail-value {
  font-size: 13px;
  color: var(--ce-text-primary);
}
</style>
