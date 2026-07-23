<script setup lang="ts">
import { ArrowDown, Document, List } from '@element-plus/icons-vue'
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

async function onLogout() {
  await auth.logout()
  router.replace({ name: 'login' })
}
</script>

<template>
  <el-container class="layout">
    <el-aside width="208px" class="aside">
      <div class="brand">
        <span class="brand-mark">A</span>
        <span class="brand-text">Agent批量测试平台</span>
        <el-icon class="brand-caret"><ArrowDown /></el-icon>
      </div>

      <nav class="menu">
        <router-link
          class="menu-item"
          :class="{ active: route.name === 'cases' }"
          :to="{ name: 'cases' }"
        >
          <el-icon><Document /></el-icon>
          <span>用例管理</span>
        </router-link>
        <router-link
          class="menu-item"
          :class="{ active: route.name === 'tasks' }"
          :to="{ name: 'tasks' }"
        >
          <el-icon><List /></el-icon>
          <span>任务管理</span>
        </router-link>
      </nav>
    </el-aside>

    <el-container>
      <el-header class="header">
        <div class="breadcrumb">{{ (route.meta.title as string) || '' }}</div>
        <el-button link type="primary" @click="onLogout">退出登录</el-button>
      </el-header>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.layout {
  height: 100vh;
  background: #f5f5f5;
}

.aside {
  display: flex;
  flex-direction: column;
  background: #001529;
  overflow: hidden;
}

.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 64px;
  padding: 0 16px;
  color: #fff;
  cursor: default;
  user-select: none;
}

.brand-mark {
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  border-radius: 6px;
  background: linear-gradient(135deg, #1677ff, #69b1ff);
  color: #fff;
  font-size: 14px;
  font-weight: 700;
  line-height: 28px;
  text-align: center;
}

.brand-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.2px;
}

.brand-caret {
  flex-shrink: 0;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.65);
}

.menu {
  display: flex;
  flex-direction: column;
  padding-top: 4px;
}

.menu-item {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 40px;
  padding: 0 24px 0 24px;
  color: rgba(255, 255, 255, 0.65);
  font-size: 14px;
  text-decoration: none;
  transition: background 0.2s, color 0.2s;
}

.menu-item:hover {
  color: #fff;
}

.menu-item.active {
  color: #fff;
  background: #1677ff;
}

.menu-item .el-icon {
  font-size: 16px;
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 48px;
  padding: 0 24px;
  background: #fff;
  border-bottom: 1px solid #f0f0f0;
}

.breadcrumb {
  color: #1677ff;
  font-size: 14px;
}

.main {
  padding: 16px 24px 24px;
  background: #f5f5f5;
}
</style>
