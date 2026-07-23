import http from './http'

export interface LoginPayload {
  phone: string
  password: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
}

export function login(payload: LoginPayload) {
  return http.post<TokenResponse>('/api/auth/login', payload).then((r) => r.data)
}

export function fetchMe() {
  return http.get('/api/auth/me').then((r) => r.data)
}
