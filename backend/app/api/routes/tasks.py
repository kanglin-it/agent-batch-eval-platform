import io
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.routes.cases import CASE_SELECTION_LIMIT
from app.db.case_session import get_case_db
from app.db.session import get_db
from app.models.eval_task import CaseStage, EvalTask, EvalTaskCase, TaskStatus
from app.schemas.auth import CurrentUser
from app.schemas.task import CreateTaskRequest, TaskListItem
from app.services.case_data import fetch_case_data
from app.services.export_excel import build_result_xlsx
from app.services.task_runner import run_task

XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

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
    case_db: AsyncSession = Depends(get_case_db),
    current: CurrentUser = Depends(get_current_user),
):
    if not body.case_ids:
        raise HTTPException(400, "请先选择评测用例")
    if len(body.case_ids) > CASE_SELECTION_LIMIT:
        raise HTTPException(400, f"勾选用例最多 {CASE_SELECTION_LIMIT} 条")
    case_ids = body.case_ids

    # Hydrate each case (question / files / stance / historical baseline) from the
    # PG dataset tables so the Agent has real input to rerun.
    data = await fetch_case_data(case_db, case_ids)

    task = EvalTask(
        name=body.name,
        eval_workflow_id=body.eval_workflow_id,
        case_count=len(case_ids),
        status=TaskStatus.agent_running,
        creator=(body.creator or current.username).strip() or current.username,
        creator_phone=current.phone,          # owner key for isolation
        filter_snapshot=body.filter_snapshot,
    )
    task.cases = [
        EvalTaskCase(
            source_case_id=cid,
            source=data.get(cid, {}).get("source"),
            question=data.get(cid, {}).get("question", ""),
            files=data.get(cid, {}).get("files", []),
            stance=data.get(cid, {}).get("stance"),
            baseline_answer=data.get(cid, {}).get("baseline_answer"),
            stage=CaseStage.pending,
        )
        for cid in case_ids
    ]
    db.add(task)
    await db.commit()
    await db.refresh(task, attribute_names=["cases"])

    # Kick off the two-stage async pipeline. In production use a real queue/worker
    # (Celery / RQ / Arq) instead of BackgroundTasks so it survives restarts.
    background.add_task(run_task, task.id)

    return TaskListItem(progress=_progress(task), **_task_fields(task))


@router.get("", response_model=list[TaskListItem])
async def list_tasks(
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    # User isolation: regular users see only their own tasks; superusers see all.
    stmt = select(EvalTask).order_by(EvalTask.created_at.desc())
    if not current.is_superuser:
        stmt = stmt.where(EvalTask.creator_phone == current.phone)
    result = await db.execute(stmt)
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
    current: CurrentUser = Depends(get_current_user),
):
    task = await db.get(EvalTask, task_id)
    if task is None or (not current.is_superuser and task.creator_phone != current.phone):
        raise HTTPException(404, "任务不存在")
    if task.status != TaskStatus.failed:
        raise HTTPException(400, "仅失败状态的任务可重试")
    # Re-run the SAME task (do not create a new one); only failed cases are retried.
    task.status = TaskStatus.agent_running
    await db.commit()
    background.add_task(run_task, task.id)
    await db.refresh(task, attribute_names=["cases"])
    return TaskListItem(progress=_progress(task), **_task_fields(task))


@router.get("/workflow-ids", response_model=list[str])
async def list_workflow_ids(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    """Distinct 历史使用过的 Coze workflow_id, most-recently-used first.

    Feeds the create-task dialog's Workflow dropdown (可下拉选择, 也可手动输入).
    """
    stmt = (
        select(EvalTask.eval_workflow_id)
        .where(EvalTask.eval_workflow_id.isnot(None), EvalTask.eval_workflow_id != "")
        .group_by(EvalTask.eval_workflow_id)
        .order_by(func.max(EvalTask.created_at).desc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return list(rows)


@router.get("/{task_id}/download")
async def download_result(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """Download the 评测结果 Excel for a task (no 幻觉数 column)."""
    task = await db.get(EvalTask, task_id)
    if task is None or (not current.is_superuser and task.creator_phone != current.phone):
        raise HTTPException(404, "任务不存在")
    if task.status not in (TaskStatus.completed, TaskStatus.failed):
        raise HTTPException(400, "任务执行中，暂不可下载")
    await db.refresh(task, attribute_names=["cases"])
    cases = sorted(task.cases, key=lambda c: c.id)

    content = build_result_xlsx(task, cases)
    filename = quote(f"评测结果_{task.name}.xlsx")
    return StreamingResponse(
        io.BytesIO(content),
        media_type=XLSX_MEDIA,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


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
