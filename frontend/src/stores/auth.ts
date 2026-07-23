import { defineStore } from 'pinia'
import { ref } from 'vue'

import { login as loginApi, type LoginPayload } from '@/api/auth'

const TOKEN_KEY = 'abep_token'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>(localStorage.getItem(TOKEN_KEY) || '')

  async function login(payload: LoginPayload) {
    const res = await loginApi(payload)
    token.value = res.access_token
    localStorage.setItem(TOKEN_KEY, res.access_token)
  }

  function logout() {
    token.value = ''
    localStorage.removeItem(TOKEN_KEY)
  }

  return { token, login, logout }
})
