from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.routes.cases import CASE_SELECTION_LIMIT
from app.db.session import get_db
from app.models.eval_task import CaseStage, EvalTask, EvalTaskCase, TaskStatus
from app.schemas.auth import CurrentUser
from app.schemas.task import CreateTaskRequest, TaskListItem
from app.services.task_runner import run_task

router = APIRouter(prefix="/api/eval-tasks", tags=["tasks"])


def _progress(task: EvalTask) -> str:
    done = sum(1 for c in task.cases if c.stage in (CaseStage.agent_done, CaseStage.compared))
    if task.status == TaskStatus.comparing:
        done = sum(1 for c in task.cases if c.stage == CaseStage.compared)
    return f"{done}/{task.case_count}"


@router.post("", response_model=TaskListItem)
async def create_task(
    body: CreateTaskRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    if not body.case_ids:
        raise HTTPException(400, "请先选择评测用例")
    case_ids = body.case_ids[:CASE_SELECTION_LIMIT]  # enforce 500 cap server-side

    task = EvalTask(
        name=body.name,
        eval_workflow_id=body.eval_workflow_id,
        case_count=len(case_ids),
        status=TaskStatus.agent_running,
        creator=current.username,
        filter_snapshot=body.filter_snapshot,
    )
    task.cases = [EvalTaskCase(source_case_id=cid, question="", stage=CaseStage.pending) for cid in case_ids]
    db.add(task)
    await db.commit()
    await db.refresh(task, attribute_names=["cases"])

    # Kick off the two-stage async pipeline. In production use a real queue/worker
    # (Celery / RQ / Arq) instead of BackgroundTasks so it survives restarts.
    background.add_task(run_task, task.id)

    return TaskListItem(progress=_progress(task), **_task_fields(task))


@router.get("", response_model=list[TaskListItem])
async def list_tasks(db: AsyncSession = Depends(get_db), _: CurrentUser = Depends(get_current_user)):
    result = await db.execute(select(EvalTask).order_by(EvalTask.created_at.desc()))
    tasks = result.scalars().unique().all()
    out = []
    for t in tasks:
        await db.refresh(t, attribute_names=["cases"])
        out.append(TaskListItem(progress=_progress(t), **_task_fields(t)))
    return out


@router.post("/{task_id}/retry", response_model=TaskListItem)
async def retry_task(
    task_id: int,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    task = await db.get(EvalTask, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    if task.status != TaskStatus.failed:
        raise HTTPException(400, "仅失败状态的任务可重试")
    # Re-run the SAME task (do not create a new one); only failed cases are retried.
    task.status = TaskStatus.agent_running
    await db.commit()
    background.add_task(run_task, task.id)
    await db.refresh(task, attribute_names=["cases"])
    return TaskListItem(progress=_progress(task), **_task_fields(task))


def _task_fields(t: EvalTask) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "eval_workflow_id": t.eval_workflow_id,
        "eval_skill_id": t.eval_skill_id,
        "case_count": t.case_count,
        "status": t.status,
        "win_rate": t.win_rate,
        "avg_latency_ms": t.avg_latency_ms,
        "hallucination_count": t.hallucination_count,
        "creator": t.creator,
        "created_at": t.created_at,
    }
