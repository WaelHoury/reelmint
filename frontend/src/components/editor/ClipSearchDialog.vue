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
        :key="result.video_id"
        class="result-card"
        :class="{ selected: selectedId === result.video_id }"
        @click="selectedId = result.video_id"
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
          <span class="result-author">{{ result.author || 'Pexels' }}</span>
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
import { searchReplacementClips } from '../../api'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  initialKeywords: { type: Array, default: () => [] },
  orientation: { type: String, default: 'landscape' },
})

const emit = defineEmits(['update:modelValue', 'select'])

const searchText = ref('')
const results = ref([])
const isSearching = ref(false)
const searched = ref(false)
const selectedId = ref(null)

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
    const { data } = await searchReplacementClips(keywords, props.orientation, 6)
    results.value = data
  } catch (err) {
    results.value = []
  } finally {
    isSearching.value = false
  }
}

function confirmSelection() {
  const result = results.value.find(r => r.video_id === selectedId.value)
  if (result) {
    emit('select', result)
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
  border-radius: 8px;
  overflow: hidden;
  border: 2px solid transparent;
  cursor: pointer;
  transition: border-color 0.2s;
  background: #f5f5f5;
}

.result-card:hover {
  border-color: #409eff88;
}

.result-card.selected {
  border-color: #409eff;
  box-shadow: 0 0 0 2px rgba(64, 158, 255, 0.2);
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
  color: #666;
}

.result-author {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 70%;
}

.result-duration {
  color: #999;
  flex-shrink: 0;
}

.no-results {
  grid-column: 1 / -1;
  text-align: center;
  color: #999;
  padding: 40px 0;
}
</style>
