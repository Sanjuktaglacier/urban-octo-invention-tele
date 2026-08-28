"""Core processor: download from source, forward to dest."""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.client import UserClient
from app.config import Config
from app.database import Database
from app.downloader import Downloader
from app.errors import (
    AccessDeniedError, DownloadError, FloodWaitError, UploadError,
)
from app.progress import BatchProgress, Progress
from app.uploader import Uploader
from app.utils import fmt_size, media_size

logger = logging.getLogger(__name__)


class Processor:
    def __init__(
        self,
        cfg: Config,
        client: UserClient,
        dl: Downloader,
        ul: Uploader,
        db: Database,
    ) -> None:
        self._cfg    = cfg
        self._client = client
        self._dl     = dl
        self._ul     = ul
        self._db     = db

    async def process_one(
        self,
        user_id: int,
        source_chat: str,
        msg_id: int,
        dest_chat: int,
        bot,
        progress_chat: int,
        progress_msg_id: int,
    ) -> bool:
        try:
            entity    = await self._client.resolve(source_chat)
            msg       = await self._client.get_msg(entity, msg_id)
        except FloodWaitError as e:
            await asyncio.sleep(e.wait_seconds)
            return False
        except Exception as e:
            logger.error("Cannot resolve/fetch msg %d: %s", msg_id, e)
            return False

        if msg is None or not msg.media:
            return False

        src_title = await self._client.title(entity)
        size      = media_size(msg)

        prog = Progress(
            bot=bot,
            chat_id=progress_chat,
            msg_id=progress_msg_id,
            total=size if size > 0 else 0,
            name=f"msg_{msg_id}",
            interval=self._cfg.progress_interval,
        )

        path: Optional[str] = None
        try:
            path = await self._dl.download(msg, progress_cb=prog)
            await prog.done(True)
            cap = (
                f"From: {src_title}\n"
                f"Message: #{msg_id}\n"
                f"Size: {fmt_size(size)}"
            )
            await self._ul.send(chat_id=dest_chat, path=path, caption=cap)
            await self._ul.delete(path)
            await self._db.mark_processed(source_chat, msg_id)
            return True
        except DownloadError as e:
            await prog.done(False, str(e))
            logger.error("Download failed msg=%d: %s", msg_id, e)
            return False
        except UploadError as e:
            if path:
                await self._ul.delete(path)
            await prog.done(False, str(e))
            logger.error("Upload failed msg=%d: %s", msg_id, e)
            return False
        except Exception as e:
            if path:
                await self._ul.delete(path)
            logger.error("Unexpected error msg=%d: %s", msg_id, e, exc_info=True)
            return False

    async def process_batch(
        self,
        user_id: int,
        source_chat: str,
        start_id: int,
        end_id: int,
        dest_chat: int,
        bot,
        progress_chat: int,
        progress_msg_id: int,
        topic_id: Optional[int] = None,
    ) -> None:
        batch_id = str(uuid.uuid4())

        try:
            entity    = await self._client.resolve(source_chat)
            src_title = await self._client.title(entity)
        except (AccessDeniedError, Exception) as e:
            logger.error("Batch: cannot resolve %s: %s", source_chat, e)
            try:
                await bot.edit_message_text(
                    chat_id=progress_chat,
                    message_id=progress_msg_id,
                    text=f"Cannot access source: {e}",
                )
            except Exception:
                pass
            return

        await self._db.create_batch({
            "batch_id":    batch_id,
            "user_id":     user_id,
            "source_chat": source_chat,
            "dest_chat":   str(dest_chat),
            "start_msg":   start_id,
            "end_msg":     end_id,
            "status":      "RUNNING",
            "total":       end_id - start_id + 1,
            "success":     0,
            "failed":      0,
            "skipped":     0,
        })

        prog = BatchProgress(
            bot=bot, chat_id=progress_chat, msg_id=progress_msg_id,
            source=src_title, start=start_id, end=end_id,
        )

        success = failed = skipped = 0
        logger.info(
            "Batch %s started: chat=%s range=%d-%d",
            batch_id, source_chat, start_id, end_id,
        )

        try:
            async for msg in self._client.iter_msgs(
                entity, start_id, end_id, topic_id=topic_id
            ):
                mid = msg.id

                if await self._db.is_processed(source_chat, mid):
                    skipped += 1
                    continue

                if not msg.media:
                    await self._db.mark_processed(source_chat, mid, batch_id)
                    skipped += 1
                    continue

                path: Optional[str] = None
                try:
                    path = await self._dl.download(msg)
                    if path:
                        size = media_size(msg)
                        cap  = (
                            f"From: {src_title}\n"
                            f"Message: #{mid}\n"
                            f"Size: {fmt_size(size)}"
                        )
                        await self._ul.send(
                            chat_id=dest_chat, path=path, caption=cap
                        )
                        await self._ul.delete(path)
                        await self._db.mark_processed(source_chat, mid, batch_id)
                        success += 1
                        await prog.record(mid, True, max(size, 0))
                        logger.info("Batch: msg %d OK", mid)
                    else:
                        skipped += 1
                        await prog.record(mid, False)

                except DownloadError as e:
                    if path:
                        await self._ul.delete(path)
                    failed += 1
                    await prog.record(mid, False)
                    logger.error("Batch dl failed msg=%d: %s", mid, e)

                except UploadError as e:
                    if path:
                        await self._ul.delete(path)
                    failed += 1
                    await prog.record(mid, False)
                    logger.error("Batch ul failed msg=%d: %s", mid, e)

                except FloodWaitError as e:
                    logger.warning("FloodWait %ds in batch", e.wait_seconds)
                    await asyncio.sleep(e.wait_seconds)
                    failed += 1
                    await prog.record(mid, False)

                except Exception as e:
                    if path:
                        await self._ul.delete(path)
                    failed += 1
                    await prog.record(mid, False)
                    logger.error("Batch unexpected msg=%d: %s", mid, e, exc_info=True)

                await asyncio.sleep(0.05)

        except FloodWaitError as e:
            await asyncio.sleep(e.wait_seconds)
        except AccessDeniedError as e:
            logger.error("Batch access denied: %s", e)
        except Exception as e:
            logger.error("Batch iteration error: %s", e, exc_info=True)
        finally:
            await self._db.update_batch(
                batch_id,
                status="COMPLETED",
                success=success,
                failed=failed,
                skipped=skipped,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            await prog.finish(skipped=skipped)
            logger.info(
                "Batch %s done: ok=%d fail=%d skip=%d",
                batch_id, success, failed, skipped,
            )
