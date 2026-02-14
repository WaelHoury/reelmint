import { ref, computed } from 'vue'
import api from '../api'

const token = ref(localStorage.getItem('auth_token') || '')
const user = ref(JSON.parse(localStorage.getItem('auth_user') || 'null'))

export const isAuthenticated = computed(() => !!token.value)
export const currentUser = computed(() => user.value)

// Set Authorization header on init if token exists
if (token.value) {
  api.defaults.headers.common['Authorization'] = `Bearer ${token.value}`
}

export function setAuth(newToken, newUser) {
  token.value = newToken
  user.value = newUser
  localStorage.setItem('auth_token', newToken)
  localStorage.setItem('auth_user', JSON.stringify(newUser))
  api.defaults.headers.common['Authorization'] = `Bearer ${newToken}`
}

export function logout() {
  token.value = ''
  user.value = null
  localStorage.removeItem('auth_token')
  localStorage.removeItem('auth_user')
  delete api.defaults.headers.common['Authorization']
}
