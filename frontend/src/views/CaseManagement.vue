<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, reactive, ref } from 'vue'

import http from '@/api/http'
import { createTask } from '@/api/task'
import { useRouter } from 'vue-router'

const router = useRouter()
const SELECTION_LIMIT = 500

// 功能类型(source) 枚举 -> 中文
const SOURCE_LABELS: Record<string, string> = {
  legal_research: '法律研究',
  document_draft: '文书起草',
  case_ai: 'AI类案',
  law_ai: 'AI搜法',
  contract_review: '合同审查',
  file_review: '文件审查',
}

// ---- 筛选条件 ----
const filters = reactive({
  created_start: '',
  created_end: '',
  function_type: '',
  has_file: null as boolean | null,
  user_rating: '',
  keyword: '',
  dedup: false,
  exclude_failed: false,
})

interface CaseItem {
  kind: string
  task_id: string
  function_module?: string
  sub_function?: string
  question?: string
  attachment?: string
  has_file?: boolean
  rating?: string
  result_score?: number | null
  system_answer?: string
  is_empty_result?: boolean | null
  stance?: Record<string, any> | null
  created_at?: string
}

const rows = ref<CaseItem[]>([])
const total = ref(0)
const page = reactive({ current: 1, size: 20 })
const selectedIds = ref<Set<string>>(new Set())
const loading = ref(false)

const countText = computed(() =>
  selectedIds.value.size > 0
    ? `共 ${total.value} 条，已选中 ${selectedIds.value.size} 条`
    : `共 ${total.value} 条`,
)
const allSelected = computed(() => total.value > 0 && selectedIds.value.size >= Math.min(total.value, SELECTION_LIMIT))

function sourceLabel(s?: string) {
  return (s && SOURCE_LABELS[s]) || s || '—'
}
function ratingLabel(r?: string) {
  return r === 'good' ? '好评' : r === 'bad' ? '差评' : '—'
}
function ratingTag(r?: string) {
  return r === 'good' ? 'success' : r === 'bad' ? 'danger' : 'info'
}
function stanceText(row: CaseItem) {
  const s = row.stance
  if (!s) return '—'
  if (s.review_stance || s.subject) return [s.review_stance, s.subject].filter(Boolean).join(' / ')
  if (s.custom_require) return s.custom_require
  return JSON.stringify(s)
}

async function search() {
  loading.value = true
  try {
    const { data } = await http.post('/api/cases', filters, {
      params: { page: page.current, page_size: page.size },
    })
    rows.value = data.items
    total.value = data.total
    if (data.total === 0) ElMessage.info('无相关用例')
  } finally {
    loading.value = false
  }
}

function reset() {
  Object.assign(filters, {
    created_start: '', created_end: '', function_type: '', has_file: null,
    user_rating: '', keyword: '', dedup: false, exclude_failed: false,
  })
}

// "全部选中" — 跨所有页, 由后端按当前筛选返回全部 task_id(上限 500)
async function toggleSelectAll() {
  if (allSelected.value) {
    selectedIds.value = new Set()
    return
  }
  const { data } = await http.post('/api/cases/ids', filters)
  selectedIds.value = new Set<string>(data.ids)
  if (data.capped) ElMessage.warning('用例数量上限为 500 条')
}

function onRowSelect(row: CaseItem, checked: boolean) {
  const next = new Set(selectedIds.value)
  if (checked) {
    if (next.size >= SELECTION_LIMIT) {
      ElMessage.warning('用例数量上限为 500 条')
      return
    }
    next.add(row.task_id)
  } else {
    next.delete(row.task_id)
  }
  selectedIds.value = next
}

// ---- 创建评测任务弹窗 ----
const dialogVisible = ref(false)
const taskForm = reactive({ name: '', eval_workflow_id: '' })

function openCreateDialog() {
  taskForm.name = new Date().toLocaleString('zh-CN')
  taskForm.eval_workflow_id = ''
  dialogVisible.value = true
}

async function submitTask() {
  if (!taskForm.name) return ElMessage.warning('请填写任务名称')
  if (!taskForm.eval_workflow_id) return ElMessage.warning('请填写评测标准 workflow 后，重新提交')
  const ids = Array.from(selectedIds.value)
  await createTask({ name: taskForm.name, eval_workflow_id: taskForm.eval_workflow_id, case_ids: ids, filter_snapshot: { ...filters } })
  ElMessage.success(`任务创建成功，共 ${ids.length} 条用例`)
  dialogVisible.value = false
  router.push({ name: 'tasks' })
}
</script>

