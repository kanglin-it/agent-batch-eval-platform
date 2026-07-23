# 本地跑起来（快速预览）

目标：在你自己的 Mac 上把前后端跑起来，用**开发登录**直接进去看效果。
后端配置全部在 **`backend/config.ini`**（直连你的 PostgreSQL 服务器库）。

前置：Python 3.11+、Node 18+。

## 1. 后端

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 生成配置文件并按需修改
cp config.ini.example config.ini
```

编辑 `backend/config.ini`（密码含特殊字符直接写，**不用转义**）：

```ini
[database]
host = <你的PG服务器地址>
port = 5432
user = zhiexa_saas
password = @@Password#0805$@#!)
dbname = saas
user_table = auth_user

[case_database]
# 留空 -> 复用 [database] 的连接(同一台 PG)
qa_table = t_history_qa_dataset
review_table = t_history_review_dataset

[jwt]
secret = local-dev-secret

[app]
cors_origins = http://localhost:5173
dev_login_enabled = true      # 本地预览: 任意账号密码即可登录

[zhiexa]
phone = 15067062596
password = <智合密码>
```

```bash
python -m scripts.init_db      # 在该 PG 库建 eval_task / eval_task_case 两张表
uvicorn app.main:app --reload  # http://127.0.0.1:8000/docs
```

> 配置文件查找顺序：环境变量 `CONFIG_FILE` > 当前目录 `./config.ini` > `backend/config.ini`。

## 2. 前端

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173  (已代理 /api -> 127.0.0.1:8000)
```

## 3. 登录看效果

浏览器开 **http://localhost:5173** → 用**任意用户名/密码**登录（`dev_login_enabled = true`）→
进去有 用例管理 / 任务管理 / 系统设置 三个页。

- 用例管理：连上 PG 且 `t_history_*_dataset` 已灌数据就能筛选/看列表。
- 任务管理：创建/查看任务（Agent 真跑需要能连 zhiexa）。
- 系统设置：改登录态时长。

## ⚠️ 上线前
`config.ini` 里 `dev_login_enabled` 设回 `false`——它让任意账号以超管登录，仅限本地。
`config.ini` 含密码，已被 gitignore，不会提交。
