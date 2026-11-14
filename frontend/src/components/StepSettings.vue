<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { createSession, getStyles, getVoiceCatalog, getVoicePreview, apiErrorMessage } from '../api'

const props = defineProps({
  mode: { type: String, default: 'longform' },
})

const emit = defineEmits(['created'])

const loading = ref(false)
const styles = ref([])
const titleError = ref('')
const catalogLoading = ref(true)

const isShorts = computed(() => props.mode === 'shorts')

// --- Voice catalog ---
const voiceCatalog = ref([])
const enabledProviders = ref([])
const defaultVoiceKey = ref('')
const selectedVoiceKey = ref('')
const previewAudio = ref(null)
const previewObjectUrl = ref(null)
const playingVoiceKey = ref(null)
const previewLoadingVoiceKey = ref(null)
let previewRequestId = 0

const groupedVoices = computed(() => {
  const providerOrder = new Map(enabledProviders.value.map((provider, index) => [provider, index]))
  const groups = {}
  const sorted = [...voiceCatalog.value].sort((a, b) => {
    const providerCompare = (providerOrder.get(a.provider) ?? 99) - (providerOrder.get(b.provider) ?? 99)
    if (providerCompare !== 0) return providerCompare
    const priorityCompare = getVoicePriority(a) - getVoicePriority(b)
    if (priorityCompare !== 0) return priorityCompare
    return a.label.localeCompare(b.label)
  })
  for (const v of sorted) {
    if (!groups[v.provider]) groups[v.provider] = []
    groups[v.provider].push(v)
  }
  return groups
})

const hasFishEnabled = computed(() => enabledProviders.value.includes('fish'))
const fishVoiceCount = computed(() => voiceCatalog.value.filter((voice) => voice.provider === 'fish').length)
const showVoiceSelector = computed(() =>
  catalogLoading.value || enabledProviders.value.length > 0 || voiceCatalog.value.length > 0
)
const showFishSetupHint = computed(() => hasFishEnabled.value && fishVoiceCount.value === 0)

const selectedVoice = computed(() =>
  voiceCatalog.value.find((v) => v.voice_key === selectedVoiceKey.value)
)

function getVoicePriority(voice) {
  const tags = new Set(voice.tags || [])
  if (voice.provider === 'fish') {
    if (tags.has('default')) return 0
    if (tags.has('official')) return 1
    if (voice.is_cloned) return 2
    if (tags.has('preset')) return 3
    if (tags.has('public')) return 4
  }
  if (voice.is_cloned) return 10
  return 20
}

function stopPreview(targetVoiceKey = null) {
  previewRequestId += 1
  if (previewAudio.value) {
    previewAudio.value.oncanplay = null
    previewAudio.value.onplaying = null
    previewAudio.value.onended = null
    previewAudio.value.onerror = null
    previewAudio.value.pause()
    previewAudio.value.src = ''
    previewAudio.value = null
  }
  if (previewObjectUrl.value) {
    URL.revokeObjectURL(previewObjectUrl.value)
    previewObjectUrl.value = null
  }
  if (!targetVoiceKey || playingVoiceKey.value === targetVoiceKey) {
    playingVoiceKey.value = null
  }
  if (!targetVoiceKey || previewLoadingVoiceKey.value === targetVoiceKey) {
    previewLoadingVoiceKey.value = null
  }
}

function voiceDescription(voice) {
  if (voice.description) return voice.description
  if (voice.is_cloned) return 'Private Fish voice clone for this account.'
  if (voice.grade) return voice.grade
  return 'Narration voice'
}

function voiceBadges(voice) {
  const tags = new Set(voice.tags || [])
  const badges = []
  if (tags.has('default')) badges.push('Default')
  else if (tags.has('official')) badges.push('Official')
  if (voice.is_cloned) badges.push('Clone')
  return badges
}

function voiceMeta(voice) {
  const meta = []
  if (voice.language) meta.push(String(voice.language).toUpperCase())
  if (voice.grade) meta.push(voice.grade)
  for (const tag of voice.tags || []) {
    if (['default', 'official', 'sample', 'public', 'private', 'cloned'].includes(tag)) continue
    meta.push(tag)
  }
  return meta.slice(0, 4)
}

