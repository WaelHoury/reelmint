<template>
  <div class="properties-panel">
    <!-- Selected element properties -->
    <div class="selection-section" v-if="selected">
      <!-- Clip selected -->
      <template v-if="selected?.type === 'clip'">
        <div class="panel-header">
          <span class="panel-title">Clip {{ selected.index + 1 }}</span>
          <el-button text size="small" @click="$emit('select', null)" class="close-btn">&times;</el-button>
        </div>
        <div class="panel-body" v-if="selectedClip">
          <div class="info-row">
            <span class="info-label">Keywords</span>
            <span class="info-value">{{ selectedClip.keywords?.join(', ') || '-' }}</span>
          </div>
          <div class="info-row">
            <span class="info-label">Description</span>
            <span class="info-value small">{{ selectedClip.visual_description || '-' }}</span>
          </div>
          <div class="info-row">
            <span class="info-label">Source</span>
            <span class="info-value">{{ selectedClip.author || 'Pexels' }}</span>
          </div>
          <div class="info-row">
            <span class="info-label">Duration</span>
            <span class="info-value">{{ (selectedClip.duration_seconds || 0).toFixed(1) }}s</span>
          </div>
          <div class="info-row">
            <span class="info-label">Time</span>
            <span class="info-value">{{ formatTime(selectedClip.start_time) }} — {{ formatTime(selectedClip.end_time) }}</span>
          </div>
          <el-divider />
          <div class="form-group">
            <label>Trim Start (s)</label>
            <el-input-number
              :model-value="selectedClip.trim_start || 0"
              @update:model-value="updateClip('trim_start', $event)"
              :step="0.5" :min="0" :precision="1"
              size="small"
              style="width: 100%"
            />
          </div>
          <div class="form-group">
            <label>Trim End (s)</label>
            <el-input-number
              :model-value="selectedClip.trim_end || 0"
              @update:model-value="updateClip('trim_end', $event)"
              :step="0.5" :min="0" :precision="1"
              size="small"
              style="width: 100%"
            />
          </div>
          <p class="trim-hint">Offset from the start/end of the source video. Changes require rendering.</p>
          <el-divider />
          <el-button type="warning" size="small" @click="$emit('replace-clip', selected.index)" style="width:100%">
            Replace Clip
          </el-button>
        </div>
      </template>

      <!-- Audio selected -->
      <template v-if="selected?.type === 'audio'">
        <div class="panel-header">
          <span class="panel-title">Narration</span>
          <el-button text size="small" @click="$emit('select', null)" class="close-btn">&times;</el-button>
        </div>
        <div class="panel-body">
          <div class="form-group">
            <label>Volume</label>
            <el-slider
              :model-value="audioData.narration_volume"
              @update:model-value="updateAudio('narration_volume', $event)"
              :min="0" :max="2" :step="0.05"
              :format-tooltip="v => `${Math.round(v * 100)}%`"
            />
          </div>
          <p class="render-hint">Volume changes require rendering to take effect.</p>
        </div>
      </template>

      <!-- Music selected -->
      <template v-if="selected?.type === 'music'">
        <div class="panel-header">
          <span class="panel-title">Background Music</span>
          <el-button text size="small" @click="$emit('select', null)" class="close-btn">&times;</el-button>
        </div>
        <div class="panel-body">
          <div class="form-group">
            <label>Volume</label>
            <el-slider
              :model-value="audioData.music_volume"
              @update:model-value="updateAudio('music_volume', $event)"
              :min="0" :max="0.5" :step="0.01"
              :format-tooltip="v => `${Math.round(v * 100)}%`"
            />
          </div>
          <p class="render-hint">Volume changes require rendering to take effect.</p>
        </div>
      </template>

      <!-- Subtitle entry selected -->
      <template v-if="selected?.type === 'subtitle'">
        <div class="panel-header">
          <span class="panel-title">Subtitle {{ selected.index + 1 }}</span>
          <el-button text size="small" @click="$emit('select', null)" class="close-btn">&times;</el-button>
        </div>
        <div class="panel-body" v-if="selectedSubtitle">
          <div class="form-group">
            <label>Text</label>
            <el-input
              type="textarea"
              :rows="3"
              :model-value="selectedSubtitle.text"
              @update:model-value="updateSubtitleEntry('text', $event)"
            />
          </div>
          <div class="form-group">
            <label>Start (s)</label>
            <el-input-number
              :model-value="selectedSubtitle.start_time"
              @update:model-value="updateSubtitleEntry('start_time', $event)"
              :step="0.1" :min="0" :precision="2"
              size="small"
              style="width: 100%"
            />
          </div>
          <div class="form-group">
            <label>End (s)</label>
            <el-input-number
              :model-value="selectedSubtitle.end_time"
              @update:model-value="updateSubtitleEntry('end_time', $event)"
              :step="0.1" :min="0" :precision="2"
              size="small"
              style="width: 100%"
            />
          </div>
        </div>
      </template>
    </div>

    <!-- Empty state when nothing selected -->
    <div v-if="!selected" class="empty-state">
      <p class="empty-state-text">Select an element on the timeline to edit its properties.</p>
    </div>

    <!-- Subtitle Settings (always visible, collapsible) -->
    <div class="subtitle-settings-section">
      <div class="section-header" @click="subtitleSettingsOpen = !subtitleSettingsOpen">
        <span class="section-title">Subtitle Settings</span>
        <el-icon class="collapse-icon" :class="{ collapsed: !subtitleSettingsOpen }">
          <ArrowDown />
        </el-icon>
      </div>
      <div class="panel-body" v-show="subtitleSettingsOpen">
        <div class="form-group">
          <label>Subtitles</label>
          <el-switch
            :model-value="subs.enabled"
            @update:model-value="updateSub('enabled', $event)"
            active-text="On"
            inactive-text="Off"
          />
        </div>

        <template v-if="subs.enabled">
          <div class="form-group">
            <label>Preset</label>
            <el-select :model-value="subs.style" @update:model-value="applyPreset($event)" size="small">
              <el-option label="YouTube" value="youtube" />
              <el-option label="TikTok" value="tiktok" />
              <el-option label="Minimal" value="minimal" />
            </el-select>
          </div>

          <div class="form-group">
            <label>Font</label>
            <el-select :model-value="subs.font" @update:model-value="updateSub('font', $event)" size="small">
              <el-option v-for="f in fonts" :key="f" :label="f" :value="f" />
            </el-select>
          </div>

          <div class="form-group">
            <label>Size</label>
            <el-slider
              :model-value="subs.font_size"
              @update:model-value="updateSub('font_size', $event)"
              :min="24" :max="96" :step="2"
              :show-tooltip="true"
            />
          </div>

          <div class="form-group">
            <label>Color</label>
            <el-color-picker
              :model-value="subs.color"
              @update:model-value="updateSub('color', $event)"
              show-alpha
              size="small"
            />
          </div>

          <div class="form-group">
            <label>Outline</label>
            <el-color-picker
              :model-value="subs.outline_color"
              @update:model-value="updateSub('outline_color', $event)"
              show-alpha
              size="small"
            />
          </div>

          <div class="form-group">
            <label>Position</label>
            <el-radio-group
              :model-value="subs.position"
              @update:model-value="updateSub('position', $event)"
              size="small"
            >
              <el-radio-button value="bottom">Bottom</el-radio-button>
              <el-radio-button value="center">Center</el-radio-button>
              <el-radio-button value="top">Top</el-radio-button>
            </el-radio-group>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { ArrowDown } from '@element-plus/icons-vue'

