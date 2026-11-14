<script setup>
import { ref, computed } from 'vue'
import StatusChip from './StatusChip.vue'
import { formatDateTime, formatRetryTime, isItemActive, nextRetryDate, previewText } from './batchUtils'

const props = defineProps({
  item: { type: Object, required: true },
  number: { type: Number, required: true },
  busy: { type: Boolean, default: false },
})

const emit = defineEmits(['retry', 'remove'])

const showError = ref(false)

const active = computed(() => isItemActive(props.item))
const displayTitle = computed(() => props.item.title || previewText(props.item.text_preview, 100) || 'Untitled')
const canRetry = computed(() => ['failed', 'cancelled'].includes(props.item.status))
const canRemove = computed(() => ['pending', 'failed', 'cancelled'].includes(props.item.status))
const showSchedule = computed(
  () => props.item.scheduled_publish_at && !['published', 'failed', 'cancelled'].includes(props.item.status)
)
const progress = computed(() => {
  const p = Number(props.item.progress_percent)
  return Number.isFinite(p) ? Math.max(0, Math.min(100, Math.round(p))) : null
})
const retryAt = computed(() => (['pending', 'failed'].includes(props.item.status) ? nextRetryDate(props.item) : null))
const errorFirstLine = computed(() => previewText((props.item.error_message || '').split('\n')[0], 120))
</script>

<template>
  <div class="item-row" :class="{ active, failed: item.status === 'failed' }">
    <div class="position">{{ number }}</div>

    <div class="main">
      <div class="title-line">
        <span class="title" :title="item.text_preview">{{ displayTitle }}</span>
        <span class="type-tag">{{ item.input_type === 'script' ? 'Script' : 'Topic' }}</span>
      </div>
      <div v-if="item.title && item.text_preview && item.text_preview.trim() !== item.title.trim()" class="subtext">{{ previewText(item.text_preview, 120) }}</div>

      <div v-if="active" class="live">
        <div class="live-bar">
          <span
            class="live-fill"
            :class="{ indeterminate: progress === null }"
            :style="progress !== null ? { width: `${progress}%` } : {}"
          ></span>
        </div>
        <span class="live-text">
          {{ item.current_step || 'Working...' }}<template v-if="progress !== null"> · {{ progress }}%</template>
        </span>
      </div>

      <div class="meta">
        <span v-if="retryAt" class="meta-item retry-at" :title="retryAt.toLocaleString()">
          <el-icon><RefreshRight /></el-icon> Retrying at {{ formatRetryTime(retryAt) }}
        </span>
        <span v-if="showSchedule" class="meta-item">
          <el-icon><Clock /></el-icon> Publishes {{ formatDateTime(item.scheduled_publish_at) }}
        </span>
        <span v-if="item.finished_at && !active" class="meta-item">
          Finished {{ formatDateTime(item.finished_at) }}
        </span>
        <span v-if="item.attempts > 1" class="meta-item">Attempt {{ item.attempts }}</span>
        <router-link v-if="item.video_id" :to="`/editor/video/${item.video_id}`" class="link">
          <el-icon><VideoPlay /></el-icon> View video
        </router-link>
        <a v-if="item.youtube_url" :href="item.youtube_url" target="_blank" rel="noopener noreferrer" class="link">
          <el-icon><TopRight /></el-icon> Open on YouTube
        </a>
      </div>

      <div v-if="item.error_message" class="error-box">
        <button type="button" class="error-toggle" @click="showError = !showError">
          <el-icon><WarningFilled /></el-icon>
          <span class="error-summary">{{ showError ? 'Hide error' : errorFirstLine }}</span>
          <el-icon class="chevron" :class="{ open: showError }"><ArrowDown /></el-icon>
        </button>
        <pre v-if="showError" class="error-full">{{ item.error_message }}</pre>
      </div>
    </div>

    <div class="side">
      <StatusChip :status="item.status" />
      <div v-if="canRetry || canRemove" class="row-actions">
        <el-button v-if="canRetry" size="small" type="primary" plain :disabled="busy" @click="emit('retry', item)">
          Retry
        </el-button>
        <el-button v-if="canRemove" size="small" type="danger" plain :disabled="busy" @click="emit('remove', item)">
          Remove
        </el-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.item-row {
  display: flex;
  gap: 14px;
  padding: 14px 18px;
  border-bottom: 1px solid var(--ce-border-subtle);
  transition: background var(--ce-transition-fast);
}

.item-row:last-child {
  border-bottom: none;
}

.item-row.active {
  background: rgba(62, 230, 168, 0.05);
}

.position {
  width: 26px;
  flex-shrink: 0;
  text-align: right;
  font-size: 13px;
  font-weight: 600;
  color: var(--ce-text-muted);
  padding-top: 1px;
}

.main {
  flex: 1;
  min-width: 0;
}

.title-line {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.title {
  font-size: 14px;
  font-weight: 600;
  color: var(--ce-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.type-tag {
  flex-shrink: 0;
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--ce-text-muted);
  border: 1px solid var(--ce-border-input);
  border-radius: 4px;
  padding: 1px 6px;
}

.subtext {
  margin-top: 2px;
  font-size: 12px;
  color: var(--ce-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.live {
  margin-top: 10px;
  display: flex;
  align-items: center;
  gap: 10px;
}

.live-bar {
  flex: 1;
  max-width: 320px;
  height: 6px;
  border-radius: 999px;
  background: #1a1a1a;
  overflow: hidden;
  position: relative;
}

.live-fill {
  display: block;
  height: 100%;
  background: var(--ce-accent-gradient);
  transition: width var(--ce-transition-slow);
}

.live-fill.indeterminate {
  width: 30%;
  position: absolute;
  animation: slide 1.4s ease-in-out infinite;
}

@keyframes slide {
  0% { left: -30%; }
  100% { left: 100%; }
}

.live-text {
  font-size: 12px;
  color: var(--ce-accent-light);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 16px;
  margin-top: 8px;
  font-size: 12px;
  color: var(--ce-text-muted);
}

.meta:empty {
  display: none;
}

.meta-item,
.link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.retry-at {
  color: #fbbf24;
  font-weight: 600;
}

.link {
  color: var(--ce-accent-light);
  text-decoration: none;
}

.link:hover {
  text-decoration: underline;
}

.error-box {
  margin-top: 8px;
}

.error-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  background: transparent;
  border: none;
  padding: 0;
  cursor: pointer;
  font-family: var(--ce-font-family);
  font-size: 12px;
  color: #f87171;
  text-align: left;
}

.error-summary {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chevron {
  flex-shrink: 0;
  transition: transform var(--ce-transition-fast);
}

.chevron.open {
  transform: rotate(180deg);
}

.error-full {
  margin-top: 8px;
  padding: 10px 12px;
  max-height: 200px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.5;
  color: var(--ce-text-secondary);
  background: rgba(248, 113, 113, 0.06);
  border: 1px solid rgba(248, 113, 113, 0.2);
  border-radius: var(--ce-radius-xs);
}

.side {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 10px;
  flex-shrink: 0;
}

.row-actions {
  display: flex;
  gap: 6px;
}

.row-actions .el-button + .el-button {
  margin-left: 0;
}

@media (max-width: 640px) {
  .item-row {
    flex-wrap: wrap;
  }

  .side {
    width: 100%;
    flex-direction: row;
    justify-content: space-between;
    align-items: center;
    padding-left: 40px;
  }
}
</style>
