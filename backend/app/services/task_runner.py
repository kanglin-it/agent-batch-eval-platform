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

from sqlalchemy import select, update

from app.core.config import settings
from app.db.case_session import CaseSessionLocal
from app.db.session import SessionLocal
from app.services.agent_limiter import agent_slot
from app.services.case_data import fetch_case_data
from app.models.eval_task import CaseStage, EvalTask, EvalTaskCase, TaskStatus
from app.services.coze_client import run_eval
from app.services.oss_file import (
    build_file_result,
    extract_text,
    normalize_agent_file_refs,
    resolve_answer_text,
)
from app.services.zhiexa_client import get_zhiexa_client

logger = logging.getLogger(__name__)

# Max cases processed concurrently WITHIN one task (per-task cap on Agent/Coze
# calls). Configurable via [task] concurrency in config.ini (default 50).
CONCURRENCY = settings.task_concurrency

# Global cap on concurrent per-case DB writes — decoupled from Agent concurrency
# so a high CONCURRENCY (or many parallel tasks) can't exhaust the write pool.
_persist_sem = asyncio.Semaphore(settings.task_persist_concurrency)

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

        task.status = TaskStatus.agent_running
        workflow_id = task.eval_workflow_id
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

        # Detach the case objects from the shared session BEFORE running the
        # pipeline: each case is persisted through its own session (targeted
        # UPDATE), and the pipeline frees each case's large text fields as soon as
        # they're safely in the DB. If the shared session still tracked them, the
        # final commit would flush those freed (None) fields back and wipe the
        # results. Only small result scalars are read afterwards for aggregation.
        for c in cases:
            db.expunge(c)

        # Per-case pipeline: run Agent then Coze-compare for one case, then release
        # its heavy text before the next case starts. Peak memory is thus bounded
        # to ~CONCURRENCY cases' worth of text — NOT all cases at once, which was
        # the real OOM driver (the old two-stage design held every case's full
        # parsed text + agent output in memory simultaneously).
        await asyncio.gather(*(_pipeline(c, sem, workflow_id) for c in cases))

        # ---- Aggregate (small scalars only; heavy text already freed) ----
        done = [c for c in cases if c.stage == CaseStage.compared]
        failed = [c for c in cases if c.stage == CaseStage.failed]
        if done:
            task.win_rate = round(sum(1 for c in done if c.is_win) / len(done), 4)
            task.hallucination_count = sum(1 for c in done if c.hallucination)
            latencies = [c.agent_latency_ms for c in done if c.agent_latency_ms is not None]
            task.avg_latency_ms = round(sum(latencies) / len(latencies), 2) if latencies else None
        task.status = TaskStatus.failed if failed and not done else TaskStatus.completed
        await db.commit()


async def _refresh_case_files(case: EvalTaskCase) -> None:
    """Re-resolve this case's file URLs from source right before use.

    File URLs are captured at task-creation (hydrate) time. For QA sources they come
    from the library public/query API as pre-signed OSS URLs with a baked-in
    `Expires`; for review sources straight from the SaaS `file_url`. Either way the
    signature goes stale if the task runs later than creation — scheduled tasks that
    wait for their hour, queue backlog, or a retry hours later — yielding 403
    Forbidden ("待上传文件全部下载失败") when the Agent downloads them.

    Re-hydrating from the SaaS source re-calls the library (or re-reads file_url),
    producing fresh, unexpired URLs. Best-effort: on any failure we keep the stored
    snapshot and let the download attempt proceed with it."""
    if not (case.source_case_id and case.files):
        return
    try:
        async with CaseSessionLocal() as case_db:
            data = await fetch_case_data(case_db, [case.source_case_id])
        fresh = (data.get(case.source_case_id) or {}).get("files")
        if fresh:
            case.files = fresh
    except Exception:  # noqa: BLE001 — a stale URL is still better than not trying
        logger.exception("refresh case %s files failed; using stored urls", case.id)


async def _pipeline(case: EvalTaskCase, sem: asyncio.Semaphore, workflow_id: str) -> None:
    """Agent → 对比 for a single case, then free its large text fields.

    Both stages run under one `sem` slot so at most CONCURRENCY cases hold heavy
    text (parsed file text / agent output / baseline) at a time. Stage idempotency
    (skip agent_done/compared) keeps retries correct."""
    async with sem:
        try:
            # Refresh pre-signed file URLs (they expire; see _refresh_case_files) so
            # both the Agent upload and the old-side parse use live URLs. Skip for
            # already-compared cases (a retry) — they touch no files.
            if case.stage != CaseStage.compared:
                await _refresh_case_files(case)
            await _stage_agent(case)            # sets agent_output/file_result/…
            if case.stage == CaseStage.agent_done:
                await _persist_case(case)
                await _stage_compare(case, workflow_id=workflow_id)
                await _persist_case(case)
        except Exception as exc:  # noqa: BLE001
            logger.exception("case %s failed", case.id)
            case.stage = CaseStage.failed
            case.error_msg = str(exc) or type(exc).__name__
            await _persist_case(case)
        finally:
            # Results are in the DB now; drop the large text so it doesn't pile up
            # across cases. Scalars used by aggregation (stage/is_win/…) are kept.
            case.agent_output = None
            case.agent_file_result = None
            case.baseline_answer = None
            case.compare_result = None
            case.files = None


