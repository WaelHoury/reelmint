<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { generateClips, getClips, updateClip, confirmClips } from '../api'

const props = defineProps({
  sessionId: { type: String, required: true },
})

const emit = defineEmits(['confirmed', 'back'])

const loading = ref(false)       // true only during initial trigger
const generating = ref(false)    // true while clips_generating
const confirming = ref(false)
const clips = ref([])
const totalClips = ref(0)
const completedClips = ref(0)
const progressPercent = ref(0)
const editingClip = ref(null)
const editForm = ref({ visual_description: '', keywords: '' })
const updatingClip = ref(false)

let pollTimer = null

const allReady = computed(() => {
  return clips.value.length > 0 &&
    clips.value.every(c => c.search_status === 'ready' || c.search_status === 'failed')
})

onMounted(async () => {
  await startClipGeneration()
})

onUnmounted(() => {
  stopPolling()
})

async function startClipGeneration() {
  loading.value = true
  try {
    // Try to get existing clips first
    try {
      const { data } = await getClips(props.sessionId)
      applyClipsData(data)

      // If still generating, start polling
      if (data.status === 'clips_generating') {
        generating.value = true
        loading.value = false
        startPolling()
        return
      }

      // Already done
      if (data.status === 'clips_ready' && data.clips.length > 0) {
        loading.value = false
        return
      }
    } catch {
      // No clips yet — start generation
    }

    // Trigger clip generation (returns immediately)
    const { data } = await generateClips(props.sessionId)
    applyClipsData(data)
    generating.value = true
    startPolling()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || 'Failed to start clip generation')
  } finally {
    loading.value = false
  }
}

function applyClipsData(data) {
  clips.value = data.clips || []
  totalClips.value = data.total_clips || 0
  completedClips.value = data.completed_clips || 0
  progressPercent.value = data.progress_percent || 0
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(pollClips, 1000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function pollClips() {
  try {
    const { data } = await getClips(props.sessionId)
    applyClipsData(data)

    if (data.status === 'clips_ready') {
      generating.value = false
      stopPolling()
    }
  } catch {
    // Silently retry on next poll
  }
}

function startEditClip(clip) {
  editingClip.value = clip.index
  editForm.value = {
    visual_description: clip.visual_description,
    keywords: clip.keywords.join(', '),
  }
}

function cancelEdit() {
  editingClip.value = null
}

async function saveClipEdit(clipIndex) {
  updatingClip.value = true
  try {
    const payload = {
      visual_description: editForm.value.visual_description,
    }

    if (editForm.value.keywords.trim()) {
      payload.keywords = editForm.value.keywords
        .split(',')
        .map(k => k.trim().toLowerCase())
        .filter(Boolean)
    }

    const { data } = await updateClip(props.sessionId, clipIndex, payload)

    const idx = clips.value.findIndex(c => c.index === clipIndex)
    if (idx !== -1) {
      clips.value[idx] = data
    }

    editingClip.value = null
    ElMessage.success('Clip updated with new video')
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || 'Failed to update clip')
  } finally {
    updatingClip.value = false
  }
}

async function handleConfirm() {
  confirming.value = true
  try {
    const { data } = await confirmClips(props.sessionId)
    emit('confirmed', { clips: clips.value })
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || 'Failed to confirm clips')
  } finally {
    confirming.value = false
  }
}
</script>

