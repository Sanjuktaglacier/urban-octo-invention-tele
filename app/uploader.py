"""Send files to Telegram via Telethon (no size limit)."""
from __future__ import annotations

import logging
import mimetypes
import os
from typing import Optional

from app.errors import UploadError
from app.utils import fmt_size

logger = logging.getLogger(__name__)

# No size limit - Telethon supports up to 2GB natively
# For files above 2GB, we still attempt but log a warning
TELETHON_LIMIT = 2 * 1024 * 1024 * 1024  # 2 GB


class Uploader:
    def __init__(self, bot=None, client=None) -> None:
        self._bot    = bot
        self._client = client  # Telethon UserClient for large files

    async def send(
        self,
        chat_id: int,
        path: str,
        caption: str = "",
        reply_id: Optional[int] = None,
    ) -> bool:
        if not os.path.exists(path):
            raise UploadError(f"File not found: {path}")

        size = os.path.getsize(path)
        name = os.path.basename(path)
        mime = _guess(path)
        cap  = caption or f"{name}\nSize: {fmt_size(size)}"

        logger.info(
            "Uploading %s (%s) to %s via %s",
            name, fmt_size(size), chat_id,
            "Telethon" if self._client else "Bot API",
        )

        # Always prefer Telethon (no Bot API 50 MB limit)
        if self._client and self._client._c:
            return await self._send_telethon(chat_id, path, cap, mime, size)

        # Fallback to Bot API (only for files under 50 MB)
        BOT_API_LIMIT = 50 * 1024 * 1024
        if size > BOT_API_LIMIT:
            raise UploadError(
                f"File {fmt_size(size)} exceeds Bot API limit and "
                "Telethon client is not available. "
                "Cannot send without Telethon client."
            )
        return await self._send_bot_api(chat_id, path, cap, mime, reply_id)

    async def _send_telethon(
        self,
        chat_id: int,
        path: str,
        caption: str,
        mime: str,
        size: int,
    ) -> bool:
        """Upload using Telethon - supports files up to 2 GB."""
        try:
            from telethon.tl.types import DocumentAttributeFilename
            name = os.path.basename(path)

            if size > TELETHON_LIMIT:
                logger.warning(
                    "File %s (%s) exceeds Telethon 2GB limit, attempting anyway.",
                    name, fmt_size(size),
                )

            # Use send_file which handles all media types and large files
            await self._client._c.send_file(
                chat_id,
                path,
                caption=caption,
                supports_streaming=True,
                part_size_kb=512,       # 512 KB parts for faster upload
                force_document=False,   # let Telethon pick best type
            )
            logger.info("Telethon upload OK: %s (%s)", name, fmt_size(size))
            return True
        except Exception as e:
            raise UploadError(f"Telethon upload failed: {e}") from e

    async def _send_bot_api(
        self,
        chat_id: int,
        path: str,
        caption: str,
        mime: str,
        reply_id: Optional[int],
    ) -> bool:
        """Upload using Bot API - fallback for files under 50 MB."""
        if not self._bot:
            raise UploadError("No bot or client available for upload.")
        from telegram.error import TelegramError, BadRequest
        from telegram.error import NetworkError as PTBNet
        name = os.path.basename(path)
        try:
            with open(path, "rb") as fh:
                kw = dict(
                    chat_id=chat_id,
                    caption=caption,
                    reply_to_message_id=reply_id,
                )
                if mime.startswith("video/"):
                    await self._bot.send_video(
                        video=fh, supports_streaming=True,
                        write_timeout=180, read_timeout=180, **kw
                    )
                elif mime.startswith("audio/"):
                    await self._bot.send_audio(
                        audio=fh, write_timeout=180, read_timeout=180, **kw
                    )
                elif mime.startswith("image/"):
                    await self._bot.send_photo(photo=fh, **kw)
                else:
                    await self._bot.send_document(
                        document=fh, write_timeout=180, read_timeout=180, **kw
                    )
            logger.info("Bot API upload OK: %s", name)
            return True
        except BadRequest as e:
            raise UploadError(f"Bad request: {e}") from e
        except PTBNet as e:
            raise UploadError(f"Network error: {e}") from e
        except TelegramError as e:
            raise UploadError(f"Telegram error: {e}") from e
        except OSError as e:
            raise UploadError(f"File read error: {e}") from e

    async def delete(self, path: str) -> None:
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError as e:
            logger.warning("Cannot delete %s: %s", path, e)


def _guess(path: str) -> str:
    m, _ = mimetypes.guess_type(path)
    return m or "application/octet-stream"
