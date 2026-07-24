import http from './http'

export interface TaskListItem {
  id: number
  name: string
  eval_workflow_id: string
  eval_skill_id: string | null
  case_count: number
  status: 'agent_running' | 'comparing' | 'completed' | 'failed'
  progress: string
  win_rate: number | null
  avg_latency_ms: number | null
  creator: string
  created_at: string
}

export interface CreateTaskPayload {
  name: string
  eval_workflow_id: string
  case_ids: string[]
  filter_snapshot?: Record<string, unknown>
  creator?: string
}

export function listTasks() {
  return http.get<TaskListItem[]>('/api/eval-tasks').then((r) => r.data)
}

export function listWorkflowIds() {
  return http.get<string[]>('/api/eval-tasks/workflow-ids').then((r) => r.data)
}

export function createTask(payload: CreateTaskPayload) {
  return http.post<TaskListItem>('/api/eval-tasks', payload).then((r) => r.data)
}

export function retryTask(id: number) {
  return http.post<TaskListItem>(`/api/eval-tasks/${id}/retry`).then((r) => r.data)
}

export function downloadResultExcel(id: number) {
  return http.get(`/api/eval-tasks/${id}/download`, { responseType: 'blob' })
}
