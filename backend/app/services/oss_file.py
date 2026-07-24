"""Download SaaS OSS files for Agent upload.

Encryption rule (by object suffix only, not by module):
  - OSS object ends with .txt  → AES-decrypt (ciphertext stored as .txt)
  - otherwise                 → use downloaded bytes as-is

合同审查的原件通常是 .txt 密文；其它模块若偶发 .txt 也会走同一套解密。
"""
from __future__ import annotations

import base64
import logging
import mimetypes
import re
from urllib.parse import unquote

import httpx
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

from app.core.config import settings

logger = logging.getLogger(__name__)

ENCRYPTED_SUFFIXES = (".txt",)


def _safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name or "file")


def _aes_cbc_decrypt(ciphertext: bytes, key_hex: str, iv_hex: str) -> bytes:
    key = bytes.fromhex(key_hex.replace(" ", ""))
    iv = bytes.fromhex(iv_hex.replace(" ", ""))
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return unpad(cipher.decrypt(ciphertext), AES.block_size)


def _aes_decrypt_any(ciphertext: bytes) -> bytes:
    last_err: Exception | None = None
    for key_hex, iv_hex in settings.aes_key_candidates:
        try:
            return _aes_cbc_decrypt(ciphertext, key_hex, iv_hex)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
    raise last_err if last_err else ValueError("no AES key candidate")


def _object_name_from_url(url: str) -> str:
    return unquote(url.split("/")[-1].split("?")[0])


def _guess_content_type(filename: str) -> str:
    ctype, _ = mimetypes.guess_type(filename)
    return ctype or "application/octet-stream"


def _ensure_extension(fname: str, data: bytes) -> str:
    """If decrypted payload has no real extension, infer a common office type."""
    base = fname.rsplit("/", 1)[-1]
    lower = base.lower()
    if "." in base and not lower.endswith(".txt"):
        return fname
    stem = fname[:-4] if lower.endswith(".txt") else fname
    if data[:2] == b"PK":
        return stem + ".docx"
    if data[:4] == b"%PDF":
        return stem + ".pdf"
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return stem + ".doc"
    return stem


def normalize_file_ref(item) -> tuple[str, str] | None:
    """Return (url, display_name) or None if not a downloadable http(s) ref."""
    if isinstance(item, dict):
        url = (item.get("url") or "").strip()
        name = (item.get("name") or "").strip()
    elif isinstance(item, str):
        url = item.strip()
        name = ""
    else:
        return None
    if not url.startswith(("http://", "https://")):
        return None
    if not name:
        name = _object_name_from_url(url)
        if name.lower().endswith(".txt"):
            # Encrypted storage uses .txt; prefer original stem if unknown.
            name = name[:-4] or name
    return url, name


async def fetch_file_bytes(url: str, file_name: str = "") -> tuple[str, bytes, str]:
    """Download URL; decrypt if OSS object is encrypted .txt.

    Returns (filename, data, content_type).
    """
    object_name = _object_name_from_url(url)
    # Only the stored OSS object suffix matters (.txt = encrypted).
    is_encrypted = object_name.lower().endswith(ENCRYPTED_SUFFIXES)

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        raw = resp.content

    fname = _safe_name(file_name or object_name)
    if is_encrypted:
        ciphertext = base64.b64decode(raw)
        plain_b64 = _aes_decrypt_any(ciphertext)
        data = base64.b64decode(plain_b64)
        # SaaS historically stores some .doc payloads that are actually docx.
        if fname.lower().endswith(".doc"):
            fname = fname[:-4] + ".docx"
        fname = _ensure_extension(fname, data)
    else:
        data = raw

    return fname, data, _guess_content_type(fname)


_BINARY_MAGIC = (b"PK", b"%PDF", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")


def _decode_text(data: bytes) -> str:
    """Best-effort decode of a downloaded parse-result payload to text.

    If the payload is a binary office/pdf document (not a plain-text parse result),
    return "" — we don't extract text from binaries here.
    """
    if data.startswith(_BINARY_MAGIC):
        return ""
    for enc in ("utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


async def build_file_result(file_refs: list | None) -> str:
    """Download the old task's files from OSS and format as `file_result_*`:

        【待审文件】<name>\n解析内容：<text>\n\n【参考文件】<name>\n解析内容：<text> ...

    The first file is treated as 待审文件 (original), the rest as 参考文件.
    Returns "" when there are no downloadable file refs.
    """
    refs = [r for r in (normalize_file_ref(x) for x in (file_refs or [])) if r]
    if not refs:
        return ""
    blocks: list[str] = []
    for idx, (url, name) in enumerate(refs):
        label = "【待审文件】" if idx == 0 else "【参考文件】"
        try:
            _, data, _ = await fetch_file_bytes(url, name)
            text = _decode_text(data)
        except Exception:
            logger.exception("build_file_result fetch failed url=%s", url[:120])
            text = ""
        blocks.append(f"{label}{name}\n解析内容：{text}")
    return "\n\n".join(blocks)


async def prepare_agent_files(file_refs: list | None) -> list[tuple[str, bytes, str]]:
    """Turn case.files entries into Agent upload tuples."""
    out: list[tuple[str, bytes, str]] = []
    for item in file_refs or []:
        ref = normalize_file_ref(item)
        if ref is None:
            continue
        url, name = ref
        try:
            out.append(await fetch_file_bytes(url, name))
        except Exception:
            logger.exception("failed to fetch/decrypt file url=%s name=%s", url[:120], name)
            raise
    return out
