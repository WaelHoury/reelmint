<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { startGeneration, getJobStatus, getVideoDownloadUrl } from '../api'

const props = defineProps({
  sessionId: { type: String, required: true },
  sessionData: { type: Object, default: null },
})

const emit = defineEmits(['reset'])

const status = ref('idle') // idle | queued | processing | completed | failed
const progressPercent = ref(0)
const currentStep = ref('')
const errorMessage = ref('')
const jobId = ref(null)
let pollTimer = null

onMounted(async () => {
  await handleGenerate()
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})

async function handleGenerate() {
  status.value = 'queued'
  progressPercent.value = 0
  currentStep.value = 'Starting video generation...'
  errorMessage.value = ''

  try {
    const { data } = await startGeneration(props.sessionId)
    jobId.value = data.job_id
    startPolling()
  } catch (err) {
    status.value = 'failed'
    errorMessage.value = err.response?.data?.detail || 'Failed to start generation'
    ElMessage.error(errorMessage.value)
  }
}

function startPolling() {
  pollTimer = setInterval(async () => {
    try {
      const { data } = await getJobStatus(props.sessionId)

      status.value = data.status
      progressPercent.value = data.progress_percent || 0
      currentStep.value = data.current_step || ''
      errorMessage.value = data.error_message || ''

      if (data.status === 'completed' || data.status === 'failed') {
        clearInterval(pollTimer)
        pollTimer = null

        if (data.status === 'completed') {
          ElMessage.success('Video generated successfully!')
        } else {
          ElMessage.error('Video generation failed')
        }
      }
    } catch {
      // Ignore poll errors, keep trying
    }
  }, 3000) // Poll every 3 seconds
}

function downloadVideo() {
  window.open(getVideoDownloadUrl(props.sessionId), '_blank')
}
</script>

<template>
  <div class="generate-step">
    <h2 style="margin-bottom: 24px">Generate Video</h2>

    <!-- Progress -->
    <div v-if="status === 'queued' || status === 'processing'" class="progress-section">
      <el-icon class="is-loading" :size="48" color="#409eff"><Loading /></el-icon>
      <h3 style="margin-top: 16px">{{ status === 'queued' ? 'Queued...' : 'Generating your video...' }}</h3>
      <p class="step-label">{{ currentStep }}</p>

      <el-progress
        :percentage="progressPercent"
        :stroke-width="12"
        :format="(p) => `${p}%`"
        style="margin-top: 24px; max-width: 500px; width: 100%"
      />

      <p class="hint">This may take a few minutes. Do not close this page.</p>
    </div>

    <!-- Completed -->
    <div v-else-if="status === 'completed'" class="result-section">
      <el-result icon="success" title="Video Ready!" sub-title="Your video has been generated successfully.">
        <template #extra>
          <div style="display: flex; gap: 12px; justify-content: center">
            <el-button type="primary" size="large" @click="downloadVideo">
              <el-icon><Download /></el-icon> Download Video
            </el-button>
            <el-button size="large" @click="emit('reset')">
              <el-icon><Plus /></el-icon> Create New Video
            </el-button>
          </div>
        </template>
      </el-result>
    </div>

    <!-- Failed -->
    <div v-else-if="status === 'failed'" class="result-section">
      <el-result icon="error" title="Generation Failed" :sub-title="errorMessage || 'An unknown error occurred.'">
        <template #extra>
          <div style="display: flex; gap: 12px; justify-content: center">
            <el-button type="primary" @click="handleGenerate">
              <el-icon><Refresh /></el-icon> Retry
            </el-button>
            <el-button @click="emit('reset')">
              Start Over
            </el-button>
          </div>
        </template>
      </el-result>
    </div>

    <!-- Idle (shouldn't normally show) -->
    <div v-else style="text-align: center; padding: 48px 0">
      <el-button type="primary" size="large" @click="handleGenerate">
        <el-icon><VideoCamera /></el-icon> Start Generation
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.generate-step {
  text-align: center;
}

.progress-section {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 48px 0;
}

.step-label {
  color: #909399;
  margin-top: 8px;
  font-size: 14px;
}

.hint {
  color: #c0c4cc;
  font-size: 12px;
  margin-top: 16px;
}

.result-section {
  padding: 24px 0;
}
</style>
