"""Download engine with retry and semaphore."""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from app.config import Config
from app.errors import DownloadError, FloodWaitError, MediaUnavailableError
from app.utils import ensure_dir

logger = logging.getLogger(__name__)


class Downloader:
    def __init__(self, config: Config, client) -> None:
        self._cfg = config
        self._cli = client
        self._sem = asyncio.Semaphore(config.max_concurrent_downloads)
        ensure_dir(config.download_dir)

    async def download(
        self,
        msg,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> str:
        max_r = self._cfg.max_retries
        last: Optional[Exception] = None

        async with self._sem:
            for attempt in range(max_r + 1):
                try:
                    path = await self._cli.download(
                        msg, self._cfg.download_dir, progress_cb
                    )
                    if path is None:
                        raise MediaUnavailableError("No media.")
                    return path

                except FloodWaitError as e:
                    logger.warning("FloodWait %ds, sleeping.", e.wait_seconds)
                    await asyncio.sleep(e.wait_seconds)
                    last = e

                except MediaUnavailableError as e:
                    raise DownloadError(str(e)) from e

                except Exception as e:
                    last = e
                    if attempt < max_r:
                        delay = min(2 ** attempt, 60)
                        logger.warning(
                            "Attempt %d/%d failed: %s. Retry in %.0fs.",
                            attempt + 1, max_r + 1, e, delay,
                        )
                        await asyncio.sleep(delay)
                    else:
                        raise DownloadError(
                            f"Failed after {max_r + 1} attempts: {e}"
                        ) from e

        raise DownloadError(f"Download failed: {last}")
