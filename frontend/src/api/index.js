import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
})

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

export function startGeneration(sessionId) {
  return api.post(`/sessions/${sessionId}/generate`)
}

export function getJobStatus(sessionId) {
  return api.get(`/sessions/${sessionId}/status`)
}

export function getVideoDownloadUrl(sessionId) {
  return `/api/sessions/${sessionId}/video`
}

export default api
