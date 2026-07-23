<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { listTasks, retryTask, type TaskListItem } from '@/api/task'

const router = useRouter()
const tasks = ref<TaskListItem[]>([])
const loading = ref(false)

const STATUS_LABEL: Record<TaskListItem['status'], string> = {
  agent_running: 'Agent执行中',
  comparing: '对比评测中',
  completed: '已完成',
  failed: '失败',
}
const STATUS_TYPE: Record<TaskListItem['status'], string> = {
  agent_running: 'warning',
  comparing: 'primary',
  completed: 'success',
  failed: 'danger',
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

function statusText(row: TaskListItem) {
  if (row.status === 'failed') return STATUS_LABEL.failed
  return `${STATUS_LABEL[row.status]}（${row.progress}）`
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-header">
      <h3 class="page-title">评测任务管理</h3>
      <div class="actions">
        <el-button @click="load">刷新</el-button>
        <el-button type="primary" @click="goCreate">创建评测任务</el-button>
      </div>
    </div>

    <el-table :data="tasks" v-loading="loading" border>
      <el-table-column prop="id" label="任务ID" width="90" />
      <el-table-column prop="name" label="任务名称" show-overflow-tooltip />
      <el-table-column prop="eval_workflow_id" label="评测标准 Workflow" width="180" />
      <el-table-column prop="case_count" label="用例数" width="90" />
      <el-table-column label="任务状态" width="180">
        <template #default="{ row }">
          <el-tag :type="STATUS_TYPE[row.status]">{{ statusText(row) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="胜率" width="90">
        <template #default="{ row }">{{ row.win_rate != null ? (row.win_rate * 100).toFixed(1) + '%' : '--' }}</template>
      </el-table-column>
      <el-table-column label="耗时" width="110">
        <template #default="{ row }">{{ row.avg_latency_ms != null ? row.avg_latency_ms + ' ms' : '--' }}</template>
      </el-table-column>
      <el-table-column prop="hallucination_count" label="幻觉数" width="90">
        <template #default="{ row }">{{ row.hallucination_count ?? '--' }}</template>
      </el-table-column>
      <el-table-column prop="creator" label="创建人" width="120" />
      <el-table-column label="操作" width="180">
        <template #default="{ row }">
          <el-button link type="primary" :disabled="row.status !== 'failed'" @click="onRetry(row)">重试</el-button>
          <el-dropdown>
            <el-button link type="primary">下载</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item>Excel 格式 · 评测结果</el-dropdown-item>
                <el-dropdown-item>CSV 格式 · 评测结果</el-dropdown-item>
                <el-dropdown-item>Excel 格式 · Agent 测试用例</el-dropdown-item>
                <el-dropdown-item>CSV 格式 · Agent 测试用例</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<style scoped>
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.page-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
}
.actions {
  display: flex;
  gap: 8px;
}
</style>
