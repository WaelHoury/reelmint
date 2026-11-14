<script setup>
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { addBatchItems } from '../../api'
import BatchItemsInput from './BatchItemsInput.vue'
import { apiErrorMessage, parseItems } from './batchUtils'

const visible = defineModel({ type: Boolean, default: false })

const props = defineProps({
  batchId: { type: [String, Number], required: true },
})

const emit = defineEmits(['added'])

const mode = ref('prompt')
const topicsText = ref('')
const scriptsText = ref('')
const saving = ref(false)

const items = computed(() => parseItems(mode.value, mode.value === 'script' ? scriptsText.value : topicsText.value))

watch(visible, (open) => {
  if (open) {
    topicsText.value = ''
    scriptsText.value = ''
  }
})

async function submit() {
  if (items.value.length === 0) return
  saving.value = true
  try {
    const { data } = await addBatchItems(props.batchId, items.value)
    const n = items.value.length
    ElMessage.success(`Added ${n} video${n === 1 ? '' : 's'}`)
    emit('added', data)
    visible.value = false
  } catch (err) {
    ElMessage.error(apiErrorMessage(err, 'Failed to add videos'))
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog v-model="visible" title="Add videos" width="620px" :close-on-click-modal="false">
    <BatchItemsInput
      v-model:mode="mode"
      v-model:topics-text="topicsText"
      v-model:scripts-text="scriptsText"
      :rows="6"
    />
    <p class="hint">New videos use this batch's settings and join the end of the queue.</p>
    <template #footer>
      <el-button text @click="visible = false">Cancel</el-button>
      <el-button type="primary" :loading="saving" :disabled="items.length === 0" @click="submit">
        Add {{ items.length || '' }} video{{ items.length === 1 ? '' : 's' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.hint {
  margin-top: 12px;
  font-size: 12px;
  color: var(--ce-text-muted);
}
</style>
