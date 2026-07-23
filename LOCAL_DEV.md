# 本地跑起来

目标：在你自己的 Mac 上把前后端跑起来，用**运营平台账号**登录看效果。
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
# t_operation_user 所在的库(运营平台库)
host = <你的PG服务器地址>
port = 5432
user = <账号>
password = <密码>
dbname = <库名>
user_table = t_operation_user

[case_database]
# 用例库。与运营平台库同库则留空(复用 [database]); 否则单独填。
schema = public

[jwt]
secret = local-dev-secret

[zhiexa]
phone = 15067062596
password = <智合密码>
```

```bash
python -m scripts.init_db      # 在 [database] 库建 eval_task / eval_task_case 两张表
uvicorn app.main:app --reload  # http://127.0.0.1:8000/docs
```

> 配置文件查找顺序：环境变量 `CONFIG_FILE` > 当前目录 `./config.ini` > `backend/config.ini`。
> 若运营平台库是只读库，`[database]` 可改用本地可写库放平台表（`init_db` 需要写权限）。

## 2. 前端

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173  (已代理 /api -> 127.0.0.1:8000)
```

## 3. 登录看效果

浏览器开 **http://localhost:5173** → 用**运营平台真实的 username + 密码**登录 → 进去有
用例管理 / 任务管理 / 系统设置。

- 登录走 `t_operation_user`（Django 哈希，passlib 校验）。
- 用例管理：连上用例库即可筛选/看列表。
- 任务管理：创建/查看任务（Agent 真跑需要能连 zhiexa）。
- 系统设置：改登录态时长。

> `config.ini` 含密码，已被 gitignore，不会提交。
