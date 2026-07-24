"""Coze evaluation-workflow client (Stage 2 对比评测).

Runs the evaluation workflow (id = eval_task.eval_workflow_id) that scores the old
(baseline/workflow) answer vs the new (Agent) answer.

Contract (per the workflow doc):
  input : query, file_result_old, file_result_new, answer_old, answer_new
  output: score_old, score_new, file_score_old, file_score_new, score_reason
"""
import json
import logging
import time

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


async def run_eval(workflow_id: str, params: dict) -> dict:
    """Call POST /v1/workflow/run and return the parsed output dict."""
    if not settings.coze_api_token:
        raise RuntimeError("未配置 Coze api_token（config.ini [coze] api_token）")

    url = f"{settings.coze_api_base}/v1/workflow/run"
    param_lens = {k: len(str(v)) for k, v in params.items()}
    logger.info("[Coze] → POST %s workflow=%s param_lens=%s", url, workflow_id, param_lens)

    start = time.monotonic()
    async with httpx.AsyncClient(timeout=settings.coze_timeout) as client:
        resp = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {settings.coze_api_token}",
                "Content-Type": "application/json",
            },
            json={"workflow_id": workflow_id, "parameters": params},
        )
    elapsed = int((time.monotonic() - start) * 1000)

    if resp.status_code >= 400:
        logger.error("[Coze] ✗ HTTP %s (%sms): %s", resp.status_code, elapsed, resp.text[:500])
        resp.raise_for_status()

    body = resp.json()
    # Coze returns code 0 on success; `data` is a JSON string of the output node.
    if body.get("code") not in (0, None):
        logger.error("[Coze] ✗ code=%s msg=%s (%sms)", body.get("code"), body.get("msg"), elapsed)
        raise RuntimeError(f"Coze 工作流失败: code={body.get('code')} msg={body.get('msg')}")

    data = body.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (TypeError, ValueError):
            logger.error("[Coze] ✗ 返回无法解析: %r", data[:500])
            raise RuntimeError(f"Coze 返回无法解析: {data!r}")
    data = data or {}
    logger.info(
        "[Coze] ✓ (%sms) score_old=%s score_new=%s file_old=%s file_new=%s",
        elapsed, data.get("score_old"), data.get("score_new"),
        data.get("file_score_old"), data.get("file_score_new"),
    )
    return data
