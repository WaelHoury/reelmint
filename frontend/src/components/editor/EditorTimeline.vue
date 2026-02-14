<template>
  <div class="timeline-container">
    <!-- Toolbar -->
    <div class="timeline-toolbar">
      <div class="toolbar-section">
        <el-button-group size="small">
          <el-button @click="zoomOut" :disabled="zoom <= 0.25" class="zoom-btn">
            <el-icon><ZoomOut /></el-icon>
          </el-button>
          <el-button @click="zoomIn" :disabled="zoom >= 4" class="zoom-btn">
            <el-icon><ZoomIn /></el-icon>
          </el-button>
        </el-button-group>
        <span class="zoom-label">{{ Math.round(zoom * 100) }}%</span>
      </div>
      <div class="toolbar-section">
        <span class="time-display">{{ formatTime(currentTime) }} / {{ formatTime(duration) }}</span>
      </div>
    </div>

    <!-- Timeline content -->
    <div class="timeline-body" ref="timelineBody">
      <!-- Layer labels (fixed column) -->
      <div class="layer-labels">
        <div class="ruler-label"></div>
        <div class="layer-label">
          <el-icon><Film /></el-icon>
          <span>Video</span>
        </div>
        <div class="layer-label">
          <el-icon><Microphone /></el-icon>
          <span>Audio</span>
        </div>
        <div class="layer-label" v-if="editorData?.audio?.has_background_music">
          <el-icon><Headset /></el-icon>
          <span>Music</span>
        </div>
        <div class="layer-label">
          <el-icon><ChatDotRound /></el-icon>
          <span>Subs</span>
        </div>
      </div>

      <!-- Scrollable timeline area -->
      <div class="timeline-scroll" ref="scrollArea" @scroll="onScroll">
        <div class="timeline-content" :style="{ width: contentWidth + 'px' }">
          <!-- Ruler -->
          <div class="ruler" @mousedown="onRulerClick">
            <canvas ref="rulerCanvas" :width="contentWidth" height="24"></canvas>
          </div>

          <!-- Video clips layer -->
          <div class="layer" @click.self="$emit('select', null)">
            <TimelineBlock
              v-for="clip in editorData?.clips"
              :key="'clip-' + clip.index"
              :start="clip.start_time"
              :end="clip.end_time"
              :label="clip.keywords?.join(', ') || `Clip ${clip.index + 1}`"
              :zoom="zoom"
              :selected="selected?.type === 'clip' && selected?.index === clip.index"
              color="#A78BFA"
              :trimmable="true"
              :trim-start="clip.trim_start || 0"
              :trim-end="clip.trim_end || 0"
              :clip-duration="clip.duration_seconds || 0"
              @click="$emit('select', { type: 'clip', index: clip.index })"
              @trim-change="onClipTrimChange(clip.index, $event)"
            />
          </div>

          <!-- Audio layer -->
          <div class="layer" @click.self="$emit('select', null)">
            <TimelineBlock
              :start="0"
              :end="duration"
              label="Narration"
              :zoom="zoom"
              :selected="selected?.type === 'audio'"
              color="#67c23a"
              @click="$emit('select', { type: 'audio' })"
            />
          </div>

          <!-- Music layer (conditional) -->
          <div class="layer" v-if="editorData?.audio?.has_background_music" @click.self="$emit('select', null)">
            <TimelineBlock
              :start="0"
              :end="duration"
              label="Background Music"
              :zoom="zoom"
              :selected="selected?.type === 'music'"
              color="#e6a23c"
              @click="$emit('select', { type: 'music' })"
            />
          </div>

          <!-- Subtitle layer -->
          <div class="layer subtitles-layer" @click.self="$emit('select', null)">
            <TimelineBlock
              v-for="sub in editorData?.subtitles?.entries"
              :key="'sub-' + sub.index"
              :start="sub.start_time"
              :end="sub.end_time"
              :label="sub.text"
              :zoom="zoom"
              :selected="selected?.type === 'subtitle' && selected?.index === sub.index"
              color="#b86bff"
              compact
              @click="$emit('select', { type: 'subtitle', index: sub.index })"
            />
          </div>

          <!-- Playhead -->
          <div class="playhead" :style="playheadStyle" @mousedown.stop="startPlayheadDrag"></div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, nextTick } from 'vue'