def _scrub_nul(value):
    """Strip NUL (0x00) — PostgreSQL text/JSON columns reject it ("invalid byte
    sequence for encoding UTF8: 0x00"). PDF/docx extraction and Agent output can
    carry stray NUL bytes; recurse into the JSON compare_result too."""
    if isinstance(value, str):
        return value.replace("\x00", "") if "\x00" in value else value
    if isinstance(value, dict):
        return {k: _scrub_nul(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_nul(v) for v in value]
    return value


async def _persist_case(case: EvalTaskCase) -> None:
    """Persist one case's current result immediately in its own session.

    This is what makes the task list show per-case progress: without it, case
    stages are only committed after the whole stage's gather finishes, so a
    20-case task appears frozen until every case is done. A targeted UPDATE on a
    fresh session keeps us clear of the concurrently-mutated shared session.

    All text/JSON values are NUL-scrubbed here — this is the single write boundary,
    so nothing reaches PG with a 0x00 byte that would abort the UPDATE.
    """
    try:
        async with _persist_sem, SessionLocal() as s:
            await s.execute(
                update(EvalTaskCase)
                .where(EvalTaskCase.id == case.id)
                .values(
                    agent_output=_scrub_nul(case.agent_output),
                    agent_latency_ms=case.agent_latency_ms,
                    agent_file_result=_scrub_nul(case.agent_file_result),
                    agent_conversation_id=case.agent_conversation_id,
                    agent_task_url=case.agent_task_url,
                    baseline_answer=_scrub_nul(case.baseline_answer),
                    compare_result=_scrub_nul(case.compare_result),
                    coze_exec_url=case.coze_exec_url,
                    is_win=case.is_win,
                    hallucination=case.hallucination,
                    stage=case.stage,
                    error_msg=_scrub_nul(case.error_msg),
                )
            )
            await s.commit()
    except Exception:  # noqa: BLE001 — progress persistence must never kill the run
        logger.exception("persist case %s progress failed", case.id)


async def _stage_agent(case: EvalTaskCase) -> None:
    if case.stage in (CaseStage.agent_done, CaseStage.compared):
        return
    output, latency, parsed, cid, task_url = await _run_agent(case)
    # Scrub NUL at the source so both the DB write AND the Coze call (which runs
    # before the next persist and also rejects 0x00) get clean text.
    case.agent_output = _scrub_nul(output)
    case.agent_latency_ms = latency
    case.agent_file_result = _scrub_nul(parsed)
    case.agent_conversation_id = cid
    case.agent_task_url = task_url
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
    case.coze_exec_url = result.get("coze_exec_url")
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


# When files are attached, ask the Agent to also save each file's parsed full text
# as a downloadable txt (parsed_<原文件名>.txt) — these become file_result_new.
# One file per input keeps it aligned with file_result_old's per-file structure.
_PARSE_PROMPT = (
    "请处理我上传的文件，分两步执行：\n"
    "1. 对【每一个】上传的文件，分别把它的全部内容原样提取成纯文本，"
    "各用 save_file_for_download 保存为一个 txt 文件（文件名以 parsed_ 开头，"
    "并带上原文件名，如 parsed_<原文件名>.txt）；有几个文件就存几个，不要遗漏；\n"
    "2. 然后完成任务：{task}\n"
)


async def _extract_parsed_text(client, result_files: list) -> str:
    """Collect ALL parsed_*.txt deliverables and concatenate them.

    The Agent may emit one parsed_ file per input (multi-file cases); grabbing only
    the first would drop the rest, leaving file_result_new incomplete and
    asymmetric with file_result_old. Each block is labelled with its filename.
    """
    blocks: list[str] = []
    for f in result_files or []:
        name = f.get("name") or ""
        if not (name.startswith("parsed_") and f.get("url")):
            continue
        try:
            text = await client.download_text(f["url"])
        except Exception:  # noqa: BLE001
            logger.exception("download parsed text failed url=%s", str(f.get("url"))[:120])
            continue
        blocks.append(f"【{name}】\n{text}")
    return "\n\n".join(blocks)


async def _extract_output_files(client, result_files: list) -> str:
    """Content of the Agent's generated deliverable files (excluding the parsed_*
    input parses). For some modules (e.g. 文书起草) the answer IS a generated file,
    not the SSE text — so this gets appended to agent_output. Downloaded as bytes and
    text-extracted (docx/pdf/txt) so binary deliverables come through as text too.
    """
    blocks: list[str] = []
    for f in result_files or []:
        name = f.get("name") or ""
        url = f.get("url")
        if not url or name.startswith("parsed_"):
            continue
        try:
            data = await client.download_bytes(url)
            text = extract_text(name, data)
        except Exception:  # noqa: BLE001 — one bad file must not sink the case
            logger.exception("download output file failed url=%s", str(url)[:120])
            continue
        if text.strip():
            blocks.append(f"【生成文件：{name}】\n{text.strip()}")
    return "\n\n".join(blocks)


async def _run_agent(case: EvalTaskCase) -> tuple[str, int, str, str | None, str | None]:
    """Rerun a case through the Zhiexa sandbox Agent (create execution task).

    For 合同审查 / 文件审查, the user-facing command is a fixed prompt; files are
    downloaded from SaaS OSS (AES-decrypt when needed) and re-uploaded to Agent.
    When files are present we also ask the Agent to emit a parsed_*.txt (the new file
    parse result). Returns (output, latency_ms, parsed_text, conversation_id, task_url).
    """
    if case.source == "contract_review":
        task_message = _contract_review_message(case.stance)
    elif case.source in REVIEW_AGENT_PROMPTS:
        task_message = REVIEW_AGENT_PROMPTS[case.source]
    else:
        task_message = case.question or ""

    file_refs = normalize_agent_file_refs(case.files)
    had_files = bool(file_refs)
    message = _PARSE_PROMPT.format(task=task_message) if had_files else task_message

    client = get_zhiexa_client()
    # Global cap (across all tasks AND workers) on concurrent Agent /api/chat runs.
    # execute() streams the input files one at a time (fetch → upload → free), so the
    # pod never holds every input file's bytes at once.
    async with agent_slot():
        result = await client.execute(message=message, file_refs=file_refs or None)
    result_files = result.get("files") or []
    parsed_text = await _extract_parsed_text(client, result_files) if had_files else ""
    # The answer may live in a generated file (not just the SSE text) — fold it in
    # so answer_new / the Excel 新版答案 include it.
    output_files_text = await _extract_output_files(client, result_files)
    output = result.get("output") or ""
    output = f"{output}\n\n{output_files_text}".strip() if output_files_text else output
    cid = result.get("conversation_id")
    # Public share link so the Excel can link straight to this Agent run.
    task_url = await client.share_link(cid) if cid else None
    return output, result["latency_ms"], parsed_text, cid, task_url


def _parse_stance(stance_raw) -> dict | None:
    if isinstance(stance_raw, dict):
        return stance_raw
    if isinstance(stance_raw, str) and stance_raw.strip():
        try:
            parsed = json.loads(stance_raw)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _compare_query(case: EvalTaskCase) -> str:
    """query for the eval workflow.

    - 合同审查: 持方页（立场 / 力度 / 要求）
    - 文件审查: 任务名/文件名 + custom_require
    - 其它: question
    """
    if case.source == "contract_review":
        stance = _parse_stance(case.stance)
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

    if case.source == "file_review":
        stance = _parse_stance(case.stance)
        custom = ""
        if isinstance(stance, dict) and stance.get("custom_require"):
            custom = str(stance["custom_require"]).strip()
        name = (case.question or "").strip()
        if name and custom:
            return f"{name}。审查要求：{custom}"
        if custom:
            return f"审查要求：{custom}"
        return name

    return case.question or ""


async def _compare(case: EvalTaskCase, workflow_id: str) -> dict:
    """Score old (baseline/workflow) vs new (Agent) via the Coze eval workflow.

    old = baseline_answer (SaaS 历史回答), new = agent_output. Win when新版 scores
    higher. Hallucination is not part of this workflow's output, so it stays unset.

    Both old-side inputs are resolved to text here:
      - file_result_old: download the 待审文件 and extract its text (.docx via stdlib).
      - answer_old:      baseline text (QA 历史回答；合同=结果卡片摘要；
                         文件审查=final_result)。若仍是历史 OSS URL 则下载抽文本。
    """
    file_result_old, answer_old = await asyncio.gather(
        build_file_result(case.files),
        resolve_answer_text(case.baseline_answer),
    )
    # Scrub NUL from every Coze input — old-side text comes from PDF/docx extraction
    # which can carry 0x00; Coze rejects it just like PG does.
    params = _scrub_nul({
        "query": _compare_query(case),
        "file_result_old": file_result_old,
        "file_result_new": case.agent_file_result or "",   # Agent 解析出的纯文本
        "answer_old": answer_old,
        "answer_new": case.agent_output or "",
    })
    logger.info("[Compare] case=%s source=%s → 调 Coze workflow=%s", case.id, case.source, workflow_id)
    out, exec_url = await run_eval(workflow_id, params)

    score_old = out.get("score_old")
    score_new = out.get("score_new")
    is_win = (
        isinstance(score_old, (int, float))
        and isinstance(score_new, (int, float))
        and score_new > score_old
    )
    return {**out, "is_win": is_win, "coze_exec_url": exec_url}
