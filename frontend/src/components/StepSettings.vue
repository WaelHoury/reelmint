<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { createSession, getStyles } from '../api'

const emit = defineEmits(['created'])

const loading = ref(false)
const styles = ref([])

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

onMounted(async () => {
  try {
    const { data } = await getStyles()
    styles.value = data.styles
  } catch {
    // Fallback styles if API unavailable
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
      // Duration is auto-calculated from script word count
      if (form.value.durationSeconds) {
        payload.duration_seconds = durationInSeconds.value
      }
    }

    const { data } = await createSession(payload)
    emit('created', data)
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || 'Failed to create session')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div>
    <h2 style="margin-bottom: 24px">Video Settings</h2>

    <el-form label-position="top" @submit.prevent="handleSubmit">
      <!-- Title -->
      <el-form-item label="Video Title">
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
            <div style="display: flex; flex-direction: column; padding: 4px 0">
              <span style="font-weight: 600">{{ style.name }}</span>
              <span style="font-size: 12px; color: #909399; line-height: 1.4">{{ style.description }}</span>
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
        <div style="display: flex; gap: 12px; align-items: center">
          <el-input-number
            v-model="form.durationSeconds"
            :min="10"
            :max="form.durationUnit === 'minutes' ? 10 : 600"
            :step="form.durationUnit === 'minutes' ? 0.5 : 10"
          />
          <el-select v-model="form.durationUnit" style="width: 120px">
            <el-option label="Seconds" value="seconds" />
            <el-option label="Minutes" value="minutes" />
          </el-select>
        </div>
        <div class="helper-text" v-if="form.inputType === 'script'">
          Optional for scripts — duration is auto-calculated from word count if not set
        </div>
      </el-form-item>

      <!-- Orientation -->
      <el-form-item label="Orientation">
        <el-radio-group v-model="form.orientation">
          <el-radio-button value="landscape">
            <el-icon><Monitor /></el-icon> Landscape
          </el-radio-button>
          <el-radio-button value="portrait">
            <el-icon><Iphone /></el-icon> Portrait
          </el-radio-button>
          <el-radio-button value="square">
            <el-icon><Grid /></el-icon> Square
          </el-radio-button>
        </el-radio-group>
      </el-form-item>

      <!-- Options Row -->
      <el-form-item label="Options">
        <div style="display: flex; gap: 24px">
          <el-checkbox v-model="form.subtitlesEnabled">Subtitles</el-checkbox>
          <el-checkbox v-model="form.loopMode">Loop Mode</el-checkbox>
        </div>
      </el-form-item>

      <!-- Submit -->
      <el-form-item>
        <el-button
          type="primary"
          size="large"
          :loading="loading"
          :disabled="!canSubmit"
          @click="handleSubmit"
        >
          Create Session & Continue
          <el-icon class="el-icon--right"><ArrowRight /></el-icon>
        </el-button>
      </el-form-item>
    </el-form>
  </div>
</template>

<style scoped>
.helper-text {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}
</style>
