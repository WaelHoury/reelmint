<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  startGeneration, getJobStatus, getVideoDownloadUrl, getVideoStreamUrl, updateSubtitles,
  checkActiveGeneration, getSessionEditorData, apiErrorMessage,
} from '../api'

const router = useRouter()

const props = defineProps({
  sessionId: { type: String, required: true },
  sessionData: { type: Object, default: null },
})

const emit = defineEmits(['completed', 'reset'])

const status = ref('idle') // idle | queued | processing | completed | failed
const progressPercent = ref(0)
const currentStep = ref('')
const errorMessage = ref('')
const jobId = ref(null)
// 1-based place among waiting renders (the render in progress is ahead of #1)
const queuePosition = ref(null)
let pollTimer = null

// Video player
const videoKey = ref(0) // cache-busting key for video reload

// Subtitle editor
const showSubtitleEditor = ref(false)
const subtitleLoading = ref(false)
const subtitleForm = ref({
  style: 'youtube',
  font: 'Arial',
  font_size: 52,
  color: '#FFFFFF',
  outline_color: '#000000',
  position: 'bottom',
})
// Fields the user set; only these are sent, the rest follow the preset for
// this video's orientation (as in the editor)
const SUBTITLE_OVERRIDE_KEYS = ['font', 'font_size', 'color', 'outline_color', 'position']
const subtitleOverrides = ref(new Set())
// Preset values for this video's orientation, from the server
const subtitlePresets = ref(null)
const subtitleStateLoading = ref(false)
const subtitleStateLoaded = ref(false)
// Bumped to re-sync a colour picker after it was cleared
const pickerKey = ref(0)

// Live CSS subtitle preview
const previewEnabled = ref(true)
const previewText = ref('This is a subtitle preview')

const fontOptions = [
  'Arial',
  'Montserrat-Bold',
  'Helvetica',
  'Comic Sans MS',
  'Impact',
  'Verdana',
  'Georgia',
  'Times New Roman',
]

const videoStreamSrc = computed(() => {
  return getVideoStreamUrl(props.sessionId) + `?t=${videoKey.value}`
})

// Scale FFmpeg font size (designed for 1920w) to ~720px player width
const previewStyle = computed(() => {
  const f = subtitleForm.value
  const scaledSize = Math.round(f.font_size * 0.375)
  const oc = f.outline_color || '#000000'
  // 8-direction text-shadow to simulate outline
  const shadow = [
    `1px 1px 2px ${oc}`, `-1px -1px 2px ${oc}`,
    `1px -1px 2px ${oc}`, `-1px 1px 2px ${oc}`,
    `0 2px 2px ${oc}`, `0 -2px 2px ${oc}`,
    `2px 0 2px ${oc}`, `-2px 0 2px ${oc}`,
  ].join(', ')

  const style = {
    fontFamily: f.font,
    fontSize: `${scaledSize}px`,
    color: f.color || '#FFFFFF',
    textShadow: shadow,
    pointerEvents: 'none',
    position: 'absolute',
    left: '50%',
    transform: 'translateX(-50%)',
    textAlign: 'center',
    maxWidth: '90%',
    fontWeight: '700',
    lineHeight: '1.3',
  }

  if (f.position === 'top') {
    style.top = '10%'
  } else if (f.position === 'center') {
    style.top = '50%'
    style.transform = 'translate(-50%, -50%)'
  } else {
    style.bottom = '15%'
  }

  return style
})

onMounted(async () => {
  // Check if generation is already running (handles page refresh)
  try {
    const { data } = await getJobStatus(props.sessionId)
    if ((data.status === 'processing' || data.status === 'queued') && data.job_id) {
      // Already generating with a real job — resume polling without re-POSTing
      status.value = data.status
      progressPercent.value = data.progress_percent || 0
      currentStep.value = data.current_step || ''
      jobId.value = data.job_id
      queuePosition.value = data.queue_position ?? null
      startPolling()
      return
    }
    if (data.status === 'completed') {
      status.value = 'completed'
      emit('completed')
      return
    }
    if (data.status === 'failed') {
      status.value = 'failed'
      errorMessage.value = data.error_message || 'Generation failed'
      return
    }
  } catch {
    // No job yet — proceed to generate
  }

  await handleGenerate()
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})

