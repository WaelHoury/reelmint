<script setup>
import { ref, computed, onMounted } from 'vue'
import { getConnectedAccounts, getYoutubeUploadQuota } from '../../api'
import {
  MIN_INTERVAL_MINUTES,
  computeSchedule,
  formatSchedule,
  localInputToDate,
  uploadsPerDay,
} from './batchUtils'
import { YOUTUBE_CATEGORIES, intervalMinutes } from './publishForm'

const form = defineModel({ type: Object, required: true })

const props = defineProps({
  itemCount: { type: Number, default: 0 },
})

const accounts = ref([])
const loadingAccounts = ref(true)
const quota = ref(null)

const youtubeAccounts = computed(() =>
  accounts.value.filter((a) => a.provider === 'youtube' && a.status === 'active')
)
const hasInactiveYoutube = computed(() =>
  accounts.value.some((a) => a.provider === 'youtube' && a.status !== 'active')
)

const minutes = computed(() => intervalMinutes(form.value))
const intervalTooShort = computed(() => minutes.value < MIN_INTERVAL_MINUTES)

const scheduleDates = computed(() => {
  if (form.value.scheduleMode !== 'interval' || intervalTooShort.value) return []
  return computeSchedule(localInputToDate(form.value.startLocal), minutes.value, props.itemCount)
})

const schedulePreview = computed(() => {
  if (scheduleDates.value.length === 0) return ''
  const n = scheduleDates.value.length
  return `${n} video${n === 1 ? '' : 's'} → ${formatSchedule(scheduleDates.value)}`
})

const quotaWarning = computed(() => {
  const limit = Number(quota.value?.uploads_per_day_estimate)
  if (!form.value.enabled || !limit || props.itemCount === 0) return ''
  if (form.value.scheduleMode === 'interval') {
    if (intervalTooShort.value) return ''
    const perDay = uploadsPerDay(minutes.value, props.itemCount)
    if (perDay > limit) {
      return `This schedule uploads up to ${perDay} videos in 24 hours, above the estimated limit of ${limit} per day. Uploads past the limit will fail until the quota resets. Use a longer interval.`
    }
    return ''
  }
  if (props.itemCount > limit) {
    return `${props.itemCount} videos may be uploaded within a day, above the estimated limit of ${limit} per day. Consider spreading them out.`
  }
  return ''
})

onMounted(async () => {
  const [accountsRes, quotaRes] = await Promise.allSettled([getConnectedAccounts(), getYoutubeUploadQuota()])
  if (accountsRes.status === 'fulfilled') {
    accounts.value = accountsRes.value.data.accounts || []
    if (!form.value.accountId && youtubeAccounts.value.length > 0) {
      form.value.accountId = youtubeAccounts.value[0].id
    }
  }
  if (quotaRes.status === 'fulfilled') quota.value = quotaRes.value.data
  loadingAccounts.value = false
})
</script>

