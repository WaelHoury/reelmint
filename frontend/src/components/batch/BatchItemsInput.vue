<script setup>
import { computed, ref } from 'vue'
import { suggestBatchTopics } from '../../api'
import { apiErrorMessage, parseItems, removeItemFromText, previewText } from './batchUtils'

const mode = defineModel('mode', { type: String, default: 'prompt' })
const topicsText = defineModel('topicsText', { type: String, default: '' })
const scriptsText = defineModel('scriptsText', { type: String, default: '' })
// "Let AI pick topics" mode: niche + count, suggested topics land in aiTopicsText (one per line)
const aiTopicsText = defineModel('aiTopicsText', { type: String, default: '' })
const niche = defineModel('niche', { type: String, default: '' })
const aiCount = defineModel('aiCount', { type: Number, default: 10 })

const props = defineProps({
  rows: { type: Number, default: 8 },
  allowAi: { type: Boolean, default: false },
  // Optional narration style, forwarded to the topic suggester
  suggestStyle: { type: String, default: '' },
})

const NICHE_EXAMPLES = ['weird food facts', 'space mysteries', 'ancient history', 'animal superpowers']

const suggesting = ref(false)
const suggestError = ref('')

const TOPICS_PLACEHOLDER = 'How bees make honey\nWhy the sky is blue\nThe history of the bicycle'
const SCRIPTS_PLACEHOLDER = 'First script...\n\n---\n\nSecond script...'

const isScripts = computed(() => mode.value === 'script')
const isAi = computed(() => mode.value === 'ai')

const text = computed({
  get: () => (isScripts.value ? scriptsText.value : isAi.value ? aiTopicsText.value : topicsText.value),
  set: (value) => {
    if (isScripts.value) scriptsText.value = value
    else if (isAi.value) aiTopicsText.value = value
    else topicsText.value = value
  },
})

async function suggestTopics() {
  const n = niche.value.trim()
  if (!n || suggesting.value) return
  suggesting.value = true
  suggestError.value = ''
  try {
    const { data } = await suggestBatchTopics({ niche: n, count: aiCount.value, style: props.suggestStyle || undefined })
    const topics = (data?.topics || []).map((t) => String(t).trim()).filter(Boolean)
    if (topics.length === 0) {
      suggestError.value = 'No topics came back. Try a broader or different niche.'
      return
    }
    aiTopicsText.value = topics.join('\n')
  } catch (err) {
    suggestError.value = apiErrorMessage(err, 'Could not suggest topics. Try again.')
  } finally {
    suggesting.value = false
  }
}

const items = computed(() => parseItems(mode.value, text.value))

const countLabel = computed(() => {
  const n = items.value.length || (isAi.value && niche.value.trim() ? aiCount.value : 0)
  return `${n} video${n === 1 ? '' : 's'}`
})

function removeAt(index) {
  text.value = removeItemFromText(mode.value, text.value, index)
}
</script>

<template>
  <div class="items-input">
    <div class="mode-row">
      <el-radio-group v-model="mode">
        <el-radio-button value="prompt">
          <el-icon><MagicStick /></el-icon> Topics
        </el-radio-button>
        <el-radio-button value="script">
          <el-icon><Document /></el-icon> Scripts
        </el-radio-button>
        <el-radio-button v-if="allowAi" value="ai">
          <el-icon><Opportunity /></el-icon> Let AI pick topics
        </el-radio-button>
      </el-radio-group>
      <span class="count-badge" :class="{ empty: items.length === 0 }">{{ countLabel }}</span>
    </div>

    <p class="helper-text">
      <template v-if="isAi">
        Describe a niche and how many videos you want. Preview the suggested topics and edit them, or skip the
        preview and topics are picked when the batch runs.
      </template>
      <template v-else-if="!isScripts">
        One topic per line. A script is written for each topic.
      </template>
      <template v-else>
        Paste full narration scripts, used as written. Separate scripts with a line containing only
        <code>---</code>.
      </template>
    </p>

    <div v-if="isAi" class="ai-panel">
      <div class="ai-row">
        <div class="ai-field ai-niche">
          <label class="ai-label" for="ai-niche-input">Niche</label>
          <el-input
            id="ai-niche-input"
            v-model="niche"
            maxlength="120"
            placeholder="e.g. weird food facts, space mysteries"
            clearable
            @keyup.enter="suggestTopics"
          />
        </div>
        <div class="ai-field ai-count">
          <label class="ai-label">Videos</label>
          <el-input-number v-model="aiCount" :min="1" :max="50" :step="1" step-strictly controls-position="right" />
        </div>
        <el-button
          type="primary"
          class="ai-suggest-btn"
          :loading="suggesting"
          :disabled="!niche.trim()"
          @click="suggestTopics"
        >
          <el-icon v-if="!suggesting"><MagicStick /></el-icon>
          <span>{{ aiTopicsText.trim() ? 'Suggest again' : 'Suggest topics' }}</span>
        </el-button>
      </div>
      <div v-if="!niche.trim()" class="ai-examples">
        <span class="ai-examples-label">Try:</span>
        <button v-for="ex in NICHE_EXAMPLES" :key="ex" type="button" class="ai-example" @click="niche = ex">
          {{ ex }}
        </button>
      </div>
      <p v-if="suggesting" class="ai-status">
        <el-icon class="is-loading"><Loading /></el-icon> Thinking up {{ aiCount }} topics…
      </p>
      <p v-else-if="suggestError" class="ai-error" role="alert">
        <el-icon><WarningFilled /></el-icon> {{ suggestError }}
      </p>
      <template v-if="aiTopicsText.trim() || items.length">
        <p class="ai-edit-hint">Suggested topics. Edit, add or delete lines freely; one topic per line.</p>
        <el-input v-model="text" type="textarea" :rows="rows" :disabled="suggesting" />
      </template>
    </div>

    <el-input
      v-else-if="!isScripts"
      v-model="text"
      type="textarea"
      :rows="rows"
      :placeholder="TOPICS_PLACEHOLDER"
    />
    <el-input
      v-else
      v-model="text"
      type="textarea"
      :rows="rows + 4"
      :placeholder="SCRIPTS_PLACEHOLDER"
    />

    <div v-if="items.length > 0" class="preview-list">
      <div v-for="(item, index) in items" :key="`${index}-${item.text.length}`" class="preview-item">
        <span class="preview-index">{{ index + 1 }}</span>
        <span class="preview-text" :title="item.text">{{ previewText(item.text) }}</span>
        <button type="button" class="remove-btn" title="Remove" @click="removeAt(index)">
          <el-icon :size="14"><Close /></el-icon>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.items-input {
  width: 100%;
}

