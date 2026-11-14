<script setup>
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getBatch,
  startBatch,
  pauseBatch,
  cancelBatch,
  deleteBatch,
  retryBatchItem,
  deleteBatchItem,
} from '../api'
import StatusChip from '../components/batch/StatusChip.vue'
import BatchProgress from '../components/batch/BatchProgress.vue'
import BatchItemRow from '../components/batch/BatchItemRow.vue'
import BatchAddItemsDialog from '../components/batch/BatchAddItemsDialog.vue'
import { apiErrorMessage, formatDate, hasScheduledRetry, isItemActive } from '../components/batch/batchUtils'

const POLL_MS = 4000
const RETRY_WAIT_POLL_MS = 30000 // only waiting on a scheduled retry: poll gently

const props = defineProps({
  id: { type: String, required: true },
})

const router = useRouter()

const batch = ref(null)
const loading = ref(true)
const loadError = ref('')
const action = ref(null) // name of the batch-level action in flight
const busyItemId = ref(null)
const addDialog = ref(false)

let pollTimer = null
let unmounted = false

const items = computed(() => [...(batch.value?.items || [])].sort((a, b) => a.position - b.position))
const status = computed(() => batch.value?.status)
const counts = computed(() => batch.value?.counts || {})
const hasActiveItem = computed(() => items.value.some(isItemActive))
const hasPendingRetry = computed(() => items.value.some((item) => hasScheduledRetry(item)))
const shouldPoll = computed(() => status.value === 'running' || hasActiveItem.value || hasPendingRetry.value)

const canStart = computed(() => {
  if (['draft', 'paused'].includes(status.value)) return true
  return ['completed', 'cancelled'].includes(status.value) && (counts.value.pending || 0) > 0
})
const startLabel = computed(() => (status.value === 'paused' ? 'Resume' : 'Start'))
const canPause = computed(() => status.value === 'running')
const canCancel = computed(() => ['draft', 'running', 'paused'].includes(status.value))

const settingsSummary = computed(() => {
  const s = batch.value?.settings || {}
  const parts = []
  if (s.orientation) parts.push(s.orientation.charAt(0).toUpperCase() + s.orientation.slice(1))
  if (s.duration_seconds) {
    const m = Math.floor(s.duration_seconds / 60)
    const sec = s.duration_seconds % 60
    parts.push(m ? `${m} min${sec ? ` ${sec}s` : ''} per topic` : `${sec}s per topic`)
  }
  if (s.narration_style) parts.push(s.narration_style)
  parts.push(s.subtitles_enabled === false ? 'no subtitles' : 'subtitles')
  return parts.join(' · ')
})

const publishSummary = computed(() => {
  const p = batch.value?.publish
  if (!batch.value?.publish_enabled && !p?.enabled) return 'Not published automatically'
  const parts = ['Publishes to YouTube']
  if (p?.privacy_status) parts.push(p.privacy_status)
  const sched = p?.schedule
  if (sched?.mode === 'interval' && sched.interval_minutes) {
    const mins = sched.interval_minutes
    parts.push(mins % 60 === 0 ? `every ${mins / 60} h` : `every ${mins} min`)
  } else {
    parts.push('as soon as ready')
  }
  return parts.join(' · ')
})

