<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { createSession, getStyles } from '../api'

const props = defineProps({
  mode: { type: String, default: 'longform' },
})

const emit = defineEmits(['created'])

const loading = ref(false)
const styles = ref([])
const titleError = ref('')

const isShorts = computed(() => props.mode === 'shorts')

const form = ref({
  title: '',
  inputType: 'prompt',
  prompt: '',
  script: '',
  narrationStyle: 'creative',
  durationSeconds: 30,
  durationUnit: 'seconds',
  loopMode: false,
  subtitlesEnabled: true,
  orientation: 'landscape',
})

// Set defaults based on mode
watch(() => props.mode, (mode) => {
  if (mode === 'shorts') {
    form.value.orientation = 'portrait'
    form.value.durationSeconds = 30
    form.value.durationUnit = 'seconds'
  } else {
    form.value.orientation = 'landscape'
    form.value.durationSeconds = 60
    form.value.durationUnit = 'seconds'
  }
}, { immediate: true })

watch(() => form.value.title, () => {
  if (titleError.value) {
    titleError.value = ''
  }
})

onMounted(async () => {
  try {
    const { data } = await getStyles()
    styles.value = data.styles
  } catch {
    styles.value = [
      { id: 'viral', name: 'Viral', description: 'Hook viewers with punchy facts' },
      { id: 'informative', name: 'Informative', description: 'Expert teacher who makes complex topics simple' },
      { id: 'funny', name: 'Funny', description: 'Comedian who makes every topic hilarious' },
      { id: 'storytelling', name: 'Storytelling', description: 'Master storyteller who turns topics into narratives' },
      { id: 'motivational', name: 'Motivational', description: 'Inspiring coach who empowers viewers' },
      { id: 'creative', name: 'Creative', description: 'Curious overthinker having a late-night epiphany' },
    ]
  }
})

const orientationOptions = computed(() => {
  if (isShorts.value) {
    return [
      { value: 'portrait', label: 'Portrait', icon: 'Iphone' },
      { value: 'square', label: 'Square', icon: 'Grid' },
    ]
  }
  return [
    { value: 'landscape', label: 'Landscape', icon: 'Monitor' },
    { value: 'portrait', label: 'Portrait', icon: 'Iphone' },
    { value: 'square', label: 'Square', icon: 'Grid' },
  ]
})

const durationMin = computed(() => isShorts.value ? 15 : 10)
const durationMax = computed(() => {
  if (isShorts.value) return 60
  return form.value.durationUnit === 'minutes' ? 10 : 600
})
const durationStep = computed(() => {
  if (isShorts.value) return 5
  return form.value.durationUnit === 'minutes' ? 0.5 : 10
})

const durationInSeconds = computed(() => {
  if (form.value.durationUnit === 'minutes') {
    return form.value.durationSeconds * 60
  }
  return form.value.durationSeconds
})

const canSubmit = computed(() => {
  if (!form.value.title.trim()) return false
  if (form.value.inputType === 'prompt' && !form.value.prompt.trim()) return false
  if (form.value.inputType === 'script' && !form.value.script.trim()) return false
  return true
})

