<script setup>
import { computed } from 'vue'
import { COUNT_LABELS } from './batchUtils'

const props = defineProps({
  counts: { type: Object, default: () => ({}) },
  total: { type: Number, default: 0 },
  compact: { type: Boolean, default: false },
})

const c = (key) => Number(props.counts?.[key] || 0)

const finished = computed(() => c('done') + c('published'))

function pct(n) {
  return props.total > 0 ? `${(n / props.total) * 100}%` : '0%'
}

const segments = computed(() => [
  { key: 'finished', width: pct(finished.value) },
  { key: 'failed', width: pct(c('failed')) },
  { key: 'cancelled', width: pct(c('cancelled')) },
  { key: 'active', width: pct(c('in_progress')) },
])

const visibleCounts = computed(() =>
  COUNT_LABELS.filter((entry) => c(entry.key) > 0).map((entry) => ({ ...entry, value: c(entry.key) }))
)
</script>

<template>
  <div class="batch-progress" :class="{ compact }">
    <div class="progress-head">
      <span class="progress-text">
        {{ finished }} of {{ total }} finished
      </span>
      <span v-if="!compact && total > 0" class="progress-pct">
        {{ Math.round((finished / total) * 100) }}%
      </span>
    </div>
    <div class="bar">
      <span
        v-for="seg in segments"
        :key="seg.key"
        class="seg"
        :class="`seg-${seg.key}`"
        :style="{ width: seg.width }"
      ></span>
    </div>
    <div v-if="visibleCounts.length" class="counts">
      <span v-for="entry in visibleCounts" :key="entry.key" class="count" :class="`tone-${entry.tone}`">
        <span class="count-dot"></span>{{ entry.value }} {{ entry.label }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.batch-progress {
  width: 100%;
}

.progress-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 6px;
}

.progress-text {
  font-size: 13px;
  color: var(--ce-text-secondary);
}

.compact .progress-text {
  font-size: 12px;
}

.progress-pct {
  font-size: 13px;
  font-weight: 600;
  color: var(--ce-accent-light);
}

.bar {
  display: flex;
  height: 8px;
  border-radius: 999px;
  background: #1a1a1a;
  overflow: hidden;
}

.compact .bar {
  height: 6px;
}

.seg {
  height: 100%;
  transition: width var(--ce-transition-slow);
}

.seg-finished {
  background: var(--ce-accent-gradient);
}

.seg-failed {
  background: #f87171;
}

.seg-cancelled {
  background: #3a3a3a;
}

.seg-active {
  background: rgba(62, 230, 168, 0.35);
  animation: ce-pulse 1.6s ease-in-out infinite;
}

.counts {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  margin-top: 8px;
}

.count {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--ce-text-secondary);
}

.count-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}

.tone-muted .count-dot { background: #9ca3af; }
.tone-active .count-dot { background: var(--ce-accent-light); }
.tone-done .count-dot { background: #2dd4bf; }
.tone-success .count-dot { background: #4ade80; }
.tone-danger .count-dot { background: #f87171; }
.tone-cancelled .count-dot { background: #555; }
</style>
