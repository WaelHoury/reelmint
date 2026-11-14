<template>
  <div
    class="timeline-block"
    :class="{ selected, compact, trimming: isTrimming }"
    :style="blockStyle"
    :title="label"
    @click.stop="$emit('click')"
  >
    <!-- Left trim handle -->
    <div v-if="trimmable && !compact"
         class="trim-handle trim-handle-left"
         @mousedown.stop="startTrim('start', $event)">
      <div class="handle-grip"></div>
    </div>

    <span class="block-label" v-if="!compact || pixelWidth > 30">{{ label }}</span>

    <!-- Right trim handle -->
    <div v-if="trimmable && !compact"
         class="trim-handle trim-handle-right"
         @mousedown.stop="startTrim('end', $event)">
      <div class="handle-grip"></div>
    </div>

    <!-- Trim duration tooltip -->
    <div v-if="isTrimming" class="trim-tooltip">
      {{ effectiveDuration }}s
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const PIXELS_PER_SECOND = 50

const props = defineProps({
  start: { type: Number, required: true },
  end: { type: Number, required: true },
  label: { type: String, default: '' },
  zoom: { type: Number, default: 1 },
  selected: { type: Boolean, default: false },
  // Any CSS colour, typically a theme token such as var(--ce-track-video)
  color: { type: String, default: 'var(--ce-track-video)' },
  compact: { type: Boolean, default: false },
  trimmable: { type: Boolean, default: false },
  trimStart: { type: Number, default: 0 },
  trimEnd: { type: Number, default: 0 },
  clipDuration: { type: Number, default: 0 },
})

const emit = defineEmits(['click', 'trim-change'])

const isTrimming = ref(false)

const effectiveDuration = computed(() => {
  const dur = (props.end - props.start) - props.trimStart - props.trimEnd
  return Math.max(0, dur).toFixed(1)
})

const pixelWidth = computed(() => {
  if (props.trimmable) {
    const dur = (props.end - props.start) - props.trimStart - props.trimEnd
    return Math.max(4, dur * PIXELS_PER_SECOND * props.zoom)
  }
  return Math.max(4, (props.end - props.start) * PIXELS_PER_SECOND * props.zoom)
})

const blockStyle = computed(() => {
  const pps = PIXELS_PER_SECOND * props.zoom
  const effectiveStart = props.trimmable ? props.start + props.trimStart : props.start
  const effectiveDur = props.trimmable
    ? (props.end - props.start) - props.trimStart - props.trimEnd
    : (props.end - props.start)
  const left = effectiveStart * pps
  const width = Math.max(4, effectiveDur * pps)
  // Tints are mixed from the track colour so any token / CSS colour works
  const fill = `color-mix(in srgb, ${props.color} ${props.selected ? 34 : 22}%, transparent)`
  const borderColor = props.selected
    ? 'var(--ce-selection)'
    : `color-mix(in srgb, ${props.color} 55%, transparent)`

  return {
    position: 'absolute',
    left: `${left}px`,
    width: `${width}px`,
    top: props.compact ? '4px' : '3px',
    height: props.compact ? '28px' : '30px',
    backgroundColor: fill,
    border: `1px solid ${borderColor}`,
    borderRadius: '4px',
    cursor: 'pointer',
    overflow: 'hidden',
    transition: isTrimming.value ? 'none' : 'left 0.15s, width 0.15s, border-color 0.15s, background-color 0.15s',
  }
})

function startTrim(side, event) {
  if (!props.trimmable) return
  event.preventDefault()
  isTrimming.value = true

  const startX = event.clientX
  const initialTrimStart = props.trimStart
  const initialTrimEnd = props.trimEnd
  const pps = PIXELS_PER_SECOND * props.zoom
  const minEffective = 0.5

  const onMove = (e) => {
    const deltaX = e.clientX - startX
    const deltaSec = deltaX / pps

    if (side === 'start') {
      const newTrim = Math.max(0, initialTrimStart + deltaSec)
      const maxTrim = props.clipDuration - (props.trimEnd || 0) - minEffective
      emit('trim-change', {
        trimStart: Math.round(Math.min(newTrim, Math.max(0, maxTrim)) * 10) / 10,
        trimEnd: props.trimEnd,
      })
    } else {
      const newTrim = Math.max(0, initialTrimEnd - deltaSec)
      const maxTrim = props.clipDuration - (props.trimStart || 0) - minEffective
      emit('trim-change', {
        trimStart: props.trimStart,
        trimEnd: Math.round(Math.min(newTrim, Math.max(0, maxTrim)) * 10) / 10,
      })
    }
  }

  const onUp = () => {
    isTrimming.value = false
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
  }

  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}
</script>

<style scoped>
.timeline-block {
  display: flex;
  align-items: center;
  padding: 0 6px;
}

.timeline-block:hover {
  filter: brightness(1.2);
}

/* Selected = mint outline + soft glow */
.timeline-block.selected {
  box-shadow: 0 0 0 1px var(--ce-selection),
              0 0 10px color-mix(in srgb, var(--ce-selection) 35%, transparent);
  z-index: 2;
}

.block-label {
  font-size: 10px;
  color: var(--ce-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  user-select: none;
  line-height: 1;
  position: relative;
  z-index: 3;
  flex: 1;
  min-width: 0;
}

.compact .block-label {
  font-size: 9px;
}

/* Trim handles */
.trim-handle {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 10px;
  cursor: col-resize;
  z-index: 5;
  opacity: 0;
  transition: opacity 0.15s;
  display: flex;
  align-items: center;
  justify-content: center;
}

.timeline-block:hover .trim-handle,
.timeline-block.trimming .trim-handle {
  opacity: 1;
}

.trim-handle-left {
  left: -1px;
  border-radius: 4px 0 0 4px;
  background: color-mix(in srgb, var(--ce-text-primary) 20%, transparent);
}

.trim-handle-right {
  right: -1px;
  border-radius: 0 4px 4px 0;
  background: color-mix(in srgb, var(--ce-text-primary) 20%, transparent);
}

.trim-handle:hover {
  background: color-mix(in srgb, var(--ce-selection) 45%, transparent);
}

.handle-grip {
  width: 2px;
  height: 14px;
  background: var(--ce-text-primary);
  opacity: 0.85;
  border-radius: 1px;
}

/* Trim tooltip */
.trim-tooltip {
  position: absolute;
  top: -22px;
  left: 50%;
  transform: translateX(-50%);
  background: var(--ce-surface-3);
  border: 1px solid var(--ce-border-strong);
  color: var(--ce-text-primary);
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 3px;
  white-space: nowrap;
  pointer-events: none;
  z-index: 10;
}
</style>