async function playPreview(voiceKey) {
  if (previewLoadingVoiceKey.value === voiceKey) {
    return
  }
  if (playingVoiceKey.value === voiceKey && previewAudio.value) {
    stopPreview(voiceKey)
    return
  }

  stopPreview()
  const requestId = previewRequestId
  previewLoadingVoiceKey.value = voiceKey

  try {
    const { data } = await getVoicePreview(voiceKey)
    if (requestId !== previewRequestId) {
      return
    }

    const objectUrl = URL.createObjectURL(data)
    const audio = new Audio(objectUrl)
    previewAudio.value = audio
    previewObjectUrl.value = objectUrl

    const clearLoading = () => {
      if (previewLoadingVoiceKey.value === voiceKey) {
        previewLoadingVoiceKey.value = null
      }
    }

    audio.oncanplay = clearLoading
    audio.onplaying = () => {
      clearLoading()
      playingVoiceKey.value = voiceKey
    }
    audio.onended = () => {
      clearLoading()
      if (playingVoiceKey.value === voiceKey) {
        playingVoiceKey.value = null
      }
    }
    audio.onerror = () => {
      clearLoading()
      if (playingVoiceKey.value === voiceKey) {
        playingVoiceKey.value = null
      }
      if (previewAudio.value === audio) {
        previewAudio.value = null
      }
      if (previewObjectUrl.value === objectUrl) {
        URL.revokeObjectURL(objectUrl)
        previewObjectUrl.value = null
      }
      ElMessage.warning('Preview unavailable for this voice')
    }

    await audio.play()
    clearLoading()
    playingVoiceKey.value = voiceKey
  } catch {
    if (requestId !== previewRequestId) {
      return
    }
    if (previewLoadingVoiceKey.value === voiceKey) {
      previewLoadingVoiceKey.value = null
    }
    if (previewAudio.value) {
      previewAudio.value = null
    }
    if (previewObjectUrl.value) {
      URL.revokeObjectURL(previewObjectUrl.value)
      previewObjectUrl.value = null
    }
    ElMessage.warning('Preview unavailable for this voice')
  }
}

