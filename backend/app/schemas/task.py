import datetime as dt

from pydantic import BaseModel, Field

from app.models.eval_task import TaskStatus


class CreateTaskRequest(BaseModel):
    name: str = Field(..., description="任务名称，默认可用创建时间")
    # 选填：为空则该任务只跑 Agent、不执行 Coze 评测工作流，评测结果字段导出为 "/"。
    eval_workflow_id: str = Field(default="", description="Coze 工作流 id（选填，为空不评测）")
    case_ids: list[str] = Field(..., max_length=500, description="选中的用例 id，上限 500")
    filter_snapshot: dict = Field(default_factory=dict)
    creator: str | None = Field(default=None, description="创建人；为空则用当前登录用户")
    # 定时执行时间（北京整点，如 "2026-07-28 15:00:00"）。为空=立即执行。
    scheduled_at: dt.datetime | None = Field(
        default=None, description="定时执行时间（北京整点）；为空立即执行"
    )


class TaskListItem(BaseModel):
    id: int
    name: str
    eval_workflow_id: str
    eval_skill_id: str | None = None
    case_count: int
    status: TaskStatus
    progress: str  # e.g. "230/1000"
    failed_count: int = 0  # 失败用例数（用于文案展示）
    retryable: bool = False  # 非执行中且存在未完成用例（失败/未跑/待对比）时可重试
    scheduled_at: dt.datetime | None = None  # 定时执行时间（有则为定时任务）
    win_rate: float | None = None
    avg_latency_ms: float | None = None
    hallucination_count: int | None = None
    creator: str
    created_at: dt.datetime

    class Config:
        from_attributes = True


class TaskPage(BaseModel):
    total: int
    items: list[TaskListItem]
