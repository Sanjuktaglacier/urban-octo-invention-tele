"""Configuration management."""
from __future__ import annotations
import os
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Config:
    api_id: int
    api_hash: str
    session_string: str
    bot_token: str
    owner_id: int
    download_dir: str = "/tmp/downloads"
    max_concurrent_downloads: int = 3
    max_retries: int = 5
    log_level: str = "INFO"
    port: int = 8080
    db_path: str = "data/saver.db"
    progress_interval: float = 4.0

    @classmethod
    def from_env(cls) -> "Config":
        errors = []

        def req(name: str) -> str:
            v = os.getenv(name, "").strip()
            if not v:
                errors.append(name)
            return v

        def opt(name: str, default: str) -> str:
            return os.getenv(name, default).strip() or default

        api_id_str     = req("API_ID")
        api_hash       = req("API_HASH")
        session_string = req("SESSION_STRING")
        bot_token      = req("BOT_TOKEN")
        owner_id_str   = req("OWNER_ID")

        if errors:
            raise EnvironmentError(
                "Missing required environment variables: " + ", ".join(errors)
            )

        try:
            api_id = int(api_id_str)
        except ValueError:
            raise EnvironmentError("API_ID must be an integer.")

        try:
            owner_id = int(owner_id_str)
        except ValueError:
            raise EnvironmentError("OWNER_ID must be an integer.")

        def safe_int(name: str, default: int) -> int:
            try:
                return int(opt(name, str(default)))
            except ValueError:
                return default

        return cls(
            api_id=api_id,
            api_hash=api_hash,
            session_string=session_string,
            bot_token=bot_token,
            owner_id=owner_id,
            download_dir=opt("DOWNLOAD_DIR", "/tmp/downloads"),
            max_concurrent_downloads=safe_int("MAX_CONCURRENT_DOWNLOADS", 3),
            max_retries=safe_int("MAX_RETRIES", 5),
            log_level=opt("LOG_LEVEL", "INFO").upper(),
            port=safe_int("PORT", 8080),
            db_path=opt("DB_PATH", "data/saver.db"),
        )

    def validate(self) -> None:
        if self.max_concurrent_downloads < 1:
            raise ValueError("MAX_CONCURRENT_DOWNLOADS must be >= 1")
        if self.max_retries < 0:
            raise ValueError("MAX_RETRIES must be >= 0")

    def log_summary(self) -> None:
        logger.info(
            "Config: dir=%s concurrent=%d retries=%d port=%d",
            self.download_dir,
            self.max_concurrent_downloads,
            self.max_retries,
            self.port,
        )
