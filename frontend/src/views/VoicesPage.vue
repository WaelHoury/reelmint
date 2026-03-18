<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listFishVoices,
  createFishVoice,
  getFishVoice,
  deleteFishVoice,
  getVoicePreview,
} from '../api'

const route = useRoute()
const router = useRouter()
const returnTo = computed(() => route.query.returnTo || null)

// --- Voice list ---
const voices = ref([])
const loading = ref(true)
const pollingTimers = new Map()

async function loadVoices() {
  loading.value = true
  try {
    const { data } = await listFishVoices()
    voices.value = data.voices
    voices.value
      .filter((voice) => voice.status === 'processing')
      .forEach((voice) => pollVoiceStatus(voice.id))
  } catch {
    ElMessage.error('Failed to load voices')
  } finally {
    loading.value = false
  }
}

onMounted(loadVoices)

// --- Recording state ---
const showRecordDialog = ref(false)
const recordingState = ref('idle') // idle, recording, recorded, uploading
const recordingTime = ref(0)
const maxRecordingTime = 30
let mediaRecorder = null
let activeStream = null
let discardRecordingOnStop = false
let audioChunks = []
let recordingTimer = null
const recordedBlob = ref(null)
const recordedUrl = ref(null)
const cloneTitle = ref('')
const cloneTranscript = ref('')
const consentChecked = ref(false)
const cloneSubmitError = ref('')

// --- File upload alternative ---
const uploadedFile = ref(null)
const uploadMode = ref('record') // 'record' or 'upload'

function resetCloneForm() {
  cloneSubmitError.value = ''
  discardRecordingOnStop = true
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.stop()
  }
  if (activeStream) {
    activeStream.getTracks().forEach((track) => track.stop())
    activeStream = null
  }
  clearInterval(recordingTimer)
  recordingState.value = 'idle'
  recordingTime.value = 0
  recordedBlob.value = null
  if (recordedUrl.value) {
    URL.revokeObjectURL(recordedUrl.value)
    recordedUrl.value = null
  }
  cloneTitle.value = ''
  cloneTranscript.value = ''
  consentChecked.value = false
  uploadedFile.value = null
  uploadMode.value = 'record'
}

function openRecordDialog() {
  resetCloneForm()
  showRecordDialog.value = true
}

function closeRecordDialog() {
  showRecordDialog.value = false
  resetCloneForm()
}

async function startRecording() {
  try {
    cloneSubmitError.value = ''
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    activeStream = stream
    discardRecordingOnStop = false
    audioChunks = []
    mediaRecorder = new MediaRecorder(stream)
    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) audioChunks.push(e.data)
    }
    mediaRecorder.onstop = () => {
      if (activeStream) {
        activeStream.getTracks().forEach((t) => t.stop())
        activeStream = null
      }
      if (discardRecordingOnStop) {
        discardRecordingOnStop = false
        return
      }
      recordedBlob.value = new Blob(audioChunks, { type: 'audio/webm' })
      recordedUrl.value = URL.createObjectURL(recordedBlob.value)
      recordingState.value = 'recorded'
    }
    mediaRecorder.start()
    recordingState.value = 'recording'
    recordingTime.value = 0
    recordingTimer = setInterval(() => {
      recordingTime.value++
      if (recordingTime.value >= maxRecordingTime) {
        stopRecording()
      }
    }, 1000)
  } catch (err) {
    ElMessage.error('Microphone access denied')
  }
}

function stopRecording() {
  discardRecordingOnStop = false
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.stop()
  }
  clearInterval(recordingTimer)
}

function resetRecording() {
  cloneSubmitError.value = ''
  recordingState.value = 'idle'
  recordingTime.value = 0
  recordedBlob.value = null
  uploadedFile.value = null
  if (recordedUrl.value) {
    URL.revokeObjectURL(recordedUrl.value)
    recordedUrl.value = null
  }
}