.mode-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.ai-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.ai-row {
  display: flex;
  align-items: flex-end;
  gap: 12px;
  flex-wrap: wrap;
}

.ai-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.ai-niche {
  flex: 1 1 260px;
  min-width: 0;
}

.ai-count :deep(.el-input-number) {
  width: 120px;
}

.ai-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--ce-text-secondary);
}

.ai-suggest-btn {
  height: 32px;
}

.ai-examples {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.ai-examples-label {
  font-size: 12px;
  color: var(--ce-text-muted);
}

.ai-example {
  background: var(--ce-bg-tag);
  border: 1px solid var(--ce-border-input);
  color: var(--ce-text-secondary);
  border-radius: 999px;
  padding: 3px 10px;
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  transition: border-color var(--ce-transition-fast), color var(--ce-transition-fast);
}

.ai-example:hover {
  border-color: var(--ce-accent-light);
  color: #fff;
}

.ai-status,
.ai-error,
.ai-edit-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}

.ai-status {
  color: var(--ce-accent-light);
}

.ai-error {
  color: #f87171;
}

.ai-edit-hint {
  color: var(--ce-text-muted);
  font-size: 12px;
  margin-top: 4px;
}

@media (max-width: 640px) {
  .ai-row {
    align-items: stretch;
  }

  .ai-count,
  .ai-suggest-btn {
    flex: 1 1 auto;
  }

  .ai-count :deep(.el-input-number) {
    width: 100%;
  }
}

.count-badge {
  background: rgba(62, 230, 168, 0.1);
  color: var(--ce-accent-light);
  padding: 4px 12px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 600;
}

.count-badge.empty {
  background: rgba(255, 255, 255, 0.04);
  color: var(--ce-text-muted);
}

.helper-text {
  font-size: 12px;
  color: var(--ce-text-muted);
  margin: 10px 0 8px;
  line-height: 1.5;
}

.helper-text code {
  background: var(--ce-bg-tag);
  border: 1px solid var(--ce-border-input);
  border-radius: 4px;
  padding: 0 4px;
  color: var(--ce-text-secondary);
}

.preview-list {
  margin-top: 12px;
  max-height: 260px;
  overflow-y: auto;
  border: 1px solid var(--ce-border-subtle);
  border-radius: var(--ce-radius-sm);
}

.preview-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--ce-border-subtle);
  font-size: 13px;
}

.preview-item:last-child {
  border-bottom: none;
}

.preview-index {
  width: 22px;
  flex-shrink: 0;
  color: var(--ce-text-muted);
  font-size: 12px;
  text-align: right;
}

.preview-text {
  flex: 1;
  min-width: 0;
  color: var(--ce-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.remove-btn {
  width: 24px;
  height: 24px;
  border: none;
  background: transparent;
  color: var(--ce-text-muted);
  border-radius: 50%;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: all var(--ce-transition-fast);
}

.remove-btn:hover {
  color: #f87171;
  background: rgba(248, 113, 113, 0.1);
}
</style>
