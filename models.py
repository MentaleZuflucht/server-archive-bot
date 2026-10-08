"""
SQLAlchemy models for the Discord server archive bot.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, false, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Attachment(Base):
    """One archived Discord attachment.

    Keyed by the attachment ID, because the URL changes on every fetch: Discord
    signs attachment URLs with query parameters that expire after about a day.
    """

    __tablename__ = 'attachments'
    __table_args__ = (
        Index('idx_attachments_channel_message_date', 'channel_id', 'message_date'),
        Index('idx_attachments_message_date', 'message_date'),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    # Null only for rows imported from the old url-keyed table.
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    # The channel or thread the message was posted in.
    channel_id: Mapped[int] = mapped_column(BigInteger)
    guild_id: Mapped[int | None] = mapped_column(BigInteger)
    author_id: Mapped[int | None] = mapped_column(BigInteger)
    filename: Mapped[str]
    content_type: Mapped[str | None]
    size: Mapped[int | None] = mapped_column(BigInteger)
    # CDN URL without the expiring signature.
    url: Mapped[str]
    message_url: Mapped[str | None]
    message_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Relative to ARCHIVE_DIR. Null until downloaded (and for imported rows).
    file_path: Mapped[str | None]
    downloaded: Mapped[bool] = mapped_column(default=False, server_default=false())
    # The message or file is gone from Discord, so the download is not retried.
    unavailable: Mapped[bool] = mapped_column(default=False, server_default=false())
    archived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self):
        return f"<Attachment(id={self.id}, filename='{self.filename}')>"


class ScanProgress(Base):
    """The newest message the history scan has finished, per channel or thread."""

    __tablename__ = 'scan_progress'

    channel_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    last_message_id: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
