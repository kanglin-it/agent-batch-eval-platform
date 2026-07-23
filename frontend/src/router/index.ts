import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/Login.vue'),
      meta: { public: true },
    },
    {
      path: '/',
      component: () => import('@/layouts/BasicLayout.vue'),
      redirect: '/cases',
      children: [
        {
          path: 'cases',
          name: 'cases',
          component: () => import('@/views/CaseManagement.vue'),
          meta: { title: '用例管理' },
        },
        {
          path: 'tasks',
          name: 'tasks',
          component: () => import('@/views/TaskManagement.vue'),
          meta: { title: '任务管理' },
        },
      ],
    },
  ],
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (!to.meta.public && !auth.token) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.name === 'login' && auth.token) {
    return { name: 'cases' }
  }
  return true
})

export default router
