"""SQLite persistence layer."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional

import aiosqlite
from app.utils import ensure_dir

logger = logging.getLogger(__name__)

_SCHEMA = (
    "PRAGMA journal_mode=WAL;\n"
    "PRAGMA foreign_keys=ON;\n"
    "CREATE TABLE IF NOT EXISTS settings ("
    " user_id INTEGER NOT NULL, key TEXT NOT NULL,"
    " value TEXT, updated_at TEXT NOT NULL DEFAULT (datetime('now')),"
    " PRIMARY KEY (user_id, key));\n"
    "CREATE TABLE IF NOT EXISTS jobs ("
    " job_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL,"
    " source_chat TEXT NOT NULL, dest_chat TEXT,"
    " message_id INTEGER NOT NULL,"
    " status TEXT NOT NULL DEFAULT 'QUEUED',"
    " progress REAL NOT NULL DEFAULT 0.0,"
    " file_name TEXT, file_size INTEGER,"
    " started_at TEXT, completed_at TEXT, error TEXT,"
    " created_at TEXT NOT NULL DEFAULT (datetime('now')));\n"
    "CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id);\n"
    "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);\n"
    "CREATE INDEX IF NOT EXISTS idx_jobs_chat ON jobs(source_chat, message_id);\n"
    "CREATE TABLE IF NOT EXISTS processed ("
    " source_chat TEXT NOT NULL, message_id INTEGER NOT NULL,"
    " processed_at TEXT NOT NULL DEFAULT (datetime('now')),"
    " job_id TEXT, PRIMARY KEY (source_chat, message_id));\n"
    "CREATE TABLE IF NOT EXISTS batches ("
    " batch_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL,"
    " source_chat TEXT NOT NULL, dest_chat TEXT,"
    " start_msg INTEGER NOT NULL, end_msg INTEGER NOT NULL,"
    " status TEXT NOT NULL DEFAULT 'RUNNING',"
    " total INTEGER NOT NULL DEFAULT 0,"
    " success INTEGER NOT NULL DEFAULT 0,"
    " failed INTEGER NOT NULL DEFAULT 0,"
    " skipped INTEGER NOT NULL DEFAULT 0,"
    " created_at TEXT NOT NULL DEFAULT (datetime('now')),"
    " completed_at TEXT);\n"
    "CREATE TABLE IF NOT EXISTS topics ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " user_id INTEGER NOT NULL, group_id TEXT NOT NULL,"
    " topic_id INTEGER NOT NULL, topic_title TEXT,"
    " created_at TEXT NOT NULL DEFAULT (datetime('now')),"
    " UNIQUE(user_id, group_id, topic_id));\n"
)


class Database:
    def __init__(self, db_path: str) -> None:
        self._path = db_path
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        ensure_dir(os.path.dirname(self._path) or ".")
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()
        logger.info("Database connected: %s", self._path)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @asynccontextmanager
    async def _tx(self) -> AsyncIterator[aiosqlite.Connection]:
        if not self._conn:
            raise RuntimeError("DB not connected.")
        async with self._lock:
            try:
                yield self._conn
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise

    async def set_setting(self, user_id: int, key: str, value: str) -> None:
        async with self._tx() as c:
            await c.execute(
                "INSERT OR REPLACE INTO settings (user_id, key, value)"
                " VALUES (?, ?, ?)",
                (user_id, key, value),
            )

    async def get_setting(self, user_id: int, key: str) -> Optional[str]:
        if not self._conn:
            return None
        async with self._conn.execute(
            "SELECT value FROM settings WHERE user_id=? AND key=?",
            (user_id, key),
        ) as c:
            row = await c.fetchone()
            return row[0] if row else None

    async def upsert_job(self, job: Dict[str, Any]) -> None:
        async with self._tx() as c:
            await c.execute(
                "INSERT INTO jobs"
                " (job_id,user_id,source_chat,dest_chat,message_id,"
                "  status,progress,file_name,file_size,"
                "  started_at,completed_at,error)"
                " VALUES"
                " (:job_id,:user_id,:source_chat,:dest_chat,:message_id,"
                "  :status,:progress,:file_name,:file_size,"
                "  :started_at,:completed_at,:error)"
                " ON CONFLICT(job_id) DO UPDATE SET"
                "  status=excluded.status,progress=excluded.progress,"
                "  file_name=excluded.file_name,file_size=excluded.file_size,"
                "  started_at=excluded.started_at,"
                "  completed_at=excluded.completed_at,error=excluded.error",
                job,
            )

    async def mark_processed(
        self, source_chat: str, message_id: int, job_id: Optional[str] = None
    ) -> None:
        async with self._tx() as c:
            await c.execute(
                "INSERT OR IGNORE INTO processed(source_chat,message_id,job_id)"
                " VALUES(?,?,?)",
                (source_chat, message_id, job_id),
            )

    async def is_processed(self, source_chat: str, message_id: int) -> bool:
        if not self._conn:
            return False
        async with self._conn.execute(
            "SELECT 1 FROM processed WHERE source_chat=? AND message_id=?",
            (source_chat, message_id),
        ) as c:
            return await c.fetchone() is not None

    async def create_batch(self, b: Dict[str, Any]) -> None:
        async with self._tx() as c:
            await c.execute(
                "INSERT OR REPLACE INTO batches"
                " (batch_id,user_id,source_chat,dest_chat,start_msg,end_msg,"
                "  status,total,success,failed,skipped)"
                " VALUES"
                " (:batch_id,:user_id,:source_chat,:dest_chat,:start_msg,:end_msg,"
                "  :status,:total,:success,:failed,:skipped)",
                b,
            )

    async def update_batch(self, batch_id: str, **kw: Any) -> None:
        if not kw:
            return
        sets = ", ".join(f"{k}=:{k}" for k in kw)
        kw["batch_id"] = batch_id
        async with self._tx() as c:
            await c.execute(
                f"UPDATE batches SET {sets} WHERE batch_id=:batch_id", kw
            )

    async def save_topic(
        self, user_id: int, group_id: str, topic_id: int, title: str
    ) -> None:
        async with self._tx() as c:
            await c.execute(
                "INSERT OR REPLACE INTO topics(user_id,group_id,topic_id,topic_title)"
                " VALUES(?,?,?,?)",
                (user_id, group_id, topic_id, title),
            )

    async def get_topics(self, user_id: int) -> List[Dict[str, Any]]:
        if not self._conn:
            return []
        async with self._conn.execute(
            "SELECT * FROM topics WHERE user_id=? ORDER BY id", (user_id,)
        ) as c:
            return [dict(r) for r in await c.fetchall()]
