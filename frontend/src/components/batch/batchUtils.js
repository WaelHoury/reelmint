// Shared helpers for the Autopilot (batch queue) pages.

// A line that contains only three or more dashes separates scripts.
const SCRIPT_SEPARATOR = /^[ \t]*-{3,}[ \t]*$/m

export const MIN_INTERVAL_MINUTES = 15

export const ACTIVE_ITEM_STATUSES = ['scripting', 'finding_clips', 'rendering', 'publishing']

export const ITEM_STATUS = {
  pending: { label: 'Queued', tone: 'muted' },
  scripting: { label: 'Writing script', tone: 'active' },
  finding_clips: { label: 'Finding clips', tone: 'active' },
  rendering: { label: 'Rendering', tone: 'active' },
  publishing: { label: 'Uploading', tone: 'upload' },
  done: { label: 'Done', tone: 'done' },
  published: { label: 'Published', tone: 'success' },
  failed: { label: 'Failed', tone: 'danger' },
  cancelled: { label: 'Cancelled', tone: 'cancelled' },
}

export const BATCH_STATUS = {
  draft: { label: 'Draft', tone: 'muted' },
  running: { label: 'Running', tone: 'active' },
  paused: { label: 'Paused', tone: 'warning' },
  completed: { label: 'Completed', tone: 'success' },
  cancelled: { label: 'Cancelled', tone: 'cancelled' },
}

export const COUNT_LABELS = [
  { key: 'pending', label: 'queued', tone: 'muted' },
  { key: 'in_progress', label: 'in progress', tone: 'active' },
  { key: 'done', label: 'done', tone: 'done' },
  { key: 'published', label: 'published', tone: 'success' },
  { key: 'failed', label: 'failed', tone: 'danger' },
  { key: 'cancelled', label: 'cancelled', tone: 'cancelled' },
]

export function isItemActive(item) {
  return ACTIVE_ITEM_STATUSES.includes(item?.status)
}

// --- Parsing the topics / scripts textarea ---

function splitTopics(text) {
  return (text || '').split('\n').map((line) => line.trim())
}

function splitScripts(text) {
  return (text || '').split(SCRIPT_SEPARATOR).map((block) => block.trim())
}

export function parseItems(mode, text) {
  const inputType = mode === 'script' ? 'script' : 'prompt'
  const parts = inputType === 'script' ? splitScripts(text) : splitTopics(text)
  return parts.filter(Boolean).map((part) => ({ input_type: inputType, text: part }))
}

// Remove the item at `index` (index among non-empty items) and return the new textarea value.
export function removeItemFromText(mode, text, index) {
  const parts = (mode === 'script' ? splitScripts(text) : splitTopics(text)).filter(Boolean)
  parts.splice(index, 1)
  return mode === 'script' ? parts.join('\n\n---\n\n') : parts.join('\n')
}

export function previewText(text, max = 80) {
  const flat = (text || '').replace(/\s+/g, ' ').trim()
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat
}

// --- Dates ---

// Backend datetimes may come without a timezone suffix; treat those as UTC.
export function parseServerDate(value) {
  if (!value) return null
  const str = String(value)
  const hasZone = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(str)
  const date = new Date(hasZone ? str : `${str}Z`)
  return Number.isNaN(date.getTime()) ? null : date
}

export function formatDateTime(value) {
  const date = value instanceof Date ? value : parseServerDate(value)
  if (!date) return '--'
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

// Hands-off retries: the backend sets next_attempt_at when it will try an item again.
// Returns the Date if that time is still in the future, else null.
export function nextRetryDate(item, now = Date.now()) {
  const date = parseServerDate(item?.next_attempt_at)
  return date && date.getTime() > now ? date : null
}

export function hasScheduledRetry(item, now = Date.now()) {
  return !!nextRetryDate(item, now) && ['pending', 'failed'].includes(item?.status)
}

// "14:05" in the viewer's local time; adds the date when it is not today.
export function formatRetryTime(date) {
  if (!date) return ''
  const time = date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  const today = new Date()
  if (date.toDateString() === today.toDateString()) return time
  return `${date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}, ${time}`
}

export function formatDate(value) {
  const date = value instanceof Date ? value : parseServerDate(value)
  if (!date) return '--'
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

function formatTime(date) {
  return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

function formatDay(date) {
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

export function defaultBatchName() {
  return `Batch – ${formatDate(new Date())}`
}

// "YYYY-MM-DDTHH:mm" in local time, as used by <input type="datetime-local">.
export function toLocalInputValue(date) {
  const pad = (n) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function localInputToDate(value) {
  if (!value) return null
  const date = new Date(value) // no zone suffix → parsed as local time
  return Number.isNaN(date.getTime()) ? null : date
}

export function nextFullHour() {
  const date = new Date()
  date.setMinutes(0, 0, 0)
  date.setHours(date.getHours() + 1)
  return date
}

// --- Schedule ---

export function computeSchedule(start, intervalMinutes, count) {
  if (!start || !intervalMinutes || count < 1) return []
  return Array.from({ length: count }, (_, i) => new Date(start.getTime() + i * intervalMinutes * 60000))
}

// "Nov 15 09:00, 13:00, 17:00, 21:00, Nov 16 01:00" — the day is repeated only when it changes.
export function formatSchedule(dates, maxShown = 8) {
  const describe = (list) => {
    let lastDay = null
    return list.map((date) => {
      const day = formatDay(date)
      const time = formatTime(date)
      if (day === lastDay) return time
      lastDay = day
      return `${day} ${time}`
    })
  }
  if (dates.length <= maxShown) return describe(dates).join(', ')
  const head = describe(dates.slice(0, maxShown - 1))
  const last = dates[dates.length - 1]
  return `${head.join(', ')}, … last on ${formatDay(last)} ${formatTime(last)}`
}

// Most uploads that can land inside any 24-hour window for an evenly spaced schedule.
export function uploadsPerDay(intervalMinutes, count) {
  if (!intervalMinutes || intervalMinutes <= 0) return count
  return Math.min(count, Math.ceil((24 * 60) / intervalMinutes))
}

// --- Errors ---

export function apiErrorMessage(err, fallback = 'Something went wrong') {
  const detail = err?.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail) && detail.length > 0) {
    return detail.map((d) => d?.msg || String(d)).join('; ')
  }
  if (detail && typeof detail === 'object' && detail.message) return detail.message
  return fallback
}
