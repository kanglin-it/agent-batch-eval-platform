<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { onMounted, ref } from 'vue'

import { getLoginTtl, updateLoginTtlDays } from '@/api/setting'

const days = ref<number>(30)
const currentDays = ref<number | null>(null)
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const res = await getLoginTtl()
    currentDays.value = res.login_ttl_days
    days.value = res.login_ttl_days
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!days.value || days.value <= 0) return ElMessage.warning('请输入大于 0 的天数')
  const res = await updateLoginTtlDays(days.value)
  currentDays.value = res.login_ttl_days
  ElMessage.success(`已更新：登录态维持 ${res.login_ttl_days} 天`)
}

onMounted(load)
</script>

<template>
  <el-card v-loading="loading" style="max-width: 520px">
    <template #header>登录态时长设置</template>
    <p class="hint">
      当前：<b>{{ currentDays ?? '--' }}</b> 天。修改后<strong>对新登录生效</strong>，已登录用户仍按原时长到期。
    </p>
    <el-form label-width="120px">
      <el-form-item label="登录态维持">
        <el-input-number v-model="days" :min="0.01" :step="1" :precision="2" />
        <span style="margin-left: 8px">天</span>
      </el-form-item>
      <el-button type="primary" @click="save">保存</el-button>
    </el-form>
  </el-card>
</template>

<style scoped>
.hint {
  color: #909399;
  font-size: 13px;
  margin-bottom: 12px;
}
</style>
