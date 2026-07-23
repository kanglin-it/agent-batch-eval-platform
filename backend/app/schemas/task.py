import datetime as dt

from pydantic import BaseModel, Field

from app.models.eval_task import TaskStatus


class CreateTaskRequest(BaseModel):
    name: str = Field(..., description="任务名称，默认可用创建时间")
    eval_workflow_id: str = Field(..., description="Coze 工作流 id")
    case_ids: list[str] = Field(..., max_length=500, description="选中的用例 id，上限 500")
    filter_snapshot: dict = Field(default_factory=dict)
    creator: str | None = Field(default=None, description="创建人；为空则用当前登录用户")


class TaskListItem(BaseModel):
    id: int
    name: str
    eval_workflow_id: str
    eval_skill_id: str | None = None
    case_count: int
    status: TaskStatus
    progress: str  # e.g. "230/1000"
    win_rate: float | None = None
    avg_latency_ms: float | None = None
    hallucination_count: int | None = None
    creator: str
    created_at: dt.datetime

    class Config:
        from_attributes = True
