"""Download SaaS OSS files for Agent upload.

Encryption rule (by object suffix only, not by module):
  - OSS object ends with .txt  → AES-decrypt (ciphertext stored as .txt)
  - otherwise                 → use downloaded bytes as-is

合同审查的原件通常是 .txt 密文；其它模块若偶发 .txt 也会走同一套解密。
"""
from __future__ import annotations

import base64
import io
import logging
import mimetypes
import re
import zipfile
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
    """Best-effort decode of a plain-text payload.

    If the payload is a binary office/pdf document (not a plain-text parse result),
    return "" — callers should route binaries through `extract_text` instead.
    """
    if data.startswith(_BINARY_MAGIC):
        return ""
    for enc in ("utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def _xml_to_text(xml_bytes: bytes) -> str:
    """Strip a WordprocessingML part down to visible text (stdlib only)."""
    xml = xml_bytes.decode("utf-8", errors="ignore")
    # Preserve structure: paragraph / line break / tab become whitespace.
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab\b[^>]*/?>", "\t", xml)
    xml = re.sub(r"<w:br\b[^>]*/?>", "\n", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    text = (
        text.replace("&lt;", "<").replace("&gt;", ">")
        .replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&")
    )
    return re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", text).strip()


def _docx_to_text(data: bytes) -> str:
    """Extract text from a .docx (zip) payload without any third-party library.

    Pulls the main body (word/document.xml) plus any 批注/comments — review result
    files carry the 审查意见 as comments, so they must be included.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = set(z.namelist())
            if "word/document.xml" not in names:
                return ""
            parts = [_xml_to_text(z.read("word/document.xml"))]
            if "word/comments.xml" in names:
                comments = _xml_to_text(z.read("word/comments.xml"))
                if comments:
                    parts.append("【批注】\n" + comments)
    except Exception:  # noqa: BLE001 — not a valid zip / corrupt docx
        logger.exception("docx text extraction failed")
        return ""
    return "\n".join(p for p in parts if p).strip()


def _pdf_to_text(data: bytes) -> str:
    """Extract text from a PDF payload (pypdf). Empty on failure / if pypdf absent.

    Keeps 旧侧 (file_result_old) able to parse PDFs, so it stays symmetric with the
    Agent's new-side parse instead of showing an empty 解析内容.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.warning("pypdf not installed; cannot extract PDF text (pip install pypdf)")
        return ""
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages).strip()
    except Exception:  # noqa: BLE001 — corrupt / encrypted PDF
        logger.exception("pdf text extraction failed")
        return ""


def extract_text(fname: str, data: bytes) -> str:
    """Downloaded file payload → text.

    - .docx (zip/PK)  → unzip and strip WordprocessingML (stdlib only)
    - .pdf            → pypdf
    - plain text       → decode utf-8 / gb18030
    - legacy .doc / other binaries → "" (no extractor bundled)
    """
    lower = (fname or "").lower()
    if data[:2] == b"PK" or lower.endswith(".docx"):
        text = _docx_to_text(data)
        if text:
            return text
    if data[:4] == b"%PDF" or lower.endswith(".pdf"):
        text = _pdf_to_text(data)
        if text:
            return text
    return _decode_text(data)


async def resolve_answer_text(baseline: str | None) -> str:
    """Resolve a case's 旧版答案 to text for Coze `answer_old`.

    Most modules (含合同/文件审查) now store baseline as plain text
    (卡片摘要 / final_result 等). Legacy rows may still hold an OSS URL to the
    annotated result doc — download and extract text in that case.
    """
    if not baseline:
        return ""
    s = baseline.strip()
    if not s.startswith(("http://", "https://")):
        return baseline
    try:
        fname, data, _ = await fetch_file_bytes(s)
        return extract_text(fname, data)
    except Exception:  # noqa: BLE001
        logger.exception("resolve answer_old failed url=%s", s[:120])
        return ""


# Only 合同审查/文件审查 need the 待审文件/参考文件 结构化文案 in file_result_*; other
# sources just want the raw parse content.
_REVIEW_FILE_SOURCES = {"contract_review", "file_review"}


async def build_file_result(file_refs: list | None, source: str | None = None) -> str:
    """Download the old task's files from OSS and format as `file_result_old`.

    - 合同审查/文件审查: 【待审文件】<name>\n解析内容：<text>\n\n【参考文件】... —
      the first file is 待审文件 (original), the rest 参考文件.
    - 其它来源: 直接拼接各文件的解析内容(不加固定文案)。

    Returns "" when there are no downloadable file refs.
    """
    refs = [r for r in (normalize_file_ref(x) for x in (file_refs or [])) if r]
    if not refs:
        return ""
    labeled = source in _REVIEW_FILE_SOURCES
    blocks: list[str] = []
    for idx, (url, name) in enumerate(refs):
        try:
            fname, data, _ = await fetch_file_bytes(url, name)
            text = extract_text(fname, data)
        except Exception:
            logger.exception("build_file_result fetch failed url=%s", url[:120])
            text = ""
        if labeled:
            label = "【待审文件】" if idx == 0 else "【参考文件】"
            blocks.append(f"{label}{name}\n解析内容：{text}")
        elif text.strip():
            blocks.append(text.strip())
    return "\n\n".join(blocks)


def normalize_agent_file_refs(file_refs: list | None) -> list[tuple[str, str]]:
    """Normalize case.files entries into (url, name) refs for the Agent upload.

    We deliberately DON'T download bytes here: the caller streams each file
    (fetch → upload → discard) one at a time so a case with many/large files
    (e.g. 阅卷笔录 = dozens of per-page PDFs) never holds every file's bytes in
    memory at once. Downloading + decrypting is done just-in-time per file.
    """
    out: list[tuple[str, str]] = []
    for item in file_refs or []:
        ref = normalize_file_ref(item)
        if ref is not None:
            out.append(ref)
    return out
