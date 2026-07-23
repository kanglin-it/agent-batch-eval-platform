# Frontend — Agent 批量评测平台 (Vue3)

Vue 3 + Vite + TypeScript + Pinia + Vue Router + Element Plus admin UI.

## Auth flow

- Login page posts to `/api/auth/login` with the **existing Django ops-backend
  account/password**; the backend verifies against Django's hash and returns a JWT.
- The JWT is stored in Pinia (`stores/auth.ts`) + `localStorage`; an axios request
  interceptor attaches `Authorization: Bearer <token>` to every call.
- A 401 response clears the session and redirects to `/login`.
- The router guard blocks non-public routes when there is no token.

> For hardening later: move the refresh token to an httpOnly cookie and keep only a
> short-lived access token in memory to reduce XSS exposure.

## Layout

```
src/
  main.ts / App.vue
  router/index.ts         # routes + auth guard
  stores/auth.ts          # pinia JWT store
  api/
    http.ts               # axios instance + interceptors
    auth.ts / task.ts
  layouts/BasicLayout.vue # sidebar shell
  views/
    Login.vue
    CaseManagement.vue    # 用例管理页：筛选 / 列表(≤500 选中) / 创建任务弹窗
    TaskManagement.vue    # 任务管理页：状态/进度/指标/重试/下载
```

## Run

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173  (proxies /api -> http://127.0.0.1:8000)
```

## TODO (tracked in the tech plan)

- Wire `list_cases` fields once the SaaS user-behavior schema is confirmed
  (attachment link, 审查立场, 结果是否为空, etc.).
- Implement true cross-page "全部选中" via a backend "ids-by-filter" endpoint (≤500).
- Implement the download actions (zip / csv / excel) once the export endpoint is built.
- Add dynamic "新增筛选条件" rows bound to `filters.extra`.