<template>
  <div class="publish-settings">
    <div class="toggle-row">
      <div>
        <div class="toggle-title">Publish to YouTube when each video is ready</div>
        <div class="helper-text">Each finished video is uploaded right away or on the schedule you set.</div>
      </div>
      <el-switch
        v-model="form.enabled"
        :disabled="loadingAccounts || youtubeAccounts.length === 0"
      />
    </div>

    <el-skeleton v-if="loadingAccounts" :rows="1" animated class="skeleton" />

    <div v-else-if="youtubeAccounts.length === 0" class="no-account">
      <p>
        {{ hasInactiveYoutube
          ? 'Your YouTube connection has expired. Reconnect it to publish automatically.'
          : 'No YouTube channel is connected. Connect one to publish automatically.' }}
      </p>
      <button type="button" class="btn-secondary small-btn" @click="$router.push('/account')">
        <el-icon><Link /></el-icon>
        Go to Account
      </button>
    </div>

    <el-form v-else-if="form.enabled" label-position="top" class="publish-form" @submit.prevent>
      <div class="grid-2">
        <el-form-item label="Channel">
          <el-select v-model="form.accountId" placeholder="Select channel" style="width: 100%">
            <el-option
              v-for="account in youtubeAccounts"
              :key="account.id"
              :value="account.id"
              :label="account.display_name || account.username"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="Privacy">
          <el-select v-model="form.privacyStatus" style="width: 100%">
            <el-option value="public" label="Public" />
            <el-option value="unlisted" label="Unlisted" />
            <el-option value="private" label="Private" />
          </el-select>
        </el-form-item>

        <el-form-item label="Made for kids (required by YouTube)">
          <el-radio-group v-model="form.madeForKids">
            <el-radio-button :value="false">No</el-radio-button>
            <el-radio-button :value="true">Yes</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <el-form-item label="Category">
          <el-select v-model="form.categoryId" style="width: 100%">
            <el-option v-for="cat in YOUTUBE_CATEGORIES" :key="cat.id" :value="cat.id" :label="cat.label" />
          </el-select>
        </el-form-item>
      </div>

      <el-form-item label="Extra tags">
        <el-input v-model="form.extraTags" placeholder="science, facts, shorts" />
        <p class="helper-text">Comma separated. Added to every video.</p>
      </el-form-item>

      <el-form-item label="Description footer">
        <el-input
          v-model="form.descriptionFooter"
          type="textarea"
          :rows="3"
          maxlength="2000"
          placeholder="Appended to every description, e.g. links to your website or socials"
        />
      </el-form-item>

      <el-form-item label="Schedule">
        <el-radio-group v-model="form.scheduleMode" class="schedule-modes">
          <el-radio value="immediate">Publish as soon as each video is ready</el-radio>
          <el-radio value="interval">Spread out on a schedule</el-radio>
        </el-radio-group>
      </el-form-item>

      <div v-if="form.scheduleMode === 'interval'" class="schedule-box">
        <div class="schedule-row">
          <span class="schedule-label">First at</span>
          <el-input v-model="form.startLocal" type="datetime-local" class="datetime-input" />
          <span class="schedule-label">then every</span>
          <el-input-number v-model="form.intervalValue" :min="1" :max="form.intervalUnit === 'hours' ? 168 : 10080" :step="1" />
          <el-select v-model="form.intervalUnit" style="width: 110px">
            <el-option value="hours" label="hours" />
            <el-option value="minutes" label="minutes" />
          </el-select>
        </div>
        <p v-if="intervalTooShort" class="warn-text">
          The interval must be at least {{ MIN_INTERVAL_MINUTES }} minutes.
        </p>
        <p v-else-if="schedulePreview" class="schedule-preview">{{ schedulePreview }}</p>
        <p class="helper-text">Times are in your local time zone.</p>
      </div>

      <p v-if="quotaWarning" class="warn-text">
        <el-icon><WarningFilled /></el-icon>
        {{ quotaWarning }}
      </p>
      <p v-if="quota?.note" class="quota-note">
        <el-icon><InfoFilled /></el-icon>
        {{ quota.note }}
      </p>
    </el-form>
  </div>
</template>

<style scoped>
.toggle-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
}

.toggle-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--ce-text-primary);
}

.helper-text {
  font-size: 12px;
  color: var(--ce-text-muted);
  margin-top: 4px;
  line-height: 1.5;
}

.skeleton {
  margin-top: 16px;
}

.no-account {
  margin-top: 16px;
  padding: 16px;
  border: 1px dashed var(--ce-border-input);
  border-radius: var(--ce-radius-sm);
  background: rgba(255, 255, 255, 0.03);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.no-account p {
  font-size: 13px;
  color: var(--ce-text-secondary);
}

.small-btn {
  padding: 8px 16px;
  font-size: 13px;
}

.publish-form {
  margin-top: 20px;
}

.grid-2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 20px;
}

.schedule-modes {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
}

.schedule-box {
  margin: -6px 0 18px;
  padding: 16px;
  border: 1px solid var(--ce-border-subtle);
  border-radius: var(--ce-radius-sm);
  background: rgba(62, 230, 168, 0.04);
}

.schedule-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.schedule-label {
  font-size: 13px;
  color: var(--ce-text-secondary);
}

.datetime-input {
  width: 240px;
}

.datetime-input :deep(input) {
  color-scheme: dark;
}

.schedule-preview {
  margin-top: 12px;
  font-size: 13px;
  color: var(--ce-accent-light);
  line-height: 1.5;
}

.warn-text {
  display: flex;
  gap: 6px;
  align-items: flex-start;
  margin-top: 10px;
  font-size: 12px;
  line-height: 1.5;
  color: #fbbf24;
}

.warn-text .el-icon {
  margin-top: 2px;
  flex-shrink: 0;
}

.quota-note {
  display: flex;
  gap: 6px;
  align-items: flex-start;
  margin-top: 10px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--ce-text-muted);
}

.quota-note .el-icon {
  margin-top: 2px;
  flex-shrink: 0;
}

@media (max-width: 640px) {
  .grid-2 {
    grid-template-columns: 1fr;
  }
}
</style>
