import {
  MIN_INTERVAL_MINUTES,
  localInputToDate,
  nextFullHour,
  toLocalInputValue,
} from './batchUtils'

export const YOUTUBE_CATEGORIES = [
  { id: '22', label: 'People & Blogs' },
  { id: '27', label: 'Education' },
  { id: '24', label: 'Entertainment' },
  { id: '28', label: 'Science & Technology' },
  { id: '26', label: 'Howto & Style' },
  { id: '25', label: 'News & Politics' },
  { id: '23', label: 'Comedy' },
  { id: '1', label: 'Film & Animation' },
  { id: '10', label: 'Music' },
  { id: '15', label: 'Pets & Animals' },
  { id: '17', label: 'Sports' },
  { id: '19', label: 'Travel & Events' },
  { id: '20', label: 'Gaming' },
  { id: '2', label: 'Autos & Vehicles' },
]

export function createPublishForm() {
  return {
    enabled: false,
    accountId: null,
    privacyStatus: 'private',
    madeForKids: null, // must be answered explicitly
    categoryId: '22',
    extraTags: '',
    descriptionFooter: '',
    scheduleMode: 'immediate',
    startLocal: toLocalInputValue(nextFullHour()),
    intervalValue: 4,
    intervalUnit: 'hours',
  }
}

export function intervalMinutes(form) {
  const value = Number(form.intervalValue) || 0
  return Math.round(form.intervalUnit === 'hours' ? value * 60 : value)
}

export function parseTags(text) {
  const seen = new Set()
  return (text || '')
    .split(',')
    .map((t) => t.trim())
    .filter((t) => {
      if (!t || seen.has(t.toLowerCase())) return false
      seen.add(t.toLowerCase())
      return true
    })
}

// Returns a user-facing message for the first problem, or '' when valid.
export function validatePublishForm(form) {
  if (!form.enabled) return ''
  if (!form.accountId) return 'Choose a YouTube channel to publish to.'
  if (form.madeForKids === null) return 'Answer whether the videos are made for kids.'
  if (form.scheduleMode === 'interval') {
    const start = localInputToDate(form.startLocal)
    if (!start) return 'Pick a start date and time for the schedule.'
    if (start.getTime() < Date.now() - 60000) return 'The schedule start time is in the past.'
    if (intervalMinutes(form) < MIN_INTERVAL_MINUTES) {
      return `The interval must be at least ${MIN_INTERVAL_MINUTES} minutes.`
    }
  }
  return ''
}

export function buildPublishPayload(form) {
  if (!form.enabled) return { enabled: false }
  const payload = {
    enabled: true,
    account_id: form.accountId,
    privacy_status: form.privacyStatus,
    made_for_kids: !!form.madeForKids,
    category_id: form.categoryId,
    extra_tags: parseTags(form.extraTags),
    description_footer: form.descriptionFooter.trim(),
    schedule: { mode: 'immediate' },
  }
  if (form.scheduleMode === 'interval') {
    payload.schedule = {
      mode: 'interval',
      start_at: localInputToDate(form.startLocal).toISOString(),
      interval_minutes: intervalMinutes(form),
    }
  }
  return payload
}
