<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage } from 'element-plus'
import { getVoiceCatalog, getVoicePreview } from '../../api'

// Compact voice selector backed by the same /tts/catalog call as the wizard.
const voiceKey = defineModel({ type: String, default: '' })
const emit = defineEmits(['state'])

const loading = ref(true)
const voices = ref([])
const providers = ref([])
const playing = ref(false)
const previewLoading = ref(false)
let audio = null
let objectUrl = null

const groups = computed(() => {
  const order = new Map(providers.value.map((p, i) => [p, i]))
  const sorted = [...voices.value].sort((a, b) => {
    const byProvider = (order.get(a.provider) ?? 99) - (order.get(b.provider) ?? 99)
    return byProvider !== 0 ? byProvider : a.label.localeCompare(b.label)
  })
  const out = []
  for (const voice of sorted) {
    let group = out.find((g) => g.provider === voice.provider)
    if (!group) {
      group = { provider: voice.provider, voices: [] }
      out.push(group)
    }
    group.voices.push(voice)
  }
  return out
})

function providerName(provider) {
  return provider ? provider.charAt(0).toUpperCase() + provider.slice(1) : ''
}

function optionLabel(voice) {
  const extra = [voice.gender, voice.is_cloned ? 'clone' : null].filter(Boolean)
  return extra.length ? `${voice.label} (${extra.join(', ')})` : voice.label
}

function stopPreview() {
  if (audio) {
    audio.onended = null
    audio.onerror = null
    audio.pause()
    audio = null
  }
  if (objectUrl) {
    URL.revokeObjectURL(objectUrl)
    objectUrl = null
  }
  playing.value = false
}

async function togglePreview() {
  if (playing.value) {
    stopPreview()
    return
  }
  if (!voiceKey.value) return
  previewLoading.value = true
  try {
    const { data } = await getVoicePreview(voiceKey.value)
    stopPreview()
    objectUrl = URL.createObjectURL(data)
    audio = new Audio(objectUrl)
    audio.onended = stopPreview
    audio.onerror = () => {
      stopPreview()
      ElMessage.warning('Preview unavailable for this voice')
    }
    await audio.play()
    playing.value = true
  } catch {
    stopPreview()
    ElMessage.warning('Preview unavailable for this voice')
  } finally {
    previewLoading.value = false
  }
}

onMounted(async () => {
  try {
    const { data } = await getVoiceCatalog()
    voices.value = data.voices || []
    providers.value = data.enabled_providers || []
    const known = voices.value.some((v) => v.voice_key === voiceKey.value)
    if (!known) {
      const fallback = voices.value.find((v) => v.voice_key === data.default_voice_key) || voices.value[0]
      voiceKey.value = fallback ? fallback.voice_key : ''
    }
  } catch {
    ElMessage.warning('Could not load narration voices right now')
  } finally {
    loading.value = false
    emit('state', { loading: false, required: providers.value.length > 0 && voices.value.length > 0 })
  }
})

onBeforeUnmount(stopPreview)
</script>

<template>
  <div class="voice-picker">
    <div class="voice-row">
      <el-select
        v-model="voiceKey"
        :loading="loading"
        :disabled="loading || voices.length === 0"
        filterable
        :placeholder="loading ? 'Loading voices...' : 'Select a voice'"
        style="flex: 1"
        @change="stopPreview"
      >
        <el-option-group v-for="group in groups" :key="group.provider" :label="providerName(group.provider)">
          <el-option
            v-for="voice in group.voices"
            :key="voice.voice_key"
            :value="voice.voice_key"
            :label="optionLabel(voice)"
          />
        </el-option-group>
      </el-select>
      <button
        type="button"
        class="preview-btn"
        :disabled="!voiceKey || previewLoading"
        :title="playing ? 'Stop preview' : 'Play preview'"
        @click="togglePreview"
      >
        <el-icon :size="16">
          <Loading v-if="previewLoading" class="is-loading" />
          <VideoPause v-else-if="playing" />
          <VideoPlay v-else />
        </el-icon>
      </button>
    </div>
    <p v-if="!loading && voices.length === 0" class="helper-text">
      No narration voices are available. The server default voice will be used.
    </p>
  </div>
</template>

<style scoped>
.voice-picker {
  width: 100%;
}

.voice-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.preview-btn {
  width: 36px;
  height: 32px;
  border-radius: var(--ce-radius-xs);
  border: 1px solid var(--ce-border-input);
  background: var(--ce-bg-input);
  color: var(--ce-text-secondary);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  flex-shrink: 0;
  transition: all var(--ce-transition-fast);
}

.preview-btn:hover:not(:disabled) {
  color: var(--ce-accent-light);
  border-color: var(--ce-accent-light);
}

.preview-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.helper-text {
  font-size: 12px;
  color: var(--ce-text-muted);
  margin-top: 6px;
}
</style>