<template>
  <div>
    <h2 class="step-title">Review Visual Clips</h2>

    <!-- Initial loading (before any clips exist) -->
    <div v-if="loading && clips.length === 0" class="loading-state">
      <el-icon class="is-loading" :size="32" color="var(--ce-accent-light)"><Loading /></el-icon>
      <p class="loading-text">Starting clip generation...</p>
    </div>

    <!-- Clips content (shown as soon as we have any data) -->
    <div v-else>
      <!-- Progress bar while generating -->
      <div v-if="generating" class="generation-progress">
        <el-progress
          :percentage="progressPercent"
          :stroke-width="8"
          color="var(--ce-accent-light)"
        />
        <p class="progress-text">
          Searching for videos... {{ completedClips }} / {{ totalClips }} clips found
        </p>
      </div>

      <el-alert
        v-if="!generating"
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom: 16px"
      >
        Review the visual clips below. Click "Edit" on any clip to change its description — a new matching video will be found.
      </el-alert>

      <div class="clips-grid">
        <div
          v-for="clip in clips"
          :key="clip.index"
          class="clip-card glass-card"
          :class="{ 'clip-pending': clip.search_status === 'pending' }"
        >
          <!-- Clip header -->
          <div class="clip-header">
            <span class="clip-number">Clip {{ clip.index + 1 }}</span>
            <el-button
              v-if="editingClip !== clip.index && clip.search_status !== 'pending'"
              text
              type="primary"
              size="small"
              @click="startEditClip(clip)"
            >
              <el-icon><Edit /></el-icon> Edit
            </el-button>
          </div>

          <!-- Pending state -->
          <div v-if="clip.search_status === 'pending'" class="clip-preview clip-loading-preview">
            <el-icon class="is-loading" :size="24" color="var(--ce-text-muted)"><Loading /></el-icon>
            <span>Searching...</span>
          </div>

          <!-- Failed state -->
          <div v-else-if="clip.search_status === 'failed' && editingClip !== clip.index">
            <div class="clip-preview clip-no-preview">
              <el-icon :size="32" color="var(--el-color-warning)"><WarningFilled /></el-icon>
              <span>No video found</span>
            </div>
            <p class="clip-description">{{ clip.visual_description }}</p>
            <div class="clip-keywords">
              <el-tag v-for="kw in clip.keywords" :key="kw" size="small" type="info">{{ kw }}</el-tag>
            </div>
          </div>

          <!-- View mode (ready) -->
          <div v-else-if="editingClip !== clip.index">
            <div class="clip-preview" v-if="clip.video_preview_url">
              <img
                v-if="clip.media_type === 'image'"
                :src="clip.video_preview_url"
                :alt="clip.visual_description"
                class="preview-video"
              />
              <video
                v-else
                :src="clip.video_preview_url"
                muted
                loop
                autoplay
                playsinline
                class="preview-video"
              />
            </div>
            <div v-else class="clip-preview clip-no-preview">
              <el-icon :size="32"><VideoCamera /></el-icon>
              <span>No preview</span>
            </div>

            <p class="clip-description">{{ clip.visual_description }}</p>

            <div class="clip-keywords">
              <el-tag v-for="kw in clip.keywords" :key="kw" size="small" type="info">{{ kw }}</el-tag>
            </div>

            <div v-if="clip.author || clip.provider" class="clip-author">
              <template v-if="clip.author">by {{ clip.author }}</template>
              <template v-if="clip.provider">{{ clip.author ? ' · ' : '' }}{{ clip.provider }}</template>
              <template v-if="clip.media_type === 'image'"> · still image</template>
            </div>
          </div>

          <!-- Edit mode -->
          <div v-else>
            <el-form label-position="top" size="small">
              <el-form-item label="Visual Description">
                <el-input
                  v-model="editForm.visual_description"
                  type="textarea"
                  :rows="3"
                  placeholder="Describe what the viewer should see..."
                />
              </el-form-item>
              <el-form-item label="Keywords (comma separated)">
                <el-input
                  v-model="editForm.keywords"
                  placeholder="e.g. ocean, waves, sunset"
                />
              </el-form-item>
              <div class="edit-actions">
                <el-button size="small" @click="cancelEdit">Cancel</el-button>
                <el-button
                  type="primary"
                  size="small"
                  :loading="updatingClip"
                  @click="saveClipEdit(clip.index)"
                >
                  Find New Video
                </el-button>
              </div>
            </el-form>
          </div>
        </div>
      </div>

      <!-- Actions -->
      <div class="actions">
        <el-button @click="emit('back')">
          <el-icon><ArrowLeft /></el-icon> Back to Script
        </el-button>

        <el-button
          type="primary"
          size="large"
          @click="handleConfirm"
          :loading="confirming"
          :disabled="!allReady || generating"
        >
          Confirm Clips & Generate Video
          <el-icon class="el-icon--right"><ArrowRight /></el-icon>
        </el-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.step-title {
  margin-bottom: 24px;
  font-size: 22px;
  font-weight: 700;
  color: var(--ce-text-primary);
}

.loading-state {
  text-align: center;
  padding: 48px 0;
}

.loading-text {
  margin-top: 12px;
  color: var(--ce-text-muted);
}

.generation-progress {
  margin-bottom: 20px;
  padding: 16px 20px;
  border-radius: 8px;
  background: var(--ce-surface-secondary, rgba(255,255,255,0.03));
}

.progress-text {
  margin-top: 8px;
  font-size: 13px;
  color: var(--ce-text-muted);
  text-align: center;
}

.clips-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}

.clip-card {
  padding: 20px;
  overflow: hidden;
  transition: opacity 0.3s;
}

.clip-pending {
  opacity: 0.6;
}

.clip-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.clip-number {
  font-weight: 600;
  font-size: 14px;
  color: var(--ce-text-primary);
}

.clip-preview {
  width: 100%;
  aspect-ratio: 16/9;
  border-radius: 8px;
  overflow: hidden;
  background: #111;
  margin-bottom: 12px;
}

.preview-video {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.clip-no-preview,
.clip-loading-preview {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--ce-text-muted);
  gap: 8px;
}

.clip-description {
  font-size: 13px;
  color: var(--ce-text-secondary);
  margin-bottom: 8px;
  line-height: 1.5;
}

.clip-keywords {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 8px;
}

.clip-author {
  font-size: 11px;
  color: var(--ce-text-muted);
}

.edit-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}

.actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 24px;
}
</style>
