# Backend — Agent 批量评测平台 (FastAPI)

FastAPI service that **reuses the Django ops-backend user system** for login and
runs the two-stage batch evaluation pipeline (Agent 执行 → 对比评测).

## Auth model

- The Django user table is mapped **read-only** (`app/models/user.py`); password
  changes / user creation stay in the Django admin.
- Login verifies the plaintext against Django's stored hash via `passlib`
  (`django_pbkdf2_sha256` etc.). Django hashes are self-describing, so **no Django
  runtime and no `SECRET_KEY` are needed** to verify them.
- On success we issue **this platform's own JWT** (`app/core/security.py`); the
  Django session is not reused.

## Layout

```
app/
  main.py                 # FastAPI app + CORS + routers
  core/
    config.py             # settings (config.ini)
    security.py           # Django password verify + JWT
  db/session.py           # async SQLAlchemy engine/session
  models/
    user.py               # read-only Django user table
    eval_task.py          # eval_task / eval_task_case (owned by this platform)
  schemas/                # pydantic request/response
  api/
    deps.py               # get_current_user (JWT)
    routes/               # auth / cases / tasks
  services/task_runner.py # two-stage async pipeline (Agent + Coze), stubbed calls
```

## Run

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.ini.example config.ini   # then edit [database] / [case_database] / [jwt]
python -m scripts.init_db          # create eval_task / eval_task_case tables
uvicorn app.main:app --reload
# Swagger UI: http://127.0.0.1:8000/docs
```

## TODO before production (tracked in the tech plan)

- Point `list_cases` at the real SaaS user-behavior source (query is stubbed).
- Wire `_run_agent` / `_compare` to the Agent and Coze APIs; confirm the
  win-rate / hallucination / latency metric definitions.
- Move the pipeline off `BackgroundTasks` onto a durable queue/worker
  (Celery / RQ / Arq) so long 500-case runs survive restarts.
- Add a migration tool (Alembic) to create the `eval_*` tables.
