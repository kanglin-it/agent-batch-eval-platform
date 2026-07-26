<script setup lang="ts">
import { Loading, Search } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import http from '@/api/http'
import { fetchMe } from '@/api/auth'
import { createTask, listWorkflowIds } from '@/api/task'

const router = useRouter()
const SELECTION_LIMIT = 500

const SOURCE_LABELS: Record<string, string> = {
  legal_research: '法律研究',
  document_draft: '文书起草',
  case_ai: 'AI类案',
  law_ai: 'AI搜法',
  contract_review: '合同审查',
  file_review: '文件审查',
}

// 列表「功能模块」列的展示：AI类案/AI搜法 归为「法律检索」，二级功能再区分（AI类案/AI搜法）。
const MODULE_LABELS: Record<string, string> = {
  ...SOURCE_LABELS,
  case_ai: '法律检索',
  law_ai: '法律检索',
}

const EXTRA_FIELD_OPTIONS = [
  { label: '二级功能', value: 'sub_function' },
  { label: '渠道', value: 'channel_type' },
  { label: '任务ID', value: 'task_id' },
]

/** 二级功能可选值（与后端 resolve_sources / document 子条件对齐） */
const SUB_FUNCTION_OPTIONS = ['传统文书', '要素式', 'AI类案', 'AI搜法']
const CHANNEL_OPTIONS = ['PC', 'H5', 'APP', 'MINI']

function defaultDateRange(): [string, string] {
  const end = new Date()
  const start = new Date()
  start.setDate(end.getDate() - 30)
  const fmt = (d: Date) => {
    const y = d.getFullYear()
    const m = String(d.getMonth() + 1).padStart(2, '0')
    const day = String(d.getDate()).padStart(2, '0')
    return `${y}-${m}-${day}`
  }
  return [fmt(start), fmt(end)]
}

const [defaultStart, defaultEnd] = defaultDateRange()

const filters = reactive({
  created_start: defaultStart,
  created_end: defaultEnd,
  function_type: '',
  has_file: null as boolean | null,
  user_rating: '',
  keyword: '',
  dedup: true,
  exclude_failed: true,
  extra: [] as { field: string; value: string }[],
})

const dateRange = computed({
  get: (): [string, string] | null =>
    filters.created_start && filters.created_end
      ? [filters.created_start, filters.created_end]
      : null,
  set: (v: [string, string] | null) => {
    filters.created_start = v?.[0] ?? ''
    filters.created_end = v?.[1] ?? ''
  },
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
  channel_type?: string
}

const rows = ref<CaseItem[]>([])
const total = ref(0)
const totalCapped = ref(false)
const totalApprox = ref(false)
const hasMore = ref(false)
const page = reactive({ current: 1, size: 20 })
const selectedIds = ref<Set<string>>(new Set())
const loading = ref(false)
/** 正在下载文件的 task_id；用于「上传文件」列 loading 反馈 */
const downloadingTaskId = ref<string | null>(null)

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / page.size)))

const canGoNext = computed(
  () => hasMore.value || page.current < totalPages.value,
)

/** 触顶或去重近似时不展示「跳末页」，避免假精确 */
const canJumpLast = computed(() => !totalCapped.value && !totalApprox.value)

const pagerSummary = computed(() => {
  const pages = totalPages.value
  const countLabel = totalCapped.value
    ? `${total.value}+ 条`
    : totalApprox.value
      ? `约 ${total.value} 条`
      : `${total.value} 条`
  const pageLabel = totalCapped.value ? `约 ${pages}+ 页` : `共 ${pages} 页`
  return `第 ${page.current} 页 / ${pageLabel}（${countLabel}）`
})

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

const pageSelectedCount = computed(
  () => rows.value.filter((r) => selectedIds.value.has(r.task_id)).length,
)

const pageAllSelected = computed(
  () => rows.value.length > 0 && pageSelectedCount.value === rows.value.length,
)

