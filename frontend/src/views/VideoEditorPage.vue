<template>
  <div class="video-editor" v-loading="isLoading" element-loading-background="rgba(17,17,27,0.8)">
    <!-- Toolbar -->
    <div class="editor-toolbar">
      <div class="toolbar-left">
        <el-button text class="back-btn" @click="goBack">
          <el-icon><ArrowLeft /></el-icon>
          <span>Back</span>
        </el-button>
        <h3 class="editor-title">{{ editorData?.title || 'Video Editor' }}</h3>
      </div>
      <div class="toolbar-right">
        <el-tag v-if="hasChanges" type="warning" size="small">Unsaved changes</el-tag>
        <el-button
          type="primary"
          @click="handleRender"
          :loading="isRendering"
          :disabled="!hasChanges"
        >
          <el-icon><VideoPlay /></el-icon>
          Render
        </el-button>
      </div>
    </div>

    <!-- Main area -->
    <div class="editor-main" v-if="editorData">
      <!-- Video preview -->
      <div class="preview-area">
        <EditorVideoPreview
          ref="videoPreview"
          :stream-url="streamUrl"
          :subtitles="editorData.subtitles"
          :current-time="currentTime"
          :video-key="videoKey"
          @timeupdate="onTimeUpdate"
          @durationchange="onDurationChange"
          @play="isPlaying = true"
          @pause="isPlaying = false"
        />
      </div>

      <!-- Properties panel -->
      <div class="properties-area">
        <EditorPropertiesPanel
          :selected="selectedElement"
          :editor-data="editorData"
          @update:subtitles="onSubtitleSettingsChange"
          @update:audio="onAudioSettingsChange"
          @update:subtitle-entry="onSubtitleEntryChange"
          @update:clip="onClipSettingsChange"
          @replace-clip="onOpenClipSearch"
          @select="onSelect"
        />
      </div>
    </div>

    <!-- Timeline -->
    <div class="timeline-area" v-if="editorData">
      <EditorTimeline
        :editor-data="editorData"
        :current-time="currentTime"
        :duration="duration"
        :zoom="timelineZoom"
        :selected="selectedElement"
        @select="onSelect"
        @seek="onSeek"
        @update:zoom="timelineZoom = $event"
        @trim-clip="onTimelineTrimChange"
      />
    </div>

    <!-- Error state -->
    <div class="editor-error" v-if="errorMessage">
      <el-empty :description="errorMessage">
        <el-button type="primary" @click="goBack">Go Back</el-button>
      </el-empty>
    </div>

    <!-- Clip search dialog -->
    <ClipSearchDialog
      v-model="clipSearchVisible"
      :initial-keywords="clipSearchKeywords"
      :orientation="editorData?.orientation || 'landscape'"
      @select="onClipReplaced"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft, VideoPlay } from '@element-plus/icons-vue'
import {
  getSessionEditorData,
  getVideoEditorData,
  renderSessionChanges,
  renderVideoChanges,
  getSessionPreviewStreamUrl,
  getVideoPreviewStreamUrl,
} from '../api'
import EditorVideoPreview from '../components/editor/EditorVideoPreview.vue'
import EditorPropertiesPanel from '../components/editor/EditorPropertiesPanel.vue'
import EditorTimeline from '../components/editor/EditorTimeline.vue'
import ClipSearchDialog from '../components/editor/ClipSearchDialog.vue'

const props = defineProps({
  sessionId: { type: String, default: null },
  videoId: { type: [String, Number], default: null },
})

const router = useRouter()
const route = useRoute()

// Core state
const editorData = ref(null)
const isLoading = ref(true)
const isRendering = ref(false)
const errorMessage = ref('')
const videoKey = ref(Date.now())

// Playback
const currentTime = ref(0)
const duration = ref(0)
const isPlaying = ref(false)

// Selection & timeline
const selectedElement = ref(null)
const timelineZoom = ref(1)

// Change tracking
const pendingSubtitles = ref(false)
const pendingAudio = ref(false)
const pendingClips = ref(new Set())

