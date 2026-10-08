"""Bot settings, read from environment variables (a .env file is loaded first, if present)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from database import asyncpg_url

BASE_DIR = Path(__file__).resolve().parent
# In Docker, mount the host folder here (see docker-compose.yml).
ARCHIVE_DIR = BASE_DIR / "archive"

TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off", ""}


class ConfigError(Exception):
    """Raised when a setting is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    # Secrets are left out of repr so they never end up in a log line.
    token: str = field(repr=False)
    channel_ids: frozenset[int]
    archive_dir: Path
    archive_history: bool
    database_url: str = field(repr=False)


def load_settings() -> Settings:
    """Read and validate all settings. Raises ConfigError with a readable message."""
    return Settings(
        token=_required("DISCORD_TOKEN"),
        channel_ids=_parse_channel_ids(_required("CHANNEL_IDS")),
        archive_dir=ARCHIVE_DIR,
        archive_history=_parse_bool("ARCHIVE_HISTORY", os.environ.get("ARCHIVE_HISTORY", "")),
        database_url=_parse_database_url(_required("DATABASE_URL")),
    )


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"Missing {name}. Copy .env.example to .env and fill it in.")
    return value


def _parse_channel_ids(raw: str) -> frozenset[int]:
    ids = set()
    for part in raw.replace(" ", "").split(","):
        if not part:
            continue
        if not part.isdigit():
            raise ConfigError(f"CHANNEL_IDS must be comma separated channel IDs, got {part!r}.")
        ids.add(int(part))
    if not ids:
        raise ConfigError("CHANNEL_IDS has no channel IDs.")
    return frozenset(ids)


def _parse_database_url(raw: str) -> str:
    try:
        asyncpg_url(raw)
    except ValueError as exc:
        raise ConfigError(f"DATABASE_URL is not valid: {exc}.") from None
    return raw


def _parse_bool(name: str, raw: str) -> bool:
    value = raw.strip().lower()
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False
    raise ConfigError(f"{name} must be true or false, got {raw!r}.")
