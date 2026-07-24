<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { downloadResultExcel, listTasks, retryTask, type TaskListItem } from '@/api/task'

const router = useRouter()
const tasks = ref<TaskListItem[]>([])
const loading = ref(false)
let timer: ReturnType<typeof setInterval> | null = null

const STATUS_LABEL: Record<TaskListItem['status'], string> = {
  agent_running: 'Agent执行中',
  comparing: '对比评测中',
  completed: '已完成',
  failed: '失败',
}

async function load() {
  loading.value = true
  try {
    tasks.value = await listTasks()
  } finally {
    loading.value = false
  }
}

function goCreate() {
  ElMessage.info('请先选择评测用例，再创建评测任务')
  router.push({ name: 'cases' })
}

async function onRetry(row: TaskListItem) {
  await retryTask(row.id)
  ElMessage.success('已重新执行')
  load()
}

// Only finished tasks (completed / failed) can be downloaded; running ones can't.
function canDownload(row: TaskListItem) {
  return row.status === 'completed' || row.status === 'failed'
}

async function onDownload(row: TaskListItem) {
  if (!canDownload(row)) return
  try {
    const res = await downloadResultExcel(row.id)
    const blob = new Blob([res.data], { type: res.headers['content-type'] })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `评测结果_${displayId(row)}.xlsx`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  } catch {
    ElMessage.error('下载失败')
  }
}

function displayId(row: TaskListItem) {
  const d = row.created_at ? new Date(row.created_at) : new Date()
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `TSK-${y}${m}${day}-${String(row.id).padStart(3, '0')}`
}

function statusText(row: TaskListItem) {
  if (row.status === 'failed') return STATUS_LABEL.failed
  return `${STATUS_LABEL[row.status]} (${row.progress})`
}

function statusClass(status: TaskListItem['status']) {
  return {
    agent_running: 'st-running',
    comparing: 'st-comparing',
    completed: 'st-done',
    failed: 'st-failed',
  }[status]
}

onMounted(() => {
  load()
  // 进行中的任务自动刷新进度
  timer = setInterval(() => {
    const busy = tasks.value.some((t) => t.status === 'agent_running' || t.status === 'comparing')
    if (busy) load()
  }, 5000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">评测任务管理</h2>
      <el-button type="primary" @click="goCreate">+ 新建任务</el-button>
    </div>

    <div class="panel">
      <el-table :data="tasks" v-loading="loading" border stripe>
        <el-table-column label="任务ID" width="180">
          <template #default="{ row }">{{ displayId(row) }}</template>
        </el-table-column>
        <el-table-column prop="name" label="任务名称" min-width="220" show-overflow-tooltip />
        <el-table-column prop="case_count" label="用例数" width="100" align="center" />
        <el-table-column label="任务状态" width="200">
          <template #default="{ row }">
            <span class="status" :class="statusClass(row.status)">{{ statusText(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="creator" label="创建人" width="120" />
        <el-table-column label="操作" width="180" align="center" fixed="right">
          <template #default="{ row }">
            <el-tooltip :content="canDownload(row) ? '下载 评测结果Excel' : '任务执行中，暂不可下载'" placement="top">
              <el-button
                class="btn-download"
                size="small"
                :disabled="!canDownload(row)"
                @click="onDownload(row)"
              >
                下载
              </el-button>
            </el-tooltip>
            <el-button
              class="btn-retry"
              size="small"
              :disabled="row.status !== 'failed'"
              @click="onRetry(row)"
            >
              重试
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.page-title {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: #1f2a37;
}

.panel {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 16px 20px;
}

.status {
  font-size: 13px;
  font-weight: 500;
}

.st-running {
  color: #d48806;
}

.st-comparing {
  color: #722ed1;
}

.st-done {
  color: #389e0d;
}

.st-failed {
  color: #cf1322;
}

.btn-download {
  background: #52c41a;
  border-color: #52c41a;
  color: #fff;
}

.btn-download:hover:not(:disabled),
.btn-download:focus:not(:disabled) {
  background: #389e0d;
  border-color: #389e0d;
  color: #fff;
}

.btn-download:disabled {
  background: #f5f5f5;
  border-color: #dcdfe6;
  color: #c0c4cc;
  cursor: not-allowed;
  opacity: 1;
}

.btn-retry {
  background: #ff4d4f;
  border-color: #ff4d4f;
  color: #fff;
}

.btn-retry:hover:not(:disabled),
.btn-retry:focus:not(:disabled) {
  background: #cf1322;
  border-color: #cf1322;
  color: #fff;
}

.btn-retry:disabled {
  background: #ffccc7;
  border-color: #ffccc7;
  color: #fff;
  opacity: 1;
}
</style>