const pageIndeterminate = computed(
  () => pageSelectedCount.value > 0 && pageSelectedCount.value < rows.value.length,
)

function sourceLabel(s?: string) {
  return (s && SOURCE_LABELS[s]) || s || '—'
}

function moduleLabel(s?: string) {
  return (s && MODULE_LABELS[s]) || s || '—'
}

function ratingLabel(r?: string) {
  return r === 'good' ? '好评' : r === 'bad' ? '差评' : '—'
}

function fileName(attachment?: string) {
  if (!attachment) return ''
  try {
    const parsed = JSON.parse(attachment)
    if (Array.isArray(parsed) && parsed.length) {
      const first = parsed[0]
      return typeof first === 'string' ? first.split('/').pop() || first : first?.name || String(first)
    }
    if (parsed && typeof parsed === 'object' && parsed.name) return parsed.name
  } catch {
    /* plain string */
  }
  return attachment.split(/[,;]/)[0]?.trim().split('/').pop() || attachment
}

function formatTime(v?: string) {
  if (!v) return '—'
  return v.replace('T', ' ').slice(0, 16)
}

function parseFilename(cd?: string): string {
  if (!cd) return ''
  const star = /filename\*=UTF-8''([^;]+)/i.exec(cd)
  if (star) {
    try {
      return decodeURIComponent(star[1])
    } catch {
      return star[1]
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(cd)
  return plain ? plain[1] : ''
}

async function onDownloadFiles(row: CaseItem) {
  if (!row.task_id || downloadingTaskId.value === row.task_id) return
  downloadingTaskId.value = row.task_id
  try {
    const res = await http.get('/api/cases/download-files', {
      params: { task_id: row.task_id },
      responseType: 'blob',
    })
    const filename = parseFilename(res.headers['content-disposition']) || `用例文件_${row.task_id}`
    const blob = new Blob([res.data], { type: res.headers['content-type'] })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  } catch {
    ElMessage.error('文件下载失败')
  } finally {
    downloadingTaskId.value = null
  }
}

function goPage(p: number) {
  if (p < 1 || p === page.current) return
  // 触顶时 total 是下界，允许在 has_more 为真时继续往后翻（仍受后端 page≤50 限制）
  if (p > totalPages.value && !hasMore.value) return
  page.current = p
  // 本页勾选不跨页保留，翻页后表头全选与勾选状态清空
  selectedIds.value = new Set()
  search()
}

function goFirst() {
  goPage(1)
}

function goPrev() {
  goPage(page.current - 1)
}

function goNext() {
  if (!canGoNext.value) return
  goPage(page.current + 1)
}

function goLast() {
  if (!canJumpLast.value) return
  goPage(totalPages.value)
}

async function search() {
  loading.value = true
  try {
    const payload = {
      ...filters,
      extra: filters.extra.filter((e) => e.field && e.value !== ''),
    }
    const { data } = await http.post('/api/cases', payload, {
      params: { page: page.current, page_size: page.size },
    })
    rows.value = data.items
    total.value = data.total ?? 0
    totalCapped.value = !!data.total_capped
    totalApprox.value = !!data.total_approx
    hasMore.value = !!data.has_more
    if (data.total === 0) ElMessage.info('无相关用例')
  } catch (e: any) {
    const msg = e?.response?.data?.detail || e?.message || '查询失败'
    ElMessage.error(typeof msg === 'string' ? msg : '查询失败')
  } finally {
    loading.value = false
  }
}

function clearPageSelection() {
  selectedIds.value = new Set()
}

function onFilterSearch() {
  page.current = 1
  clearPageSelection()
  search()
}

function reset() {
  const [start, end] = defaultDateRange()
  Object.assign(filters, {
    created_start: start,
    created_end: end,
    function_type: '',
    has_file: null,
    user_rating: '',
    keyword: '',
    dedup: true,
    exclude_failed: true,
    extra: [],
  })
  selectedIds.value = new Set()
  page.current = 1
  search()
}

function addExtraFilter() {
  filters.extra.push({ field: '', value: '' })
}

function removeExtraFilter(idx: number) {
  filters.extra.splice(idx, 1)
}

async function toggleSelectAll() {
  // 有任何勾选（含跨页全选残留）时，点一下就整体清空，无需用户再手动清一次。
  if (selectedIds.value.size > 0) {
    selectedIds.value = new Set()
    return
  }
  const payload = {
    ...filters,
    extra: filters.extra.filter((e) => e.field && e.value !== ''),
  }
  const { data } = await http.post('/api/cases/ids', payload)
  selectedIds.value = new Set<string>(data.ids.slice(0, SELECTION_LIMIT))
  if (data.capped || data.total > SELECTION_LIMIT) {
    ElMessage.warning(`勾选用例最多 ${SELECTION_LIMIT} 条，已自动截取前 ${SELECTION_LIMIT} 条`)
  }
}

function onRowSelect(row: CaseItem, checked: boolean) {
  const next = new Set(selectedIds.value)
  if (checked) {
    if (next.size >= SELECTION_LIMIT) {
      ElMessage.warning(`勾选用例最多 ${SELECTION_LIMIT} 条`)
      return
    }
    next.add(row.task_id)
  } else {
    next.delete(row.task_id)
  }
  selectedIds.value = next
}

/** 表头勾选：只选中 / 取消本页任务 */
function togglePageSelect(checked: boolean) {
  const next = new Set(selectedIds.value)
  if (checked) {
    for (const row of rows.value) {
      if (next.has(row.task_id)) continue
      if (next.size >= SELECTION_LIMIT) {
        ElMessage.warning(`勾选用例最多 ${SELECTION_LIMIT} 条`)
        break
      }
      next.add(row.task_id)
    }
  } else {
    for (const row of rows.value) {
      next.delete(row.task_id)
    }
  }
  selectedIds.value = next
}

const dialogVisible = ref(false)
const taskForm = reactive({ name: '', eval_workflow_id: '', creator: '' })
const creating = ref(false)
const workflowIds = ref<string[]>([])
const workflowLoading = ref(false)

async function loadWorkflowIds() {
  workflowLoading.value = true
  try {
    workflowIds.value = await listWorkflowIds()
  } catch {
    workflowIds.value = []
  } finally {
    workflowLoading.value = false
  }
}

const filterSummary = computed(() => {
  const parts: string[] = []
  parts.push(`自动去重：${filters.dedup ? '是' : '否'}`)
  if (filters.exclude_failed) parts.push('去除失败任务：是')
  if (filters.function_type) parts.push(`功能类型：${SOURCE_LABELS[filters.function_type] || filters.function_type}`)
  if (filters.user_rating === 'good') parts.push('用户评价：好评')
  if (filters.user_rating === 'bad') parts.push('用户评价：差评')
  if (filters.keyword) parts.push(`关键词：${filters.keyword}`)
  if (filters.created_start || filters.created_end) {
    parts.push(`创建时间：${filters.created_start || '…'} ~ ${filters.created_end || '…'}`)
  }
  return parts.join('；')
})

async function openCreateDialog() {
  if (selectedIds.value.size === 0) {
    ElMessage.warning('请先选择评测用例')
    return
  }
  if (selectedIds.value.size > SELECTION_LIMIT) {
    ElMessage.warning(`勾选用例最多 ${SELECTION_LIMIT} 条，请减少后再创建`)
    return
  }
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  taskForm.name = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`
  taskForm.eval_workflow_id = ''
  taskForm.creator = '当前用户'
  loadWorkflowIds()          // 拉历史 workflow_id 填充下拉（不阻塞弹窗打开）
  try {
    const me = await fetchMe()
    if (me?.username) taskForm.creator = me.username
  } catch {
    /* keep placeholder */
  }
  dialogVisible.value = true
}

async function submitTask() {
  if (!taskForm.name.trim()) return ElMessage.warning('请填写任务名称')
  if (!taskForm.eval_workflow_id.trim()) return ElMessage.warning('请填写评测标准 Workflow')
  if (!taskForm.creator.trim()) return ElMessage.warning('请填写创建人')
  const ids = Array.from(selectedIds.value)
  if (ids.length === 0) return ElMessage.warning('请先选择评测用例')
  if (ids.length > SELECTION_LIMIT) {
    return ElMessage.warning(`勾选用例最多 ${SELECTION_LIMIT} 条，当前已选 ${ids.length} 条`)
  }
  creating.value = true
  try {
    await createTask({
      name: taskForm.name.trim(),
      eval_workflow_id: taskForm.eval_workflow_id.trim(),
      case_ids: ids,
      filter_snapshot: { ...filters },
      creator: taskForm.creator.trim(),
    })
    ElMessage.success(`任务创建成功，共 ${ids.length} 条用例`)
    dialogVisible.value = false
    router.push({ name: 'tasks' })
  } finally {
    creating.value = false
  }
}

onMounted(() => {
  search()
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">评测用例管理</h2>
      <el-button type="primary" @click="openCreateDialog">创建评测任务</el-button>
    </div>

    <div class="panel filter-panel">
      <div class="panel-title">
        <el-icon><Search /></el-icon>
        <span>筛选条件</span>
      </div>

      <el-form label-width="100px" label-position="left" class="filter-form">
        <el-form-item label="创建时间：">
          <el-date-picker
            v-model="dateRange"
            type="daterange"
            range-separator="至"
            start-placeholder="年 / 月 / 日"
            end-placeholder="年 / 月 / 日"
            value-format="YYYY-MM-DD"
            class="filter-control"
          />
        </el-form-item>
        <el-form-item label="功能类型：">
          <el-select v-model="filters.function_type" placeholder="- 全部 -" clearable class="filter-control">
            <el-option v-for="(label, val) in SOURCE_LABELS" :key="val" :label="label" :value="val" />
          </el-select>
        </el-form-item>
        <el-form-item label="是否带文件：">
          <el-select v-model="filters.has_file" placeholder="- 全部 -" clearable class="filter-control">
            <el-option label="是" :value="true" />
            <el-option label="否" :value="false" />
          </el-select>
        </el-form-item>
        <el-form-item label="用户评价：">
          <el-select v-model="filters.user_rating" placeholder="- 全部 -" clearable class="filter-control">
            <el-option label="好评" value="good" />
            <el-option label="差评" value="bad" />
          </el-select>
        </el-form-item>
        <el-form-item label="用户提问：">
          <el-input v-model="filters.keyword" placeholder="关键词搜索" clearable class="filter-control filter-control--wide" />
        </el-form-item>
        <el-form-item label="数据处理：">
          <div class="data-ops">
            <el-checkbox v-model="filters.dedup">自动去重</el-checkbox>
            <el-checkbox v-model="filters.exclude_failed">去除失败任务</el-checkbox>
          </div>
        </el-form-item>

        <el-form-item v-for="(item, idx) in filters.extra" :key="idx" label="额外条件：">
          <div class="extra-row">
            <el-select v-model="item.field" placeholder="筛选字段" style="width: 160px" @change="item.value = ''">
              <el-option v-for="opt in EXTRA_FIELD_OPTIONS" :key="opt.value" :label="opt.label" :value="opt.value" />
            </el-select>
            <el-select
              v-if="item.field === 'sub_function'"
              v-model="item.value"
              placeholder="选择二级功能"
              clearable
              style="width: 280px"
            >
              <el-option v-for="v in SUB_FUNCTION_OPTIONS" :key="v" :label="v" :value="v" />
            </el-select>
            <el-select
              v-else-if="item.field === 'channel_type'"
              v-model="item.value"
              placeholder="选择渠道"
              clearable
              filterable
              allow-create
              style="width: 280px"
            >
              <el-option v-for="v in CHANNEL_OPTIONS" :key="v" :label="v" :value="v" />
            </el-select>
            <el-input
              v-else
              v-model="item.value"
              :placeholder="item.field === 'task_id' ? '任务 ID' : '筛选值'"
              clearable
              style="width: 280px"
            />
            <el-button link type="danger" @click="removeExtraFilter(idx)">删除</el-button>
          </div>
        </el-form-item>

        <el-form-item label=" ">
          <div class="filter-actions">
            <el-button @click="addExtraFilter">+ 新增筛选条件</el-button>
            <el-button type="primary" @click="onFilterSearch">筛选</el-button>
            <el-button @click="reset">重置</el-button>
          </div>
        </el-form-item>
      </el-form>
    </div>

    <div class="panel list-panel">
      <div class="list-header">
        <div class="list-title">
          <span>用例列表</span>
          <el-button link type="primary" @click="toggleSelectAll">
            {{ selectedIds.size ? '清空已选' : '全部选中' }}
          </el-button>
          <span v-if="selectedIds.size" class="selected-tip">
            已选中 {{ selectedIds.size }} / {{ SELECTION_LIMIT }} 条
          </span>
        </div>
      </div>

      <el-table :data="rows" v-loading="loading" border stripe>
        <el-table-column width="48" align="center" fixed>
          <template #header>
            <el-checkbox
              :model-value="pageAllSelected"
              :indeterminate="pageIndeterminate"
              @change="(v: boolean) => togglePageSelect(v)"
            />
          </template>
          <template #default="{ row }">
            <el-checkbox
              :model-value="selectedIds.has(row.task_id)"
              @change="(v: boolean) => onRowSelect(row, v)"
            />
          </template>
        </el-table-column>
        <el-table-column prop="task_id" label="任务ID" width="160" show-overflow-tooltip />
        <el-table-column label="功能模块" width="110">
          <template #default="{ row }">{{ moduleLabel(row.function_module) }}</template>
        </el-table-column>
        <el-table-column label="二级功能" width="110" show-overflow-tooltip>
          <template #default="{ row }">{{ row.sub_function || '—' }}</template>
        </el-table-column>
        <el-table-column prop="question" label="用户提问" min-width="200" show-overflow-tooltip />
        <el-table-column label="附件" width="80" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.has_file" type="success" size="small" effect="light">有</el-tag>
            <span v-else class="muted">无</span>
          </template>
        </el-table-column>
        <el-table-column label="上传文件" width="160" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.has_file && downloadingTaskId === row.task_id" class="file-downloading">
              <el-icon class="is-loading"><Loading /></el-icon>
              下载中…
            </span>
            <a
              v-else-if="row.has_file"
              class="file-link"
              href="javascript:;"
              @click="onDownloadFiles(row)"
            >
              {{ fileName(row.attachment) || '下载文件' }}
            </a>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="system_answer" label="系统回答" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ row.system_answer || '—' }}</template>
        </el-table-column>
        <el-table-column label="好差评" width="90" align="center">
          <template #default="{ row }">{{ ratingLabel(row.rating) }}</template>
        </el-table-column>
        <el-table-column label="创建时间" width="160">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="渠道" width="120" show-overflow-tooltip>
          <template #default="{ row }">{{ row.channel_type || '—' }}</template>
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
          :disabled="!canGoNext"
          aria-label="下一页"
          @click="goNext"
        >
          ›
        </button>
        <button
          v-if="canJumpLast"
          type="button"
          class="pager-btn"
          :disabled="page.current >= totalPages"
          aria-label="末页"
          @click="goLast"
        >
          »
        </button>
        <span class="pager-summary">{{ pagerSummary }}</span>
      </div>
    </div>

    <el-dialog
      v-model="dialogVisible"
      title="新建批量测试任务"
      width="520px"
      class="create-task-dialog"
      :close-on-click-modal="false"
    >
      <el-form label-position="top" class="create-task-form" @submit.prevent>
        <el-form-item required>
          <template #label><span class="req">*</span> 任务名称</template>
          <el-input v-model="taskForm.name" placeholder="请输入任务名称" />
        </el-form-item>

        <el-form-item label="用例范围">
          <div class="scope-box">
            <div class="scope-count">已选中 <em>{{ selectedIds.size }}</em> 条用例</div>
            <div class="scope-filters">筛选条件：{{ filterSummary }}</div>
          </div>
        </el-form-item>

        <el-form-item required>
          <template #label><span class="req">*</span> 评测标准 Workflow</template>
          <el-select
            v-model="taskForm.eval_workflow_id"
            class="wf-select"
            filterable
            allow-create
            default-first-option
            clearable
            :loading="workflowLoading"
            placeholder="选择历史 Workflow 或手动输入 Coze 工作流ID"
          >
            <el-option v-for="id in workflowIds" :key="id" :label="id" :value="id" />
          </el-select>
        </el-form-item>

        <el-form-item required>
          <template #label><span class="req">*</span> 创建人</template>
          <el-input v-model="taskForm.creator" placeholder="当前用户" disabled />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button class="btn-cancel" @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitTask">创建任务</el-button>
      </template>
    </el-dialog>
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

.panel-title {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 16px;
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}

.filter-form {
  max-width: 720px;
}

.filter-form :deep(.el-form-item) {
  margin-bottom: 16px;
}

.filter-form :deep(.el-form-item__label) {
  color: #606266;
  justify-content: flex-start;
}

.filter-control {
  width: 280px;
}

.filter-control--wide {
  width: 420px;
  max-width: 100%;
}

.data-ops {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 24px;
  min-height: 32px;
  align-items: center;
}

.extra-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.filter-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.list-header {
  margin-bottom: 12px;
}

.list-title {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}

.selected-tip {
  font-size: 13px;
  font-weight: 400;
  color: #909399;
}

.file-link {
  color: #409eff;
  text-decoration: none;
}

.file-link:hover {
  text-decoration: underline;
}

.file-downloading {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: #909399;
  font-size: 13px;
  cursor: wait;
}

.muted {
  color: #c0c4cc;
}

.pager {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 20px;
}

.pager-btn {
  min-width: 32px;
  height: 32px;
  padding: 0 8px;
  border: 1px solid #1677ff;
  border-radius: 4px;
  background: #1677ff;
  color: #fff;
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
}

.pager-btn:hover:not(:disabled) {
  background: #4096ff;
  border-color: #4096ff;
}

.pager-btn:disabled {
  background: #f5f5f5;
  border-color: #d9d9d9;
  color: #bfbfbf;
  cursor: not-allowed;
}

.pager-btn--active {
  font-weight: 600;
}

.pager-num {
  min-width: 24px;
  height: 32px;
  padding: 0 4px;
  border: none;
  background: transparent;
  color: #303133;
  font-size: 14px;
  cursor: pointer;
}

.pager-num:hover {
  color: #1677ff;
}

.pager-summary {
  margin-left: 8px;
  color: #606266;
  font-size: 14px;
  white-space: nowrap;
}

.create-task-form :deep(.el-form-item__label) {
  color: #303133;
  font-weight: 500;
  padding-bottom: 6px;
}

.wf-select {
  width: 100%;
}

.req {
  color: #f56c6c;
  margin-right: 2px;
}

.scope-box {
  width: 100%;
  padding: 12px 14px;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  background: #fafafa;
  line-height: 1.6;
}

.scope-count {
  color: #303133;
  font-size: 14px;
}

.scope-count em {
  font-style: normal;
  color: #1677ff;
  font-weight: 600;
}

.scope-filters {
  margin-top: 4px;
  color: #909399;
  font-size: 12px;
}

.btn-cancel {
  background: #909399;
  border-color: #909399;
  color: #fff;
}

.btn-cancel:hover,
.btn-cancel:focus {
  background: #a6a9ad;
  border-color: #a6a9ad;
  color: #fff;
}
</style>