import { ZoomIn, ZoomOut, Film, Microphone, Headset, ChatDotRound } from '@element-plus/icons-vue'
import TimelineBlock from './TimelineBlock.vue'

const props = defineProps({
  editorData: { type: Object, default: null },
  currentTime: { type: Number, default: 0 },
  duration: { type: Number, default: 0 },
  zoom: { type: Number, default: 1 },
  selected: { type: Object, default: null },
})

const emit = defineEmits(['select', 'seek', 'update:zoom', 'trim-clip'])

const PIXELS_PER_SECOND = 50
const ZOOM_LEVELS = [0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4]

const scrollArea = ref(null)
const rulerCanvas = ref(null)
const isDraggingPlayhead = ref(false)

const contentWidth = computed(() => {
  return Math.max(800, (props.duration || 60) * PIXELS_PER_SECOND * props.zoom + 100)
})

const playheadStyle = computed(() => {
  const left = props.currentTime * PIXELS_PER_SECOND * props.zoom
  return {
    position: 'absolute',
    left: `${left}px`,
    top: '0',
    bottom: '0',
    width: '2px',
    background: '#ff4757',
    zIndex: 50,
    cursor: 'col-resize',
    pointerEvents: 'auto',
  }
})

function zoomIn() {
  const idx = ZOOM_LEVELS.indexOf(props.zoom)
  if (idx < ZOOM_LEVELS.length - 1) {
    emit('update:zoom', ZOOM_LEVELS[idx + 1])
    nextTick(drawRuler)
  }
}

function zoomOut() {
  const idx = ZOOM_LEVELS.indexOf(props.zoom)
  if (idx > 0) {
    emit('update:zoom', ZOOM_LEVELS[idx - 1])
    nextTick(drawRuler)
  }
}

function positionToTime(clientX) {
  if (!scrollArea.value) return 0
  const rect = scrollArea.value.getBoundingClientRect()
  const scrollLeft = scrollArea.value.scrollLeft
  const x = clientX - rect.left + scrollLeft
  return Math.max(0, Math.min(x / (PIXELS_PER_SECOND * props.zoom), props.duration))
}

function onRulerClick(e) {
  const time = positionToTime(e.clientX)
  emit('seek', time)
}

function startPlayheadDrag(e) {
  isDraggingPlayhead.value = true

  const onMove = (ev) => {
    if (isDraggingPlayhead.value) {
      const time = positionToTime(ev.clientX)
      emit('seek', time)
    }
  }

  const onUp = () => {
    isDraggingPlayhead.value = false
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
  }

  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}

function onClipTrimChange(clipIndex, { trimStart, trimEnd }) {
  emit('trim-clip', { index: clipIndex, trimStart, trimEnd })
}

function onScroll() {
  // Could be used for syncing ruler or other elements
}

function formatTime(seconds) {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  const ms = Math.floor((seconds % 1) * 10)
  return `${m}:${String(s).padStart(2, '0')}.${ms}`
}

