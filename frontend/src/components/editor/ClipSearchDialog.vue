<template>
  <el-dialog
    :model-value="modelValue"
    @update:model-value="$emit('update:modelValue', $event)"
    title="Replace Clip"
    width="650px"
    :close-on-click-modal="false"
    class="clip-search-dialog"
  >
    <div class="search-bar">
      <el-input
        v-model="searchText"
        placeholder="Search keywords (e.g. sunset ocean waves)"
        @keyup.enter="search"
        size="large"
      >
        <template #append>
          <el-button @click="search" :loading="isSearching">Search</el-button>
        </template>
      </el-input>
    </div>

    <div class="results-grid" v-loading="isSearching">
      <div
        v-for="result in results"
        :key="resultKey(result)"
        class="result-card"
        :class="{ selected: selectedId === resultKey(result) }"
        @click="selectedId = resultKey(result)"
      >
        <video
          :src="result.preview_url"
          muted
          loop
          autoplay
          playsinline
          class="result-video"
        />
        <div class="result-info">
          <span class="result-author">{{ result.author || result.provider || 'Pexels' }}</span>
          <span class="result-duration">{{ result.duration_seconds.toFixed(1) }}s</span>
        </div>
      </div>

      <div v-if="!isSearching && searched && results.length === 0" class="no-results">
        No videos found. Try different keywords.
      </div>
    </div>

    <template #footer>
      <el-button @click="$emit('update:modelValue', false)">Cancel</el-button>
      <el-button type="primary" :disabled="!selectedId" @click="confirmSelection">
        Use This Clip
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { searchReplacementClips, apiErrorMessage } from '../../api'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  initialKeywords: { type: Array, default: () => [] },
  orientation: { type: String, default: 'landscape' },
  // Length of the clip's slot: longer results are preferred
  minDuration: { type: Number, default: 0 },
  // Clips already in the video ({ video_id, provider }), never offered again
  exclude: { type: Array, default: () => [] },
  topic: { type: String, default: '' },
})

const emit = defineEmits(['update:modelValue', 'select'])

const searchText = ref('')
const results = ref([])
const isSearching = ref(false)
const searched = ref(false)
const selectedId = ref(null)
// Keywords of the search the results came from (sent back with the pick)
const searchedKeywords = ref([])

function resultKey(r) {
  return `${r.provider || 'Pexels'}:${r.video_id}`
}

watch(() => props.modelValue, (open) => {
  if (open) {
    searchText.value = props.initialKeywords.join(' ')
    results.value = []
    selectedId.value = null
    searched.value = false
    if (searchText.value) {
      search()
    }
  }
})

async function search() {
  if (!searchText.value.trim()) return
  isSearching.value = true
  searched.value = true
  selectedId.value = null

  try {
    const keywords = searchText.value.trim().split(/\s+/)
    const { data } = await searchReplacementClips(keywords, props.orientation, 6, {
      min_duration: props.minDuration || 0,
      exclude: props.exclude,
      topic: props.topic || null,
    })
    results.value = data
    searchedKeywords.value = keywords
  } catch (err) {
    results.value = []
    ElMessage.error(apiErrorMessage(err, 'Clip search failed'))
  } finally {
    isSearching.value = false
  }
}

function confirmSelection() {
  const result = results.value.find(r => resultKey(r) === selectedId.value)
  if (result) {
    emit('select', { ...result, keywords: [...searchedKeywords.value] })
  }
}
</script>

<style scoped>
.search-bar {
  margin-bottom: 16px;
}

.results-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  min-height: 200px;
}

.result-card {
  border-radius: var(--ce-radius-xs);
  overflow: hidden;
  border: 2px solid var(--ce-border-subtle);
  cursor: pointer;
  transition: border-color 0.2s;
  background: var(--ce-surface-2);
}

.result-card:hover {
  border-color: color-mix(in srgb, var(--ce-selection) 50%, transparent);
}

.result-card.selected {
  border-color: var(--ce-selection);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--ce-selection) 20%, transparent);
}

.result-video {
  width: 100%;
  aspect-ratio: 16 / 9;
  object-fit: cover;
  display: block;
}

.result-info {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 8px;
  font-size: 12px;
  color: var(--ce-text-muted);
}

.result-author {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 70%;
}

.result-duration {
  color: var(--ce-text-secondary);
  flex-shrink: 0;
}

.no-results {
  grid-column: 1 / -1;
  text-align: center;
  color: var(--ce-text-secondary);
  padding: 40px 0;
}
</style>
