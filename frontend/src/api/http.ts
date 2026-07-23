import axios from 'axios'
import { ElMessage } from 'element-plus'

import { useAuthStore } from '@/stores/auth'
import router from '@/router'

const http = axios.create({
  baseURL: '/',
  timeout: 120000,
})

// Attach the platform JWT to every request.
http.interceptors.request.use((config) => {
  const auth = useAuthStore()
  if (auth.token) {
    config.headers.Authorization = `Bearer ${auth.token}`
  }
  return config
})

// On 401, clear session and bounce to login.
http.interceptors.response.use(
  (res) => res,
  (error) => {
    const status = error.response?.status
    if (status === 401) {
      useAuthStore().clearLocal()
      router.replace({ name: 'login' })
    }
    const detail = error.response?.data?.detail
    if (detail) ElMessage.error(detail)
    return Promise.reject(error)
  },
)

export default http