const props = defineProps({
  selected: { type: Object, default: null },
  editorData: { type: Object, default: null },
})

const emit = defineEmits(['update:subtitles', 'update:audio', 'update:subtitle-entry', 'update:clip', 'replace-clip', 'select'])

const subtitleSettingsOpen = ref(true)

// Auto-collapse subtitle settings when something is selected, expand when deselected
watch(() => props.selected, (val) => {
  subtitleSettingsOpen.value = !val
})

const fonts = [
  'Arial', 'Montserrat-Bold', 'Helvetica', 'Comic Sans MS',
  'Impact', 'Verdana', 'Georgia', 'Times New Roman',
]

const PRESETS = {
  youtube: { font: 'Arial', font_size: 52, color: '#FFFFFF', outline_color: '#000000', position: 'bottom' },
  tiktok: { font: 'Montserrat-Bold', font_size: 64, color: '#FFFFFF', outline_color: '#000000', position: 'center' },
  minimal: { font: 'Helvetica', font_size: 44, color: '#FFFFFF', outline_color: '#000000', position: 'bottom' },
}

const subs = computed(() => props.editorData?.subtitles || {})
const audioData = computed(() => props.editorData?.audio || {})

const selectedClip = computed(() => {
  if (props.selected?.type !== 'clip') return null
  return props.editorData?.clips?.[props.selected.index]
})

const selectedSubtitle = computed(() => {
  if (props.selected?.type !== 'subtitle') return null
  return props.editorData?.subtitles?.entries?.[props.selected.index]
})

function updateSub(key, value) {
  emit('update:subtitles', { [key]: value })
}

