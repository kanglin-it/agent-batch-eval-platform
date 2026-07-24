<script setup lang="ts">
import { ArrowDown, ArrowLeft, ArrowRight, Document, Expand, Fold, List } from '@element-plus/icons-vue'
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const collapsed = ref(false)
function toggleSidebar() {
  collapsed.value = !collapsed.value
}

async function onLogout() {
  await auth.logout()
  router.replace({ name: 'login' })
}
</script>

<template>
  <el-container class="layout">
    <el-aside :width="collapsed ? '0px' : '208px'" class="aside">
      <div class="brand">
        <span class="brand-mark">A</span>
        <span class="brand-text">Agent批量测试平台</span>
        <el-icon class="brand-caret" title="收起侧边栏" @click="toggleSidebar"><ArrowDown /></el-icon>
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

    <!-- 悬浮在侧边栏边缘、垂直居中的收起/展开按钮 -->
    <div
      class="rail-toggle"
      :style="{ left: collapsed ? '0px' : '208px' }"
      :title="collapsed ? '展开侧边栏' : '收起侧边栏'"
      @click="toggleSidebar"
    >
      <el-icon><component :is="collapsed ? ArrowRight : ArrowLeft" /></el-icon>
    </div>

    <el-container>
      <el-header class="header">
        <div class="header-left">
          <el-icon class="sidebar-toggle" :title="collapsed ? '展开侧边栏' : '收起侧边栏'" @click="toggleSidebar">
            <component :is="collapsed ? Expand : Fold" />
          </el-icon>
          <div class="breadcrumb">{{ (route.meta.title as string) || '' }}</div>
        </div>
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
  position: relative;
}

.rail-toggle {
  position: absolute;
  top: 50%;
  transform: translate(-50%, -50%);
  z-index: 20;
  width: 22px;
  height: 44px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #fff;
  color: #5c6b7a;
  border: 1px solid #e4e7ed;
  border-radius: 0 8px 8px 0;
  box-shadow: 2px 0 8px rgba(0, 0, 0, 0.08);
  cursor: pointer;
  transition: left 0.2s ease, color 0.2s;
}
.rail-toggle:hover {
  color: #1677ff;
}

.aside {
  display: flex;
  flex-direction: column;
  background: #001529;
  overflow: hidden;
  transition: width 0.2s ease;
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
  cursor: pointer;
}
.brand-caret:hover {
  color: #fff;
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

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.sidebar-toggle {
  font-size: 18px;
  color: #5c6b7a;
  cursor: pointer;
}
.sidebar-toggle:hover {
  color: #1677ff;
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