<template>
  <div>
    <!-- 筛选条件 -->
    <el-form :inline="true" class="filter-bar">
      <el-form-item label="功能类型">
        <el-select v-model="filters.function_type" placeholder="全部" clearable style="width: 140px">
          <el-option v-for="(label, val) in SOURCE_LABELS" :key="val" :label="label" :value="val" />
        </el-select>
      </el-form-item>
      <el-form-item label="是否带文件">
        <el-select v-model="filters.has_file" placeholder="全部" clearable style="width: 110px">
          <el-option label="是" :value="true" />
          <el-option label="否" :value="false" />
        </el-select>
      </el-form-item>
      <el-form-item label="用户评价">
        <el-select v-model="filters.user_rating" placeholder="全部" clearable style="width: 110px">
          <el-option label="好评" value="good" />
          <el-option label="差评" value="bad" />
        </el-select>
      </el-form-item>
      <el-form-item label="用户提问">
        <el-input v-model="filters.keyword" placeholder="关键词检索" clearable />
      </el-form-item>
      <el-form-item>
        <el-checkbox v-model="filters.dedup">自动去重</el-checkbox>
        <el-checkbox v-model="filters.exclude_failed">去除失败任务</el-checkbox>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" @click="search">筛选</el-button>
        <el-button @click="reset">重置</el-button>
      </el-form-item>
    </el-form>

    <!-- 工具条 -->
    <div class="toolbar">
      <div>
        <el-button size="small" @click="toggleSelectAll">{{ allSelected ? '取消全选' : '全部选中' }}</el-button>
        <span class="count">{{ countText }}</span>
      </div>
      <el-button type="primary" :disabled="selectedIds.size === 0" @click="openCreateDialog">创建评测任务</el-button>
    </div>

    <!-- 用例列表 -->
    <el-table :data="rows" v-loading="loading" border>
      <el-table-column width="55">
        <template #default="{ row }">
          <el-checkbox :model-value="selectedIds.has(row.task_id)" @change="(v: boolean) => onRowSelect(row, v)" />
        </template>
      </el-table-column>
      <el-table-column prop="task_id" label="任务ID" width="150" show-overflow-tooltip />
      <el-table-column label="功能模块" width="110">
        <template #default="{ row }">{{ sourceLabel(row.function_module) }}</template>
      </el-table-column>
      <el-table-column prop="question" label="用户提问" min-width="200" show-overflow-tooltip />
      <el-table-column label="是否带文件" width="100" align="center">
        <template #default="{ row }">{{ row.has_file ? '是' : '否' }}</template>
      </el-table-column>
      <el-table-column label="好差评" width="90" align="center">
        <template #default="{ row }">
          <el-tag :type="ratingTag(row.rating)" size="small" effect="light">{{ ratingLabel(row.rating) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="审查立场" min-width="150" show-overflow-tooltip>
        <template #default="{ row }">{{ stanceText(row) }}</template>
      </el-table-column>
      <el-table-column prop="system_answer" label="系统回答" min-width="200" show-overflow-tooltip>
        <template #default="{ row }">{{ row.system_answer ?? '—' }}</template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" width="170" />
    </el-table>

    <el-pagination
      class="pager"
      layout="total, prev, pager, next, sizes"
      :total="total"
      :page-size="page.size"
      :current-page="page.current"
      @current-change="(p: number) => { page.current = p; search() }"
      @size-change="(s: number) => { page.size = s; search() }"
    />

    <!-- 新建评测任务弹窗 -->
    <el-dialog v-model="dialogVisible" title="新建批量测试任务" width="520px">
      <el-form label-width="130px">
        <el-form-item label="任务名称" required>
          <el-input v-model="taskForm.name" />
        </el-form-item>
        <el-form-item label="用例范围">
          <span>已选中 {{ selectedIds.size }} 条用例</span>
        </el-form-item>
        <el-form-item label="评测标准 workflow" required>
          <el-input v-model="taskForm.eval_workflow_id" placeholder="输入 Coze 工作流 id" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submitTask">创建任务</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.filter-bar {
  margin-bottom: 8px;
}
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 8px 0;
}
.count {
  margin-left: 12px;
  color: #909399;
  font-size: 12px;
}
.pager {
  margin-top: 12px;
  justify-content: flex-end;
}
</style>
