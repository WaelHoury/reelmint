<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { listBatches } from '../api'
import StatusChip from '../components/batch/StatusChip.vue'
import BatchProgress from '../components/batch/BatchProgress.vue'
import { apiErrorMessage, formatDate, parseServerDate } from '../components/batch/batchUtils'

const POLL_MS = 8000

const router = useRouter()

const batches = ref([])
const loading = ref(true)
const loadError = ref('')
let pollTimer = null
let unmounted = false

const sorted = computed(() =>
  [...batches.value].sort(
    (a, b) => (parseServerDate(b.created_at)?.getTime() || 0) - (parseServerDate(a.created_at)?.getTime() || 0)
  )
)

const anyRunning = computed(() => batches.value.some((b) => b.status === 'running'))

function schedulePoll() {
  clearTimeout(pollTimer)
  pollTimer = null
  if (!unmounted && anyRunning.value) {
    pollTimer = setTimeout(() => fetchBatches(true), POLL_MS)
  }
}

async function fetchBatches(silent = false) {
  if (!silent) loading.value = true
  try {
    const { data } = await listBatches()
    if (unmounted) return
    batches.value = data.batches || []
    loadError.value = ''
  } catch (err) {
    if (unmounted) return
    if (!silent) loadError.value = apiErrorMessage(err, 'Failed to load batches')
  } finally {
    if (!silent) loading.value = false
    schedulePoll()
  }
}

onMounted(() => fetchBatches())

onBeforeUnmount(() => {
  unmounted = true
  clearTimeout(pollTimer)
})
</script>

<template>
  <div class="batch-list">
    <div class="page-header">
      <div>
        <h2 class="page-title">
          Autopilot
          <el-tag v-if="batches.length > 0" type="info" size="small" class="count-tag">{{ batches.length }}</el-tag>
        </h2>
        <p class="page-subtitle">Batches of videos produced one after another, with optional publishing to YouTube.</p>
      </div>
      <button class="btn-primary-glow new-btn" @click="router.push('/autopilot/new')">
        <el-icon><Plus /></el-icon>
        New batch
      </button>
    </div>

    <div v-if="loading" class="rows">
      <div v-for="n in 3" :key="n" class="glass-card batch-row">
        <el-skeleton :rows="2" animated />
      </div>
    </div>

    <div v-else-if="loadError" class="glass-card state-card">
      <p>{{ loadError }}</p>
      <button class="btn-secondary" @click="fetchBatches()">Try again</button>
    </div>

    <div v-else-if="batches.length === 0" class="glass-card empty-card">
      <div class="empty-icon">
        <el-icon :size="36"><Timer /></el-icon>
      </div>
      <h3 class="empty-title">No batches yet</h3>
      <p class="empty-text">
        Add a list of topics or scripts and the app makes the videos one at a time.
        Each finished video can be published to your YouTube channel right away or on a schedule.
      </p>
      <button class="btn-primary-glow" @click="router.push('/autopilot/new')">
        <el-icon><Plus /></el-icon>
        New batch
      </button>
    </div>

    <div v-else class="rows">
      <div
        v-for="batch in sorted"
        :key="batch.id"
        class="glass-card glass-card-hover batch-row"
        role="link"
        tabindex="0"
        @click="router.push(`/autopilot/${batch.id}`)"
        @keyup.enter="router.push(`/autopilot/${batch.id}`)"
      >
        <div class="row-info">
          <div class="row-title-line">
            <span class="row-title">{{ batch.name }}</span>
            <StatusChip :status="batch.status" kind="batch" />
            <span v-if="batch.publish_enabled" class="publish-badge">
              <el-icon><Upload /></el-icon> Auto-publish
            </span>
            <span v-if="batch.hands_off" class="publish-badge hands-off">
              <el-icon><Moon /></el-icon> Hands-off
            </span>
          </div>
          <div class="row-meta">
            Created {{ formatDate(batch.created_at) }} · {{ batch.total }} video{{ batch.total === 1 ? '' : 's' }}
          </div>
        </div>
        <div class="row-progress">
          <BatchProgress :counts="batch.counts" :total="batch.total" compact />
        </div>
        <el-icon class="row-arrow"><ArrowRight /></el-icon>
      </div>
    </div>
  </div>
</template>

<style scoped>
.batch-list {
  max-width: 1000px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 24px;
  flex-wrap: wrap;
}

.page-title {
  font-size: 24px;
  font-weight: 700;
  color: var(--ce-text-primary);
}

.count-tag {
  margin-left: 8px;
  vertical-align: middle;
}

.page-subtitle {
  margin-top: 6px;
  font-size: 14px;
  color: var(--ce-text-muted);
}

.new-btn {
  padding: 12px 22px;
}

.rows {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.batch-row {
  display: flex;
  align-items: center;
  gap: 24px;
  padding: 18px 22px;
  border-radius: var(--ce-radius-md);
  cursor: pointer;
}

.batch-row:focus-visible {
  outline: 2px solid var(--ce-accent-light);
  outline-offset: 2px;
}

.row-info {
  flex: 1;
  min-width: 0;
}

.row-title-line {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.row-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--ce-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}

.publish-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  color: var(--ce-text-secondary);
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.12);
}

.publish-badge.hands-off {
  color: var(--ce-accent-light);
  background: rgba(62, 230, 168, 0.1);
  border-color: rgba(62, 230, 168, 0.3);
}

.row-meta {
  margin-top: 6px;
  font-size: 12px;
  color: var(--ce-text-muted);
}

.row-progress {
  width: 320px;
  flex-shrink: 0;
}

.row-arrow {
  color: var(--ce-text-muted);
  flex-shrink: 0;
}

.state-card {
  padding: 32px;
}

.state-card p {
  color: var(--ce-text-secondary);
  margin-bottom: 16px;
}

.empty-card {
  padding: 48px 32px;
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.empty-icon {
  width: 72px;
  height: 72px;
  border-radius: 50%;
  background: rgba(62, 230, 168, 0.1);
  color: var(--ce-accent-light);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 18px;
}

.empty-title {
  font-size: 18px;
  font-weight: 700;
  color: var(--ce-text-primary);
  margin-bottom: 8px;
}

.empty-text {
  max-width: 460px;
  font-size: 14px;
  line-height: 1.6;
  color: var(--ce-text-muted);
  margin-bottom: 24px;
}

@media (max-width: 768px) {
  .batch-row {
    flex-direction: column;
    align-items: stretch;
    gap: 14px;
  }

  .row-progress {
    width: 100%;
  }

  .row-arrow {
    display: none;
  }
}
</style>