onBeforeUnmount(() => {
  stopPreview()
})

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
  catalogLoading.value = true
  // Load styles and voice catalog in parallel
  const stylesPromise = getStyles().catch(() => ({
    data: {
      styles: [
        { id: 'viral', name: 'Viral', description: 'Hook viewers with punchy facts' },
        { id: 'informative', name: 'Informative', description: 'Expert teacher who makes complex topics simple' },
        { id: 'funny', name: 'Funny', description: 'Comedian who makes every topic hilarious' },
        { id: 'storytelling', name: 'Storytelling', description: 'Master storyteller who turns topics into narratives' },
        { id: 'motivational', name: 'Motivational', description: 'Inspiring coach who empowers viewers' },
        { id: 'creative', name: 'Creative', description: 'Curious overthinker having a late-night epiphany' },
      ],
    },
  }))

  const catalogPromise = getVoiceCatalog().catch(() => {
    ElMessage.warning('Could not load narration voices right now')
    return {
      data: { voices: [], enabled_providers: [], default_voice_key: '' },
    }
  })

  try {
    const [stylesRes, catalogRes] = await Promise.all([stylesPromise, catalogPromise])
    styles.value = stylesRes.data.styles
    voiceCatalog.value = catalogRes.data.voices
    enabledProviders.value = catalogRes.data.enabled_providers
    defaultVoiceKey.value = catalogRes.data.default_voice_key
    if (voiceCatalog.value.some((voice) => voice.voice_key === defaultVoiceKey.value)) {
      selectedVoiceKey.value = defaultVoiceKey.value
    } else if (voiceCatalog.value.length > 0) {
      selectedVoiceKey.value = voiceCatalog.value[0].voice_key
    } else {
      selectedVoiceKey.value = ''
    }
  } finally {
    catalogLoading.value = false
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

// Shorts: 15 s to 3 min. Long-form: 10 s to 10 min (0.5 to 10 in minutes)
const SHORTS_MAX_SECONDS = 180
const LONGFORM_MAX_SECONDS = 600
const durationMin = computed(() => {
  if (isShorts.value) return 15
  return form.value.durationUnit === 'minutes' ? 0.5 : 10
})
const durationMax = computed(() => {
  if (isShorts.value) return SHORTS_MAX_SECONDS
  return form.value.durationUnit === 'minutes' ? LONGFORM_MAX_SECONDS / 60 : LONGFORM_MAX_SECONDS
})
const durationStep = computed(() => {
  if (isShorts.value) return 5
  return form.value.durationUnit === 'minutes' ? 0.5 : 10
})

// The backend takes whole seconds
const durationInSeconds = computed(() => {
  if (form.value.durationUnit === 'minutes') {
    return Math.round(form.value.durationSeconds * 60)
  }
  return form.value.durationSeconds
})

// Keep the same length when the unit changes (60 seconds <-> 1 minute)
function onDurationUnitChange(unit) {
  const value = form.value.durationSeconds
  if (!value) return
  if (unit === 'minutes') {
    const minutes = Math.round((value / 60) * 2) / 2
    form.value.durationSeconds = Math.min(LONGFORM_MAX_SECONDS / 60, Math.max(0.5, minutes))
  } else {
    form.value.durationSeconds = Math.min(LONGFORM_MAX_SECONDS, Math.max(10, Math.round(value * 60)))
  }
}

const canSubmit = computed(() => {
  if (catalogLoading.value) return false
  if (!form.value.title.trim()) return false
  if (form.value.inputType === 'prompt' && !form.value.prompt.trim()) return false
  if (form.value.inputType === 'script' && !form.value.script.trim()) return false
  if (enabledProviders.value.length > 0 && !selectedVoiceKey.value) return false
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

    // TTS selection
    if (selectedVoiceKey.value) {
      const voice = selectedVoice.value
      const provider = selectedVoiceKey.value.split(':')[0]
      payload.tts_selection = {
        provider,
        voice_key: selectedVoiceKey.value,
        label: voice ? voice.label : selectedVoiceKey.value,
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
    ElMessage.error(apiErrorMessage(err, 'Failed to create session'))
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
            @change="onDurationUnitChange"
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

      <!-- Voice Selection -->
      <el-form-item label="Narration Voice" v-if="showVoiceSelector">
        <div class="voice-selector">
          <div v-if="catalogLoading" class="voice-loading-state">
            <el-icon class="voice-loading-spinner is-loading"><Loading /></el-icon>
            <div>
              <div class="voice-empty-title">Loading narration voices...</div>
              <p class="voice-empty-copy">
                Fetching official Fish starter voices and any private voice clones linked to this account.
              </p>
            </div>
          </div>

          <template v-else-if="voiceCatalog.length > 0">
            <div
              v-for="(providerVoices, providerName) in groupedVoices"
              :key="providerName"
              class="voice-provider-group"
            >
              <div class="provider-label">{{ providerName }}</div>
              <div v-if="providerName === 'fish'" class="fish-voice-grid">
                <div
                  v-for="voice in providerVoices"
                  :key="voice.voice_key"
                  class="fish-voice-card"
                  :class="{
                    selected: selectedVoiceKey === voice.voice_key,
                    loading: previewLoadingVoiceKey === voice.voice_key,
                  }"
                  @click="selectedVoiceKey = voice.voice_key"
                >
                  <div class="fish-voice-card-header">
                    <div class="fish-voice-copy">
                      <div class="fish-voice-title-row">
                        <span class="fish-voice-title">{{ voice.label }}</span>
                        <span
                          v-for="badge in voiceBadges(voice)"
                          :key="`${voice.voice_key}-${badge}`"
                          class="fish-badge"
                        >
                          {{ badge }}
                        </span>
                      </div>
                      <p class="fish-voice-description">{{ voiceDescription(voice) }}</p>
                    </div>
                    <button
                      type="button"
                      class="fish-preview-btn"
                      :disabled="previewLoadingVoiceKey === voice.voice_key"
                      @click.stop="playPreview(voice.voice_key)"
                    >
                      <el-icon v-if="previewLoadingVoiceKey === voice.voice_key" class="is-loading">
                        <Loading />
                      </el-icon>
                      <el-icon v-else>
                        <VideoPause v-if="playingVoiceKey === voice.voice_key" />
                        <VideoPlay v-else />
                      </el-icon>
                    </button>
                  </div>

                  <div v-if="voiceMeta(voice).length > 0" class="fish-voice-meta">
                    <span
                      v-for="item in voiceMeta(voice)"
                      :key="`${voice.voice_key}-${item}`"
                      class="fish-meta-pill"
                    >
                      {{ item }}
                    </span>
                  </div>

                  <div class="fish-voice-footer">
                    <span v-if="selectedVoiceKey === voice.voice_key" class="fish-selected-label">
                      Selected for narration
                    </span>
                    <span v-else class="fish-select-label">Click to use this voice</span>
                  </div>

                  <div v-if="previewLoadingVoiceKey === voice.voice_key" class="fish-preview-loading">
                    <el-icon class="is-loading"><Loading /></el-icon>
                    <span>Loading preview...</span>
                  </div>
                </div>
              </div>

              <div v-else class="voice-options">
                <div
                  v-for="voice in providerVoices"
                  :key="voice.voice_key"
                  class="voice-option"
                  :class="{
                    selected: selectedVoiceKey === voice.voice_key,
                    cloned: voice.is_cloned,
                  }"
                  @click="selectedVoiceKey = voice.voice_key"
                >
                  <div class="voice-option-info">
                    <span class="voice-option-label">{{ voice.label }}</span>
                    <span v-if="voice.gender" class="voice-option-meta">{{ voice.gender }}</span>
                    <el-tag v-if="voice.is_cloned" size="small" type="info">Clone</el-tag>
                  </div>
                  <button
                    type="button"
                    class="voice-preview-btn"
                    @click.stop="playPreview(voice.voice_key)"
                    :disabled="previewLoadingVoiceKey === voice.voice_key"
                    :title="playingVoiceKey === voice.voice_key ? 'Stop' : 'Preview'"
                  >
                    <el-icon :size="14">
                      <Loading v-if="previewLoadingVoiceKey === voice.voice_key" class="is-loading" />
                      <VideoPause v-else-if="playingVoiceKey === voice.voice_key" />
                      <VideoPlay v-else />
                    </el-icon>
                  </button>
                </div>
              </div>
            </div>

            <div v-if="showFishSetupHint" class="voice-inline-hint">
              Fish is enabled, but no Fish voices are available right now. You can still create a
              private clone below.
            </div>

            <div v-if="hasFishEnabled" class="voice-create-link">
              <router-link :to="{ path: '/voices', query: { returnTo: $route.fullPath } }">
                + Create a custom Fish voice clone
              </router-link>
            </div>
          </template>

          <div v-else class="voice-empty-state">
            <div class="voice-empty-title">No narration voices available yet</div>
            <p v-if="hasFishEnabled" class="voice-empty-copy">
              Fish is enabled, but the Fish voice catalog is empty right now. You can create a
              private Fish clone to continue.
            </p>
            <p v-else class="voice-empty-copy">
              Enable at least one TTS provider in `.env` to choose a narration voice.
            </p>
            <div v-if="hasFishEnabled" class="voice-create-link">
              <router-link :to="{ path: '/voices', query: { returnTo: $route.fullPath } }">
                Create a private Fish voice
              </router-link>
            </div>
          </div>
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

/* Voice selector */
.voice-selector {
  width: 100%;
}

.voice-empty-state,
.voice-loading-state {
  width: 100%;
  padding: 18px;
  border: 1px dashed var(--ce-border-input);
  border-radius: var(--ce-radius-sm);
  background: rgba(255, 255, 255, 0.03);
}

.voice-loading-state {
  display: flex;
  align-items: center;
  gap: 14px;
}

.voice-loading-spinner {
  font-size: 22px;
  color: var(--ce-accent-light);
  flex-shrink: 0;
}

.voice-empty-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--ce-text-primary);
}

.voice-empty-copy {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 1.5;
  color: var(--ce-text-secondary);
}

.voice-provider-group {
  margin-bottom: 12px;
}

.provider-label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--ce-text-muted);
  margin-bottom: 6px;
  padding-left: 2px;
}

