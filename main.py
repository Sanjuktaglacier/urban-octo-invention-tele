"""Application entry point."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from app.bot import Bot
from app.client import UserClient
from app.config import Config
from app.database import Database
from app.downloader import Downloader
from app.errors import SessionError
from app.processor import Processor
from app.uploader import Uploader
from app.utils import ensure_dir


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)-25s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    for noisy in ("telethon", "httpx", "httpcore", "aiosqlite"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


async def health_server(port: int) -> None:
    from aiohttp import web
    async def ok(_):  return web.json_response({"status": "ok"})
    async def hl(_):  return web.json_response({"status": "healthy"})
    app = web.Application()
    app.router.add_get("/", ok)
    app.router.add_get("/health", hl)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logger.info("Health server on port %d", port)


def chk(label: str, ok: bool, err: str = "") -> None:
    s = "OK  " if ok else "FAIL"
    print(f"  [{s}] {label}" + (f" -- {err}" if err else ""))


async def main() -> None:
    try:
        cfg = Config.from_env()
        cfg.validate()
    except (EnvironmentError, ValueError) as e:
        print("\nStartup Error\n")
        print(str(e))
        sys.exit(1)

    setup_logging(cfg.log_level)

    print("\n=== Startup Validation ===")
    chk("BOT_TOKEN",      bool(cfg.bot_token))
    chk("API_ID",         bool(cfg.api_id))
    chk("API_HASH",       bool(cfg.api_hash))
    chk("SESSION_STRING", bool(cfg.session_string))
    chk("OWNER_ID",       bool(cfg.owner_id))
    print("=========================\n")

    cfg.log_summary()

    try:
        ensure_dir(cfg.download_dir)
        chk("Storage", True)
    except OSError as e:
        chk("Storage", False, str(e))
        sys.exit(1)

    db = Database(cfg.db_path)
    try:
        await db.connect()
        chk("Database", True)
    except Exception as e:
        chk("Database", False, str(e))
        sys.exit(1)

    client = UserClient(cfg)
    try:
        await client.start()
        chk("Telegram client", True)
    except SessionError as e:
        chk("Telegram client", False, str(e))
        await db.close()
        sys.exit(1)

    # Pass client to Uploader so it can use Telethon for files > 50 MB
    ul   = Uploader(bot=None, client=client)
    dl   = Downloader(cfg, client)
    proc = Processor(cfg, client, dl, ul, db)
    bot  = Bot(cfg, client, db, dl, ul, proc)
    app  = bot.build()
    ul._bot = app.bot  # also set bot for small-file fallback
    chk("Bot application", True)

    stop = asyncio.Event()

    def _sig(*_):
        logger.info("Shutdown signal received.")
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _sig)
        except (OSError, ValueError):
            pass

    logger.info("All systems ready. No file size restrictions.")

    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"],
        )

        ht = asyncio.create_task(health_server(cfg.port))
        await stop.wait()

        logger.info("Shutting down...")
        ht.cancel()
        try:
            await ht
        except asyncio.CancelledError:
            pass

        await app.updater.stop()
        await app.stop()
        await app.shutdown()

    await client.stop()
    await db.close()
    logger.info("Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
