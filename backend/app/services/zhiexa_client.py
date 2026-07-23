"""Client for the Zhiexa sandbox-conversation Agent.

This is the Agent the eval platform reruns cases through (Stage 1 「Agent 执行」).

Flow:
  1. Login with fixed credentials -> satoken.
  2. Exchange satoken for this-system JWT (cached ~6 days; JWT is valid 7).
  3. Create an execution task = POST /api/chat (SSE stream), aggregate the answer.
  4. With files: per file presign -> PUT to OSS -> confirm, then pass the returned
     file objects in uploaded_files (all sharing one conversation_id).

SSE event shape is not fully documented; parsing here is defensive (aggregates any
text-ish field on text events, captures the conversation id, stops on `done`).
Verify field names against a real stream and tighten `_extract_*` if needed.
"""
import asyncio
import json
import logging
import time
import uuid

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class ZhiexaClient:
    def __init__(self) -> None:
        self._jwt: str | None = None
        self._jwt_exp: float = 0.0
        self._lock = asyncio.Lock()

    # ---------- auth ----------
    async def _get_satoken(self, client: httpx.AsyncClient) -> str:
        r = await client.post(
            settings.zhiexa_login_url,
            json={
                "phoneNum": settings.zhiexa_phone,
                "password": settings.zhiexa_password,
                "channelType": settings.zhiexa_channel_type,
            },
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 200 or not data.get("data", {}).get("satoken"):
            raise RuntimeError(f"zhiexa login failed: {data.get('message')}")
        return data["data"]["satoken"]

    async def _exchange_jwt(self, client: httpx.AsyncClient, satoken: str) -> str:
        r = await client.post(f"{settings.zhiexa_skill_base}/api/auth/sso", json={"token": satoken})
        r.raise_for_status()
        data = r.json()
        if not data.get("success") or not data.get("token"):
            raise RuntimeError(f"zhiexa sso exchange failed: {data}")
        return data["token"]

    async def get_jwt(self) -> str:
        async with self._lock:
            if self._jwt and time.time() < self._jwt_exp:
                return self._jwt
            async with httpx.AsyncClient(timeout=30) as client:
                satoken = await self._get_satoken(client)
                jwt = await self._exchange_jwt(client, satoken)
            self._jwt = jwt
            self._jwt_exp = time.time() + settings.zhiexa_jwt_ttl_seconds
            return jwt

    # ---------- file upload (presign -> PUT OSS -> confirm) ----------
    async def _upload_file(
        self, client: httpx.AsyncClient, headers: dict, conversation_id: str,
        filename: str, data: bytes, content_type: str,
    ) -> dict:
        presign = await client.post(
            f"{settings.zhiexa_skill_base}/api/upload/presign",
            headers=headers,
            json={"filename": filename, "file_size": len(data),
                  "content_type": content_type, "conversation_id": conversation_id},
        )
        presign.raise_for_status()
        pr = presign.json()
        file_meta = pr["file"]

        put = await client.put(pr["put_url"], content=data,
                               headers={"Content-Type": pr.get("content_type", content_type)})
        put.raise_for_status()

        confirm = await client.post(
            f"{settings.zhiexa_skill_base}/api/upload/confirm",
            headers=headers, json={"file": file_meta, "conversation_id": conversation_id},
        )
        confirm.raise_for_status()
        return file_meta

    # ---------- create execution task = POST /api/chat (SSE) ----------
    async def execute(
        self, message: str, files: list[tuple[str, bytes, str]] | None = None,
        conversation_id: str | None = None,
    ) -> dict:
        """Run one Agent task. `files` = list of (filename, bytes, content_type).
        Returns {output, conversation_id, latency_ms}."""
        jwt = await self.get_jwt()
        headers = {"Authorization": f"Bearer {jwt}"}
        cid = conversation_id or str(uuid.uuid4())
        start = time.monotonic()

        async with httpx.AsyncClient(timeout=settings.zhiexa_chat_timeout) as client:
            uploaded: list[dict] = []
            for filename, data, ctype in files or []:
                uploaded.append(await self._upload_file(client, headers, cid, filename, data, ctype))

            texts: list[str] = []
            async with client.stream(
                "POST", f"{settings.zhiexa_skill_base}/api/chat", headers=headers,
                json={"conversation_id": cid, "message": message, "uploaded_files": uploaded},
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload:
                        continue
                    try:
                        evt = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    etype = evt.get("type") or evt.get("event")
                    if etype == "conversation":
                        cid = evt.get("conversation_id") or evt.get("id") or cid
                    elif etype in ("text", "message", "answer") or "content" in evt:
                        chunk = evt.get("content") or evt.get("text") or evt.get("delta")
                        if isinstance(chunk, str):
                            texts.append(chunk)
                    elif etype == "done":
                        break

        return {"output": "".join(texts), "conversation_id": cid,
                "latency_ms": int((time.monotonic() - start) * 1000)}


_client: ZhiexaClient | None = None


def get_zhiexa_client() -> ZhiexaClient:
    global _client
    if _client is None:
        _client = ZhiexaClient()
    return _client