const hasChanges = computed(() => {
  return pendingSubtitles.value || pendingAudio.value || pendingClips.value.size > 0
})

// Clip search dialog
const clipSearchVisible = ref(false)
const clipSearchKeywords = ref([])
const clipSearchIndex = ref(null)

// Video preview ref
const videoPreview = ref(null)

// Determine source type
const isSessionBased = computed(() => !!props.sessionId)
const sourceId = computed(() => props.sessionId || props.videoId)

// Stream URL — uses subtitle-free preview with cache-busting timestamp
const streamUrl = computed(() => {
  if (!editorData.value) return ''
  const t = videoKey.value
  if (isSessionBased.value) {
    return getSessionPreviewStreamUrl(props.sessionId) + `&_t=${t}`
  }
  return getVideoPreviewStreamUrl(props.videoId) + `&_t=${t}`
})

// Load editor data
async function loadEditorData() {
  isLoading.value = true
  errorMessage.value = ''
  try {
    let response
    if (isSessionBased.value) {
      response = await getSessionEditorData(props.sessionId)
    } else {
      response = await getVideoEditorData(props.videoId)
    }
    editorData.value = response.data
    duration.value = editorData.value.duration_seconds || 0
  } catch (err) {
    const detail = err.response?.data?.detail || err.message
    errorMessage.value = detail
  } finally {
    isLoading.value = false
  }
}

// Event handlers
function onTimeUpdate(time) {
  currentTime.value = time
}

function onDurationChange(dur) {
  duration.value = dur
}

function onSelect(element) {
  selectedElement.value = element
}

function onSeek(time) {
  currentTime.value = time
  videoPreview.value?.seekTo(time)
}

function onSubtitleSettingsChange(settings) {
  if (!editorData.value) return
  editorData.value.subtitles = { ...editorData.value.subtitles, ...settings }
  pendingSubtitles.value = true
}

function onSubtitleEntryChange({ index, entry }) {
  if (!editorData.value?.subtitles?.entries) return
  const entries = [...editorData.value.subtitles.entries]
  entries[index] = { ...entries[index], ...entry }
  editorData.value.subtitles.entries = entries
  pendingSubtitles.value = true
}

function onAudioSettingsChange(settings) {
  if (!editorData.value) return
  editorData.value.audio = { ...editorData.value.audio, ...settings }
  pendingAudio.value = true
}

function onClipSettingsChange({ index, changes }) {
  if (!editorData.value?.clips) return
  const clips = [...editorData.value.clips]
  clips[index] = { ...clips[index], ...changes }
  editorData.value.clips = clips
  pendingClips.value.add(index)
}

function onTimelineTrimChange({ index, trimStart, trimEnd }) {
  if (!editorData.value?.clips) return
  const clips = [...editorData.value.clips]
  clips[index] = {
    ...clips[index],
    trim_start: trimStart,
    trim_end: trimEnd,
  }
  editorData.value.clips = clips
  pendingClips.value.add(index)
}

function onOpenClipSearch(clipIndex) {
  const clip = editorData.value?.clips?.[clipIndex]
  if (clip) {
    clipSearchKeywords.value = clip.keywords || []
    clipSearchIndex.value = clipIndex
    clipSearchVisible.value = true
  }
}

function onClipReplaced(result) {
  if (clipSearchIndex.value === null) return
  const idx = clipSearchIndex.value
  const clips = [...editorData.value.clips]
  clips[idx] = {
    ...clips[idx],
    video_id: result.video_id,
    keywords: clipSearchKeywords.value,
    _replaced: true,
  }
  editorData.value.clips = clips
  pendingClips.value.add(idx)
  clipSearchVisible.value = false
  clipSearchIndex.value = null
}

