"""Recover Chinese text that was stored as mojibake / unicode escapes."""
from __future__ import annotations

import json
import re

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_BAD_CHAR_RE = re.compile(r"[\ufffd\ue000-\uf8ff]")  # replacement / private-use
_UNICODE_ESCAPE_RE = re.compile(r"\\u[0-9a-fA-F]{4}")
# Common UTF-8-as-latin1 / cp1252 mojibake lead bytes for Chinese
_MOJIBAKE_HINT_RE = re.compile(
    r"Ã.|Â.|å.|æ.|ç.|è.|é.|ê.|ë.|ä.|æ–|ä¸|çš|æ˜"
)


def _cjk_count(s: str) -> int:
    return len(_CJK_RE.findall(s))


def _try_json_pretty(s: str) -> str | None:
    """If s is JSON (often dumped with ensure_ascii=True), re-dump as readable Chinese."""
    t = s.strip()
    if not t or t[0] not in "{[\"":
        return None
    try:
        parsed = json.loads(t)
    except (TypeError, ValueError):
        return None
    if isinstance(parsed, str):
        return parsed
    try:
        return json.dumps(parsed, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return None


def _try_unicode_escape(s: str) -> str | None:
    """Expand literal \\uXXXX sequences without touching existing CJK chars."""
    if not _UNICODE_ESCAPE_RE.search(s):
        return None

    def repl(m: re.Match) -> str:
        try:
            return chr(int(m.group(0)[2:], 16))
        except ValueError:
            return m.group(0)

    return _UNICODE_ESCAPE_RE.sub(repl, s)


def _try_utf8_mojibake(s: str) -> str | None:
    """UTF-8 bytes wrongly decoded as latin-1/cp1252 → encode back and decode utf-8."""
    for enc in ("latin-1", "cp1252"):
        try:
            return s.encode(enc).decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            continue
    return None


def _try_gb_mojibake(s: str) -> str | None:
    """Less common: GBK/GB18030 bytes shown as latin-1."""
    for enc in ("latin-1", "cp1252"):
        try:
            raw = s.encode(enc)
        except UnicodeEncodeError:
            continue
        for target in ("gb18030", "gbk"):
            try:
                return raw.decode(target)
            except UnicodeDecodeError:
                continue
    return None


def recover_chinese_text(value: str | None) -> str | None:
    """Best-effort: turn mojibake / \\uXXXX / ascii-JSON into readable Chinese.

    Keeps the original string when no candidate has more CJK characters.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    if not value:
        return value

    candidates: list[str] = [value]

    pretty = _try_json_pretty(value)
    if pretty is not None:
        candidates.append(pretty)
        # Also try recovering nested escaped content inside the pretty form
        nested = _try_unicode_escape(pretty)
        if nested is not None:
            candidates.append(nested)

    escaped = _try_unicode_escape(value)
    if escaped is not None:
        candidates.append(escaped)

    # Binary mojibake: prefer UTF-8 recovery; only fall back to GB* if UTF-8 fails.
    if _cjk_count(value) == 0 or _MOJIBAKE_HINT_RE.search(value):
        utf8_fixed = _try_utf8_mojibake(value)
        if utf8_fixed is not None and _cjk_count(utf8_fixed) > _cjk_count(value):
            candidates.append(utf8_fixed)
        else:
            gb_fixed = _try_gb_mojibake(value)
            if gb_fixed is not None and _cjk_count(gb_fixed) > _cjk_count(value):
                candidates.append(gb_fixed)

    # Prefer more Chinese; penalize escapes / replacement / private-use chars.
    def score(s: str) -> tuple:
        return (
            _cjk_count(s),
            -len(_BAD_CHAR_RE.findall(s)),
            0 if _UNICODE_ESCAPE_RE.search(s) else 1,
            -abs(len(s) - len(value)),
        )

    best = max(candidates, key=score)
    if score(best) > score(value):
        return best
    return value