function handleFileUpload(file) {
  cloneSubmitError.value = ''
  const rawFile = file?.raw || file
  const mimeType = rawFile?.type || file?.type || ''
  if (!mimeType.startsWith('audio/')) {
    ElMessage.error('Please upload an audio file')
    return false
  }
  if ((rawFile?.size || 0) > 50 * 1024 * 1024) {
    ElMessage.error('File too large (max 50MB)')
    return false
  }
  uploadedFile.value = rawFile
  recordingState.value = 'recorded'
  return false // prevent Element auto-upload
}

const canSubmitClone = computed(() => {
  if (!cloneTitle.value.trim()) return false
  if (!consentChecked.value) return false
  if (uploadMode.value === 'record' && !recordedBlob.value) return false
  if (uploadMode.value === 'record' && recordingTime.value < 10) return false
  if (uploadMode.value === 'upload' && !uploadedFile.value) return false
  return true
})

async function submitClone() {
  if (!canSubmitClone.value || recordingState.value === 'uploading') return
  cloneSubmitError.value = ''
  recordingState.value = 'uploading'

  const formData = new FormData()
  formData.append('title', cloneTitle.value.trim())
  formData.append('transcript', cloneTranscript.value.trim())

  if (uploadMode.value === 'record') {
    formData.append('audio_file', recordedBlob.value, 'recording.webm')
  } else {
    formData.append('audio_file', uploadedFile.value)
  }

  try {
    const { data } = await createFishVoice(formData)
    ElMessage.success('Voice clone created! It may take a moment to process.')
    closeRecordDialog()
    voices.value.unshift(data)
    // Start polling for status
    pollVoiceStatus(data.id)
  } catch (err) {
    const msg = err.response?.data?.detail || 'Failed to create voice clone'
    cloneSubmitError.value = typeof msg === 'string' ? msg : JSON.stringify(msg)
    ElMessage.error(cloneSubmitError.value)
    recordingState.value = 'recorded'
  }
}

// --- Polling ---
const pollingIds = new Set()

function pollVoiceStatus(voiceId) {
  if (pollingIds.has(voiceId) || pollingTimers.has(voiceId)) return
  pollingIds.add(voiceId)

  const interval = setInterval(async () => {
    try {
      const { data } = await getFishVoice(voiceId)
      const idx = voices.value.findIndex((v) => v.id === voiceId)
      if (idx >= 0) voices.value[idx] = data
      if (data.status !== 'processing') {
        clearInterval(interval)
        pollingIds.delete(voiceId)
        pollingTimers.delete(voiceId)
        if (data.status === 'ready') {
          ElMessage.success(`Voice "${data.title}" is ready!`)
        }
      }
    } catch {
      clearInterval(interval)
      pollingIds.delete(voiceId)
      pollingTimers.delete(voiceId)
    }
  }, 5000)

  pollingTimers.set(voiceId, interval)
}

onBeforeUnmount(() => {
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.stop()
  }
  clearInterval(recordingTimer)
  stopPreview()
  if (recordedUrl.value) {
    URL.revokeObjectURL(recordedUrl.value)
  }
  for (const timer of pollingTimers.values()) {
    clearInterval(timer)
  }
  pollingTimers.clear()
  pollingIds.clear()
})