// Render
async function handleRender() {
  isRendering.value = true
  const payload = {}

  if (pendingSubtitles.value) {
    const s = editorData.value.subtitles
    payload.subtitles = {
      enabled: s.enabled,
      style: s.style,
      font: s.font,
      font_size: s.font_size,
      color: s.color,
      outline_color: s.outline_color,
      position: s.position,
      entries: s.entries || null,
    }
  }

  if (pendingAudio.value) {
    payload.audio = {
      narration_volume: editorData.value.audio.narration_volume,
      music_volume: editorData.value.audio.music_volume,
    }
  }

  if (pendingClips.value.size > 0) {
    payload.replaced_clips = [...pendingClips.value].map(idx => {
      const clip = editorData.value.clips[idx]
      return {
        index: idx,
        keywords: clip.keywords,
        video_id: clip.video_id,
        trim_start: clip.trim_start || 0,
        trim_end: clip.trim_end || 0,
      }
    })
  }

  try {
    const renderFn = isSessionBased.value ? renderSessionChanges : renderVideoChanges
    const id = isSessionBased.value ? props.sessionId : props.videoId
    const { data } = await renderFn(id, payload)

    ElMessage.success(data.message || 'Video rendered successfully')

    // Refresh video
    videoKey.value = Date.now()

    // Clear changes
    pendingSubtitles.value = false
    pendingAudio.value = false
    pendingClips.value = new Set()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || 'Render failed')
  } finally {
    isRendering.value = false
  }
}

function goBack() {
  if (window.history.length > 1) {
    router.back()
  } else {
    router.push('/videos')
  }
}

onMounted(() => {
  loadEditorData()
})
</script>

<style scoped>
.video-editor {
  /* Color tokens */
  --bg-primary: #1a1a2e;
  --bg-secondary: #16162a;
  --bg-tertiary: #111122;
  --bg-surface: #1e1e36;
  --bg-input: #22223a;
  --border-primary: #2a2a4a;
  --border-secondary: #22223a;
  --border-active: #3a3a5a;
  --text-primary: #e0e0e0;
  --text-secondary: #c0c0e0;
  --text-muted: #a0a0c0;
  --text-label: #8a8aaa;
  --text-dim: #6a6a8a;
  --accent-blue: #409eff;
  --accent-green: #67c23a;
  --accent-orange: #e6a23c;
  --accent-purple: #b86bff;
  --accent-red: #ff4757;

  position: fixed;
  inset: 0;
  z-index: 200;
  background: var(--bg-primary);
  color: var(--text-primary);
  display: grid;
  grid-template-rows: 52px 1fr 230px;
  grid-template-columns: 1fr 320px;
  overflow: hidden;
  font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
}

/* Toolbar */
.editor-toolbar {
  grid-column: 1 / -1;
  background: linear-gradient(180deg, var(--bg-secondary) 0%, #13132a 100%);
  border-bottom: 1px solid var(--border-primary);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
}

.toolbar-left {
  display: flex;
  align-items: center;
  gap: 14px;
}

.back-btn {
  color: var(--text-muted) !important;
  transition: color 0.15s;
}
.back-btn:hover {
  color: #fff !important;
}

.editor-title {
  font-size: 15px;
  font-weight: 500;
  color: var(--text-primary);
  margin: 0;
  max-width: 400px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

/* Main area */
.editor-main {
  display: contents;
}

.preview-area {
  grid-row: 2;
  grid-column: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: var(--bg-tertiary);
  overflow: hidden;
}

.properties-area {
  grid-row: 2;
  grid-column: 2;
  background: var(--bg-primary);
  border-left: 1px solid var(--border-primary);
  overflow-y: auto;
  overflow-x: hidden;
}

/* Timeline */
.timeline-area {
  grid-row: 3;
  grid-column: 1 / -1;
  background: var(--bg-surface);
  border-top: 1px solid var(--border-primary);
  overflow: hidden;
}

/* Error state */
.editor-error {
  grid-column: 1 / -1;
  grid-row: 2 / 4;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* Dark theme overrides for Element Plus */
.video-editor :deep(.el-loading-mask) {
  background-color: rgba(17, 17, 27, 0.8);
}
.video-editor :deep(.el-tag--warning) {
  background-color: rgba(230, 162, 60, 0.15);
  border-color: rgba(230, 162, 60, 0.3);
  color: var(--accent-orange);
}
.video-editor :deep(.el-button--primary) {
  --el-button-bg-color: var(--accent-blue);
  --el-button-border-color: var(--accent-blue);
}
</style>
