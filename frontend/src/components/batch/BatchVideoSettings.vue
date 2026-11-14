<script setup>
import { ref, onMounted } from 'vue'
import { getStyles } from '../../api'
import BatchVoicePicker from './BatchVoicePicker.vue'

// settings: { orientation, durationSeconds, narrationStyle, subtitlesEnabled, subtitleStyle, loopMode, voiceKey }
const settings = defineModel({ type: Object, required: true })

defineProps({
  inputMode: { type: String, default: 'prompt' },
})

const emit = defineEmits(['voice-state'])

const FALLBACK_STYLES = [
  { id: 'viral', name: 'Viral', description: 'Hook viewers with punchy facts' },
  { id: 'informative', name: 'Informative', description: 'Expert teacher who makes complex topics simple' },
  { id: 'funny', name: 'Funny', description: 'Comedian who makes every topic hilarious' },
  { id: 'storytelling', name: 'Storytelling', description: 'Master storyteller who turns topics into narratives' },
  { id: 'motivational', name: 'Motivational', description: 'Inspiring coach who empowers viewers' },
  { id: 'creative', name: 'Creative', description: 'Curious overthinker having a late-night epiphany' },
]

const styles = ref(FALLBACK_STYLES)

const orientationOptions = [
  { value: 'portrait', label: 'Portrait', icon: 'Iphone' },
  { value: 'landscape', label: 'Landscape', icon: 'Monitor' },
  { value: 'square', label: 'Square', icon: 'Grid' },
]

const subtitleStyles = [
  { value: 'youtube', label: 'YouTube' },
  { value: 'tiktok', label: 'TikTok' },
  { value: 'minimal', label: 'Minimal' },
]

const durationMarks = { 30: '30s', 60: '1m', 90: '1m 30s', 120: '2m', 180: '3m' }

function formatDuration(seconds) {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  if (m === 0) return `${s}s`
  return s === 0 ? `${m} min` : `${m} min ${s}s`
}

onMounted(async () => {
  try {
    const { data } = await getStyles()
    if (Array.isArray(data?.styles) && data.styles.length > 0) styles.value = data.styles
  } catch {
    // keep fallback list
  }
})
</script>

<template>
  <el-form label-position="top" class="video-settings" @submit.prevent>
    <el-form-item label="Orientation">
      <el-radio-group v-model="settings.orientation">
        <el-radio-button v-for="opt in orientationOptions" :key="opt.value" :value="opt.value">
          <el-icon><component :is="opt.icon" /></el-icon> {{ opt.label }}
        </el-radio-button>
      </el-radio-group>
    </el-form-item>

    <el-form-item :label="inputMode === 'prompt' ? `Duration: ${formatDuration(settings.durationSeconds)}` : 'Duration'">
      <template v-if="inputMode === 'prompt'">
        <el-slider
          v-model="settings.durationSeconds"
          :min="30"
          :max="180"
          :step="15"
          :marks="durationMarks"
          :format-tooltip="formatDuration"
          class="duration-slider"
        />
      </template>
      <p v-else class="helper-text">
        Set by each script's length.
      </p>
    </el-form-item>

    <el-form-item label="Narration Style">
      <el-select v-model="settings.narrationStyle" style="width: 100%" :disabled="inputMode === 'script'">
        <el-option v-for="style in styles" :key="style.id" :value="style.id" :label="style.name">
          <div class="style-option">
            <span class="style-name">{{ style.name }}</span>
            <span class="style-desc">{{ style.description }}</span>
          </div>
        </el-option>
      </el-select>
      <p v-if="inputMode === 'script'" class="helper-text">Scripts are used as written.</p>
    </el-form-item>

    <el-form-item label="Narration Voice">
      <BatchVoicePicker v-model="settings.voiceKey" @state="emit('voice-state', $event)" />
    </el-form-item>

    <el-form-item label="Subtitles">
      <div class="subtitle-row">
        <el-switch v-model="settings.subtitlesEnabled" />
        <el-select
          v-if="settings.subtitlesEnabled"
          v-model="settings.subtitleStyle"
          style="width: 180px"
        >
          <el-option v-for="opt in subtitleStyles" :key="opt.value" :value="opt.value" :label="opt.label" />
        </el-select>
        <span v-else class="helper-text inline">Off</span>
      </div>
    </el-form-item>

    <el-form-item label="Options">
      <el-checkbox v-model="settings.loopMode">Loop mode</el-checkbox>
    </el-form-item>
  </el-form>
</template>

<style scoped>
.helper-text {
  font-size: 12px;
  color: var(--ce-text-muted);
  margin-top: 4px;
  line-height: 1.5;
}

.helper-text.inline {
  margin-top: 0;
}

.duration-slider {
  width: 100%;
  padding: 0 8px 18px;
}

.subtitle-row {
  display: flex;
  align-items: center;
  gap: 14px;
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
</style>
