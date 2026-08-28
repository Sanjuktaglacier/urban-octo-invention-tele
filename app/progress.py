"""Rate-limited progress message editing."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from app.utils import fmt_size, fmt_speed, fmt_dur, pbar, eta_str

logger = logging.getLogger(__name__)


class Progress:
    def __init__(
        self, bot, chat_id: int, msg_id: int,
        total: int, name: str, interval: float = 4.0,
    ) -> None:
        self._bot   = bot
        self._cid   = chat_id
        self._mid   = msg_id
        self._total = total
        self._name  = name
        self._ivl   = interval
        self._done  = 0
        self._t0    = time.monotonic()
        self._last  = 0.0
        self._lock  = asyncio.Lock()

    async def __call__(self, current: int, total: int) -> None:
        async with self._lock:
            self._done  = current
            self._total = total
        now = time.monotonic()
        if now - self._last < self._ivl:
            return
        self._last = now
        await self._edit()

    async def _edit(self) -> None:
        elapsed = time.monotonic() - self._t0
        spd = self._done / elapsed if elapsed > 0 else 0
        pct = int(100 * self._done / self._total) if self._total > 0 else 0
        text = (
            f"Downloading\n"
            f"File: {self._name}\n"
            f"Progress: {pbar(self._done, self._total)} {pct}%\n"
            f"Done: {fmt_size(self._done)} / {fmt_size(self._total)}\n"
            f"Speed: {fmt_speed(spd)}\n"
            f"ETA: {eta_str(self._done, self._total, elapsed)}"
        )
        try:
            await self._bot.edit_message_text(
                chat_id=self._cid, message_id=self._mid, text=text
            )
        except Exception as e:
            logger.debug("Progress edit skipped: %s", e)

    async def done(self, ok: bool, detail: str = "") -> None:
        elapsed = time.monotonic() - self._t0
        if ok:
            text = (
                f"Download Complete\n"
                f"File: {self._name}\n"
                f"Size: {fmt_size(self._done)}\n"
                f"Time: {fmt_dur(elapsed)}"
            )
        else:
            text = (
                f"Download Failed\n"
                f"File: {self._name}\n"
                f"Reason: {detail}\n"
                f"Time: {fmt_dur(elapsed)}"
            )
        try:
            await self._bot.edit_message_text(
                chat_id=self._cid, message_id=self._mid, text=text
            )
        except Exception as e:
            logger.debug("Progress done edit skipped: %s", e)


class BatchProgress:
    def __init__(
        self, bot, chat_id: int, msg_id: int,
        source: str, start: int, end: int, interval: float = 5.0,
    ) -> None:
        self._bot   = bot
        self._cid   = chat_id
        self._mid   = msg_id
        self._src   = source
        self._start = start
        self._end   = end
        self._total = end - start + 1
        self._done  = 0
        self._ok    = 0
        self._fail  = 0
        self._cur   = start
        self._bytes = 0
        self._t0    = time.monotonic()
        self._last  = 0.0
        self._ivl   = interval
        self._lock  = asyncio.Lock()

    async def record(self, msg_id: int, ok: bool, nb: int = 0) -> None:
        async with self._lock:
            self._done += 1
            self._cur   = msg_id
            self._bytes += nb
            if ok:
                self._ok   += 1
            else:
                self._fail += 1
        now = time.monotonic()
        if now - self._last < self._ivl:
            return
        self._last = now
        await self._edit()

    async def _edit(self) -> None:
        elapsed = time.monotonic() - self._t0
        spd = self._bytes / elapsed if elapsed > 0 else 0
        pct = int(100 * self._done / self._total) if self._total > 0 else 0
        text = (
            f"Batch Processing\n\n"
            f"Source: {self._src}\n"
            f"Range: #{self._start} -> #{self._end}\n\n"
            f"Progress: {pbar(self._done, self._total)} {pct}%\n"
            f"Processed: {self._done}/{self._total}\n"
            f"Success: {self._ok} | Failed: {self._fail}\n"
            f"Current: #{self._cur}\n"
            f"Speed: {fmt_speed(spd)}"
        )
        try:
            await self._bot.edit_message_text(
                chat_id=self._cid, message_id=self._mid, text=text
            )
        except Exception as e:
            logger.debug("Batch progress edit skipped: %s", e)

    async def finish(self, skipped: int = 0) -> None:
        elapsed = time.monotonic() - self._t0
        text = (
            f"Batch Complete\n\n"
            f"Source: {self._src}\n"
            f"Range: #{self._start} -> #{self._end}\n\n"
            f"Total: {self._total}\n"
            f"Success: {self._ok}\n"
            f"Failed: {self._fail}\n"
            f"Skipped: {skipped}\n"
            f"Duration: {fmt_dur(elapsed)}"
        )
        try:
            await self._bot.edit_message_text(
                chat_id=self._cid, message_id=self._mid, text=text
            )
        except Exception as e:
            logger.debug("Batch finish edit skipped: %s", e)
