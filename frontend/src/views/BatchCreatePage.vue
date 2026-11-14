<script setup>
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { createBatch } from '../api'
import BatchItemsInput from '../components/batch/BatchItemsInput.vue'
import BatchVideoSettings from '../components/batch/BatchVideoSettings.vue'
import BatchPublishSettings from '../components/batch/BatchPublishSettings.vue'
import { apiErrorMessage, defaultBatchName, parseItems } from '../components/batch/batchUtils'
import { buildPublishPayload, createPublishForm, validatePublishForm } from '../components/batch/publishForm'

const router = useRouter()

const name = ref(defaultBatchName())
const inputMode = ref('prompt')
const topicsText = ref('')
const scriptsText = ref('')
const aiTopicsText = ref('')
const niche = ref('')
const aiCount = ref(10)
const handsOff = ref(false)

const settings = ref({
  orientation: 'portrait',
  durationSeconds: 60,
  narrationStyle: 'informative',
  subtitlesEnabled: true,
  subtitleStyle: 'tiktok',
  loopMode: false,
  voiceKey: '',
})
const voiceState = ref({ loading: true, required: false })

const publish = ref(createPublishForm())
const submitting = ref(null) // 'start' | 'draft' | null

const itemsText = computed(() => {
  if (inputMode.value === 'script') return scriptsText.value
  if (inputMode.value === 'ai') return aiTopicsText.value
  return topicsText.value
})

const items = computed(() => parseItems(inputMode.value, itemsText.value))

// AI mode with no previewed topics: the backend picks `count` topics for the niche itself.
const useTheme = computed(() => inputMode.value === 'ai' && items.value.length === 0 && !!niche.value.trim())
const videoCount = computed(() => (useTheme.value ? aiCount.value : items.value.length))

// BatchVideoSettings only distinguishes prompt vs script
const settingsInputMode = computed(() => (inputMode.value === 'script' ? 'script' : 'prompt'))

const validationError = computed(() => {
  if (items.value.length === 0 && !useTheme.value) {
    if (inputMode.value === 'ai') return 'Enter a niche, then suggest topics or create directly.'
    return inputMode.value === 'script' ? 'Add at least one script.' : 'Add at least one topic.'
  }
  if (voiceState.value.required && !settings.value.voiceKey) return 'Choose a narration voice.'
  return validatePublishForm(publish.value)
})

const canSubmit = computed(() => !voiceState.value.loading && !validationError.value && !submitting.value)

function buildPayload(start) {
  const s = settings.value
  const payloadSettings = {
    orientation: s.orientation,
    duration_seconds: s.durationSeconds,
    narration_style: s.narrationStyle,
    loop_mode: s.loopMode,
    subtitles_enabled: s.subtitlesEnabled,
    subtitle_style: s.subtitleStyle,
  }
  if (s.voiceKey) {
    payloadSettings.tts_voice_key = s.voiceKey
    payloadSettings.tts_provider = s.voiceKey.split(':')[0]
  }
  const payload = {
    name: name.value.trim() || defaultBatchName(),
    items: useTheme.value ? [] : items.value,
    settings: payloadSettings,
    publish: buildPublishPayload(publish.value),
    hands_off: handsOff.value,
    start,
  }
  if (useTheme.value) payload.theme = { niche: niche.value.trim(), count: aiCount.value }
  return payload
}

async function submit(start) {
  if (!canSubmit.value) {
    if (validationError.value) ElMessage.warning(validationError.value)
    return
  }
  submitting.value = start ? 'start' : 'draft'
  try {
    const { data } = await createBatch(buildPayload(start))
    ElMessage.success(start ? 'Batch created and started' : 'Batch saved as draft')
    router.push(`/autopilot/${data.id}`)
  } catch (err) {
    ElMessage.error(apiErrorMessage(err, 'Failed to create batch'))
  } finally {
    submitting.value = null
  }
}
</script>