function clearPoll() {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

function schedulePoll() {
  clearPoll()
  if (unmounted || !shouldPoll.value) return
  const busy = status.value === 'running' || hasActiveItem.value
  pollTimer = setTimeout(() => fetchBatch({ silent: true }), busy ? POLL_MS : RETRY_WAIT_POLL_MS)
}

function applyBatch(data) {
  batch.value = data
  schedulePoll()
}

async function fetchBatch({ silent = false } = {}) {
  if (!silent) loading.value = true
  try {
    const { data } = await getBatch(props.id)
    if (unmounted) return
    loadError.value = ''
    applyBatch(data)
  } catch (err) {
    if (unmounted) return
    if (!silent || !batch.value) {
      loadError.value = err.response?.status === 404 ? 'This batch does not exist.' : apiErrorMessage(err, 'Failed to load batch')
    }
    // Keep polling through transient errors if the batch was active.
    if (batch.value) schedulePoll()
  } finally {
    if (!silent) loading.value = false
  }
}

async function runAction(name, fn, successMessage) {
  action.value = name
  try {
    const { data } = await fn(props.id)
    applyBatch(data)
    if (successMessage) ElMessage.success(successMessage)
  } catch (err) {
    ElMessage.error(apiErrorMessage(err, `Failed to ${name} batch`))
  } finally {
    action.value = null
  }
}

const handleStart = () => runAction('start', startBatch, status.value === 'paused' ? 'Batch resumed' : 'Batch started')
const handlePause = () =>
  runAction('pause', pauseBatch, 'Batch paused. The video in progress will finish first.')

async function handleCancel() {
  try {
    await ElMessageBox.confirm(
      'Cancel this batch? Queued videos will not be produced. Finished videos are kept.',
      'Cancel batch',
      { type: 'warning', confirmButtonText: 'Cancel batch', cancelButtonText: 'Keep batch', confirmButtonClass: 'el-button--danger' }
    )
  } catch {
    return
  }
  runAction('cancel', cancelBatch, 'Batch cancelled')
}

async function handleDelete() {
  try {
    await ElMessageBox.confirm(
      `Delete "${batch.value.name}"? The queue is removed. Videos already in your library are kept.`,
      'Delete batch',
      { type: 'warning', confirmButtonText: 'Delete', cancelButtonText: 'Cancel', confirmButtonClass: 'el-button--danger' }
    )
  } catch {
    return
  }
  action.value = 'delete'
  try {
    await deleteBatch(props.id)
    ElMessage.success('Batch deleted')
    router.push('/autopilot')
  } catch (err) {
    ElMessage.error(apiErrorMessage(err, 'Failed to delete batch'))
  } finally {
    action.value = null
  }
}

async function handleRetry(item) {
  busyItemId.value = item.id
  try {
    const { data } = await retryBatchItem(props.id, item.id)
    applyBatch(data)
    ElMessage.success('Video queued again')
  } catch (err) {
    ElMessage.error(apiErrorMessage(err, 'Failed to retry video'))
  } finally {
    busyItemId.value = null
  }
}

async function handleRemove(item) {
  busyItemId.value = item.id
  try {
    await deleteBatchItem(props.id, item.id)
    await fetchBatch({ silent: true })
  } catch (err) {
    ElMessage.error(apiErrorMessage(err, 'Failed to remove video'))
  } finally {
    busyItemId.value = null
  }
}

watch(
  () => props.id,
  () => {
    clearPoll()
    batch.value = null
    fetchBatch()
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  unmounted = true
  clearPoll()
})
</script>

<template>
  <div class="batch-detail">
    <div class="page-header">
      <button class="btn-secondary back-btn" @click="router.push('/autopilot')">
        <el-icon><ArrowLeft /></el-icon>
        All batches
      </button>
    </div>

    <div v-if="loading && !batch" class="glass-card state-card">
      <el-skeleton :rows="5" animated />
    </div>

    <div v-else-if="loadError && !batch" class="glass-card state-card error-state">
      <p>{{ loadError }}</p>
      <div class="state-actions">
        <button class="btn-secondary" @click="fetchBatch()">Try again</button>
        <button class="btn-secondary" @click="router.push('/autopilot')">Back to Autopilot</button>
      </div>
    </div>

    <template v-else-if="batch">
      <div class="glass-card header-card">
        <div class="header-top">
          <div class="header-info">
            <div class="title-row">
              <h2 class="page-title">{{ batch.name }}</h2>
              <StatusChip :status="batch.status" kind="batch" />
              <span
                v-if="batch.hands_off"
                class="hands-off-badge"
                title="Retries failures automatically, waits out YouTube's daily limit, and never stops to ask."
              >
                <el-icon><Moon /></el-icon> Hands-off
              </span>
            </div>
            <p class="summary-line">
              Created {{ formatDate(batch.created_at) }} · {{ batch.total }} video{{ batch.total === 1 ? '' : 's' }}
              <template v-if="settingsSummary"> · {{ settingsSummary }}</template>
            </p>
            <p class="summary-line">
              <el-icon class="inline-icon"><Upload /></el-icon>
              {{ publishSummary }}
            </p>
          </div>

          <div class="header-actions">
            <el-button v-if="canStart" type="primary" :loading="action === 'start'" :disabled="!!action" @click="handleStart">
              <el-icon class="btn-icon"><VideoPlay /></el-icon>{{ startLabel }}
            </el-button>
            <el-button v-if="canPause" type="primary" plain :loading="action === 'pause'" :disabled="!!action" @click="handlePause">
              <el-icon class="btn-icon"><VideoPause /></el-icon>Pause
            </el-button>
            <el-button type="primary" plain :disabled="!!action" @click="addDialog = true">
              <el-icon class="btn-icon"><Plus /></el-icon>Add videos
            </el-button>
            <el-button v-if="canCancel" type="danger" plain :loading="action === 'cancel'" :disabled="!!action" @click="handleCancel">
              Cancel
            </el-button>
            <el-button type="danger" plain :loading="action === 'delete'" :disabled="!!action" @click="handleDelete">
              <el-icon><Delete /></el-icon>
            </el-button>
          </div>
        </div>

        <BatchProgress :counts="batch.counts" :total="batch.total" class="header-progress" />

        <p v-if="batch.status === 'paused' && hasActiveItem" class="note">
          Paused. The video in progress will finish, then the queue stops.
        </p>
        <p v-if="batch.status === 'draft'" class="note">This batch is a draft. Press Start to begin producing videos.</p>
      </div>

      <div class="glass-card items-card">
        <div class="items-head">
          <h3 class="section-title">Queue</h3>
          <span v-if="shouldPoll" class="live-indicator">
            <span class="live-dot"></span> Updating
          </span>
        </div>
        <div v-if="items.length === 0" class="empty-items">
          No videos in this batch. Use "Add videos" to queue some.
        </div>
        <BatchItemRow
          v-for="(item, index) in items"
          :key="item.id"
          :item="item"
          :number="index + 1"
          :busy="busyItemId === item.id"
          @retry="handleRetry"
          @remove="handleRemove"
        />
      </div>
    </template>

    <BatchAddItemsDialog v-if="batch" v-model="addDialog" :batch-id="id" @added="applyBatch" />
  </div>
</template>

<style scoped>
.batch-detail {
  max-width: 1000px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: 20px;
}

.back-btn {
  padding: 8px 16px;
  font-size: 13px;
}

.state-card {
  padding: 32px;
}

.error-state p {
  color: var(--ce-text-secondary);
  margin-bottom: 16px;
}

.state-actions {
  display: flex;
  gap: 12px;
}

.header-card {
  padding: 28px 32px;
  margin-bottom: 20px;
}

.header-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 20px;
  flex-wrap: wrap;
}

