"""Utility functions."""
from __future__ import annotations

import re
import os
import unicodedata
import logging
from typing import Optional, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)

_PRIV_RE   = re.compile(r"https?://t\.me/c/(\d+)/(\d+)(?:/(\d+))?")
_PUB_RE    = re.compile(r"https?://t\.me/([A-Za-z0-9_]+)/(\d+)(?:/(\d+))?")
_INVITE_RE = re.compile(r"https?://t\.me/(?:\+|joinchat/)([A-Za-z0-9_-]+)")


def parse_link(link: str) -> Tuple[str, int, Optional[int]]:
    from app.errors import InvalidLinkError
    link = link.strip()
    m = _PRIV_RE.match(link)
    if m:
        full_id = int(f"-100{m.group(1)}")
        if m.group(3):
            return str(full_id), int(m.group(3)), int(m.group(2))
        return str(full_id), int(m.group(2)), None
    m = _PUB_RE.match(link)
    if m:
        if m.group(3):
            return m.group(1), int(m.group(3)), int(m.group(2))
        return m.group(1), int(m.group(2)), None
    raise InvalidLinkError(f"Cannot parse link: {link!r}")


def is_invite(link: str) -> bool:
    return bool(_INVITE_RE.match(link.strip()))


def invite_hash(link: str) -> Optional[str]:
    m = _INVITE_RE.match(link.strip())
    return m.group(1) if m else None


def normalize_chat_id(raw: str) -> list:
    s = raw.strip()
    try:
        numeric = int(s)
    except ValueError:
        return [s]
    candidates = []
    if numeric < 0:
        abs_str = str(abs(numeric))
        if abs_str.startswith("100") and len(abs_str) > 3:
            raw_id = abs_str[3:]
            candidates = [numeric, int(raw_id), int(f"-100{raw_id}")]
        else:
            candidates = [numeric, abs(numeric), int(f"-100{abs(numeric)}")]
    else:
        candidates = [numeric, int(f"-100{numeric}"), -numeric]
    seen, result = set(), []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            result.append(c)
    return result


def sanitize(name: str, max_len: int = 200) -> str:
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode("ascii")
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"[_ ]+", "_", name).strip("_. ")
    if len(name) > max_len:
        base, ext = os.path.splitext(name)
        name = base[: max_len - len(ext)] + ext
    return name or "file"


def unique_path(directory: str, filename: str) -> str:
    directory = os.path.realpath(directory)
    filename  = sanitize(os.path.basename(filename))
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(directory, filename)
    n = 1
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{base}_{n}{ext}")
        n += 1
    real = os.path.realpath(candidate)
    if not real.startswith(directory + os.sep) and real != directory:
        raise ValueError(f"Path traversal: {candidate}")
    return candidate


def fmt_size(b: int) -> str:
    if b < 0:
        return "unknown"
    for u in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} PB"


def fmt_speed(bps: float) -> str:
    return f"{fmt_size(int(bps))}/s"


def fmt_dur(s: float) -> str:
    s = int(s)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m {s:02d}s"


def pbar(done: int, total: int, w: int = 16) -> str:
    if total <= 0:
        return "\u2591" * w
    filled = min(int(w * done / total), w)
    return "\u2588" * filled + "\u2591" * (w - filled)


def eta_str(dl: int, total: int, elapsed: float) -> str:
    if elapsed <= 0 or dl <= 0:
        return "unknown"
    spd = dl / elapsed
    if spd <= 0:
        return "unknown"
    return fmt_dur((total - dl) / spd)


def ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def media_type(msg) -> str:
    if not msg:
        return "None"
    if getattr(msg, "photo", None):
        return "Photo"
    if getattr(msg, "document", None):
        mime = getattr(msg.document, "mime_type", "") or ""
        if mime.startswith("video/"):  return "Video"
        if mime.startswith("audio/"):  return "Audio"
        if mime.startswith("image/"):  return "Image"
        return "Document"
    if getattr(msg, "video",     None): return "Video"
    if getattr(msg, "audio",     None): return "Audio"
    if getattr(msg, "voice",     None): return "Voice"
    if getattr(msg, "sticker",   None): return "Sticker"
    if getattr(msg, "animation", None): return "GIF"
    if getattr(msg, "text",      None): return "Text"
    return "Unknown"


def media_size(msg) -> int:
    try:
        if getattr(msg, "document", None):
            return msg.document.size
        if getattr(msg, "photo", None):
            sz = msg.photo.sizes
            if sz:
                return getattr(sz[-1], "size", -1)
    except Exception:
        pass
    return -1


def filename_from_msg(msg, msg_id: int) -> str:
    try:
        if getattr(msg, "document", None):
            for a in msg.document.attributes:
                fn = getattr(a, "file_name", None)
                if fn:
                    return sanitize(fn)
            mime = getattr(msg.document, "mime_type", "")
            ext  = _mime_ext(mime)
            return sanitize(f"doc_{msg_id}{ext}")
        if getattr(msg, "photo",     None): return sanitize(f"photo_{msg_id}.jpg")
        if getattr(msg, "video",     None): return sanitize(f"video_{msg_id}.mp4")
        if getattr(msg, "audio",     None): return sanitize(f"audio_{msg_id}.mp3")
        if getattr(msg, "voice",     None): return sanitize(f"voice_{msg_id}.ogg")
        if getattr(msg, "animation", None): return sanitize(f"anim_{msg_id}.mp4")
    except Exception:
        pass
    return sanitize(f"file_{msg_id}")


def _mime_ext(mime: str) -> str:
    return {
        "video/mp4": ".mp4", "video/x-matroska": ".mkv",
        "video/webm": ".webm", "audio/mpeg": ".mp3",
        "audio/ogg": ".ogg", "audio/x-flac": ".flac",
        "image/jpeg": ".jpg", "image/png": ".png",
        "image/gif": ".gif", "image/webp": ".webp",
        "application/pdf": ".pdf", "application/zip": ".zip",
        "text/plain": ".txt",
    }.get(mime, "")