async function handleGenerate() {
  // Multi-tab guard: check if user already has an active generation elsewhere
  try {
    const { data: active } = await checkActiveGeneration()
    if (active.session_id && active.session_id !== props.sessionId) {
      status.value = 'failed'
      errorMessage.value = 'You already have a video generating in another session. Please wait for it to finish.'
      ElMessage.warning(errorMessage.value)
      return
    }
  } catch {
    // Proceed if check fails
  }

  status.value = 'queued'
  progressPercent.value = 0
  currentStep.value = 'Starting video generation...'
  errorMessage.value = ''

  try {
    const { data } = await startGeneration(props.sessionId)
    jobId.value = data.job_id
    queuePosition.value = data.queue_position ?? null
    startPolling()
  } catch (err) {
    status.value = 'failed'
    errorMessage.value = apiErrorMessage(err, 'Failed to start generation')
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
      queuePosition.value = data.queue_position ?? null

      if (data.status === 'completed' || data.status === 'failed') {
        clearInterval(pollTimer)
        pollTimer = null

        if (data.status === 'completed') {
          emit('completed')
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

// Landscape fallback only; the server sends each preset for this video's orientation
const FALLBACK_PRESETS = {
  youtube: { font: 'Arial', font_size: 52, color: '#FFFFFF', outline_color: '#000000', position: 'bottom' },
  tiktok: { font: 'Montserrat-Bold', font_size: 64, color: '#FFFFFF', outline_color: '#000000', position: 'center' },
  minimal: { font: 'Helvetica', font_size: 44, color: '#FFFFFF', outline_color: '#000000', position: 'bottom' },
}

function presetValues(name) {
  return subtitlePresets.value?.[name] || FALLBACK_PRESETS[name] || FALLBACK_PRESETS.youtube
}

// Current style, values and overrides, so the form starts from what the video shows
async function loadSubtitleState() {
  if (subtitleStateLoaded.value || subtitleStateLoading.value) return
  subtitleStateLoading.value = true
  try {
    const { data } = await getSessionEditorData(props.sessionId)
    const subs = data.subtitles || {}
    if (subs.style) subtitleForm.value.style = subs.style
    for (const key of SUBTITLE_OVERRIDE_KEYS) {
      if (subs[key] !== null && subs[key] !== undefined && subs[key] !== '') subtitleForm.value[key] = subs[key]
    }
    subtitleOverrides.value = new Set(subs.overrides || [])
    subtitlePresets.value = subs.presets || null
    subtitleStateLoaded.value = true
  } catch {
    // Keep the defaults; Apply still sends only the fields the user changes
  } finally {
    subtitleStateLoading.value = false
  }
}

watch(showSubtitleEditor, (open) => {
  if (open) loadSubtitleState()
})

function setSubtitleField(key, value) {
  subtitleForm.value[key] = value
  subtitleOverrides.value = new Set(subtitleOverrides.value).add(key)
}

// Clearing a picker emits null: fall back to the preset's colour, no override
function setSubtitleColor(key, value) {
  if (value) {
    setSubtitleField(key, value.toUpperCase())
    return
  }
  subtitleForm.value[key] = presetValues(subtitleForm.value.style)[key] || subtitleForm.value[key]
  const overrides = new Set(subtitleOverrides.value)
  overrides.delete(key)
  subtitleOverrides.value = overrides
  pickerKey.value++
}

function onStylePresetChange(preset) {
  // A preset was applied: every field now follows it
  Object.assign(subtitleForm.value, presetValues(preset))
  subtitleOverrides.value = new Set()
}

async function applySubtitleChanges() {
  subtitleLoading.value = true
  try {
    const f = subtitleForm.value
    const payload = { style: f.style }
    for (const key of subtitleOverrides.value) {
      if (f[key] !== null && f[key] !== undefined && f[key] !== '') payload[key] = f[key]
    }
    await updateSubtitles(props.sessionId, payload)
    // Reload the video player by bumping the cache key
    videoKey.value = Date.now()
    ElMessage.success('Subtitles updated!')
  } catch (err) {
    ElMessage.error(apiErrorMessage(err, 'Failed to update subtitles'))
  } finally {
    subtitleLoading.value = false
  }
}
</script>

<template>
  <div class="generate-step">
    <h2 class="step-title">Generate Video</h2>

    <!-- Progress -->
    <div v-if="status === 'queued' || status === 'processing'" class="progress-section">
      <div class="progress-icon ce-pulse">
        <el-icon :size="48" color="var(--ce-accent-light)"><Loading /></el-icon>
      </div>
      <h3 class="progress-heading">{{ status === 'queued' ? 'Queued...' : 'Generating your video...' }}</h3>
      <p v-if="status === 'queued' && queuePosition > 0" class="step-label">
        Waiting in queue: {{ queuePosition }} ahead
      </p>
      <p v-else class="step-label">{{ currentStep }}</p>

      <el-progress
        :percentage="progressPercent"
        :stroke-width="12"
        :format="(p) => `${p}%`"
        class="progress-bar"
      />

      <p class="hint">This may take a few minutes. Do not close this page.</p>
    </div>

    <!-- Completed -->
    <div v-else-if="status === 'completed'" class="result-section">
      <el-result icon="success" title="Video Ready!" sub-title="Your video has been generated successfully." />

      <!-- Video Player with CSS Subtitle Preview -->
      <div class="video-player-section">
        <div class="video-container">
          <video
            :key="videoKey"
            controls
            width="100%"
            class="video-player"
          >
            <source :src="videoStreamSrc" type="video/mp4">
            Your browser does not support the video tag.
          </video>
          <div
            v-if="showSubtitleEditor && previewEnabled"
            class="subtitle-preview"
            :style="previewStyle"
          >
            {{ previewText }}
          </div>
        </div>
      </div>

      <!-- Action Buttons -->
      <div class="action-buttons">
        <button type="button" class="btn-primary-glow action-btn action-btn-download" @click="downloadVideo">
          <el-icon><Download /></el-icon> Download Video
        </button>
        <button type="button" class="btn-secondary action-btn action-btn-edit" @click="router.push(`/editor/session/${props.sessionId}`)">
          <el-icon><Edit /></el-icon> Edit Video
        </button>
        <button type="button" class="btn-secondary action-btn" @click="showSubtitleEditor = !showSubtitleEditor">
          <el-icon><EditPen /></el-icon> {{ showSubtitleEditor ? 'Hide' : 'Customize' }} Subtitles
        </button>
        <button type="button" class="btn-secondary action-btn action-btn-new" @click="emit('reset')">
          <el-icon><Plus /></el-icon> Create New Video
        </button>
      </div>

      <!-- Subtitle Editor -->
      <el-collapse-transition>
        <div v-if="showSubtitleEditor" class="subtitle-editor">
          <div class="glass-card subtitle-card">
            <h3 class="subtitle-card-title">Customize Subtitles</h3>

            <el-form label-position="top" style="text-align: left">
              <!-- Live Preview Controls -->
              <div class="preview-controls">
                <el-switch v-model="previewEnabled" active-text="Live Preview" />
                <el-input
                  v-if="previewEnabled"
                  v-model="previewText"
                  placeholder="Preview text"
                  size="small"
                  style="flex: 1"
                />
              </div>

              <!-- Style Preset -->
              <el-form-item label="Style Preset">
                <el-select v-model="subtitleForm.style" @change="onStylePresetChange" style="width: 100%">
                  <el-option value="youtube" label="YouTube" />
                  <el-option value="tiktok" label="TikTok" />
                  <el-option value="minimal" label="Minimal" />
                </el-select>
              </el-form-item>

              <!-- Font -->
              <el-form-item label="Font">
                <el-select
                  :model-value="subtitleForm.font"
                  @update:model-value="setSubtitleField('font', $event)"
                  style="width: 100%"
                >
                  <el-option v-for="f in fontOptions" :key="f" :value="f" :label="f" />
                </el-select>
              </el-form-item>

              <!-- Font Size -->
              <el-form-item label="Font Size">
                <el-slider
                  :model-value="subtitleForm.font_size"
                  @update:model-value="setSubtitleField('font_size', $event)"
                  :min="24"
                  :max="96"
                  :step="2"
                  show-input
                  style="padding-right: 16px"
                />
              </el-form-item>

              <!-- Colors Row -->
              <div class="color-row">
                <el-form-item label="Text Color" style="flex: 1">
                  <el-color-picker
                    :key="'color-' + pickerKey"
                    :model-value="subtitleForm.color"
                    @update:model-value="setSubtitleColor('color', $event)"
                    color-format="hex"
                  />
                </el-form-item>
                <el-form-item label="Outline Color" style="flex: 1">
                  <el-color-picker
                    :key="'outline-' + pickerKey"
                    :model-value="subtitleForm.outline_color"
                    @update:model-value="setSubtitleColor('outline_color', $event)"
                    color-format="hex"
                  />
                </el-form-item>
              </div>

              <!-- Position -->
              <el-form-item label="Position">
                <el-radio-group
                  :model-value="subtitleForm.position"
                  @update:model-value="setSubtitleField('position', $event)"
                >
                  <el-radio-button value="bottom">Bottom</el-radio-button>
                  <el-radio-button value="center">Center</el-radio-button>
                  <el-radio-button value="top">Top</el-radio-button>
                </el-radio-group>
              </el-form-item>

              <!-- Apply Button -->
              <el-form-item>
                <el-button
                  type="primary"
                  :loading="subtitleLoading"
                  :disabled="subtitleStateLoading"
                  @click="applySubtitleChanges"
                >
                  Apply Changes
                </el-button>
              </el-form-item>
            </el-form>
          </div>
        </div>
      </el-collapse-transition>
    </div>

    <!-- Failed -->
    <div v-else-if="status === 'failed'" class="result-section">
      <el-result icon="error" title="Generation Failed" :sub-title="errorMessage || 'An unknown error occurred.'">
        <template #extra>
          <div class="action-buttons">
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
    <div v-else class="idle-state">
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

.step-title {
  margin-bottom: 24px;
  font-size: 22px;
  font-weight: 700;
  color: var(--ce-text-primary);
}

.progress-section {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 48px 0;
}

.progress-icon {
  margin-bottom: 16px;
}

.progress-heading {
  color: var(--ce-text-primary);
}

.step-label {
  color: var(--ce-text-muted);
  margin-top: 8px;
  font-size: 14px;
}

.progress-bar {
  margin-top: 24px;
  max-width: 500px;
  width: 100%;
}

.hint {
  color: var(--ce-text-muted);
  font-size: 12px;
  margin-top: 16px;
}

.result-section {
  padding: 24px 0;
}

.video-player-section {
  display: flex;
  justify-content: center;
  margin: 24px 0;
}

.video-container {
  position: relative;
  display: inline-block;
  max-width: 720px;
  width: 100%;
}

.video-player {
  max-width: 720px;
  border-radius: 12px;
  background: #000;
}

.subtitle-preview {
  pointer-events: none;
  user-select: none;
  z-index: 10;
}

.action-buttons {
  display: flex;
  gap: 12px;
  justify-content: center;
  margin-top: 20px;
  flex-wrap: wrap;
}

.action-btn {
  min-width: 180px;
  min-height: 46px;
  font-weight: 600;
}

/* Download = primary; Edit / Subtitles / New share the secondary style */

.subtitle-editor {
  max-width: 500px;
  margin: 24px auto 0;
}

.subtitle-card {
  padding: 24px;
}

.subtitle-card-title {
  font-weight: 600;
  font-size: 16px;
  color: var(--ce-text-primary);
  margin-bottom: 20px;
}

.preview-controls {
  display: flex;
  gap: 16px;
  align-items: center;
  margin-bottom: 16px;
}

.color-row {
  display: flex;
  gap: 24px;
}

.idle-state {
  text-align: center;
  padding: 48px 0;
}
</style>
