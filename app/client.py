"""Telethon user-client wrapper."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import AsyncIterator, Callable, List, Optional, Any

from telethon import TelegramClient, errors as te
from telethon.sessions import StringSession
from telethon.tl import functions
from telethon.tl.types import Message

from app.config import Config
from app.errors import (
    AccessDeniedError, FloodWaitError,
    MediaUnavailableError, SessionError, TopicError,
)
from app.utils import filename_from_msg, unique_path, normalize_chat_id

logger = logging.getLogger(__name__)


class UserClient:
    def __init__(self, config: Config) -> None:
        self._cfg = config
        self._c: Optional[TelegramClient] = None

    async def start(self) -> None:
        try:
            self._c = TelegramClient(
                StringSession(self._cfg.session_string),
                self._cfg.api_id,
                self._cfg.api_hash,
                connection_retries=10,
                retry_delay=2,
                auto_reconnect=True,
                request_retries=5,
            )
            await self._c.connect()
            if not await self._c.is_user_authorized():
                raise SessionError("SESSION_STRING is not authorized.")
            me = await self._c.get_me()
            logger.info(
                "UserClient connected: id=%s name=%s",
                getattr(me, "id", "?"),
                getattr(me, "first_name", "?"),
            )
        except SessionError:
            raise
        except Exception as e:
            raise SessionError(f"Client start failed: {e}") from e

    async def stop(self) -> None:
        if self._c and self._c.is_connected():
            await self._c.disconnect()

    def raw(self) -> TelegramClient:
        if not self._c:
            raise SessionError("Client not started.")
        return self._c

    async def resolve(self, chat: str) -> Any:
        candidates = normalize_chat_id(chat)
        logger.debug("Resolving %r -> candidates %s", chat, candidates)
        last = None
        for cand in candidates:
            try:
                e = await self._c.get_entity(cand)
                logger.info(
                    "Resolved %r via %r: id=%s title=%s",
                    chat, cand,
                    getattr(e, "id", "?"),
                    getattr(e, "title", None) or getattr(e, "username", "?"),
                )
                return e
            except te.FloodWaitError as ex:
                raise FloodWaitError(ex.seconds) from ex
            except (
                te.ChannelPrivateError,
                te.ChatAdminRequiredError,
                te.UserNotParticipantError,
            ) as ex:
                raise AccessDeniedError(
                    f"Access denied to {chat}.\n"
                    "Make sure your account is a member of this chat."
                ) from ex
            except Exception as ex:
                last = ex
                logger.debug("Candidate %r failed: %s", cand, ex)
                continue
        raise AccessDeniedError(
            f"Cannot resolve {chat!r}.\n"
            f"Tried: {candidates}\n"
            f"Last error: {last}\n\n"
            "Tips:\n"
            "1. Make sure your account is a member.\n"
            "2. Use the invite link with /setchannel.\n"
            "3. Get correct ID from @userinfobot."
        )

    async def title(self, entity: Any) -> str:
        return (
            getattr(entity, "title", None)
            or getattr(entity, "username", None)
            or str(getattr(entity, "id", "?"))
        )

    async def get_msg(self, entity: Any, msg_id: int) -> Optional[Message]:
        try:
            msgs = await self._c.get_messages(entity, ids=msg_id)
            return msgs[0] if isinstance(msgs, list) else msgs
        except te.FloodWaitError as e:
            raise FloodWaitError(e.seconds) from e
        except Exception as e:
            logger.debug("get_msg %d error: %s", msg_id, e)
            return None

    async def iter_msgs(
        self,
        entity: Any,
        min_id: int,
        max_id: int,
        topic_id: Optional[int] = None,
    ) -> AsyncIterator[Message]:
        kw: dict = {"min_id": min_id - 1, "max_id": max_id + 1, "reverse": True}
        if topic_id is not None:
            kw["reply_to"] = topic_id
        try:
            async for m in self._c.iter_messages(entity, **kw):
                if isinstance(m, Message):
                    yield m
        except te.FloodWaitError as e:
            raise FloodWaitError(e.seconds) from e
        except (te.ChannelPrivateError, te.ChatAdminRequiredError) as e:
            raise AccessDeniedError(str(e)) from e

    async def download(
        self,
        msg: Message,
        dl_dir: str,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> Optional[str]:
        if not msg.media:
            return None
        fname = filename_from_msg(msg, msg.id)
        dest  = unique_path(dl_dir, fname)
        tmp   = dest + ".tmp"
        try:
            res = await self._c.download_media(
                msg, file=tmp, progress_callback=progress_cb
            )
            if res is None:
                _rm(tmp)
                raise MediaUnavailableError(
                    f"Media unavailable for message {msg.id}."
                )
            os.rename(tmp, dest)
            logger.info(
                "Downloaded: msg=%d -> %s (%d bytes)",
                msg.id, dest, os.path.getsize(dest),
            )
            return dest
        except MediaUnavailableError:
            raise
        except te.FloodWaitError as e:
            _rm(tmp)
            raise FloodWaitError(e.seconds) from e
        except te.FileReferenceExpiredError as e:
            _rm(tmp)
            raise MediaUnavailableError(f"File reference expired: msg {msg.id}.") from e
        except (OSError, IOError) as e:
            _rm(tmp)
            raise MediaUnavailableError(f"FS error: {e}") from e
        except Exception as e:
            _rm(tmp)
            raise MediaUnavailableError(f"Download error: {e}") from e

    async def get_topics(self, entity: Any) -> list:
        try:
            r = await self._c(
                functions.channels.GetForumTopicsRequest(
                    channel=entity, q="",
                    offset_date=0, offset_id=0, offset_topic=0, limit=100,
                )
            )
            if not hasattr(r, "topics"):
                raise TopicError("Not a forum group.")
            return r.topics
        except TopicError:
            raise
        except te.FloodWaitError as e:
            raise FloodWaitError(e.seconds) from e
        except Exception as e:
            raise TopicError(f"Cannot get topics: {e}") from e

    async def join_invite(self, hash_: str) -> Any:
        try:
            return await self._c(
                functions.messages.ImportChatInviteRequest(hash=hash_)
            )
        except te.UserAlreadyParticipantError:
            return await self._c(
                functions.messages.CheckChatInviteRequest(hash=hash_)
            )
        except te.FloodWaitError as e:
            raise FloodWaitError(e.seconds) from e
        except te.InviteHashExpiredError as e:
            raise AccessDeniedError("Invite link expired.") from e
        except te.InviteHashInvalidError as e:
            raise AccessDeniedError("Invite link invalid.") from e
        except Exception as e:
            raise AccessDeniedError(f"Cannot join: {e}") from e


def _rm(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass
