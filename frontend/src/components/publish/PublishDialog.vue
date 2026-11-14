<script setup>
import { ref, watch, computed, onBeforeUnmount } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getConnectedAccounts,
  publishVideo,
  getPublishJob,
  getPublishJobs,
  retryPublishJob,
  apiErrorMessage,
} from '../../api'

const props = defineProps({
  modelValue: Boolean,
  videoId: { type: Number, required: true },
  videoTitle: { type: String, default: '' },
})

const emit = defineEmits(['update:modelValue'])

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

// State
const accounts = ref([])
const selectedAccountId = ref(null)
const loadingAccounts = ref(false)
const publishing = ref(false)
const activeJob = ref(null)
const publishHistory = ref([])
const pollTimer = ref(null)

// YouTube metadata form
const metadata = ref({
  title: '',
  description: '',
  tags: [],
  privacy_status: 'private',
  made_for_kids: false,
})
const tagInput = ref('')

const selectedAccount = computed(() =>
  accounts.value.find((a) => a.id === selectedAccountId.value)
)

const activeAccounts = computed(() =>
  accounts.value.filter((a) => a.status === 'active')
)

// Load data when dialog opens
watch(visible, async (v) => {
  if (v) {
    metadata.value.title = props.videoTitle
    metadata.value.description = ''
    metadata.value.tags = []
    metadata.value.privacy_status = 'private'
    metadata.value.made_for_kids = false
    activeJob.value = null
    selectedAccountId.value = null
    await Promise.all([loadAccounts(), loadHistory()])
  } else {
    stopPolling()
  }
}, { immediate: true }) // the dialog is often mounted already open (v-if + v-model)

async function loadAccounts() {
  loadingAccounts.value = true
  try {
    const { data } = await getConnectedAccounts()
    accounts.value = data.accounts
    if (activeAccounts.value.length === 1) {
      selectedAccountId.value = activeAccounts.value[0].id
    }
  } catch {
    // ignore
  } finally {
    loadingAccounts.value = false
  }
}

async function loadHistory() {
  try {
    const { data } = await getPublishJobs(props.videoId)
    publishHistory.value = data.jobs
  } catch {
    // ignore
  }
}

async function handlePublish() {
  if (!selectedAccountId.value) {
    ElMessage.warning('Select an account first')
    return
  }
  publishing.value = true
  try {
    const { data } = await publishVideo(props.videoId, {
      account_id: selectedAccountId.value,
      mode: 'upload',
      metadata: {
        title: metadata.value.title,
        description: metadata.value.description,
        tags: metadata.value.tags,
        privacy_status: metadata.value.privacy_status,
        made_for_kids: metadata.value.made_for_kids,
      },
    })
    activeJob.value = data
    startPolling(data.id)
  } catch (err) {
    ElMessage.error(apiErrorMessage(err, 'Failed to start publish'))
  } finally {
    publishing.value = false
  }
}

function startPolling(jobId) {
  stopPolling()
  pollTimer.value = setInterval(async () => {
    try {
      const { data } = await getPublishJob(jobId)
      activeJob.value = data
      if (['published', 'draft_ready', 'failed', 'reconnect_required'].includes(data.status)) {
        stopPolling()
        loadHistory()
      }
    } catch {
      stopPolling()
    }
  }, 3000)
}

function stopPolling() {
  if (pollTimer.value) {
    clearInterval(pollTimer.value)
    pollTimer.value = null
  }
}

async function handleRetry(jobId) {
  try {
    const { data } = await retryPublishJob(jobId)
    activeJob.value = data
    startPolling(data.id)
  } catch (err) {
    ElMessage.error(apiErrorMessage(err, 'Failed to retry'))
  }
}

function addTag() {
  const tag = tagInput.value.trim()
  if (tag && !metadata.value.tags.includes(tag)) {
    metadata.value.tags.push(tag)
  }
  tagInput.value = ''
}

function removeTag(tag) {
  metadata.value.tags = metadata.value.tags.filter((t) => t !== tag)
}

function statusType(status) {
  const map = {
    published: 'success',
    draft_ready: 'success',
    uploading: 'primary',
    processing: 'primary',
    queued: 'info',
    failed: 'danger',
    reconnect_required: 'warning',
  }
  return map[status] || 'info'
}

function providerLabel(provider) {
  const labels = { youtube: 'YouTube', tiktok: 'TikTok', instagram: 'Instagram' }
  return labels[provider] || provider
}

onBeforeUnmount(stopPolling)
</script>

<template>
  <el-dialog
    v-model="visible"
    title="Share Video"
    width="540px"
    destroy-on-close
    :close-on-click-modal="false"
  >
    <!-- No accounts state -->
    <div v-if="!loadingAccounts && activeAccounts.length === 0 && !activeJob" class="empty-state">
      <p>No connected accounts. Connect YouTube on the <router-link to="/account">Account page</router-link> first.</p>
    </div>

    <!-- Active job status -->
    <div v-else-if="activeJob" class="job-status">
      <div class="job-status-header">
        <el-tag :type="statusType(activeJob.status)" size="large">
          {{ activeJob.status.replace('_', ' ') }}
        </el-tag>
        <span class="job-provider">{{ providerLabel(activeJob.provider) }}</span>
      </div>

      <div v-if="activeJob.status === 'published' && activeJob.platform_post_url" class="job-success">
        <p>Video published successfully!</p>
        <el-link type="primary" :href="activeJob.platform_post_url" target="_blank">
          View on {{ providerLabel(activeJob.provider) }}
        </el-link>
      </div>

      <div v-if="activeJob.status === 'uploading' || activeJob.status === 'queued'" class="job-progress">
        <el-progress :percentage="activeJob.status === 'uploading' ? 50 : 10" :indeterminate="true" />
        <p class="status-text">{{ activeJob.status === 'uploading' ? 'Uploading to YouTube...' : 'Waiting in queue...' }}</p>
      </div>

      <div v-if="activeJob.status === 'failed' || activeJob.status === 'reconnect_required'" class="job-error">
        <p class="error-text">{{ activeJob.error_message || 'Publishing failed' }}</p>
        <el-button
          v-if="activeJob.status === 'failed'"
          type="primary"
          size="small"
          @click="handleRetry(activeJob.id)"
        >
          Retry
        </el-button>
        <router-link v-if="activeJob.status === 'reconnect_required'" to="/account">
          <el-button type="warning" size="small">Reconnect Account</el-button>
        </router-link>
      </div>

      <el-button class="new-publish-btn" text @click="activeJob = null">
        New publish
      </el-button>
    </div>

    <!-- Publish form -->
    <div v-else-if="activeAccounts.length > 0" class="publish-form">
      <!-- Account selection -->
      <el-form-item label="Account">
        <el-select v-model="selectedAccountId" placeholder="Select account" style="width: 100%">
          <el-option
            v-for="account in activeAccounts"
            :key="account.id"
            :value="account.id"
            :label="`${providerLabel(account.provider)} — ${account.display_name}`"
          />
        </el-select>
      </el-form-item>

      <!-- YouTube metadata -->
      <template v-if="selectedAccount?.provider === 'youtube'">
        <el-form-item label="Title">
          <el-input v-model="metadata.title" maxlength="100" show-word-limit />
        </el-form-item>

        <el-form-item label="Description">
          <el-input
            v-model="metadata.description"
            type="textarea"
            :rows="3"
            maxlength="5000"
            placeholder="Optional"
          />
        </el-form-item>

        <el-form-item label="Tags">
          <div class="tags-input">
            <el-tag
              v-for="tag in metadata.tags"
              :key="tag"
              closable
              @close="removeTag(tag)"
              class="tag-item"
            >
              {{ tag }}
            </el-tag>
            <el-input
              v-model="tagInput"
              size="small"
              placeholder="Add tag + Enter"
              style="width: 140px"
              @keyup.enter="addTag"
            />
          </div>
        </el-form-item>

        <el-form-item label="Privacy">
          <el-select v-model="metadata.privacy_status" style="width: 100%">
            <el-option value="private" label="Private" />
            <el-option value="unlisted" label="Unlisted" />
            <el-option value="public" label="Public" />
          </el-select>
        </el-form-item>

        <el-form-item label="Made for kids">
          <el-switch v-model="metadata.made_for_kids" />
        </el-form-item>
      </template>

      <el-button
        type="primary"
        :loading="publishing"
        :disabled="!selectedAccountId"
        @click="handlePublish"
        style="width: 100%"
      >
        Publish to {{ selectedAccount ? providerLabel(selectedAccount.provider) : '...' }}
      </el-button>
    </div>

    <!-- Publish history -->
    <div v-if="publishHistory.length > 0 && !activeJob" class="publish-history">
      <h4 class="history-title">Previous Publishes</h4>
      <div v-for="job in publishHistory" :key="job.id" class="history-item">
        <div class="history-info">
          <el-tag :type="statusType(job.status)" size="small">{{ job.status.replace('_', ' ') }}</el-tag>
          <span class="history-provider">{{ providerLabel(job.provider) }}</span>
          <span class="history-date">{{ new Date(job.created_at).toLocaleDateString() }}</span>
        </div>
        <div class="history-actions">
          <el-link
            v-if="job.platform_post_url"
            type="primary"
            :href="job.platform_post_url"
            target="_blank"
            :underline="false"
          >
            View
          </el-link>
          <el-button
            v-if="job.status === 'failed'"
            size="small"
            text
            @click="handleRetry(job.id)"
          >
            Retry
          </el-button>
        </div>
      </div>
    </div>
  </el-dialog>
</template>

<style scoped>
.empty-state {
  text-align: center;
  padding: 24px;
  color: var(--ce-text-muted);
}

.empty-state a {
  color: var(--ce-accent);
}

.publish-form {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.tags-input {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.tag-item {
  margin: 0;
}

.job-status {
  text-align: center;
  padding: 16px 0;
}

.job-status-header {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  margin-bottom: 16px;
}

.job-provider {
  font-size: 14px;
  color: var(--ce-text-muted);
}

.job-success {
  margin: 16px 0;
}

.job-progress {
  margin: 16px 0;
}

.status-text {
  margin-top: 8px;
  color: var(--ce-text-muted);
  font-size: 13px;
}

.job-error {
  margin: 16px 0;
}

.error-text {
  color: var(--el-color-danger);
  font-size: 13px;
  margin-bottom: 12px;
}

.new-publish-btn {
  margin-top: 12px;
}

.publish-history {
  margin-top: 24px;
  border-top: 1px solid var(--ce-border-subtle);
  padding-top: 16px;
}

.history-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--ce-text-primary);
  margin-bottom: 12px;
}

.history-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 0;
  border-bottom: 1px solid var(--ce-border-subtle);
}

.history-item:last-child {
  border-bottom: none;
}

.history-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.history-provider {
  font-size: 13px;
  color: var(--ce-text-primary);
}

.history-date {
  font-size: 12px;
  color: var(--ce-text-muted);
}

.history-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
</style>