async function handleSubmit() {
  if (!canSubmit.value) return

  titleError.value = ''
  loading.value = true
  try {
    const payload = {
      title: form.value.title.trim(),
      input_type: form.value.inputType,
      loop_mode: form.value.loopMode,
      subtitles_enabled: form.value.subtitlesEnabled,
      orientation: form.value.orientation,
    }

    if (form.value.inputType === 'prompt') {
      payload.prompt = form.value.prompt.trim()
      payload.duration_seconds = durationInSeconds.value
      payload.narration_style = form.value.narrationStyle
    } else {
      payload.script = form.value.script.trim()
      if (form.value.durationSeconds) {
        payload.duration_seconds = durationInSeconds.value
      }
    }

    const { data } = await createSession(payload)
    emit('created', data)
  } catch (err) {
    const detail = err.response?.data?.detail
    if (err.response?.status === 409 && detail?.code === 'DUPLICATE_TITLE') {
      titleError.value = detail.message || 'A video with this title already exists. Choose a different title.'
      return
    }
    const message = typeof detail === 'string' ? detail : detail?.message
    ElMessage.error(message || 'Failed to create session')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div>
    <h2 class="step-title">Video Settings</h2>

    <el-form label-position="top" @submit.prevent="handleSubmit">
      <!-- Title -->
      <el-form-item label="Project Title" :error="titleError">
        <el-input
          v-model="form.title"
          placeholder="e.g. How Bees Make Honey"
          maxlength="200"
          show-word-limit
        />
      </el-form-item>

      <!-- Input Type -->
      <el-form-item label="Input Type">
        <el-radio-group v-model="form.inputType">
          <el-radio-button value="prompt">
            <el-icon><MagicStick /></el-icon> Prompt (AI generates script)
          </el-radio-button>
          <el-radio-button value="script">
            <el-icon><Document /></el-icon> Script (use as-is)
          </el-radio-button>
        </el-radio-group>
      </el-form-item>

      <!-- Prompt Input -->
      <el-form-item v-if="form.inputType === 'prompt'" label="Prompt">
        <el-input
          v-model="form.prompt"
          type="textarea"
          :rows="4"
          placeholder="Describe the video topic. e.g. Explain how bees make honey in a fun, engaging way..."
        />
      </el-form-item>

      <!-- Narration Style (only for prompt mode) -->
      <el-form-item v-if="form.inputType === 'prompt'" label="Narration Style">
        <el-select v-model="form.narrationStyle" style="width: 100%">
          <el-option
            v-for="style in styles"
            :key="style.id"
            :value="style.id"
            :label="style.name"
          >
            <div class="style-option">
              <span class="style-name">{{ style.name }}</span>
              <span class="style-desc">{{ style.description }}</span>
            </div>
          </el-option>
        </el-select>
      </el-form-item>

      <!-- Script Input -->
      <el-form-item v-if="form.inputType === 'script'" label="Narration Script">
        <el-input
          v-model="form.script"
          type="textarea"
          :rows="8"
          placeholder="Paste your narration script here. It will be used exactly as written..."
        />
        <div class="helper-text" v-if="form.script">
          {{ form.script.split(/\s+/).filter(Boolean).length }} words
        </div>
      </el-form-item>

      <!-- Duration -->
      <el-form-item label="Duration">
        <div class="duration-row">
          <el-input-number
            v-model="form.durationSeconds"
            :min="durationMin"
            :max="durationMax"
            :step="durationStep"
          />
          <el-select
            v-if="!isShorts"
            v-model="form.durationUnit"
            style="width: 120px"
          >
            <el-option label="Seconds" value="seconds" />
            <el-option label="Minutes" value="minutes" />
          </el-select>
          <span v-else class="helper-text" style="margin-top: 0">seconds</span>
        </div>
        <div class="helper-text" v-if="form.inputType === 'script'">
          Optional for scripts — duration is auto-calculated from word count if not set
        </div>
      </el-form-item>

      <!-- Orientation -->
      <el-form-item label="Orientation">
        <el-radio-group v-model="form.orientation">
          <el-radio-button
            v-for="opt in orientationOptions"
            :key="opt.value"
            :value="opt.value"
          >
            <el-icon><component :is="opt.icon" /></el-icon> {{ opt.label }}
          </el-radio-button>
        </el-radio-group>
      </el-form-item>

      <!-- Options Row -->
      <el-form-item label="Options">
        <div class="options-row">
          <el-checkbox v-model="form.subtitlesEnabled">Subtitles</el-checkbox>
          <el-checkbox v-model="form.loopMode">Loop Mode</el-checkbox>
        </div>
      </el-form-item>

      <!-- Submit -->
      <el-form-item>
        <button
          type="button"
          class="btn-primary-glow submit-btn"
          :disabled="!canSubmit || loading"
          @click="handleSubmit"
        >
          <el-icon v-if="loading" class="is-loading"><Loading /></el-icon>
          <el-icon v-else><ArrowRight /></el-icon>
          {{ loading ? 'Creating...' : 'Create Session & Continue' }}
        </button>
      </el-form-item>
    </el-form>
  </div>
</template>

<style scoped>
.step-title {
  margin-bottom: 24px;
  font-size: 22px;
  font-weight: 700;
  color: var(--ce-text-primary);
}

.helper-text {
  font-size: 12px;
  color: var(--ce-text-muted);
  margin-top: 4px;
}

.duration-row {
  display: flex;
  gap: 12px;
  align-items: center;
}

.options-row {
  display: flex;
  gap: 24px;
}

.style-option {
  display: flex;
  flex-direction: column;
  padding: 4px 0;
}

.style-name {
  font-weight: 600;
  color: var(--ce-text-primary);
}

.style-desc {
  font-size: 12px;
  color: var(--ce-text-muted);
  line-height: 1.4;
}

.submit-btn {
  width: 100%;
  padding: 16px;
  font-size: 15px;
}
</style>
