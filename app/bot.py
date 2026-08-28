"""Telegram Bot command handlers."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, ContextTypes, CallbackQueryHandler,
)

from app.client import UserClient
from app.config import Config
from app.database import Database
from app.downloader import Downloader
from app.errors import (
    AccessDeniedError, FloodWaitError, InvalidLinkError, TopicError,
)
from app.processor import Processor
from app.uploader import Uploader
from app.utils import (
    fmt_size, media_type, media_size, parse_link,
    is_invite, invite_hash,
)

logger = logging.getLogger(__name__)

HELP = (
    "Ultimate Telegram Content Saver Bot\n\n"
    "Commands:\n\n"
    "/start\n"
    "  Show this help.\n\n"
    "/setchannel <chat_id or invite_link>\n"
    "  Set the destination channel for forwarded files.\n"
    "  Example: /setchannel -1001234567890\n"
    "  Example: /setchannel https://t.me/+xxxxxxxxxxxx\n\n"
    "/test <message_link>\n"
    "  Test if a message is accessible and downloadable.\n"
    "  Example: /test https://t.me/example/123\n\n"
    "/forward <message_link>\n"
    "  Download one message and forward to your set channel.\n"
    "  Example: /forward https://t.me/c/1234567890/42\n\n"
    "/batch <start_link> <end_link>\n"
    "  Download a range of messages and forward all.\n"
    "  Example: /batch https://t.me/example/100 https://t.me/example/200\n\n"
    "/topic <invite_link_or_username>\n"
    "  List forum topics in a group.\n"
    "  Example: /topic https://t.me/+xxxxxxxxxxxx\n\n"
    "/topic_select <number>\n"
    "  Select a topic after using /topic.\n\n"
    "/status\n"
    "  Show bot status.\n\n"
    "/cancel\n"
    "  Cancel running batch.\n\n"
    "/history\n"
    "  Show download statistics.\n"
)


def owner_only(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        cfg: Config = ctx.bot_data["cfg"]
        uid = update.effective_user.id if update.effective_user else 0
        if uid != cfg.owner_id:
            await update.message.reply_text("Unauthorized.")
            logger.warning("Unauthorized access by user %d", uid)
            return
        return await func(update, ctx)
    wrapper.__name__ = func.__name__
    return wrapper


class Bot:
    def __init__(
        self,
        cfg: Config,
        client: UserClient,
        db: Database,
        dl: Downloader,
        ul: Uploader,
        proc: Processor,
    ) -> None:
        self._cfg    = cfg
        self._client = client
        self._db     = db
        self._dl     = dl
        self._ul     = ul
        self._proc   = proc
        self._app: Optional[Application] = None
        self._pending_topics: dict = {}
        self._batch_tasks: dict = {}

    def build(self) -> Application:
        self._app = (
            Application.builder()
            .token(self._cfg.bot_token)
            .read_timeout(60)
            .write_timeout(180)
            .connect_timeout(30)
            .pool_timeout(60)
            .build()
        )
        bd = self._app.bot_data
        bd["cfg"]     = self._cfg
        bd["client"]  = self._client
        bd["db"]      = self._db
        bd["dl"]      = self._dl
        bd["ul"]      = self._ul
        bd["proc"]    = self._proc
        bd["bot_obj"] = self

        for name, fn in [
            ("start",        _cmd_start),
            ("setchannel",   _cmd_setchannel),
            ("test",         _cmd_test),
            ("forward",      _cmd_forward),
            ("batch",        _cmd_batch),
            ("topic",        _cmd_topic),
            ("topic_select", _cmd_topic_select),
            ("status",       _cmd_status),
            ("cancel",       _cmd_cancel),
            ("history",      _cmd_history),
        ]:
            self._app.add_handler(CommandHandler(name, fn))
        self._app.add_handler(CallbackQueryHandler(_cb))
        return self._app

    @property
    def app(self) -> Optional[Application]:
        return self._app


async def _get_dest(ctx: ContextTypes.DEFAULT_TYPE, uid: int) -> Optional[int]:
    db: Database = ctx.bot_data["db"]
    val = await db.get_setting(uid, "dest_chat")
    if val:
        try:
            return int(val)
        except ValueError:
            pass
    return None


async def _need_dest(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE
) -> Optional[int]:
    uid  = update.effective_user.id
    dest = await _get_dest(ctx, uid)
    if not dest:
        await update.message.reply_text(
            "No destination channel set.\n"
            "Use /setchannel <chat_id> first."
        )
    return dest


async def _cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config  = ctx.bot_data["cfg"]
    uid = update.effective_user.id if update.effective_user else 0
    if uid != cfg.owner_id:
        await update.message.reply_text("Unauthorized.")
        return
    db: Database = ctx.bot_data["db"]
    dest = await db.get_setting(uid, "dest_chat")
    dest_str = f"Set: {dest}" if dest else "Not set (use /setchannel)"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Status",  callback_data="status"),
        InlineKeyboardButton("History", callback_data="history"),
    ]])
    await update.message.reply_text(
        HELP + f"\nDestination: {dest_str}",
        reply_markup=kb,
    )


@owner_only
async def _cmd_setchannel(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE
) -> None:
    if not ctx.args:
        await update.message.reply_text(
            "Usage: /setchannel <chat_id or invite_link>\n"
            "Example: /setchannel -1001234567890"
        )
        return
    uid    = update.effective_user.id
    arg    = ctx.args[0].strip()
    client: UserClient = ctx.bot_data["client"]
    db: Database       = ctx.bot_data["db"]
    msg = await update.message.reply_text("Verifying destination channel...")
    if is_invite(arg):
        h = invite_hash(arg)
        if h:
            try:
                await client.join_invite(h)
            except Exception as e:
                await msg.edit_text(f"Cannot join: {e}")
                return
    try:
        entity = await client.resolve(arg)
        t      = await client.title(entity)
        cid    = getattr(entity, "id", None)
        if cid is None:
            await msg.edit_text("Cannot determine channel ID.")
            return
        raw = str(cid)
        if not raw.startswith("-"):
            raw = f"-100{raw}"
        await db.set_setting(uid, "dest_chat", raw)
        await msg.edit_text(
            f"Destination channel set!\n"
            f"Name: {t}\n"
            f"ID: {raw}\n\n"
            "Use /forward or /batch to download content."
        )
        logger.info("User %d set dest_chat=%s", uid, raw)
    except AccessDeniedError as e:
        await msg.edit_text(f"Access denied: {e}")
    except Exception as e:
        await msg.edit_text(f"Error: {e}")


@owner_only
async def _cmd_test(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE
) -> None:
    if not ctx.args:
        await update.message.reply_text(
            "Usage: /test <message_link>\n"
            "Example: /test https://t.me/example/123"
        )
        return
    link   = ctx.args[0].strip()
    client: UserClient = ctx.bot_data["client"]
    msg = await update.message.reply_text("Testing message...")
    try:
        chat_str, msg_id, topic_id = parse_link(link)
    except InvalidLinkError as e:
        await msg.edit_text(f"Invalid link: {e}")
        return
    try:
        entity = await client.resolve(chat_str)
    except AccessDeniedError as e:
        await msg.edit_text(
            f"Access Denied\n\n{e}\n\n"
            "Make sure your Telegram account is a member of this chat."
        )
        return
    except Exception as e:
        await msg.edit_text(f"Cannot resolve chat: {e}")
        return
    t = await client.title(entity)
    try:
        message = await client.get_msg(entity, msg_id)
    except FloodWaitError as e:
        await msg.edit_text(f"Rate limited. Wait {e.wait_seconds}s.")
        return
    except Exception as e:
        await msg.edit_text(f"Cannot fetch message: {e}")
        return
    if message is None:
        await msg.edit_text(
            f"Message Not Found\n"
            f"Chat: {t}\n"
            f"Message ID: {msg_id}"
        )
        return
    mtype = media_type(message)
    msize = media_size(message)
    has_m = message.media is not None
    text = (
        f"Message Test Result\n\n"
        f"Chat: {t}\n"
        f"Message ID: {msg_id}\n"
        f"Media Type: {mtype}\n"
        f"Size: {fmt_size(msize) if msize >= 0 else 'N/A'}\n"
        f"Accessible: Yes\n"
        f"Downloadable: {'Yes' if has_m else 'No (text only)'}\n"
    )
    if topic_id:
        text += f"Topic ID: {topic_id}\n"
    if message.text and not has_m:
        text += f"\nPreview: {message.text[:150]}"
    if has_m:
        text += "\nReady to download with /forward"
    await msg.edit_text(text)


@owner_only
async def _cmd_forward(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE
) -> None:
    uid = update.effective_user.id
    if not ctx.args:
        await update.message.reply_text(
            "Usage: /forward <message_link>\n"
            "Example: /forward https://t.me/example/123"
        )
        return
    dest = await _need_dest(update, ctx)
    if not dest:
        return
    link = ctx.args[0].strip()
    try:
        chat_str, msg_id, _ = parse_link(link)
    except InvalidLinkError as e:
        await update.message.reply_text(f"Invalid link: {e}")
        return
    client: UserClient = ctx.bot_data["client"]
    proc: Processor    = ctx.bot_data["proc"]
    status_msg = await update.message.reply_text("Starting download...")
    try:
        entity = await client.resolve(chat_str)
        t      = await client.title(entity)
    except AccessDeniedError as e:
        await status_msg.edit_text(f"Access Denied\n\n{e}")
        return
    except Exception as e:
        await status_msg.edit_text(f"Error: {e}")
        return
    await status_msg.edit_text(
        f"Downloading from: {t}\n"
        f"Message: #{msg_id}\n"
        f"Destination: {dest}\n\n"
        "Please wait..."
    )
    ok = await proc.process_one(
        user_id=uid, source_chat=chat_str, msg_id=msg_id,
        dest_chat=dest, bot=ctx.bot,
        progress_chat=update.effective_chat.id,
        progress_msg_id=status_msg.message_id,
    )
    if ok:
        await status_msg.edit_text(
            f"Forwarded Successfully\n"
            f"Source: {t}\nMessage: #{msg_id}\nSent to: {dest}"
        )
    else:
        await status_msg.edit_text(
            f"Forward Failed\n"
            f"Source: {t}\nMessage: #{msg_id}\n"
            "Check if message has media and your account has access."
        )


@owner_only
async def _cmd_batch(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE
) -> None:
    uid = update.effective_user.id
    if not ctx.args or len(ctx.args) < 2:
        await update.message.reply_text(
            "Usage: /batch <start_link> <end_link>\n"
            "Example: /batch https://t.me/example/100 https://t.me/example/200"
        )
        return
    dest = await _need_dest(update, ctx)
    if not dest:
        return
    bot_obj: Bot = ctx.bot_data["bot_obj"]
    existing = bot_obj._batch_tasks.get(uid)
    if existing and not existing.done():
        await update.message.reply_text(
            "A batch is already running.\nUse /cancel to stop it first."
        )
        return
    try:
        sc, si, st = parse_link(ctx.args[0].strip())
        ec, ei, et = parse_link(ctx.args[1].strip())
    except InvalidLinkError as e:
        await update.message.reply_text(f"Invalid link: {e}")
        return
    if sc != ec:
        await update.message.reply_text(
            "Both links must be from the same chat."
        )
        return
    if si > ei:
        si, ei = ei, si
    topic_id = st
    client: UserClient = ctx.bot_data["client"]
    proc: Processor    = ctx.bot_data["proc"]
    prog_msg = await update.message.reply_text("Initializing batch...")
    try:
        entity = await client.resolve(sc)
        t      = await client.title(entity)
    except AccessDeniedError as e:
        await prog_msg.edit_text(f"Access Denied\n\n{e}")
        return
    except Exception as e:
        await prog_msg.edit_text(f"Error: {e}")
        return
    await prog_msg.edit_text(
        f"Batch Started\n\n"
        f"Source: {t}\n"
        f"Range: #{si} -> #{ei}\n"
        f"Total: {ei - si + 1} messages\n"
        f"Destination: {dest}\n\n"
        "Processing..."
    )
    logger.info(
        "Batch started: user=%d src=%s range=%d-%d dest=%d",
        uid, sc, si, ei, dest,
    )
    task = asyncio.create_task(
        proc.process_batch(
            user_id=uid, source_chat=sc,
            start_id=si, end_id=ei,
            dest_chat=dest, bot=ctx.bot,
            progress_chat=update.effective_chat.id,
            progress_msg_id=prog_msg.message_id,
            topic_id=topic_id,
        )
    )
    bot_obj._batch_tasks[uid] = task


@owner_only
async def _cmd_topic(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE
) -> None:
    if not ctx.args:
        await update.message.reply_text(
            "Usage: /topic <invite_link_or_username>\n"
            "Example: /topic https://t.me/+xxxxxxxxxxxx"
        )
        return
    uid    = update.effective_user.id
    arg    = ctx.args[0].strip()
    client: UserClient = ctx.bot_data["client"]
    bot_obj: Bot       = ctx.bot_data["bot_obj"]
    msg = await update.message.reply_text("Fetching topics...")
    if is_invite(arg):
        h = invite_hash(arg)
        if h:
            try:
                await client.join_invite(h)
            except Exception as e:
                await msg.edit_text(f"Cannot join: {e}")
                return
    try:
        entity = await client.resolve(arg)
        t      = await client.title(entity)
        gid    = str(getattr(entity, "id", ""))
        raw    = await client.get_topics(entity)
    except TopicError as e:
        await msg.edit_text(f"Topic Error: {e}")
        return
    except AccessDeniedError as e:
        await msg.edit_text(f"Access Denied: {e}")
        return
    except FloodWaitError as e:
        await msg.edit_text(f"Rate limited. Wait {e.wait_seconds}s.")
        return
    except Exception as e:
        await msg.edit_text(f"Error: {e}")
        return
    topics = [
        {"id": tp.id, "title": getattr(tp, "title", f"Topic {tp.id}")}
        for tp in raw
    ]
    if not topics:
        await msg.edit_text("No topics found in this group.")
        return
    bot_obj._pending_topics[uid] = (gid, topics)
    lines = [f"Topics in: {t}\n"]
    for i, tp in enumerate(topics, 1):
        lines.append(f"{i}. {tp['title']} (ID: {tp['id']})")
    lines.append("\nUse /topic_select <number> to choose.")
    lines.append("Then use /batch to download from that topic.")
    await msg.edit_text("\n".join(lines))


@owner_only
async def _cmd_topic_select(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE
) -> None:
    uid     = update.effective_user.id
    bot_obj: Bot     = ctx.bot_data["bot_obj"]
    db: Database     = ctx.bot_data["db"]
    if not ctx.args:
        await update.message.reply_text("Usage: /topic_select <number>")
        return
    try:
        n = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("Please provide a valid number.")
        return
    pending = bot_obj._pending_topics.get(uid)
    if not pending:
        await update.message.reply_text(
            "No pending topic list.\nUse /topic <link> first."
        )
        return
    gid, topics = pending
    idx = n - 1
    if idx < 0 or idx >= len(topics):
        await update.message.reply_text(
            f"Invalid number. Choose 1-{len(topics)}."
        )
        return
    tp = topics[idx]
    await db.save_topic(uid, gid, tp["id"], tp["title"])
    await db.set_setting(uid, "active_topic", f"{gid}:{tp['id']}")
    del bot_obj._pending_topics[uid]
    dest = await _get_dest(ctx, uid)
    await update.message.reply_text(
        f"Topic Selected: {tp['title']}\n"
        f"Group ID: {gid}\n"
        f"Topic ID: {tp['id']}\n"
        f"Destination: {dest or 'Not set'}\n\n"
        "Now use /batch with message links from this group."
    )


@owner_only
async def _cmd_status(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE
) -> None:
    uid     = update.effective_user.id
    bot_obj: Bot     = ctx.bot_data["bot_obj"]
    db: Database     = ctx.bot_data["db"]
    dest    = await _get_dest(ctx, uid)
    task    = bot_obj._batch_tasks.get(uid)
    bs      = "Running" if (task and not task.done()) else "Idle"
    at      = await db.get_setting(uid, "active_topic") or "None"
    await update.message.reply_text(
        f"Bot Status\n\n"
        f"Destination: {dest or 'Not set'}\n"
        f"Batch: {bs}\n"
        f"Active topic: {at}\n"
    )


@owner_only
async def _cmd_cancel(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE
) -> None:
    uid     = update.effective_user.id
    bot_obj: Bot = ctx.bot_data["bot_obj"]
    task    = bot_obj._batch_tasks.get(uid)
    if task and not task.done():
        task.cancel()
        bot_obj._batch_tasks.pop(uid, None)
        await update.message.reply_text("Batch cancelled.")
    else:
        await update.message.reply_text("No active batch to cancel.")


@owner_only
async def _cmd_history(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE
) -> None:
    db: Database = ctx.bot_data["db"]
    if not db._conn:
        await update.message.reply_text("Database not connected.")
        return
    async with db._conn.execute("SELECT COUNT(*) FROM processed") as c:
        row = await c.fetchone()
        total = row[0] if row else 0
    async with db._conn.execute(
        "SELECT COUNT(*) FROM batches WHERE status='COMPLETED'"
    ) as c:
        row = await c.fetchone()
        done_batches = row[0] if row else 0
    async with db._conn.execute(
        "SELECT COUNT(*) FROM batches WHERE status='RUNNING'"
    ) as c:
        row = await c.fetchone()
        running = row[0] if row else 0
    await update.message.reply_text(
        f"Download History\n\n"
        f"Total messages processed: {total}\n"
        f"Completed batches: {done_batches}\n"
        f"Running batches: {running}\n"
    )


async def _cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    await q.answer()
    cfg: Config = ctx.bot_data["cfg"]
    if q.from_user.id != cfg.owner_id:
        return
    uid = q.from_user.id
    if q.data == "status":
        bot_obj: Bot     = ctx.bot_data["bot_obj"]
        db: Database     = ctx.bot_data["db"]
        dest    = await _get_dest(ctx, uid)
        task    = bot_obj._batch_tasks.get(uid)
        bs      = "Running" if (task and not task.done()) else "Idle"
        try:
            await q.edit_message_text(
                f"Status\n\nDestination: {dest or 'Not set'}\nBatch: {bs}"
            )
        except Exception:
            pass
    elif q.data == "history":
        db: Database = ctx.bot_data["db"]
        if db._conn:
            async with db._conn.execute(
                "SELECT COUNT(*) FROM processed"
            ) as c:
                row = await c.fetchone()
                total = row[0] if row else 0
            try:
                await q.edit_message_text(
                    f"History\n\nTotal processed: {total}"
                )
            except Exception:
                pass
