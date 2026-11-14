import { ref, computed } from 'vue'
import { getMe } from '../api'

// Self-hosted, single-user app: no login. This just holds the local user's
// profile (name/email) for display, loaded once from /api/auth/me.
const user = ref(null)
let loading = null

export const currentUser = computed(() => user.value)

export function loadCurrentUser() {
  if (!loading) {
    loading = getMe()
      .then(({ data }) => {
        user.value = data
        return data
      })
      .catch(() => {
        loading = null
        return null
      })
  }
  return loading
}
