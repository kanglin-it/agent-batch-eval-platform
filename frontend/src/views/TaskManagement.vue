<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { downloadResultExcel, listTasks, retryTask, type TaskListItem } from '@/api/task'

const router = useRouter()
const tasks = ref<TaskListItem[]>([])
const total = ref(0)
const loading = ref(false)
const page = reactive({ current: 1, size: 20 })
let timer: ReturnType<typeof setInterval> | null = null

const STATUS_LABEL: Record<TaskListItem['status'], string> = {
  agent_running: 'Agent执行中',
  comparing: '对比评测中',
  completed: '已完成',
  failed: '失败',
}

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / page.size)))

const visiblePages = computed(() => {
  const maxVisible = 5
  const tp = totalPages.value
  if (tp <= maxVisible) {
    return Array.from({ length: tp }, (_, i) => i + 1)
  }
  let start = Math.max(1, page.current - 2)
  let end = start + maxVisible - 1
  if (end > tp) {
    end = tp
    start = Math.max(1, end - maxVisible + 1)
  }
  return Array.from({ length: end - start + 1 }, (_, i) => start + i)
})

async function load() {
  loading.value = true
  try {
    const data = await listTasks(page.current, page.size)
    tasks.value = data.items
    total.value = data.total
    // 删除后当前页可能变空，回退一页
    if (tasks.value.length === 0 && page.current > 1 && total.value > 0) {
      page.current = Math.min(page.current, Math.max(1, Math.ceil(total.value / page.size)))
      await load()
    }
  } finally {
    loading.value = false
  }
}

// 轮询用：静默刷新当前页，不开 loading 遮罩，原地更新行数据避免整表闪烁。
async function refreshSilently() {
  let data
  try {
    data = await listTasks(page.current, page.size)
  } catch {
    return
  }
  total.value = data.total
  patchTasks(data.items)
}

function patchTasks(next: TaskListItem[]) {
  const nextIds = new Set(next.map((t) => t.id))
  for (const cur of tasks.value) {
    const n = next.find((t) => t.id === cur.id)
    if (n) Object.assign(cur, n)
  }
  for (let i = tasks.value.length - 1; i >= 0; i--) {
    if (!nextIds.has(tasks.value[i].id)) tasks.value.splice(i, 1)
  }
  const curIds = new Set(tasks.value.map((t) => t.id))
  for (const n of next) {
    if (!curIds.has(n.id)) tasks.value.push(n)
  }
  const order = new Map(next.map((t, i) => [t.id, i]))
  tasks.value.sort((a, b) => (order.get(a.id) ?? 0) - (order.get(b.id) ?? 0))
}

function goPage(p: number) {
  if (p < 1 || p > totalPages.value || p === page.current) return
  page.current = p
  load()
}

function goFirst() {
  goPage(1)
}

function goPrev() {
  goPage(page.current - 1)
}

function goNext() {
  goPage(page.current + 1)
}

function goLast() {
  goPage(totalPages.value)
}

function goCreate() {
  ElMessage.info('请先选择评测用例，再创建评测任务')
  router.push({ name: 'cases' })
}

// 存在未完成用例（失败/未跑/待对比）且不在执行中即可重试；只重跑未完成的，不动已成功的。
function canRetry(row: TaskListItem) {
  return row.retryable
}

async function onRetry(row: TaskListItem) {
  if (!canRetry(row)) return
  await retryTask(row.id)
  ElMessage.success('已重新执行未完成的用例')
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
    const blob = new Blob([res.data], { type: String(res.headers['content-type'] ?? '') })
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
  const base = `${STATUS_LABEL[row.status]} (${row.progress})`
  // 已完成但有失败用例时，标出失败数，提示可重试
  return row.failed_count > 0 ? `${base}，${row.failed_count} 失败` : base
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
  // 进行中的任务自动刷新进度（只刷当前页）
  timer = setInterval(() => {
    const busy = tasks.value.some((t) => t.status === 'agent_running' || t.status === 'comparing')
    if (busy) refreshSilently()
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
            <el-tooltip
              :content="canRetry(row) ? '重试未完成的用例（已成功的不再重跑）' : '无未完成用例可重试'"
              placement="top"
            >
              <el-button
                class="btn-retry"
                size="small"
                :disabled="!canRetry(row)"
                @click="onRetry(row)"
              >
                重试
              </el-button>
            </el-tooltip>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <button
          type="button"
          class="pager-btn"
          :disabled="page.current <= 1"
          aria-label="首页"
          @click="goFirst"
        >
          «
        </button>
        <button
          type="button"
          class="pager-btn"
          :disabled="page.current <= 1"
          aria-label="上一页"
          @click="goPrev"
        >
          ‹
        </button>

        <template v-for="p in visiblePages" :key="p">
          <button
            v-if="p === page.current"
            type="button"
            class="pager-btn pager-btn--active"
            @click="goPage(p)"
          >
            {{ p }}
          </button>
          <button v-else type="button" class="pager-num" @click="goPage(p)">{{ p }}</button>
        </template>

        <button
          type="button"
          class="pager-btn"
          :disabled="page.current >= totalPages"
          aria-label="下一页"
          @click="goNext"
        >
          ›
        </button>
        <button
          type="button"
          class="pager-btn"
          :disabled="page.current >= totalPages"
          aria-label="末页"
          @click="goLast"
        >
          »
        </button>
        <span class="pager-summary">第 {{ page.current }} 页 / 共 {{ totalPages }} 页（{{ total }} 条）</span>
      </div>
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

.pager {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  margin-top: 16px;
  flex-wrap: wrap;
}

.pager-btn,
.pager-num {
  min-width: 32px;
  height: 32px;
  padding: 0 8px;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  background: #fff;
  color: #606266;
  cursor: pointer;
  font-size: 13px;
}

.pager-btn:disabled {
  color: #c0c4cc;
  cursor: not-allowed;
  background: #f5f7fa;
}

.pager-btn--active,
.pager-num:hover {
  color: #409eff;
  border-color: #409eff;
}

.pager-btn--active {
  background: #ecf5ff;
  font-weight: 600;
}

.pager-summary {
  margin-left: 8px;
  font-size: 13px;
  color: #909399;
}
</style>
