<template>
  <div class="video-editor" v-loading="isLoading">
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
      :min-duration="clipSearchMinDuration"
      :exclude="clipSearchExclude"
      :topic="editorData?.title || ''"
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
  apiErrorMessage,
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
// Subtitle fields the user explicitly set (the rest follow the style preset)
const subtitleOverrides = ref(new Set())
// Caption text/timing edited: only then are entries sent (keeps word timing)
const subtitleEntriesEdited = ref(false)
const SUBTITLE_OVERRIDE_KEYS = ['font', 'font_size', 'color', 'outline_color', 'position']

const hasChanges = computed(() => {
  return pendingSubtitles.value || pendingAudio.value || pendingClips.value.size > 0
})

// Clip search dialog
const clipSearchVisible = ref(false)
const clipSearchKeywords = ref([])
const clipSearchIndex = ref(null)
const clipSearchMinDuration = ref(0)
const clipSearchExclude = ref([])

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
    return getSessionPreviewStreamUrl(props.sessionId) + `?_t=${t}`
  }
  return getVideoPreviewStreamUrl(props.videoId) + `?_t=${t}`
})

// Load editor data. silent: refresh after a render without the full-page overlay
async function loadEditorData({ silent = false } = {}) {
  if (!silent) {
    isLoading.value = true
    errorMessage.value = ''
  }
  try {
    let response
    if (isSessionBased.value) {
      response = await getSessionEditorData(props.sessionId)
    } else {
      response = await getVideoEditorData(props.videoId)
    }
    editorData.value = response.data
    duration.value = editorData.value.duration_seconds || 0
    subtitleOverrides.value = new Set(editorData.value.subtitles?.overrides || [])
    subtitleEntriesEdited.value = false
  } catch (err) {
    const detail = apiErrorMessage(err, err.message)
    if (silent) {
      ElMessage.warning(`Rendered, but the editor could not refresh: ${detail}`)
    } else {
      errorMessage.value = detail
    }
  } finally {
    if (!silent) isLoading.value = false
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

function onSubtitleSettingsChange(settings, { reset = [] } = {}) {
  if (!editorData.value) return
  editorData.value.subtitles = { ...editorData.value.subtitles, ...settings }
  const overrides = new Set(subtitleOverrides.value)
  if ('style' in settings) {
    // A preset was applied: every field now follows that preset
    overrides.clear()
  } else {
    for (const key of Object.keys(settings)) {
      if (SUBTITLE_OVERRIDE_KEYS.includes(key)) overrides.add(key)
    }
  }
  for (const key of reset) overrides.delete(key)
  subtitleOverrides.value = overrides
  pendingSubtitles.value = true
}

function onSubtitleEntryChange({ index, entry }) {
  if (!editorData.value?.subtitles?.entries) return
  const entries = [...editorData.value.subtitles.entries]
  entries[index] = { ...entries[index], ...entry }
  editorData.value.subtitles.entries = entries
  subtitleEntriesEdited.value = true
  pendingSubtitles.value = true
}

function onAudioSettingsChange(settings) {
  if (!editorData.value) return
  editorData.value.audio = { ...editorData.value.audio, ...settings }
  pendingAudio.value = true
}

// The loop copy (last clip in loop mode) mirrors clip 1; the server rejects edits to it
function isLoopCopy(index) {
  return editorData.value?.clips?.[index]?.loop_copy === true
}

function onClipSettingsChange({ index, changes }) {
  if (!editorData.value?.clips || isLoopCopy(index)) return
  const clips = [...editorData.value.clips]
  clips[index] = { ...clips[index], ...changes }
  editorData.value.clips = clips
  pendingClips.value.add(index)
}

function onTimelineTrimChange({ index, trimStart, trimEnd }) {
  if (!editorData.value?.clips || isLoopCopy(index)) return
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
  if (clip && !clip.loop_copy) {
    clipSearchKeywords.value = clip.keywords || []
    clipSearchIndex.value = clipIndex
    // Slot length: the replacement should cover it without a freeze-frame
    clipSearchMinDuration.value = clip.duration_seconds || 0
    // Never offer footage the video's other clips already use
    clipSearchExclude.value = (editorData.value.clips || [])
      .filter((c, i) => i !== clipIndex && c.video_id)
      .map(c => ({ video_id: String(c.video_id), provider: c.provider || 'Pexels' }))
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
    provider: result.provider || 'Pexels',
    media_type: result.media_type || 'video',
    // The keywords the user actually searched with
    keywords: result.keywords?.length ? result.keywords : clipSearchKeywords.value,
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
    payload.subtitles = { enabled: s.enabled, style: s.style }
    // Only the fields the user changed; the rest come from the preset
    for (const key of subtitleOverrides.value) {
      if (s[key] !== null && s[key] !== undefined && s[key] !== '') payload.subtitles[key] = s[key]
    }
    // Entries only when caption text/timing changed (keeps word timing otherwise)
    if (subtitleEntriesEdited.value && s.entries) payload.subtitles.entries = s.entries
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
        keywords: clip.keywords || [],
        video_id: clip.video_id,
        provider: clip.provider || null,
        media_type: clip.media_type || null,
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

    // Clear changes
    pendingSubtitles.value = false
    pendingAudio.value = false
    pendingClips.value = new Set()

    // Reload so clip/caption times match the new render, then refresh video
    await loadEditorData({ silent: true })
    videoKey.value = Date.now()
  } catch (err) {
    if (err.response?.status === 429) {
      ElMessage.error('Too many renders, wait a minute')
    } else {
      ElMessage.error(apiErrorMessage(err, 'Render failed'))
    }
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
  /* All colours come from the app theme (assets/styles/theme.css) */
  position: fixed;
  inset: 0;
  z-index: 200;
  background: var(--ce-bg-base);
  color: var(--ce-text-primary);
  display: grid;
  grid-template-rows: 52px 1fr 230px;
  grid-template-columns: 1fr 320px;
  overflow: hidden;
  font-family: var(--ce-font-family, 'Inter', system-ui, sans-serif);
}

/* Toolbar */
.editor-toolbar {
  grid-column: 1 / -1;
  background: var(--ce-surface-1);
  border-bottom: 1px solid var(--ce-border-subtle);
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
  color: var(--ce-text-secondary) !important;
  transition: color 0.15s;
}
.back-btn:hover {
  color: var(--ce-text-primary) !important;
}

.editor-title {
  font-size: 15px;
  color: var(--ce-text-primary);
  font-weight: 600;
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
  background:
    radial-gradient(ellipse 70% 60% at 50% 40%, var(--ce-accent-glow) 0%, transparent 70%),
    var(--ce-bg-base);
  overflow: hidden;
}

.properties-area {
  grid-row: 2;
  grid-column: 2;
  background: var(--ce-surface-1);
  border-left: 1px solid var(--ce-border-subtle);
  overflow-y: auto;
  overflow-x: hidden;
}

/* Timeline */
.timeline-area {
  grid-row: 3;
  grid-column: 1 / -1;
  background: var(--ce-timeline-bg);
  border-top: 1px solid var(--ce-border-subtle);
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

</style>