.fish-voice-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 12px;
}

.fish-voice-card {
  position: relative;
  border: 1px solid var(--ce-border-subtle);
  border-radius: var(--ce-radius-sm);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.03), rgba(255, 255, 255, 0.015));
  padding: 14px;
  cursor: pointer;
  transition: border-color var(--ce-transition-fast), transform var(--ce-transition-fast), box-shadow var(--ce-transition-fast);
}

.fish-voice-card:hover {
  border-color: var(--ce-border-input);
  transform: translateY(-1px);
}

.fish-voice-card.selected {
  border-color: var(--ce-accent-light);
  box-shadow: 0 0 0 1px rgba(62, 230, 168, 0.28);
  background: rgba(62, 230, 168, 0.08);
}

.fish-voice-card.loading {
  pointer-events: none;
}

.fish-voice-card-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.fish-voice-copy {
  min-width: 0;
}

.fish-voice-title-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.fish-voice-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--ce-text-primary);
}

.fish-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(62, 230, 168, 0.12);
  color: var(--ce-accent-light);
  font-size: 11px;
  font-weight: 600;
}

.fish-voice-description {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 1.5;
  color: var(--ce-text-secondary);
}

.fish-preview-btn {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  border: 1px solid var(--ce-border-input);
  background: transparent;
  color: var(--ce-text-secondary);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all var(--ce-transition-fast);
  flex-shrink: 0;
}

