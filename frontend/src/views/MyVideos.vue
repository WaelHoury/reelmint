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
  if (!bytes) return '—'
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatDuration(seconds) {
  if (!seconds) return '—'
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return m > 0 ? `${m}m ${s}s` : `${s}s`
}

function formatDate(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString()
}

const orientationTag = (o) => {
  const map = { landscape: '', portrait: '', square: '' }
  return map[o] || o
}

const orientationColor = (o) => {
  const map = { landscape: '', portrait: 'warning', square: 'info' }
  return map[o] || ''
}

onMounted(fetchVideos)
</script>

<template>
  <div>
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px">
      <h2>
        My Videos
        <el-tag v-if="total > 0" type="info" size="small" style="margin-left: 8px">{{ total }}</el-tag>
      </h2>
    </div>

    <div v-loading="loading">
      <el-empty v-if="!loading && videos.length === 0" description="No videos yet. Create your first video!">
        <el-button type="primary" @click="$router.push('/create')">Create Video</el-button>
      </el-empty>

      <el-row :gutter="20" v-if="videos.length > 0">
        <el-col :xs="24" :sm="12" :md="8" :lg="6" v-for="video in videos" :key="video.id" style="margin-bottom: 20px">
          <el-card shadow="hover" :body-style="{ padding: '0' }">
            <div class="thumb-container" @click="playVideo(video)">
              <img v-if="video.has_thumbnail" :src="getThumbnailUrl(video.id)" class="thumb-img" />
              <div v-else class="thumb-placeholder">
                <el-icon :size="48" color="#c0c4cc"><VideoCamera /></el-icon>
              </div>
              <div class="play-overlay">
                <el-icon :size="40" color="#fff"><VideoPlay /></el-icon>
              </div>
            </div>
            <div style="padding: 12px">
              <div class="video-title" :title="video.title">{{ video.title }}</div>
              <div class="video-meta">
                <el-tag size="small" :type="orientationColor(video.orientation)">{{ video.orientation }}</el-tag>
                <span>{{ formatDuration(video.duration_seconds) }}</span>
                <span>{{ formatSize(video.file_size_bytes) }}</span>
              </div>
              <div class="video-meta" style="margin-top: 4px">
                <span style="color: #909399; font-size: 12px">{{ formatDate(video.created_at) }}</span>
              </div>
              <div style="display: flex; gap: 8px; margin-top: 8px">
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
          </el-card>
        </el-col>
      </el-row>

      <el-pagination
        v-if="total > pageSize"
        :current-page="page"
        :page-size="pageSize"
        :total="total"
        layout="prev, pager, next"
        @current-change="handlePageChange"
        style="margin-top: 16px; justify-content: center"
      />
    </div>

    <!-- Play Dialog -->
    <el-dialog v-model="playDialog" :title="playingVideo?.title" width="720px" @close="closePlay" destroy-on-close>
      <video
        v-if="playingVideo"
        :src="getLibraryStreamUrl(playingVideo.id)"
        controls
        autoplay
        style="width: 100%; border-radius: 8px"
      ></video>
    </el-dialog>
  </div>
</template>

<style scoped>
.thumb-container {
  position: relative;
  width: 100%;
  aspect-ratio: 16/9;
  background: #f0f2f5;
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
  background: rgba(0,0,0,0.3);
  opacity: 0;
  transition: opacity 0.2s;
}

.thumb-container:hover .play-overlay {
  opacity: 1;
}

.video-title {
  font-weight: 600;
  font-size: 14px;
  margin-bottom: 6px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.video-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #606266;
}
</style>