function applyPreset(preset) {
  const settings = { style: preset, ...PRESETS[preset] }
  emit('update:subtitles', settings)
}

function updateAudio(key, value) {
  emit('update:audio', { [key]: value })
}

function updateClip(key, value) {
  if (props.selected?.type !== 'clip') return
  emit('update:clip', {
    index: props.selected.index,
    changes: { [key]: value },
  })
}

function updateSubtitleEntry(key, value) {
  if (props.selected?.type !== 'subtitle') return
  emit('update:subtitle-entry', {
    index: props.selected.index,
    entry: { [key]: value },
  })
}

function formatTime(seconds) {
  if (seconds == null) return '--'
  const m = Math.floor(seconds / 60)
  const s = (seconds % 60).toFixed(1)
  return `${m}:${s.padStart(4, '0')}`
}
</script>

<style scoped>
.properties-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.selection-section {
  flex-shrink: 0;
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid #2a2a4a;
  background: rgba(0, 0, 0, 0.15);
  flex-shrink: 0;
}

.panel-title {
  font-size: 11px;
  font-weight: 600;
  color: #c0c0e0;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.close-btn {
  color: #8a8aaa !important;
  font-size: 18px;
  padding: 0 4px !important;
  transition: color 0.15s;
}
.close-btn:hover {
  color: #e0e0e0 !important;
}

.panel-body {
  padding: 14px 16px;
  overflow-y: auto;
}

.form-group {
  margin-bottom: 16px;
}

.form-group label {
  display: block;
  font-size: 11px;
  color: #8a8aaa;
  margin-bottom: 6px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  font-weight: 500;
}

/* Info rows for clip details */
.info-row {
  margin-bottom: 10px;
}

.info-label {
  display: block;
  font-size: 11px;
  color: #6a6a8a;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 2px;
  font-weight: 500;
}

.info-value {
  font-size: 13px;
  color: #c0c0e0;
  word-break: break-word;
}

.info-value.small {
  font-size: 12px;
  color: #a0a0c0;
}

.trim-hint,
.render-hint {
  font-size: 11px;
  color: #6a6a8a;
  margin: 6px 0 0;
  font-style: italic;
}

/* Empty state */
.empty-state {
  padding: 24px 16px;
  flex: 1;
}

.empty-state-text {
  font-size: 12px;
  color: #6a6a8a;
  text-align: center;
  margin: 0;
  line-height: 1.5;
}

/* Subtitle settings section (always visible) */
.subtitle-settings-section {
  border-top: 1px solid #2a2a4a;
  margin-top: auto;
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  background: rgba(0, 0, 0, 0.15);
  cursor: pointer;
  user-select: none;
  transition: background 0.15s;
}

.section-header:hover {
  background: rgba(0, 0, 0, 0.25);
}

.section-title {
  font-size: 11px;
  font-weight: 600;
  color: #c0c0e0;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.collapse-icon {
  transition: transform 0.2s;
  font-size: 12px;
  color: #8a8aaa;
}

.collapse-icon.collapsed {
  transform: rotate(-90deg);
}

/* Dark theme overrides for Element Plus components */
.properties-panel :deep(.el-select) {
  width: 100%;
}

.properties-panel :deep(.el-input__wrapper) {
  background-color: #22223a !important;
  border-color: #3a3a5a !important;
  box-shadow: none !important;
}

.properties-panel :deep(.el-input__inner) {
  color: #e0e0e0 !important;
}

.properties-panel :deep(.el-textarea__inner) {
  background-color: #22223a !important;
  border-color: #3a3a5a !important;
  color: #e0e0e0 !important;
  box-shadow: none !important;
}

.properties-panel :deep(.el-slider__runway) {
  background-color: #2a2a4a;
}

.properties-panel :deep(.el-slider__bar) {
  background-color: #A78BFA;
}

.properties-panel :deep(.el-radio-button__inner) {
  background-color: #22223a !important;
  border-color: #3a3a5a !important;
  color: #a0a0c0 !important;
}

.properties-panel :deep(.el-radio-button__original-radio:checked + .el-radio-button__inner) {
  background-color: #6D28D9 !important;
  border-color: #6D28D9 !important;
  color: #fff !important;
}

.properties-panel :deep(.el-switch__core) {
  background-color: #3a3a5a;
}

.properties-panel :deep(.el-divider) {
  border-color: #2a2a4a;
}

.properties-panel :deep(.el-input-number) {
  width: 100%;
}

.properties-panel :deep(.el-input-number .el-input__wrapper) {
  background-color: #22223a !important;
}
</style>
