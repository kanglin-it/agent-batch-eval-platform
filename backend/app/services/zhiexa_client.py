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
            headers={"Domain": "www.zhiexa.com"},
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

    async def get_jwt(self, *, force: bool = False) -> str:
        """Return cached JWT, refreshing with retries on transient network errors."""
        async with self._lock:
            if not force and self._jwt and time.time() < self._jwt_exp:
                return self._jwt

            last_exc: Exception | None = None
            for attempt in range(1, 4):
                try:
                    async with httpx.AsyncClient(timeout=30) as client:
                        satoken = await self._get_satoken(client)
                        jwt = await self._exchange_jwt(client, satoken)
                    self._jwt = jwt
                    self._jwt_exp = time.time() + settings.zhiexa_jwt_ttl_seconds
                    return jwt
                except (httpx.TransportError, httpx.TimeoutException) as exc:
                    last_exc = exc
                    self._jwt = None
                    self._jwt_exp = 0.0
                    logger.warning(
                        "zhiexa JWT exchange failed (attempt %s/3): %s",
                        attempt, exc or type(exc).__name__,
                    )
                    if attempt < 3:
                        await asyncio.sleep(0.5 * attempt)
            assert last_exc is not None
            raise last_exc

    def invalidate_jwt(self) -> None:
        self._jwt = None
        self._jwt_exp = 0.0

    # ---------- shareable task link ----------
    async def share_link(self, conversation_id: str) -> str | None:
        """Create (or fetch the existing) public share link for a conversation.

        The workbench has no deep-link by cid; the only viewable-by-URL form is the
        public share page https://.../share/<token>. Re-sharing the same cid returns
        a stable token, so this is idempotent. Best-effort: returns None on failure.
        """
        if not conversation_id:
            return None
        try:
            jwt = await self.get_jwt()
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    f"{settings.zhiexa_skill_base}/api/conversations/{conversation_id}/share",
                    headers={"Authorization": f"Bearer {jwt}"},
                    json={
                        "expires_days": settings.zhiexa_share_expires_days,
                        "allow_download": settings.zhiexa_share_allow_download,
                    },
                )
                r.raise_for_status()
                data = r.json()
            return (data.get("share") or {}).get("url")
        except Exception:  # noqa: BLE001 — a missing link must not fail the case
            logger.exception("share_link failed cid=%s", conversation_id)
            return None

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

    # ---------- result artifacts (AI-generated deliverables) ----------
    async def _result_files(self, client: httpx.AsyncClient, headers: dict, cid: str) -> list[dict]:
        """Generated files for a conversation (each with a ~1h signed url)."""
        r = await client.get(
            f"{settings.zhiexa_skill_base}/api/files",
            headers=headers, params={"conversation_id": cid},
        )
        r.raise_for_status()
        files = r.json().get("files", [])
        return [
            {"name": f.get("name"), "url": f.get("url"), "ext": f.get("ext"),
             "size": f.get("size_bytes"), "id": f.get("id")}
            for f in files if f.get("source") == "generated"
        ]

    async def download_text(self, url: str, encoding: str = "utf-8") -> str:
        """Download a signed file url as text."""
        async with httpx.AsyncClient(timeout=max(settings.zhiexa_chat_timeout, 120),
                                     follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            r.encoding = encoding
            return r.text

    async def download_bytes(self, url: str) -> bytes:
        """Download a signed file url as raw bytes (for docx/pdf output files)."""
        async with httpx.AsyncClient(timeout=max(settings.zhiexa_chat_timeout, 120),
                                     follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.content

    # ---------- create execution task = POST /api/chat (SSE) ----------
    async def execute(
        self, message: str, files: list[tuple[str, bytes, str]] | None = None,
        conversation_id: str | None = None, *, _auth_retry: bool = True,
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
            # Files are now on OSS; drop the (potentially large) input bytes before
            # the minutes-long SSE so they don't sit in pod memory. Clearing the list
            # frees them from the caller too (same object) — caller uses `had_files`.
            if files:
                files.clear()

            texts: list[str] = []
            try:
                async with client.stream(
                    "POST", f"{settings.zhiexa_skill_base}/api/chat", headers=headers,
                    json={"conversation_id": cid, "message": message, "uploaded_files": uploaded},
                ) as resp:
                    if resp.status_code == 401 and _auth_retry:
                        self.invalidate_jwt()
                        raise httpx.HTTPStatusError(
                            "Unauthorized", request=resp.request, response=resp,
                        )
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
            except httpx.HTTPStatusError as exc:
                if (
                    _auth_retry
                    and exc.response is not None
                    and exc.response.status_code == 401
                ):
                    self.invalidate_jwt()
                    return await self.execute(
                        message=message, files=files, conversation_id=cid, _auth_retry=False,
                    )
                raise

            # AI-generated deliverables (e.g. the parsed_*.txt we asked for).
            result_files: list[dict] = []
            try:
                result_files = await self._result_files(client, headers, cid)
            except Exception:  # noqa: BLE001
                logger.exception("fetch result files failed cid=%s", cid)

        return {"output": "".join(texts), "conversation_id": cid,
                "latency_ms": int((time.monotonic() - start) * 1000),
                "files": result_files}


_client: ZhiexaClient | None = None


def get_zhiexa_client() -> ZhiexaClient:
    global _client
    if _client is None:
        _client = ZhiexaClient()
    return _client
