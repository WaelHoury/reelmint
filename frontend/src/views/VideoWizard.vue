<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getSession } from '../api'
import StepSettings from '../components/StepSettings.vue'
import StepScript from '../components/StepScript.vue'
import StepClips from '../components/StepClips.vue'
import StepGenerate from '../components/StepGenerate.vue'

const STORAGE_KEY = 'wizard_state'

const props = defineProps({
  mode: { type: String, default: 'longform', validator: v => ['shorts', 'longform'].includes(v) },
})

const router = useRouter()
const activeStep = ref(0)
const sessionId = ref(null)
const sessionData = ref(null)
const restoring = ref(false)

function clearStoredWizardState() {
  sessionStorage.removeItem(STORAGE_KEY)
}

function inferStepFromStatus(status) {
  const map = {
    'draft': 0,
    'script_ready': 1,
    'clips_generating': 2,
    'clips_ready': 2,
    'generating': 3,
    'completed': 3,
    'failed': 3,
  }
  return map[status] ?? 0
}

// Restore wizard state from sessionStorage on mount
onMounted(async () => {
  const saved = sessionStorage.getItem(STORAGE_KEY)
  if (!saved) return

  try {
    const { sid } = JSON.parse(saved)
    if (!sid) return

    restoring.value = true
    const { data } = await getSession(sid)
    if (data.status === 'completed') {
      clearStoredWizardState()
      return
    }
    sessionId.value = sid
    sessionData.value = data
    activeStep.value = inferStepFromStatus(data.status)
  } catch {
    // Session expired or invalid — clear and start fresh
    clearStoredWizardState()
  } finally {
    restoring.value = false
  }
})

// Persist wizard state to sessionStorage whenever it changes
watch([sessionId, activeStep, sessionData], () => {
  if (sessionId.value && sessionData.value?.status !== 'completed') {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
      sid: sessionId.value,
      step: activeStep.value,
    }))
  } else {
    clearStoredWizardState()
  }
})

const steps = [
  { title: 'Settings', icon: 'Setting' },
  { title: 'Script', icon: 'Document' },
  { title: 'Clips', icon: 'Film' },
  { title: 'Generate', icon: 'VideoCamera' },
]

const modeLabel = computed(() => props.mode === 'shorts' ? 'Short Form' : 'Long Form')

function onSessionCreated(data) {
  sessionId.value = data.session_id
  sessionData.value = data
  activeStep.value = 1
  ElMessage.success('Session created')
}

function onScriptConfirmed(data) {
  sessionData.value = { ...sessionData.value, ...data }
  activeStep.value = 2
  ElMessage.success('Script confirmed')
}

function onClipsConfirmed(data) {
  sessionData.value = { ...sessionData.value, clips: data.clips }
  activeStep.value = 3
  ElMessage.success('Clips confirmed — ready to generate')
}

function onReset() {
  clearStoredWizardState()
  sessionId.value = null
  sessionData.value = null
  activeStep.value = 0
  router.push('/')
}

function onGenerationCompleted() {
  sessionData.value = { ...(sessionData.value || {}), status: 'completed' }
  clearStoredWizardState()
}
</script>

<template>
  <div class="wizard">
    <div class="wizard-header">
      <button class="btn-secondary wizard-back" @click="router.push('/')">
        <el-icon><ArrowLeft /></el-icon>
        Back
      </button>
      <span class="ce-label mode-badge">{{ modeLabel }}</span>
    </div>

    <el-steps :active="activeStep" finish-status="success" align-center class="wizard-steps">
      <el-step v-for="step in steps" :key="step.title" :title="step.title">
        <template #icon>
          <el-icon><component :is="step.icon" /></el-icon>
        </template>
      </el-step>
    </el-steps>

    <div class="wizard-content glass-card">
      <StepSettings
        v-if="activeStep === 0"
        :mode="mode"
        @created="onSessionCreated"
      />

      <StepScript
        v-else-if="activeStep === 1"
        :session-id="sessionId"
        :session-data="sessionData"
        @confirmed="onScriptConfirmed"
        @back="activeStep = 0"
      />

      <StepClips
        v-else-if="activeStep === 2"
        :session-id="sessionId"
        @confirmed="onClipsConfirmed"
        @back="activeStep = 1"
      />

      <StepGenerate
        v-else-if="activeStep === 3"
        :session-id="sessionId"
        :session-data="sessionData"
        @completed="onGenerationCompleted"
        @reset="onReset"
      />
    </div>
  </div>
</template>

<style scoped>
.wizard {
  max-width: 900px;
  margin: 0 auto;
}

.wizard-header {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 24px;
}

.wizard-back {
  padding: 8px 16px;
  font-size: 13px;
}

.mode-badge {
  background: rgba(167, 139, 250, 0.1);
  color: var(--ce-purple-light);
  padding: 4px 12px;
  border-radius: 20px;
  font-size: 11px;
}

.wizard-steps {
  margin-bottom: 28px;
}

.wizard-content {
  padding: 32px;
}
</style>
