<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const form = reactive({ phone: '', password: '' })
const loading = ref(false)

async function onSubmit() {
  if (!form.phone || !form.password) {
    ElMessage.warning('请输入手机号和密码')
    return
  }
  loading.value = true
  try {
    // Uses the existing ops-platform account (login by phone).
    await auth.login({ phone: form.phone, password: form.password })
    const redirect = (route.query.redirect as string) || '/cases'
    router.replace(redirect)
  } catch {
    // error toast handled by the axios interceptor
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <!-- 动态光晕背景 -->
    <div class="aurora aurora-1" />
    <div class="aurora aurora-2" />
    <div class="aurora aurora-3" />
    <div class="grid-overlay" />

    <el-card class="login-card" shadow="never">
      <div class="brand">
        <span class="brand-mark">A</span>
        <h2 class="title">Agent 批量评测平台</h2>
      </div>
      <p class="subtitle">使用运营平台账号登录</p>
      <el-form label-position="top" @submit.prevent="onSubmit">
        <el-form-item label="手机号">
          <el-input v-model="form.phone" size="large" placeholder="运营平台手机号" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="form.password"
            type="password"
            size="large"
            show-password
            placeholder="密码"
            @keyup.enter="onSubmit"
          />
        </el-form-item>
        <el-button type="primary" size="large" :loading="loading" class="submit-btn" @click="onSubmit">
          登 录
        </el-button>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
.login-wrap {
  position: relative;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  background: radial-gradient(circle at 20% 20%, #1b2a5b 0%, #0b1020 45%, #05060d 100%);
}

/* 流动的彩色光晕 */
.aurora {
  position: absolute;
  border-radius: 50%;
  filter: blur(90px);
  opacity: 0.55;
  will-change: transform;
}
.aurora-1 {
  width: 520px;
  height: 520px;
  top: -120px;
  left: -100px;
  background: #4f7cff;
  animation: float1 16s ease-in-out infinite;
}
.aurora-2 {
  width: 460px;
  height: 460px;
  bottom: -140px;
  right: -80px;
  background: #a855f7;
  animation: float2 20s ease-in-out infinite;
}
.aurora-3 {
  width: 380px;
  height: 380px;
  top: 40%;
  left: 55%;
  background: #22d3ee;
  opacity: 0.4;
  animation: float3 18s ease-in-out infinite;
}

@keyframes float1 {
  0%, 100% { transform: translate(0, 0) scale(1); }
  50% { transform: translate(120px, 80px) scale(1.15); }
}
@keyframes float2 {
  0%, 100% { transform: translate(0, 0) scale(1); }
  50% { transform: translate(-100px, -60px) scale(1.1); }
}
@keyframes float3 {
  0%, 100% { transform: translate(0, 0) scale(1); }
  50% { transform: translate(-80px, 60px) scale(0.9); }
}

/* 细网格叠层，增加科技感 */
.grid-overlay {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(255, 255, 255, 0.04) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.04) 1px, transparent 1px);
  background-size: 44px 44px;
  mask-image: radial-gradient(circle at center, #000 30%, transparent 80%);
}

/* 玻璃拟态卡片 */
.login-card {
  position: relative;
  z-index: 1;
  width: 380px;
  padding: 8px 12px 4px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.08);
  backdrop-filter: blur(18px);
  -webkit-backdrop-filter: blur(18px);
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.45);
}
:deep(.el-card__body) {
  padding: 28px 26px 30px;
}

.brand {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
}
.brand-mark {
  width: 34px;
  height: 34px;
  border-radius: 9px;
  background: linear-gradient(135deg, #4f7cff, #a855f7);
  color: #fff;
  font-size: 18px;
  font-weight: 800;
  line-height: 34px;
  text-align: center;
  box-shadow: 0 6px 18px rgba(79, 124, 255, 0.5);
}
.title {
  margin: 0;
  font-size: 20px;
  color: #fff;
  letter-spacing: 0.5px;
}
.subtitle {
  margin: 8px 0 22px;
  text-align: center;
  color: rgba(255, 255, 255, 0.6);
  font-size: 13px;
}

:deep(.el-form-item__label) {
  color: rgba(255, 255, 255, 0.75);
}
.submit-btn {
  width: 100%;
  margin-top: 6px;
  letter-spacing: 4px;
  background: linear-gradient(135deg, #4f7cff, #7c5cff);
  border: none;
}
.submit-btn:hover {
  opacity: 0.92;
}
</style>