.fish-preview-btn:hover:not(:disabled) {
  background: var(--ce-bg-hover);
  color: var(--ce-text-primary);
}

.fish-preview-btn:disabled,
.voice-preview-btn:disabled {
  opacity: 0.7;
  cursor: progress;
}

.fish-voice-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 12px;
}

.fish-meta-pill {
  display: inline-flex;
  align-items: center;
  padding: 4px 8px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.05);
  color: var(--ce-text-muted);
  font-size: 11px;
}

.fish-voice-footer {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}

.fish-preview-loading {
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: rgba(0, 0, 0, 0.75);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--ce-text-primary);
  font-size: 13px;
  font-weight: 600;
}

.fish-selected-label,
.fish-select-label {
  font-size: 12px;
  font-weight: 600;
}

.fish-selected-label {
  color: var(--ce-accent-light);
}

.fish-select-label {
  color: var(--ce-text-muted);
}

.voice-options {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.voice-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding: 6px 10px;
  border: 1px solid var(--ce-border-subtle);
  border-radius: var(--ce-radius-xs);
  background: transparent;
  cursor: pointer;
  transition: all var(--ce-transition-fast);
  font-size: 13px;
}

.voice-option:hover {
  border-color: var(--ce-border-input);
  background: var(--ce-bg-hover);
}

.voice-option.selected {
  border-color: var(--ce-accent-light);
  background: rgba(62, 230, 168, 0.08);
}

.voice-option.cloned {
  border-style: dashed;
}

.voice-option-info {
  display: flex;
  align-items: center;
  gap: 6px;
}

.voice-option-label {
  color: var(--ce-text-primary);
  white-space: nowrap;
}

.voice-option-meta {
  font-size: 11px;
  color: var(--ce-text-muted);
}

.voice-preview-btn {
  width: 24px;
  height: 24px;
  border: none;
  background: transparent;
  color: var(--ce-text-muted);
  cursor: pointer;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all var(--ce-transition-fast);
  flex-shrink: 0;
}

.voice-preview-btn:hover {
  color: var(--ce-accent-light);
  background: rgba(62, 230, 168, 0.12);
}

.voice-create-link {
  margin-top: 8px;
}

.voice-inline-hint {
  margin-top: 10px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--ce-text-muted);
}

.voice-create-link a {
  font-size: 13px;
  color: var(--ce-accent-light);
  text-decoration: none;
}

.voice-create-link a:hover {
  text-decoration: underline;
}

@media (max-width: 640px) {
  .duration-row,
  .options-row {
    flex-direction: column;
    align-items: stretch;
    gap: 10px;
  }

  .voice-options {
    flex-direction: column;
  }

  .fish-voice-grid {
    grid-template-columns: 1fr;
  }

  .voice-option {
    width: 100%;
  }
}
</style>
