import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
})

// Set token from localStorage on init
const savedToken = localStorage.getItem('auth_token')
if (savedToken) {
  api.defaults.headers.common['Authorization'] = `Bearer ${savedToken}`
}

// 401 interceptor — redirect to login
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('auth_token')
      localStorage.removeItem('auth_user')
      delete api.defaults.headers.common['Authorization']
      if (window.location.pathname !== '/login') {
        window.location.href = '/login'
      }
    }
    return Promise.reject(error)
  }
)

// === Auth ===

export function googleLogin(idToken) {
  return api.post('/auth/google', { id_token: idToken })
}

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

function _getToken() {
  return localStorage.getItem('auth_token') || ''
}

export function getVideoDownloadUrl(sessionId) {
  return `/api/sessions/${sessionId}/video?token=${_getToken()}`
}

export function getVideoStreamUrl(sessionId) {
  return `/api/sessions/${sessionId}/video/stream?token=${_getToken()}`
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
  return `/api/videos/${videoId}/stream?token=${_getToken()}`
}

export function getLibraryDownloadUrl(videoId) {
  return `/api/videos/${videoId}/download?token=${_getToken()}`
}

export function getThumbnailUrl(videoId) {
  return `/api/videos/${videoId}/thumbnail?token=${_getToken()}`
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
  return `/api/sessions/${sessionId}/editor/preview-stream?token=${_getToken()}`
}

export function getVideoPreviewStreamUrl(videoId) {
  return `/api/videos/${videoId}/editor/preview-stream?token=${_getToken()}`
}

export default api
