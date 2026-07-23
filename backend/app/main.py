from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import auth, cases, settings as settings_route, tasks
from app.core.config import settings

app = FastAPI(title="Agent 批量评测平台 API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(cases.router)
app.include_router(tasks.router)
app.include_router(settings_route.router)


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok"}
