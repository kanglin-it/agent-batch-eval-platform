# 本地跑起来（快速预览）

目标：在你自己的 Mac 上把前后端跑起来，用**开发登录**直接进去看效果——不用先配 Django 用户库。

前置：Python 3.11+、Node 18+。

## 1. 后端

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install aiosqlite            # 本地用 SQLite 当平台库(任务表), 免装 MySQL

# 生成本地 .env
cat > .env <<'ENV'
# 开发登录: 任意账号密码都能进(超管), 仅本地用
DEV_LOGIN_ENABLED=true

# 平台自己的库(评测任务表) 用本地 SQLite, 无需 MySQL
DATABASE_URL=sqlite+aiosqlite:///./dev.db

# 用例数据库(PG saas): 你的 Mac 若在 RDS 白名单内就能连真数据; 否则用例列表会查不到
CASE_DATABASE_URL=postgresql+asyncpg://zhiexa_saas:PASSWORD_URLENCODED@pgr-uf6sf049xl2k192lro.pg.rds.aliyuncs.com:5432/saas

JWT_SECRET=local-dev-secret
CORS_ORIGINS=http://localhost:5173
ENV

# 建平台库的表(eval_task / eval_task_case)
python -m scripts.init_db

# 启动
uvicorn app.main:app --reload
# 打开 http://127.0.0.1:8000/docs 看接口
```

> 注意：`CASE_DATABASE_URL` 里的密码含特殊字符 `@ # $ ! )`，放进 URL 必须做 **URL 编码**
> （`@`→`%40`、`#`→`%23`、`$`→`%24`、`!`→`%21`、`)`→`%29`）。
> 例：`@@Password#0805$@#!)` → `%40%40Password%230805%24%40%23%21%29`。
> 连不上 PG（IP 不在白名单）也没关系——登录和任务页照常，只是用例列表查不到数据。

## 2. 前端

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173  (已代理 /api -> 127.0.0.1:8000)
```

## 3. 登录看效果

浏览器开 **http://localhost:5173** → 用**任意用户名/密码**登录（因为 `DEV_LOGIN_ENABLED=true`）→
进去后左侧有 **用例管理 / 任务管理 / 系统设置** 三个页。

- 用例管理：能连上 PG saas 就能筛选/看列表；连不上则筛选为空。
- 任务管理：走本地 SQLite，能创建/看任务列表（Agent 真跑需要能连 zhiexa）。
- 系统设置：改登录态时长。

## ⚠️ 上线前务必
把 `DEV_LOGIN_ENABLED` 设回 `false`（或删掉）——它会让任意账号以超管身份登录，**只能本地用**。
