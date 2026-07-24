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
import functools
import json
import logging

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.eval_task import CaseStage, EvalTask, EvalTaskCase, TaskStatus
from app.services.coze_client import run_eval
from app.services.oss_file import build_file_result, prepare_agent_files
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
        await _run_stage(db, cases, sem, functools.partial(_stage_compare, workflow_id=task.eval_workflow_id))

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
    output, latency, parsed = await _run_agent(case)
    case.agent_output = output
    case.agent_latency_ms = latency
    case.agent_file_result = parsed
    case.stage = CaseStage.agent_done
    case.error_msg = None


async def _stage_compare(case: EvalTaskCase, workflow_id: str) -> None:
    if case.stage == CaseStage.compared:
        return
    if case.stage != CaseStage.agent_done:
        return  # never ran the agent successfully
    result = await _compare(case, workflow_id)
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


# When files are attached, ask the Agent to also save the parsed full text as a
# downloadable txt (parsed_*.txt) — that becomes file_result_new for the eval.
_PARSE_PROMPT = (
    "请处理我上传的文件，分两步执行：\n"
    "1. 把文件的全部内容原样提取成纯文本，用 save_file_for_download 保存为一个 txt 文件"
    "（文件名以 parsed_ 开头）；\n"
    "2. 然后完成任务：{task}\n"
)


async def _extract_parsed_text(client, result_files: list) -> str:
    """Find the parsed_*.txt deliverable and download it as text."""
    for f in result_files or []:
        name = f.get("name") or ""
        if name.startswith("parsed_") and f.get("url"):
            try:
                return await client.download_text(f["url"])
            except Exception:  # noqa: BLE001
                logger.exception("download parsed text failed url=%s", str(f.get("url"))[:120])
                return ""
    return ""


async def _run_agent(case: EvalTaskCase) -> tuple[str, int, str]:
    """Rerun a case through the Zhiexa sandbox Agent (create execution task).

    For 合同审查 / 文件审查, the user-facing command is a fixed prompt; files are
    downloaded from SaaS OSS (AES-decrypt when needed) and re-uploaded to Agent.
    When files are present we also ask the Agent to emit a parsed_*.txt (the new file
    parse result). Returns (output, latency_ms, parsed_text).
    """
    if case.source == "contract_review":
        task_message = _contract_review_message(case.stance)
    elif case.source in REVIEW_AGENT_PROMPTS:
        task_message = REVIEW_AGENT_PROMPTS[case.source]
    else:
        task_message = case.question or ""

    files = await prepare_agent_files(case.files)
    message = _PARSE_PROMPT.format(task=task_message) if files else task_message

    client = get_zhiexa_client()
    result = await client.execute(message=message, files=files or None)
    parsed_text = await _extract_parsed_text(client, result.get("files") or []) if files else ""
    return result["output"], result["latency_ms"], parsed_text


def _compare_query(case: EvalTaskCase) -> str:
    """query for the eval workflow: 合同审查 uses the 持方页 info, else the question."""
    if case.source == "contract_review":
        stance = case.stance
        if isinstance(stance, str):
            try:
                stance = json.loads(stance)
            except (TypeError, ValueError):
                stance = None
        if isinstance(stance, dict):
            parts = []
            if stance.get("review_stance") or stance.get("subject"):
                parts.append(f"持方：{stance.get('review_stance', '')}（{stance.get('subject', '')}）")
            if stance.get("review_status"):
                parts.append(f"审查力度：{stance['review_status']}")
            if stance.get("custom_require"):
                parts.append(f"审查要求：{stance['custom_require']}")
            joined = "。".join(p for p in parts if p)
            if joined:
                return joined
        if isinstance(case.stance, str) and case.stance:
            return case.stance
    return case.question or ""


async def _compare(case: EvalTaskCase, workflow_id: str) -> dict:
    """Score old (baseline/workflow) vs new (Agent) via the Coze eval workflow.

    old = baseline_answer (SaaS 历史回答), new = agent_output. Win when新版 scores
    higher. file_result_old/new are the old/new file-parsing outputs — not captured
    yet, so passed empty for now (TODO). Hallucination is not part of this workflow's
    output, so it stays unset.
    """
    # 旧版文件解析结果：直接下载旧任务的文件 OSS 链接并拼成文本。
    file_result_old = await build_file_result(case.files)
    params = {
        "query": _compare_query(case),
        "file_result_old": file_result_old,
        "file_result_new": case.agent_file_result or "",   # Agent 解析出的纯文本
        "answer_old": case.baseline_answer or "",
        "answer_new": case.agent_output or "",
    }
    out = await run_eval(workflow_id, params)

    score_old = out.get("score_old")
    score_new = out.get("score_new")
    is_win = (
        isinstance(score_old, (int, float))
        and isinstance(score_new, (int, float))
        and score_new > score_old
    )
    return {**out, "is_win": is_win}