// Draw ruler ticks
function drawRuler() {
  const canvas = rulerCanvas.value
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  const width = contentWidth.value
  canvas.width = width

  ctx.clearRect(0, 0, width, 24)
  ctx.fillStyle = '#4a4a6a'
  ctx.font = '10px sans-serif'

  const pps = PIXELS_PER_SECOND * props.zoom

  // Determine tick interval based on zoom
  let majorInterval = 5 // seconds
  let minorInterval = 1
  if (props.zoom >= 2) { majorInterval = 2; minorInterval = 0.5 }
  else if (props.zoom >= 1) { majorInterval = 5; minorInterval = 1 }
  else if (props.zoom >= 0.5) { majorInterval = 10; minorInterval = 5 }
  else { majorInterval = 30; minorInterval = 10 }

  const totalSeconds = props.duration || 60

  // Minor ticks
  ctx.strokeStyle = '#3a3a5a'
  ctx.lineWidth = 1
  for (let t = 0; t <= totalSeconds; t += minorInterval) {
    const x = Math.round(t * pps) + 0.5
    ctx.beginPath()
    ctx.moveTo(x, 16)
    ctx.lineTo(x, 24)
    ctx.stroke()
  }

  // Major ticks + labels
  ctx.strokeStyle = '#6a6a8a'
  ctx.lineWidth = 1
  for (let t = 0; t <= totalSeconds; t += majorInterval) {
    const x = Math.round(t * pps) + 0.5
    ctx.beginPath()
    ctx.moveTo(x, 8)
    ctx.lineTo(x, 24)
    ctx.stroke()

    const label = formatTime(t)
    ctx.fillText(label, x + 3, 8)
  }
}

watch(() => [props.zoom, props.duration], () => {
  nextTick(drawRuler)
})

onMounted(() => {
  nextTick(drawRuler)
})
</script>

<style scoped>
.timeline-container {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.timeline-toolbar {
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 12px;
  background: #16162a;
  border-bottom: 1px solid #2a2a4a;
  flex-shrink: 0;
}

.toolbar-section {
  display: flex;
  align-items: center;
  gap: 8px;
}

.zoom-btn {
  background: #2a2a4a !important;
  border-color: #3a3a5a !important;
  color: #b0b0d0 !important;
}
.zoom-btn:hover {
  background: #3a3a5a !important;
  color: #fff !important;
}

.zoom-label {
  font-size: 11px;
  color: #8a8aaa;
  min-width: 40px;
}

.time-display {
  font-size: 12px;
  color: #a0a0c0;
  font-variant-numeric: tabular-nums;
}

.timeline-body {
  flex: 1;
  display: flex;
  overflow: hidden;
}

/* Fixed label column */
.layer-labels {
  width: 80px;
  flex-shrink: 0;
  background: #1a1a30;
  border-right: 1px solid #2a2a4a;
}

.ruler-label {
  height: 24px;
  border-bottom: 1px solid #2a2a4a;
}

.layer-label {
  height: 36px;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 10px;
  font-size: 11px;
  color: #8a8aaa;
  border-bottom: 1px solid #22223a;
  white-space: nowrap;
  overflow: hidden;
  font-weight: 500;
  letter-spacing: 0.3px;
}

.layer-label .el-icon {
  font-size: 13px;
  flex-shrink: 0;
}

/* Scrollable content */
.timeline-scroll {
  flex: 1;
  overflow-x: auto;
  overflow-y: hidden;
}

.timeline-content {
  position: relative;
  min-height: 100%;
}

/* Ruler */
.ruler {
  height: 24px;
  background: #1a1a30;
  border-bottom: 1px solid #2a2a4a;
  cursor: pointer;
}

/* Layers */
.layer {
  height: 36px;
  position: relative;
  border-bottom: 1px solid #22223a;
}

.layer:nth-child(odd) {
  background: rgba(255, 255, 255, 0.01);
}

.layer:nth-child(even) {
  background: rgba(0, 0, 0, 0.05);
}

.subtitles-layer {
  min-height: 36px;
}

/* Playhead */
.playhead {
  cursor: col-resize;
}

.playhead::before {
  content: '';
  position: absolute;
  top: 0;
  left: -5px;
  width: 0;
  height: 0;
  border-left: 5px solid transparent;
  border-right: 5px solid transparent;
  border-top: 6px solid #ff4757;
}

/* Wider invisible hit target for easier grabbing */
.playhead::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: -6px;
  width: 14px;
  cursor: col-resize;
}
</style>
