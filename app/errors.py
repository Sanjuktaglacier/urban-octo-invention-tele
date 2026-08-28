"""Custom exception hierarchy."""
from __future__ import annotations


class SaverError(Exception):
    pass


class ConfigError(SaverError): pass
class AuthError(SaverError): pass
class InvalidLinkError(SaverError): pass
class InvalidChatIDError(SaverError): pass
class AccessDeniedError(SaverError): pass
class MessageNotFoundError(SaverError): pass
class MediaUnavailableError(SaverError): pass
class DownloadError(SaverError): pass
class UploadError(SaverError): pass
class StorageError(SaverError): pass
class SessionError(SaverError): pass
class NetworkError(SaverError): pass
class QueueError(SaverError): pass
class BatchError(SaverError): pass
class TopicError(SaverError): pass


class FloodWaitError(SaverError):
    def __init__(self, wait_seconds: int) -> None:
        super().__init__(f"FloodWait: {wait_seconds}s")
        self.wait_seconds = wait_seconds
