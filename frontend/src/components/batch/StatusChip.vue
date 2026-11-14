<script setup>
import { computed } from 'vue'
import { ITEM_STATUS, BATCH_STATUS } from './batchUtils'

const props = defineProps({
  status: { type: String, required: true },
  kind: { type: String, default: 'item' }, // 'item' | 'batch'
})

const meta = computed(() => {
  const table = props.kind === 'batch' ? BATCH_STATUS : ITEM_STATUS
  return table[props.status] || { label: props.status, tone: 'muted' }
})

const animated = computed(() => ['active', 'upload'].includes(meta.value.tone))
</script>

<template>
  <span class="status-chip" :class="[`tone-${meta.tone}`, { animated }]">
    <span class="dot"></span>
    {{ meta.label }}
  </span>
</template>

<style scoped>
.status-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.3px;
  white-space: nowrap;
  border: 1px solid transparent;
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  flex-shrink: 0;
}

.animated .dot {
  animation: ce-pulse 1.6s ease-in-out infinite;
}

.tone-muted {
  background: rgba(156, 163, 175, 0.1);
  border-color: rgba(156, 163, 175, 0.2);
  color: #9ca3af;
}

.tone-active {
  background: rgba(62, 230, 168, 0.12);
  border-color: rgba(62, 230, 168, 0.25);
  color: var(--ce-accent-light);
}

.tone-upload {
  background: rgba(96, 165, 250, 0.12);
  border-color: rgba(96, 165, 250, 0.25);
  color: #60a5fa;
}

.tone-done {
  background: rgba(45, 212, 191, 0.1);
  border-color: rgba(45, 212, 191, 0.22);
  color: #2dd4bf;
}

.tone-success {
  background: rgba(74, 222, 128, 0.1);
  border-color: rgba(74, 222, 128, 0.2);
  color: #4ade80;
}

.tone-warning {
  background: rgba(251, 191, 36, 0.1);
  border-color: rgba(251, 191, 36, 0.2);
  color: #fbbf24;
}

.tone-danger {
  background: rgba(248, 113, 113, 0.1);
  border-color: rgba(248, 113, 113, 0.2);
  color: #f87171;
}

.tone-cancelled {
  background: rgba(255, 255, 255, 0.03);
  border-color: var(--ce-border-subtle);
  color: var(--ce-text-muted);
}
</style>
