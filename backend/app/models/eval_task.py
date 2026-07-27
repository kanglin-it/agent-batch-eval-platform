"""Evaluation domain models owned by THIS platform.

Tables use VARCHAR for status/stage (not PG ENUM types) — see DB DDL.
"""
import datetime as dt
import enum

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class TaskStatus(str, enum.Enum):
    agent_running = "agent_running"      # Agent 执行中 (x/N)
    comparing = "comparing"              # 对比评测中 (x/N)
    completed = "completed"              # 已完成 (N/N)
    failed = "failed"                    # 失败（可重试）


class CaseStage(str, enum.Enum):
    pending = "pending"
    agent_done = "agent_done"
    compared = "compared"
    failed = "failed"


# Persist as VARCHAR to match DB DDL (status/stage are varchar, not PG enums).
_TaskStatusCol = Enum(
    TaskStatus,
    name="taskstatus",
    native_enum=False,
    length=200,
    values_callable=lambda enum_cls: [m.value for m in enum_cls],
)
_CaseStageCol = Enum(
    CaseStage,
    name="casestage",
    native_enum=False,
    length=200,
    values_callable=lambda enum_cls: [m.value for m in enum_cls],
)


class EvalTask(Base):
    __tablename__ = "eval_task"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    eval_workflow_id: Mapped[str] = mapped_column(String(100))   # Coze workflow id
    eval_skill_id: Mapped[str | None] = mapped_column(String(100), nullable=True)  # reserved (P1)
    case_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[TaskStatus] = mapped_column(_TaskStatusCol, default=TaskStatus.agent_running)
    creator: Mapped[str] = mapped_column(String(150))          # display name
    creator_phone: Mapped[str | None] = mapped_column(String(30), index=True)  # owner (isolation)

    # Snapshot of the filter used, so the "用例范围" can be shown later.
    filter_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)

    # Aggregated metrics
    win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    hallucination_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc)
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.timezone.utc),
        onupdate=lambda: dt.datetime.now(dt.timezone.utc),
    )

    cases: Mapped[list["EvalTaskCase"]] = relationship(back_populates="task", cascade="all, delete-orphan")


class EvalTaskCase(Base):
    __tablename__ = "eval_task_case"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("eval_task.id"), index=True)

    # Reference back to the source case in the SaaS user-behavior table.
    source_case_id: Mapped[str] = mapped_column(String(100), index=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)  # legal_research / file_review / …
    question: Mapped[str] = mapped_column(Text)
    files: Mapped[list] = mapped_column(JSON, default=list)
    stance: Mapped[str | None] = mapped_column(Text, nullable=True)   # 持方页/中间页 info (合同审查)

    # Agent rerun result
    agent_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 新版文件解析结果 (Agent parsed the uploaded files into text) — file_result_new
    agent_file_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Agent 会话 id + 公开分享链接（工作台无深链，只能靠 /share/<token> 查看该任务）
    agent_conversation_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    agent_task_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Historical baseline = the answer the SaaS system originally returned
    baseline_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 旧版 SaaS 任务在 t_coze_log.debug_url 上的执行链接
    baseline_coze_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Comparison outcome
    compare_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    coze_exec_url: Mapped[str | None] = mapped_column(Text, nullable=True)  # 评测 Coze 执行链接(debug_url)
    is_win: Mapped[bool | None] = mapped_column(nullable=True)
    hallucination: Mapped[bool | None] = mapped_column(nullable=True)

    stage: Mapped[CaseStage] = mapped_column(_CaseStageCol, default=CaseStage.pending)
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)

    task: Mapped["EvalTask"] = relationship(back_populates="cases")
