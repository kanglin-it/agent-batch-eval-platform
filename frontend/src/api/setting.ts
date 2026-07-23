import http from './http'

export interface LoginTtl {
  login_ttl_seconds: number
  login_ttl_days: number
}

export function getLoginTtl() {
  return http.get<LoginTtl>('/api/settings/login-ttl').then((r) => r.data)
}

export function updateLoginTtlDays(days: number) {
  return http.put<LoginTtl>('/api/settings/login-ttl', { login_ttl_days: days }).then((r) => r.data)
}
