import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
})

// === Local user ===

export function getMe() {
  return api.get('/auth/me')
}

// === Config ===

export function getStyles() {
  return api.get('/styles')
}

// === Sessions ===

export function createSession(data) {
  return api.post('/sessions', data)
}

export function getSession(sessionId) {
  return api.get(`/sessions/${sessionId}`)
}

export function deleteSession(sessionId) {
  return api.delete(`/sessions/${sessionId}`)
}

export function listSessions() {
  return api.get('/sessions')
}

// === Script ===

export function generateScript(sessionId) {
  return api.post(`/sessions/${sessionId}/script`)
}

export function getScript(sessionId) {
  return api.get(`/sessions/${sessionId}/script`)
}

export function updateScript(sessionId, scriptText) {
  return api.put(`/sessions/${sessionId}/script`, { script_text: scriptText })
}

export function confirmScript(sessionId) {
  return api.post(`/sessions/${sessionId}/script/confirm`)
}

// === Clips ===

export function generateClips(sessionId) {
  return api.post(`/sessions/${sessionId}/clips`)
}

export function getClips(sessionId) {
  return api.get(`/sessions/${sessionId}/clips`)
}

export function updateClip(sessionId, clipIndex, data) {
  return api.put(`/sessions/${sessionId}/clips/${clipIndex}`, data)
}

export function confirmClips(sessionId) {
  return api.post(`/sessions/${sessionId}/clips/confirm`)
}

// === Video Generation ===

export function checkActiveGeneration() {
  return api.get('/sessions/active-generation')
}

export function startGeneration(sessionId) {
  return api.post(`/sessions/${sessionId}/generate`)
}

export function getJobStatus(sessionId) {
  return api.get(`/sessions/${sessionId}/status`)
}

export function getVideoDownloadUrl(sessionId) {
  return `/api/sessions/${sessionId}/video`
}

export function getVideoStreamUrl(sessionId) {
  return `/api/sessions/${sessionId}/video/stream`
}

// === Subtitles ===

export function updateSubtitles(sessionId, settings) {
  return api.put(`/sessions/${sessionId}/subtitles`, settings)
}

// === Video Library ===

export function getMyVideos(page = 1, pageSize = 20) {
  return api.get('/videos', { params: { page, page_size: pageSize } })
}

export function deleteVideo(videoId) {
  return api.delete(`/videos/${videoId}`)
}

export function getLibraryStreamUrl(videoId) {
  return `/api/videos/${videoId}/stream`
}

export function getLibraryDownloadUrl(videoId) {
  return `/api/videos/${videoId}/download`
}

export function getThumbnailUrl(videoId) {
  return `/api/videos/${videoId}/thumbnail`
}

// === TTS / Voices ===

export function getVoiceCatalog() {
  return api.get('/tts/catalog')
}

export function getVoicePreviewUrl(voiceKey) {
  return `/api/tts/preview?voice_key=${encodeURIComponent(voiceKey)}`
}

export function getVoicePreview(voiceKey) {
  return api.get('/tts/preview', {
    params: { voice_key: voiceKey },
    responseType: 'blob',
  })
}

export function updateSessionTTS(sessionId, ttsSelection) {
  return api.put(`/sessions/${sessionId}/tts`, ttsSelection)
}

export function listFishVoices() {
  return api.get('/tts/fish/voices')
}

export function createFishVoice(formData) {
  return api.post('/tts/fish/voices', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export function getFishVoice(voiceId) {
  return api.get(`/tts/fish/voices/${voiceId}`)
}

export function deleteFishVoice(voiceId) {
  return api.delete(`/tts/fish/voices/${voiceId}`)
}

// === Editor ===

export function getSessionEditorData(sessionId) {
  return api.get(`/sessions/${sessionId}/editor-data`)
}

export function getVideoEditorData(videoId) {
  return api.get(`/videos/${videoId}/editor-data`)
}

export function renderSessionChanges(sessionId, payload) {
  return api.put(`/sessions/${sessionId}/editor/render`, payload)
}

export function renderVideoChanges(videoId, payload) {
  return api.put(`/videos/${videoId}/editor/render`, payload)
}

export function searchReplacementClips(keywords, orientation = 'landscape', count = 6) {
  return api.post('/editor/search-clips', { keywords, orientation, count })
}

export function getSessionPreviewStreamUrl(sessionId) {
  return `/api/sessions/${sessionId}/editor/preview-stream`
}

export function getVideoPreviewStreamUrl(videoId) {
  return `/api/videos/${videoId}/editor/preview-stream`
}

// === Social Publishing ===

export function getConnectedAccounts() {
  return api.get('/publish/accounts')
}

export function startOAuth(provider, returnTo = '/account') {
  return api.post(`/publish/oauth/${provider}/start`, { return_to: returnTo })
}

export function disconnectAccount(accountId) {
  return api.delete(`/publish/accounts/${accountId}`)
}

export function publishVideo(videoId, payload) {
  return api.post(`/videos/${videoId}/publish`, payload)
}

export function getPublishJobs(videoId) {
  return api.get(`/videos/${videoId}/publish-jobs`)
}

export function getPublishJob(jobId) {
  return api.get(`/publish/jobs/${jobId}`)
}

export function retryPublishJob(jobId) {
  return api.post(`/publish/jobs/${jobId}/retry`)
}

export default api