<template>
  <div class="batch-create">
    <div class="page-header">
      <button class="btn-secondary back-btn" @click="router.push('/autopilot')">
        <el-icon><ArrowLeft /></el-icon>
        Back
      </button>
      <span class="ce-label mode-badge">Autopilot</span>
    </div>

    <h2 class="page-title">New batch</h2>
    <p class="page-subtitle">
      Queue several videos at once. They are produced one at a time and can be published to YouTube as each one finishes.
    </p>

    <section class="glass-card section">
      <el-form label-position="top" @submit.prevent>
        <el-form-item label="Batch name">
          <el-input v-model="name" maxlength="120" />
        </el-form-item>
      </el-form>
      <h3 class="section-title">Videos</h3>
      <BatchItemsInput
        v-model:mode="inputMode"
        v-model:topics-text="topicsText"
        v-model:scripts-text="scriptsText"
        v-model:ai-topics-text="aiTopicsText"
        v-model:niche="niche"
        v-model:ai-count="aiCount"
        allow-ai
        :suggest-style="settings.narrationStyle"
      />
    </section>

    <section class="glass-card section hands-off-card" :class="{ on: handsOff }">
      <div class="hands-off-row">
        <div class="hands-off-icon"><el-icon :size="20"><Moon /></el-icon></div>
        <div class="hands-off-copy">
          <label class="hands-off-title" for="hands-off-switch">Hands-off mode</label>
          <p class="hands-off-text">
            Retries failures automatically, waits out YouTube's daily limit, and never stops to ask. Safe to leave overnight.
          </p>
        </div>
        <el-switch id="hands-off-switch" v-model="handsOff" size="large" aria-label="Hands-off mode" />
      </div>
    </section>

    <section class="glass-card section">
      <h3 class="section-title">Video settings</h3>
      <p class="section-hint">Applied to every video in this batch.</p>
      <BatchVideoSettings
        v-model="settings"
        :input-mode="settingsInputMode"
        @voice-state="voiceState = $event"
      />
    </section>

    <section class="glass-card section">
      <h3 class="section-title">Publishing</h3>
      <BatchPublishSettings v-model="publish" :item-count="videoCount" />
    </section>

    <div class="actions">
      <p v-if="validationError" class="validation-text">{{ validationError }}</p>
      <div class="action-buttons">
        <button type="button" class="btn-secondary" :disabled="!canSubmit" @click="submit(false)">
          <el-icon v-if="submitting === 'draft'" class="is-loading"><Loading /></el-icon>
          <el-icon v-else><DocumentAdd /></el-icon>
          Save as draft
        </button>
        <button type="button" class="btn-primary-glow" :disabled="!canSubmit" @click="submit(true)">
          <el-icon v-if="submitting === 'start'" class="is-loading"><Loading /></el-icon>
          <el-icon v-else><VideoPlay /></el-icon>
          {{ videoCount > 0 ? `Create and start (${videoCount})` : 'Create and start' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.batch-create {
  max-width: 900px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 24px;
}

.back-btn {
  padding: 8px 16px;
  font-size: 13px;
}

.mode-badge {
  background: rgba(62, 230, 168, 0.1);
  color: var(--ce-accent-light);
  padding: 4px 12px;
  border-radius: 20px;
  font-size: 11px;
}

.page-title {
  font-size: 24px;
  font-weight: 700;
  color: var(--ce-text-primary);
  margin-bottom: 6px;
}

.page-subtitle {
  font-size: 14px;
  color: var(--ce-text-muted);
  margin-bottom: 24px;
  line-height: 1.5;
}

.section {
  padding: 28px 32px;
  margin-bottom: 20px;
}

.section-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--ce-text-primary);
  margin-bottom: 12px;
}

.section-hint {
  font-size: 12px;
  color: var(--ce-text-muted);
  margin: -6px 0 16px;
}

.hands-off-card {
  padding: 20px 24px;
  border-color: rgba(62, 230, 168, 0.18);
  transition: border-color var(--ce-transition-normal), background var(--ce-transition-normal);
}

.hands-off-card.on {
  border-color: rgba(62, 230, 168, 0.5);
  background: linear-gradient(135deg, rgba(62, 230, 168, 0.08), rgba(18, 179, 168, 0.03)), var(--ce-bg-card);
}

.hands-off-row {
  display: flex;
  align-items: center;
  gap: 16px;
}

.hands-off-icon {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(62, 230, 168, 0.1);
  color: var(--ce-accent-light);
}

.hands-off-copy {
  flex: 1;
  min-width: 0;
}

.hands-off-title {
  display: block;
  font-size: 15px;
  font-weight: 700;
  color: var(--ce-text-primary);
  margin-bottom: 2px;
  cursor: pointer;
}

.hands-off-text {
  font-size: 13px;
  line-height: 1.5;
  color: var(--ce-text-secondary);
}

.actions {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 10px;
  margin-bottom: 40px;
}

.action-buttons {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.btn-secondary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.validation-text {
  font-size: 13px;
  color: #fbbf24;
}

@media (max-width: 640px) {
  .section {
    padding: 20px;
  }

  .hands-off-card {
    padding: 16px;
  }

  .hands-off-row {
    gap: 12px;
    align-items: flex-start;
  }

  .hands-off-icon {
    display: none;
  }

  .actions {
    align-items: stretch;
  }

  .action-buttons {
    flex-direction: column-reverse;
  }
}
</style>
