"""Two-stage evaluation pipeline (后台处理).

Stage 1 — Agent 执行: replay each case's question (+ files, + 持方页 info for 合同审查)
          through the new Agent, capturing output and latency.
Stage 2 — 对比评测: send Agent output vs. the historical baseline answer to the
          evaluation Coze workflow, deriving win / hallucination / latency, then
          aggregate task-level 胜率 / 幻觉数 / 耗时.

This is a runnable skeleton with the control flow and idempotency points in place;
the external calls (`_run_agent`, `_compare`) are stubs to wire up once the Agent
and Coze contracts + metric definitions are confirmed.
"""
import asyncio
import json
import logging

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.eval_task import CaseStage, EvalTask, EvalTaskCase, TaskStatus
from app.services.zhiexa_client import get_zhiexa_client

logger = logging.getLogger(__name__)

# Protect downstream Agent/Coze services from 500 concurrent calls.
CONCURRENCY = 5


async def run_task(task_id: int) -> None:
    async with SessionLocal() as db:
        task = await db.get(EvalTask, task_id)
        if task is None:
            return
        cases = (await db.execute(select(EvalTaskCase).where(EvalTaskCase.task_id == task_id))).scalars().all()

        sem = asyncio.Semaphore(CONCURRENCY)

        # ---- Stage 1: Agent 执行 ----
        task.status = TaskStatus.agent_running
        await db.commit()
        await _run_stage(db, cases, sem, _stage_agent)

        # ---- Stage 2: 对比评测 ----
        task.status = TaskStatus.comparing
        await db.commit()
        await _run_stage(db, cases, sem, _stage_compare)

        # ---- Aggregate ----
        done = [c for c in cases if c.stage == CaseStage.compared]
        failed = [c for c in cases if c.stage == CaseStage.failed]
        if done:
            task.win_rate = round(sum(1 for c in done if c.is_win) / len(done), 4)
            task.hallucination_count = sum(1 for c in done if c.hallucination)
            latencies = [c.agent_latency_ms for c in done if c.agent_latency_ms is not None]
            task.avg_latency_ms = round(sum(latencies) / len(latencies), 2) if latencies else None
        task.status = TaskStatus.failed if failed and not done else TaskStatus.completed
        await db.commit()


async def _run_stage(db, cases, sem, worker) -> None:
    async def guarded(case):
        # Idempotency: skip cases already past this stage (supports retry).
        async with sem:
            try:
                await worker(case)
            except Exception as exc:  # noqa: BLE001
                logger.exception("case %s failed", case.id)
                case.stage = CaseStage.failed
                case.error_msg = str(exc)

    await asyncio.gather(*(guarded(c) for c in cases))
    await db.commit()


async def _stage_agent(case: EvalTaskCase) -> None:
    if case.stage in (CaseStage.agent_done, CaseStage.compared):
        return
    output, latency = await _run_agent(case)
    case.agent_output = output
    case.agent_latency_ms = latency
    case.stage = CaseStage.agent_done
    case.error_msg = None


async def _stage_compare(case: EvalTaskCase) -> None:
    if case.stage == CaseStage.compared:
        return
    if case.stage != CaseStage.agent_done:
        return  # never ran the agent successfully
    result = await _compare(case)
    case.compare_result = result
    case.is_win = result.get("is_win")
    case.hallucination = result.get("hallucination")
    case.stage = CaseStage.compared


# --------- external integrations ---------
async def _run_agent(case: EvalTaskCase) -> tuple[str, int]:
    """Rerun a case through the Zhiexa sandbox Agent (create execution task).

    For 合同审查, the 持方页(stance) info is appended to the message; files (case.files)
    still need to be fetched from OSS and re-uploaded — see TODO below.
    Returns (output, latency_ms).
    """
    message = case.question or ""
    if case.stance:
        stance = case.stance if isinstance(case.stance, str) else json.dumps(case.stance, ensure_ascii=False)
        message = f"{message}\n\n[审查立场/持方页]\n{stance}"

    # TODO: case.files are OSS references; to send them, download the bytes and pass
    #   files=[(name, data, content_type), ...] to execute(). Text-only for now.
    result = await get_zhiexa_client().execute(message=message, files=None)
    return result["output"], result["latency_ms"]


async def _compare(case: EvalTaskCase) -> dict:
    """Call the evaluation Coze workflow (task.eval_workflow_id) comparing
    case.agent_output against case.baseline_answer. Returns metric dict, e.g.
    {"is_win": True, "hallucination": False, ...}."""
    raise NotImplementedError("Wire up the Coze workflow once metric definitions are confirmed")
