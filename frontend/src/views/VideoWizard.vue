<script setup>
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import StepSettings from '../components/StepSettings.vue'
import StepScript from '../components/StepScript.vue'
import StepClips from '../components/StepClips.vue'
import StepGenerate from '../components/StepGenerate.vue'

const activeStep = ref(0)
const sessionId = ref(null)
const sessionData = ref(null)

const steps = [
  { title: 'Settings', icon: 'Setting' },
  { title: 'Script', icon: 'Document' },
  { title: 'Clips', icon: 'Film' },
  { title: 'Generate', icon: 'VideoCamera' },
]

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
  activeStep.value = 0
  sessionId.value = null
  sessionData.value = null
}
</script>

<template>
  <div class="wizard">
    <el-steps :active="activeStep" finish-status="success" align-center class="wizard-steps">
      <el-step v-for="step in steps" :key="step.title" :title="step.title">
        <template #icon>
          <el-icon><component :is="step.icon" /></el-icon>
        </template>
      </el-step>
    </el-steps>

    <div class="wizard-content">
      <StepSettings
        v-if="activeStep === 0"
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

.wizard-steps {
  margin-bottom: 32px;
}

.wizard-content {
  background: #fff;
  border-radius: 8px;
  padding: 32px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06);
}
</style>
