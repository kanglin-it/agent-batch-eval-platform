import http from './http'

export interface TaskListItem {
  id: number
  name: string
  eval_workflow_id: string
  eval_skill_id: string | null
  case_count: number
  status: 'agent_running' | 'comparing' | 'completed' | 'failed'
  progress: string
  failed_count: number
  win_rate: number | null
  avg_latency_ms: number | null
  creator: string
  created_at: string
}

export interface TaskPage {
  total: number
  items: TaskListItem[]
}

export interface CreateTaskPayload {
  name: string
  eval_workflow_id: string
  case_ids: string[]
  filter_snapshot?: Record<string, unknown>
  creator?: string
}

export function listTasks(page = 1, pageSize = 20) {
  return http
    .get<TaskPage>('/api/eval-tasks', { params: { page, page_size: pageSize } })
    .then((r) => r.data)
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
