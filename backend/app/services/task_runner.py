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
from app.services.oss_file import prepare_agent_files
from app.services.zhiexa_client import get_zhiexa_client

logger = logging.getLogger(__name__)

# Protect downstream Agent/Coze services from 500 concurrent calls.
CONCURRENCY = 5

# Fixed agent prompts for review modules (question/task_name is not the user command).
REVIEW_AGENT_PROMPTS = {
    "file_review": "请帮我审查一下这份文件",
    "contract_review": "请帮我审查一下这份合同",
}


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
        # Warm JWT once before fan-out so concurrent cases don't race on auth.
        try:
            await get_zhiexa_client().get_jwt()
        except Exception as exc:  # noqa: BLE001
            logger.exception("zhiexa auth failed before agent stage")
            for c in cases:
                if c.stage not in (CaseStage.agent_done, CaseStage.compared):
                    c.stage = CaseStage.failed
                    c.error_msg = f"zhiexa auth failed: {exc or type(exc).__name__}"
            task.status = TaskStatus.failed
            await db.commit()
            return
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
                case.error_msg = str(exc) or type(exc).__name__

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
def _contract_review_message(stance_raw) -> str:
    """Build contract-review Agent command from 持方页 fields."""
    stance = stance_raw
    if isinstance(stance_raw, str):
        try:
            stance = json.loads(stance_raw)
        except (TypeError, ValueError):
            stance = {}
    if not isinstance(stance, dict):
        stance = {}

    review_stance = stance.get("review_stance") or ""
    subject = stance.get("subject") or ""
    review_status = stance.get("review_status") or ""
    custom_require = stance.get("custom_require")
    if custom_require is None:
        custom_require = ""
    else:
        custom_require = str(custom_require).strip()

    message = (
        f"@合同审查 【审查立场】{review_stance}（{subject}） "
        f"【审查力度】{review_status}审查"
    )
    if custom_require:
        message = f"{message} 【审查要求】{custom_require}"
    return message


async def _run_agent(case: EvalTaskCase) -> tuple[str, int]:
    """Rerun a case through the Zhiexa sandbox Agent (create execution task).

    For 合同审查 / 文件审查, the user-facing command is a fixed prompt; files are
    downloaded from SaaS OSS (AES-decrypt when needed) and re-uploaded to Agent.
    Returns (output, latency_ms).
    """
    if case.source == "contract_review":
        message = _contract_review_message(case.stance)
    elif case.source in REVIEW_AGENT_PROMPTS:
        message = REVIEW_AGENT_PROMPTS[case.source]
    else:
        message = case.question or ""

    files = await prepare_agent_files(case.files)
    result = await get_zhiexa_client().execute(message=message, files=files or None)
    return result["output"], result["latency_ms"]


async def _compare(case: EvalTaskCase) -> dict:
    """Call the evaluation Coze workflow (task.eval_workflow_id) comparing
    case.agent_output against case.baseline_answer. Returns metric dict, e.g.
    {"is_win": True, "hallucination": False, ...}."""
    raise NotImplementedError("Wire up the Coze workflow once metric definitions are confirmed")
