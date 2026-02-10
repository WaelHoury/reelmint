<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getMyVideos, deleteVideo, getLibraryStreamUrl, getLibraryDownloadUrl, getThumbnailUrl } from '../api'

const router = useRouter()

const videos = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(12)
const loading = ref(false)

const playDialog = ref(false)
const playingVideo = ref(null)

async function fetchVideos() {
  loading.value = true
  try {
    const { data } = await getMyVideos(page.value, pageSize.value)
    videos.value = data.videos
    total.value = data.total
  } catch (err) {
    ElMessage.error('Failed to load videos')
  } finally {
    loading.value = false
  }
}

function handlePageChange(newPage) {
  page.value = newPage
  fetchVideos()
}

function playVideo(video) {
  playingVideo.value = video
  playDialog.value = true
}

function closePlay() {
  playDialog.value = false
  playingVideo.value = null
}

function downloadVideo(video) {
  window.open(getLibraryDownloadUrl(video.id), '_blank')
}

async function handleDelete(video) {
  try {
    await ElMessageBox.confirm(
      `Delete "${video.title}"? This cannot be undone.`,
      'Delete Video',
      { type: 'warning', confirmButtonText: 'Delete', cancelButtonText: 'Cancel' }
    )
    await deleteVideo(video.id)
    ElMessage.success('Video deleted')
    fetchVideos()
  } catch (err) {
    if (err !== 'cancel') {
      ElMessage.error('Failed to delete video')
    }
  }
}

function formatSize(bytes) {
  if (!bytes) return '--'
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatDuration(seconds) {
  if (!seconds) return '--'
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return m > 0 ? `${m}m ${s}s` : `${s}s`
}

function formatDate(iso) {
  if (!iso) return '--'
  return new Date(iso).toLocaleDateString()
}

onMounted(fetchVideos)
</script>

<template>
  <div>
    <div class="page-header">
      <h2 class="page-title">
        My Videos
        <el-tag v-if="total > 0" type="info" size="small" class="count-tag">{{ total }}</el-tag>
      </h2>
    </div>

    <div v-loading="loading">
      <el-empty v-if="!loading && videos.length === 0" description="No videos yet. Create your first video!">
        <el-button type="primary" @click="$router.push('/')">Create Video</el-button>
      </el-empty>

      <div v-if="videos.length > 0" class="videos-grid">
        <div v-for="video in videos" :key="video.id" class="video-card glass-card">
          <div class="thumb-container" @click="playVideo(video)">
            <img v-if="video.has_thumbnail" :src="getThumbnailUrl(video.id)" class="thumb-img" />
            <div v-else class="thumb-placeholder">
              <el-icon :size="48" color="var(--ce-text-muted)"><VideoCamera /></el-icon>
            </div>
            <div class="play-overlay">
              <el-icon :size="40" color="#fff"><VideoPlay /></el-icon>
            </div>
          </div>
          <div class="card-body">
            <div class="video-title" :title="video.title">{{ video.title }}</div>
            <div class="video-meta">
              <el-tag size="small">{{ video.orientation }}</el-tag>
              <span>{{ formatDuration(video.duration_seconds) }}</span>
              <span>{{ formatSize(video.file_size_bytes) }}</span>
            </div>
            <div class="video-date">
              {{ formatDate(video.created_at) }}
            </div>
            <div class="card-actions">
              <el-button size="small" type="primary" plain @click.stop="router.push(`/editor/video/${video.id}`)">
                <el-icon><Edit /></el-icon>
              </el-button>
              <el-button size="small" @click.stop="downloadVideo(video)">
                <el-icon><Download /></el-icon>
              </el-button>
              <el-button size="small" type="danger" plain @click.stop="handleDelete(video)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </div>
          </div>
        </div>
      </div>

      <el-pagination
        v-if="total > pageSize"
        :current-page="page"
        :page-size="pageSize"
        :total="total"
        layout="prev, pager, next"
        @current-change="handlePageChange"
        class="pagination"
      />
    </div>

    <!-- Play Dialog -->
    <el-dialog v-model="playDialog" :title="playingVideo?.title" width="720px" @close="closePlay" destroy-on-close>
      <video
        v-if="playingVideo"
        :src="getLibraryStreamUrl(playingVideo.id)"
        controls
        autoplay
        class="dialog-video"
      ></video>
    </el-dialog>
  </div>
</template>

<style scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
}

.page-title {
  font-size: 24px;
  font-weight: 700;
  color: var(--ce-text-primary);
}

.count-tag {
  margin-left: 8px;
  vertical-align: middle;
}

.videos-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 20px;
}

.video-card {
  overflow: hidden;
  transition: transform var(--ce-transition-normal), box-shadow var(--ce-transition-normal);
}

.video-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--ce-shadow-glow);
}

.thumb-container {
  position: relative;
  width: 100%;
  aspect-ratio: 16/9;
  background: #111;
  cursor: pointer;
  overflow: hidden;
}

.thumb-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.thumb-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.play-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.4);
  opacity: 0;
  transition: opacity 0.2s;
}

.thumb-container:hover .play-overlay {
  opacity: 1;
}

.card-body {
  padding: 14px 16px;
}

.video-title {
  font-weight: 600;
  font-size: 14px;
  color: var(--ce-text-primary);
  margin-bottom: 8px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.video-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--ce-text-secondary);
}

.video-date {
  margin-top: 4px;
  color: var(--ce-text-muted);
  font-size: 12px;
}

.card-actions {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}

.pagination {
  margin-top: 24px;
  justify-content: center;
}

.dialog-video {
  width: 100%;
  border-radius: 12px;
}
</style>