.header-info {
  min-width: 0;
  flex: 1;
}

.title-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 6px;
}

.hands-off-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  color: var(--ce-accent-light);
  background: rgba(62, 230, 168, 0.12);
  border: 1px solid rgba(62, 230, 168, 0.35);
  white-space: nowrap;
}

.page-title {
  font-size: 24px;
  font-weight: 700;
  color: var(--ce-text-primary);
  word-break: break-word;
}

.summary-line {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--ce-text-muted);
  margin-top: 4px;
  flex-wrap: wrap;
}

.inline-icon {
  flex-shrink: 0;
}

.header-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.header-actions .el-button + .el-button {
  margin-left: 0;
}

.btn-icon {
  margin-right: 4px;
}

.header-progress {
  margin-top: 24px;
}

.note {
  margin-top: 14px;
  font-size: 13px;
  color: var(--ce-text-secondary);
}

.items-card {
  padding: 8px 0;
  margin-bottom: 40px;
}

.items-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 18px;
  border-bottom: 1px solid var(--ce-border-subtle);
}

.section-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--ce-text-primary);
}

.live-indicator {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--ce-text-muted);
}

.live-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ce-accent-light);
  animation: ce-pulse 1.6s ease-in-out infinite;
}

.empty-items {
  padding: 28px 18px;
  text-align: center;
  font-size: 13px;
  color: var(--ce-text-muted);
}

@media (max-width: 640px) {
  .header-card {
    padding: 20px;
  }
}
</style>
