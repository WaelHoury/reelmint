<script setup>
import { ref, onMounted, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { generateScript, updateScript, confirmScript } from '../api'

const props = defineProps({
  sessionId: { type: String, required: true },
  sessionData: { type: Object, required: true },
})

const emit = defineEmits(['confirmed', 'back'])

const loading = ref(false)
const confirming = ref(false)
const saving = ref(false)
const scriptText = ref('')
const wordCount = ref(0)
const estimatedDuration = ref(0)
const generated = ref(false)
const edited = ref(false)

const estimatedMinutes = computed(() => {
  return (estimatedDuration.value / 60).toFixed(1)
})

onMounted(async () => {
  // If session already has a script (e.g. script input type), show it directly
  if (props.sessionData?.script_text) {
    scriptText.value = props.sessionData.script_text
    wordCount.value = props.sessionData.word_count || scriptText.value.split(/\s+/).filter(Boolean).length
    estimatedDuration.value = props.sessionData.estimated_duration_seconds || 0
    generated.value = true
    return
  }
  // Otherwise, generate script from prompt
  await handleGenerate()
})

async function handleGenerate() {
  loading.value = true
  try {
    const { data } = await generateScript(props.sessionId)
    scriptText.value = data.script_text
    wordCount.value = data.word_count
    estimatedDuration.value = data.estimated_duration_seconds
    generated.value = true
    edited.value = false
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || 'Failed to generate script')
  } finally {
    loading.value = false
  }
}

async function handleSave() {
  saving.value = true
  try {
    const { data } = await updateScript(props.sessionId, scriptText.value)
    wordCount.value = data.word_count
    estimatedDuration.value = data.estimated_duration_seconds
    edited.value = false
    ElMessage.success('Script saved')
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || 'Failed to save script')
  } finally {
    saving.value = false
  }
}

async function handleConfirm() {
  // Save first if edited
  if (edited.value) {
    await handleSave()
  }

  confirming.value = true
  try {
    const { data } = await confirmScript(props.sessionId)
    emit('confirmed', {
      script_text: scriptText.value,
      word_count: wordCount.value,
      estimated_duration_seconds: estimatedDuration.value,
    })
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || 'Failed to confirm script')
  } finally {
    confirming.value = false
  }
}

function onScriptInput() {
  edited.value = true
  wordCount.value = scriptText.value.split(/\s+/).filter(Boolean).length
}
</script>

<template>
  <div>
    <h2 class="step-title">Review Script</h2>

    <!-- Loading state -->
    <div v-if="loading" class="loading-state">
      <el-icon class="is-loading" :size="32" color="var(--ce-purple-light)"><Loading /></el-icon>
      <p class="loading-text">Generating script from prompt...</p>
    </div>

    <!-- Script editor -->
    <div v-else-if="generated">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom: 16px"
      >
        Review and edit the narration script below. The video will use this text exactly as shown.
      </el-alert>

      <!-- Stats -->
      <div class="script-stats">
        <el-tag type="info">{{ wordCount }} words</el-tag>
        <el-tag type="info">~{{ estimatedMinutes }} min</el-tag>
        <el-tag v-if="edited" type="warning">Unsaved changes</el-tag>
      </div>

      <!-- Editor -->
      <el-input
        v-model="scriptText"
        type="textarea"
        :rows="14"
        @input="onScriptInput"
        style="margin-bottom: 16px"
      />

      <!-- Actions -->
      <div class="actions">
        <el-button @click="emit('back')">
          <el-icon><ArrowLeft /></el-icon> Back
        </el-button>

        <div class="actions-right">
          <el-button
            v-if="props.sessionData?.input_type === 'prompt'"
            @click="handleGenerate"
            :loading="loading"
          >
            <el-icon><Refresh /></el-icon> Regenerate
          </el-button>

          <el-button
            v-if="edited"
            @click="handleSave"
            :loading="saving"
          >
            Save Changes
          </el-button>

          <el-button
            type="primary"
            @click="handleConfirm"
            :loading="confirming"
            :disabled="!scriptText.trim()"
          >
            Confirm Script
            <el-icon class="el-icon--right"><ArrowRight /></el-icon>
          </el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.step-title {
  margin-bottom: 24px;
  font-size: 22px;
  font-weight: 700;
  color: var(--ce-text-primary);
}

.loading-state {
  text-align: center;
  padding: 48px 0;
}

.loading-text {
  margin-top: 12px;
  color: var(--ce-text-muted);
}

.script-stats {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.actions-right {
  display: flex;
  gap: 8px;
}
</style>