// --- Preview ---
const previewAudio = ref(null)
const previewObjectUrl = ref(null)
const playingVoiceKey = ref(null)
const previewLoadingVoiceKey = ref(null)
let previewRequestId = 0

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
      if (previewAudio.value === audio) {
        previewAudio.value = null
      }
      if (previewObjectUrl.value === objectUrl) {
        URL.revokeObjectURL(objectUrl)
        previewObjectUrl.value = null
      }
      if (playingVoiceKey.value === voiceKey) {
        playingVoiceKey.value = null
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

// --- Delete ---
async function handleDelete(voice) {
  try {
    await ElMessageBox.confirm(
      `Delete voice "${voice.title}"? This cannot be undone.`,
      'Delete Voice',
      { confirmButtonText: 'Delete', type: 'warning' }
    )
    await deleteFishVoice(voice.id)
    voices.value = voices.value.filter((v) => v.id !== voice.id)
    ElMessage.success('Voice deleted')
  } catch {
    // Cancelled or error
  }
}

// --- Return to wizard ---
function goBack() {
  if (returnTo.value) {
    router.push(returnTo.value)
  } else {
    router.push('/')
  }
}

function formatTime(seconds) {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}:${s.toString().padStart(2, '0')}`
}
</script>

<template>
  <div class="voices-page">
    <div class="page-header">
      <div>
        <h1 class="page-title">My Voices</h1>
        <p class="page-subtitle">Create custom voice clones for your video narrations</p>
      </div>
      <div class="header-actions">
        <button v-if="returnTo" type="button" class="btn-secondary" @click="goBack">
          <el-icon><ArrowLeft /></el-icon> Back to Wizard
        </button>
        <button type="button" class="btn-primary-glow" @click="openRecordDialog">
          <el-icon><Plus /></el-icon> New Voice Clone
        </button>
      </div>
    </div>

    <!-- Voice List -->
    <div v-if="loading" class="loading-state">
      <el-icon class="is-loading" :size="32"><Loading /></el-icon>
      <span>Loading voices...</span>
    </div>

    <div v-else-if="voices.length === 0" class="empty-state">
      <el-icon :size="48" color="#555"><Microphone /></el-icon>
      <h3>No voice clones yet</h3>
      <p>Record a 10-30 second audio sample to create your first custom voice clone.</p>
      <button type="button" class="btn-primary-glow" @click="openRecordDialog">
        <el-icon><Plus /></el-icon> Create Your First Voice
      </button>
    </div>

    <div v-else class="voice-grid">
      <div
        v-for="voice in voices"
        :key="voice.id"
        class="voice-card"
        :class="{
          processing: voice.status === 'processing',
          'preview-loading': previewLoadingVoiceKey === voice.voice_key,
        }"
      >
        <div class="voice-card-header">
          <span class="voice-title">{{ voice.title }}</span>
          <el-tag :type="voice.status === 'ready' ? 'success' : 'warning'" size="small">
            {{ voice.status }}
          </el-tag>
        </div>

        <div class="voice-card-body">
          <span class="voice-key">{{ voice.voice_key }}</span>
        </div>

        <div class="voice-card-actions">
          <button
            v-if="voice.status === 'ready'"
            type="button"
            class="btn-icon"
            :class="{ active: playingVoiceKey === voice.voice_key }"
            :disabled="previewLoadingVoiceKey === voice.voice_key"
            @click="playPreview(voice.voice_key)"
            title="Preview"
          >
            <el-icon>
              <Loading v-if="previewLoadingVoiceKey === voice.voice_key" class="is-loading" />
              <VideoPause v-else-if="playingVoiceKey === voice.voice_key" />
              <VideoPlay v-else />
            </el-icon>
          </button>
          <button type="button" class="btn-icon danger" @click="handleDelete(voice)" title="Delete">
            <el-icon><Delete /></el-icon>
          </button>
        </div>

        <div v-if="previewLoadingVoiceKey === voice.voice_key" class="voice-card-preview-loading">
          <el-icon class="is-loading"><Loading /></el-icon>
          <span>Loading preview...</span>
        </div>
      </div>
    </div>

    <!-- Clone Dialog -->
    <el-dialog
      v-model="showRecordDialog"
      title="Create Voice Clone"
      width="560px"
      :close-on-click-modal="false"
      destroy-on-close
      @closed="resetCloneForm"
    >
      <el-form label-position="top" @submit.prevent="submitClone">
        <!-- Voice name -->
        <el-form-item label="Voice Name" required>
          <el-input
            v-model="cloneTitle"
            placeholder="e.g. My Narrator Voice"
            maxlength="100"
            show-word-limit
          />
        </el-form-item>

        <!-- Input mode toggle -->
        <el-form-item label="Audio Source">
          <el-radio-group v-model="uploadMode" @change="resetRecording">
            <el-radio-button value="record">Record</el-radio-button>
            <el-radio-button value="upload">Upload File</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <!-- Recording UI -->
        <div v-if="uploadMode === 'record'" class="record-section">
          <div class="record-controls">
            <div v-if="recordingState === 'idle'" class="record-prompt">
              <p>Record a 10-30 second audio sample. Speak naturally in your normal voice.</p>
              <button type="button" class="btn-primary-glow record-btn" @click="startRecording">
                <el-icon><Microphone /></el-icon> Start Recording
              </button>
            </div>

            <div v-else-if="recordingState === 'recording'" class="recording-active">
              <div class="recording-indicator">
                <span class="pulse-dot"></span>
                <span>Recording... {{ formatTime(recordingTime) }} / {{ formatTime(maxRecordingTime) }}</span>
              </div>
              <el-progress
                :percentage="(recordingTime / maxRecordingTime) * 100"
                :show-text="false"
                color="#A78BFA"
              />
              <button type="button" class="btn-secondary" @click="stopRecording">
                <el-icon><VideoPause /></el-icon> Stop
              </button>
            </div>

            <div v-else-if="recordingState === 'recorded'" class="recording-done">
              <audio v-if="recordedUrl" :src="recordedUrl" controls class="audio-preview" />
              <button type="button" class="btn-secondary" @click="resetRecording">
                <el-icon><RefreshRight /></el-icon> Re-record
              </button>
              <p v-if="recordingTime < 10" class="form-note form-note-error">
                Record at least 10 seconds before creating the voice clone.
              </p>
            </div>
          </div>
        </div>

        <!-- File upload -->
        <div v-else class="upload-section">
          <el-upload
            drag
            :auto-upload="false"
            :limit="1"
            accept="audio/*"
            :before-upload="handleFileUpload"
            :on-change="handleFileUpload"
          >
            <el-icon :size="40"><Upload /></el-icon>
            <div class="el-upload__text">
              Drop audio file here or <em>click to upload</em>
            </div>
            <template #tip>
              <div class="el-upload__tip">Audio file, 10-30 seconds, max 50MB</div>
            </template>
          </el-upload>
          <p v-if="uploadedFile" class="form-note">
            Selected file: {{ uploadedFile.name }}
          </p>
        </div>

        <!-- Optional transcript -->
        <el-form-item label="Transcript (optional)">
          <el-input
            v-model="cloneTranscript"
            type="textarea"
            :rows="3"
            placeholder="If you have the exact text of what you said in the recording, paste it here for better cloning accuracy."
            maxlength="5000"
          />
        </el-form-item>

        <!-- Consent -->
        <el-form-item>
          <el-checkbox v-model="consentChecked">
            I confirm that I own the rights to this audio and consent to creating a voice clone from it.
          </el-checkbox>
        </el-form-item>

        <p v-if="cloneSubmitError" class="form-note form-note-error">{{ cloneSubmitError }}</p>
      </el-form>

      <template #footer>
        <button type="button" class="btn-secondary" @click="closeRecordDialog">Cancel</button>
        <button
          type="button"
          class="btn-primary-glow"
          :disabled="!canSubmitClone || recordingState === 'uploading'"
          @click="submitClone"
        >
          <el-icon v-if="recordingState === 'uploading'" class="is-loading"><Loading /></el-icon>
          {{ recordingState === 'uploading' ? 'Creating...' : 'Create Voice Clone' }}
        </button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.voices-page {
  width: min(100%, 960px);
  margin: 0 auto;
  padding-bottom: 40px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  flex-wrap: wrap;
  gap: 16px;
  margin-bottom: 32px;
}

.page-title {
  font-size: 24px;
  font-weight: 700;
  color: var(--ce-text-primary);
  margin: 0 0 4px 0;
}

.page-subtitle {
  font-size: 14px;
  color: var(--ce-text-muted);
  margin: 0;
}

.header-actions {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

/* Voice Grid */
.voice-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 16px;
  align-items: stretch;
}

.voice-card {
  position: relative;
  background: var(--ce-bg-card);
  border: 1px solid var(--ce-border-subtle);
  border-radius: var(--ce-radius-sm);
  padding: 16px;
  min-height: 136px;
  display: flex;
  flex-direction: column;
  transition: border-color var(--ce-transition-fast);
}

.voice-card:hover {
  border-color: var(--ce-border-input);
}

.voice-card.processing {
  opacity: 0.7;
}

.voice-card.preview-loading {
  pointer-events: none;
}

.voice-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.voice-title {
  font-weight: 600;
  color: var(--ce-text-primary);
  font-size: 15px;
  min-width: 0;
}

.voice-card-body {
  margin-bottom: 12px;
}

.voice-key {
  font-size: 12px;
  color: var(--ce-text-muted);
  font-family: monospace;
  word-break: break-all;
}

.voice-card-actions {
  display: flex;
  gap: 8px;
  margin-top: auto;
}

.voice-card-preview-loading {
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: rgba(15, 23, 42, 0.72);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--ce-text-primary);
  font-size: 13px;
  font-weight: 600;
}

.btn-icon {
  width: 32px;
  height: 32px;
  border-radius: var(--ce-radius-xs);
  border: 1px solid var(--ce-border-input);
  background: transparent;
  color: var(--ce-text-secondary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all var(--ce-transition-fast);
}

.btn-icon:hover {
  background: var(--ce-bg-hover);
  color: var(--ce-text-primary);
}

.btn-icon:disabled {
  opacity: 0.7;
  cursor: progress;
}

.btn-icon.active {
  color: var(--ce-purple-light);
  border-color: var(--ce-purple-light);
}

.btn-icon.danger:hover {
  color: #f87171;
  border-color: #f87171;
  background: rgba(248, 113, 113, 0.08);
}

/* Empty state */
.empty-state, .loading-state {
  min-height: 280px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 60px 20px;
  color: var(--ce-text-muted);
  background: var(--ce-bg-card);
  border: 1px dashed var(--ce-border-input);
  border-radius: var(--ce-radius-sm);
}

.empty-state h3 {
  margin: 16px 0 8px;
  color: var(--ce-text-secondary);
}

.empty-state p {
  margin-bottom: 24px;
}

.loading-state {
  gap: 12px;
}

/* Recording */
.record-section {
  margin-bottom: 16px;
}

.record-prompt p {
  font-size: 13px;
  color: var(--ce-text-secondary);
  margin-bottom: 12px;
}

.record-btn {
  padding: 12px 24px;
}

.recording-active {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.recording-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  color: var(--ce-text-primary);
}

.pulse-dot {
  width: 10px;
  height: 10px;
  background: #ef4444;
  border-radius: 50%;
  animation: pulse 1s infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

.recording-done {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.audio-preview {
  width: 100%;
  height: 40px;
}

.upload-section {
  margin-bottom: 16px;
}

.form-note {
  margin: 10px 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--ce-text-muted);
}

.form-note-error {
  color: #fda4af;
}

/* Button styles (inheriting from global theme) */
.btn-secondary {
  padding: 8px 16px;
  border-radius: var(--ce-radius-xs);
  border: 1px solid var(--ce-border-input);
  background: transparent;
  color: var(--ce-text-secondary);
  cursor: pointer;
  font-family: var(--ce-font-family);
  font-size: 13px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  transition: all var(--ce-transition-fast);
}

.btn-secondary:hover {
  background: var(--ce-bg-hover);
  color: var(--ce-text-primary);
}

@media (max-width: 768px) {
  .page-header {
    align-items: stretch;
  }

  .header-actions {
    width: 100%;
    justify-content: stretch;
  }

  .header-actions > button {
    flex: 1 1 220px;
    justify-content: center;
  }

  .voice-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  :deep(.el-dialog) {
    width: calc(100vw - 24px) !important;
    max-width: calc(100vw - 24px) !important;
  }
}
</style>
