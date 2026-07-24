"""Coze evaluation-workflow client (Stage 2 对比评测).

Runs the evaluation workflow (id = eval_task.eval_workflow_id) that scores the old
(baseline/workflow) answer vs the new (Agent) answer.

Contract (per the workflow doc):
  input : query, file_result_old, file_result_new, answer_old, answer_new
  output: score_old, score_new, file_score_old, file_score_new, score_reason
"""
import json

import httpx

from app.core.config import settings


async def run_eval(workflow_id: str, params: dict) -> dict:
    """Call POST /v1/workflow/run and return the parsed output dict."""
    if not settings.coze_api_token:
        raise RuntimeError("未配置 Coze api_token（config.ini [coze] api_token）")

    async with httpx.AsyncClient(timeout=settings.coze_timeout) as client:
        resp = await client.post(
            f"{settings.coze_api_base}/v1/workflow/run",
            headers={
                "Authorization": f"Bearer {settings.coze_api_token}",
                "Content-Type": "application/json",
            },
            json={"workflow_id": workflow_id, "parameters": params},
        )
    resp.raise_for_status()
    body = resp.json()

    # Coze returns code 0 on success; `data` is a JSON string of the output node.
    if body.get("code") not in (0, None):
        raise RuntimeError(f"Coze 工作流失败: code={body.get('code')} msg={body.get('msg')}")

    data = body.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (TypeError, ValueError):
            raise RuntimeError(f"Coze 返回无法解析: {data!r}")
    return data or {}
