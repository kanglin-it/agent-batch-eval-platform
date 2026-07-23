<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

function onLogout() {
  auth.logout()
  router.replace({ name: 'login' })
}
</script>

<template>
  <el-container class="layout">
    <el-aside width="200px" class="aside">
      <div class="logo">Agent 批量评测</div>
      <el-menu :default-active="route.name as string" router>
        <el-menu-item index="cases" :route="{ name: 'cases' }">用例管理</el-menu-item>
        <el-menu-item index="tasks" :route="{ name: 'tasks' }">任务管理</el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="header">
        <span>{{ (route.meta.title as string) || '' }}</span>
        <el-button link type="primary" @click="onLogout">退出登录</el-button>
      </el-header>
      <el-main>
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.layout {
  height: 100vh;
}
.aside {
  background: #001529;
}
.logo {
  color: #fff;
  height: 60px;
  line-height: 60px;
  text-align: center;
  font-weight: 600;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border-bottom: 1px solid #eee;
}
</style>
