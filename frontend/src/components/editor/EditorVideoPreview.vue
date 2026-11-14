<template>
  <div class="video-preview-container">
    <div class="video-wrapper" :style="videoWrapperStyle">
      <video
        ref="videoEl"
        :key="videoKey"
        :src="streamUrl"
        class="preview-video"
        @timeupdate="onTimeUpdate"
        @durationchange="onDurationChange"
        @play="$emit('play')"
        @pause="$emit('pause')"
        controls
        preload="auto"
      />
      <!-- CSS subtitle overlay -->
      <div v-if="currentSubtitle && subtitles?.enabled" class="subtitle-overlay" :style="subtitleOverlayPosition">
        <span class="subtitle-text" :style="subtitleTextStyle">{{ currentSubtitle.text }}</span>
        <span v-if="isPreviewSubtitle" class="preview-badge">Preview</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'

const props = defineProps({
  streamUrl: { type: String, default: '' },
  subtitles: { type: Object, default: null },
  currentTime: { type: Number, default: 0 },
  videoKey: { type: Number, default: 0 },
})

const emit = defineEmits(['timeupdate', 'durationchange', 'play', 'pause'])

const videoEl = ref(null)

// Find current subtitle based on time, with preview fallback
const subtitleAtTime = computed(() => {
  if (!props.subtitles?.entries?.length) return null
  return props.subtitles.entries.find(
    s => props.currentTime >= s.start_time && props.currentTime <= s.end_time
  )
})

const isPreviewSubtitle = computed(() => !subtitleAtTime.value && props.subtitles?.entries?.length > 0)

const currentSubtitle = computed(() => {
  if (subtitleAtTime.value) return subtitleAtTime.value
  // Fallback: show first subtitle as preview when nothing at current time
  if (props.subtitles?.entries?.length) return props.subtitles.entries[0]
  return null
})

// Scale factor: editor preview is ~720px, source is 1920
const scaleFactor = computed(() => 720 / 1920)

const videoWrapperStyle = computed(() => {
  return { position: 'relative', maxWidth: '100%', maxHeight: '100%' }
})

// Subtitle position
const subtitleOverlayPosition = computed(() => {
  const pos = props.subtitles?.position || 'bottom'
  const base = {
    position: 'absolute',
    left: '0',
    right: '0',
    display: 'flex',
    justifyContent: 'center',
    pointerEvents: 'none',
    zIndex: 10,
  }
  if (pos === 'top') return { ...base, top: '8%' }
  if (pos === 'center') return { ...base, top: '50%', transform: 'translateY(-50%)' }
  return { ...base, bottom: '10%' }
})

// Subtitle text style
const subtitleTextStyle = computed(() => {
  const s = props.subtitles || {}
  const fontSize = Math.round((s.font_size || 52) * scaleFactor.value)
  const color = s.color || '#FFFFFF'
  const outlineColor = s.outline_color || '#000000'
  const font = s.font || 'Arial'

  // 8-direction shadow to simulate outline
  const d = Math.max(1, Math.round(fontSize * 0.06))
  const shadow = [
    `${d}px 0 ${outlineColor}`, `${-d}px 0 ${outlineColor}`,
    `0 ${d}px ${outlineColor}`, `0 ${-d}px ${outlineColor}`,
    `${d}px ${d}px ${outlineColor}`, `${-d}px ${d}px ${outlineColor}`,
    `${d}px ${-d}px ${outlineColor}`, `${-d}px ${-d}px ${outlineColor}`,
  ].join(', ')

  return {
    fontFamily: `'${font}', sans-serif`,
    fontSize: `${fontSize}px`,
    color,
    textShadow: shadow,
    textAlign: 'center',
    padding: '4px 12px',
    lineHeight: '1.3',
    fontWeight: font.includes('Bold') ? 'bold' : 'normal',
    maxWidth: '90%',
    wordWrap: 'break-word',
  }
})

function onTimeUpdate() {
  if (videoEl.value) {
    emit('timeupdate', videoEl.value.currentTime)
  }
}

function onDurationChange() {
  if (videoEl.value) {
    emit('durationchange', videoEl.value.duration)
  }
}

function seekTo(time) {
  if (videoEl.value) {
    videoEl.value.currentTime = time
  }
}

defineExpose({ seekTo })
</script>

<style scoped>
.video-preview-container {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.video-wrapper {
  position: relative;
  display: inline-block;
}

.preview-video {
  max-width: 100%;
  max-height: calc(100vh - 48px - 230px - 32px);
  border-radius: var(--ce-radius-xs);
  background: var(--ce-bg-base);
  box-shadow: 0 0 0 1px var(--ce-border-subtle), var(--ce-shadow-card);
}

.subtitle-overlay {
  pointer-events: none;
}

.subtitle-text {
  display: inline-block;
  user-select: none;
}

.preview-badge {
  position: absolute;
  top: -18px;
  right: 0;
  font-size: 10px;
  color: var(--ce-text-secondary);
  background: var(--ce-surface-2);
  border: 1px solid var(--ce-border-subtle);
  padding: 1px 6px;
  border-radius: 3px;
  pointer-events: none;
}
</style>
