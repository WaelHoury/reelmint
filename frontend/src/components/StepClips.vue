<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { generateClips, getClips, updateClip, confirmClips } from '../api'

const props = defineProps({
  sessionId: { type: String, required: true },
})

const emit = defineEmits(['confirmed', 'back'])

const loading = ref(false)
const confirming = ref(false)
const clips = ref([])
const editingClip = ref(null) // Index of clip being edited
const editForm = ref({ visual_description: '', keywords: '' })
const updatingClip = ref(false)

onMounted(async () => {
  await loadClips()
})

async function loadClips() {
  loading.value = true
  try {
    // Try to get existing clips first
    try {
      const { data } = await getClips(props.sessionId)
      clips.value = data.clips
    } catch {
      // No clips yet, generate them
      const { data } = await generateClips(props.sessionId)
      clips.value = data.clips
    }
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || 'Failed to load clips')
  } finally {
    loading.value = false
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

    // Parse keywords if provided
    if (editForm.value.keywords.trim()) {
      payload.keywords = editForm.value.keywords
        .split(',')
        .map(k => k.trim().toLowerCase())
        .filter(Boolean)
    }

    const { data } = await updateClip(props.sessionId, clipIndex, payload)

    // Update local clip data
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

    <!-- Loading -->
    <div v-if="loading" class="loading-state">
      <el-icon class="is-loading" :size="32" color="var(--ce-purple-light)"><Loading /></el-icon>
      <p class="loading-text">Generating visual clips and searching for videos...</p>
    </div>

    <!-- Clips list -->
    <div v-else>
      <el-alert
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
        >
          <!-- Clip header -->
          <div class="clip-header">
            <span class="clip-number">Clip {{ clip.index + 1 }}</span>
            <el-button
              v-if="editingClip !== clip.index"
              text
              type="primary"
              size="small"
              @click="startEditClip(clip)"
            >
              <el-icon><Edit /></el-icon> Edit
            </el-button>
          </div>

          <!-- View mode -->
          <div v-if="editingClip !== clip.index">
            <!-- Video preview -->
            <div class="clip-preview" v-if="clip.video_preview_url">
              <video
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

            <!-- Description -->
            <p class="clip-description">{{ clip.visual_description }}</p>

            <!-- Keywords -->
            <div class="clip-keywords">
              <el-tag
                v-for="kw in clip.keywords"
                :key="kw"
                size="small"
                type="info"
              >
                {{ kw }}
              </el-tag>
            </div>

            <!-- Attribution -->
            <div v-if="clip.author" class="clip-author">
              by {{ clip.author }}
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
          :disabled="clips.length === 0"
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

.clips-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}

.clip-card {
  padding: 20px;
  overflow: hidden;
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

.clip-no-preview {
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
