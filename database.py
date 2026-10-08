"""
Async PostgreSQL access for the Discord server archive bot.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime

from sqlalchemy import func, inspect, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from models import Attachment, Base, ScanProgress

logger = logging.getLogger('bot.database')

LEGACY_TABLE = 'attachments_legacy'

# Rows from the old table, which was keyed by the (expiring) URL. They were all downloaded.
# The attachment ID is the second number in /attachments/<channel id>/<attachment id>/<name>.
IMPORT_LEGACY_ROWS = text(f"""
    INSERT INTO attachments (id, channel_id, filename, url, message_date, downloaded)
    SELECT att_id, channel_id, filename, split_part(url, '?', 1),
           message_date AT TIME ZONE 'UTC', true
    FROM (
        SELECT substring(url from '/attachments/[0-9]+/([0-9]+)/')::bigint AS att_id, *
        FROM {LEGACY_TABLE}
    ) AS legacy
    WHERE att_id IS NOT NULL
    ON CONFLICT (id) DO NOTHING
""")


@dataclass(frozen=True)
class AttachmentInfo:
    """Everything stored about an attachment before it is downloaded."""

    id: int
    message_id: int
    channel_id: int
    guild_id: int | None
    author_id: int | None
    filename: str
    content_type: str | None
    size: int
    url: str
    message_url: str
    message_date: datetime


class Database:
    """Async PostgreSQL access through SQLAlchemy and asyncpg."""

    def __init__(self, database_url: str):
        self.engine = create_async_engine(
            _asyncpg_url(database_url),
            pool_size=5,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def init(self) -> None:
        """Create missing tables and import rows from the old schema, if there is one."""
        async with self.engine.begin() as conn:
            renamed = await conn.run_sync(_rename_legacy_table)
            await conn.run_sync(Base.metadata.create_all)
            if renamed:
                result = await conn.execute(IMPORT_LEGACY_ROWS)
                logger.info(
                    'Imported %s attachments from the old table, which is kept as %s.',
                    result.rowcount, LEGACY_TABLE,
                )
        logger.info('Database ready')

    async def record_attachment(self, info: AttachmentInfo) -> bool:
        """Store an attachment's details. Returns True if it was downloaded before.

        Existing rows keep their values; only fields that are still empty get filled in.
        """
        values = asdict(info)
        stmt = insert(Attachment).values(**values)
        fill_missing = {
            name: func.coalesce(Attachment.__table__.c[name], stmt.excluded[name])
            for name in values
            if name != 'id'
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=[Attachment.id], set_=fill_missing
        ).returning(Attachment.downloaded)
        async with self.sessions.begin() as session:
            return bool(await session.scalar(stmt))

    async def mark_downloaded(self, attachment_id: int, file_path: str) -> None:
        stmt = (
            update(Attachment)
            .where(Attachment.id == attachment_id)
            .values(downloaded=True, unavailable=False, file_path=file_path)
        )
        async with self.sessions.begin() as session:
            await session.execute(stmt)

    async def mark_unavailable(self, attachment_id: int) -> None:
        stmt = update(Attachment).where(Attachment.id == attachment_id).values(unavailable=True)
        async with self.sessions.begin() as session:
            await session.execute(stmt)

    async def pending_downloads(self) -> list[Attachment]:
        """Attachments whose download failed, but which may still be on Discord."""
        stmt = (
            select(Attachment)
            .where(
                Attachment.downloaded.is_(False),
                Attachment.unavailable.is_(False),
                Attachment.message_id.is_not(None),
            )
            .order_by(Attachment.id)
        )
        async with self.sessions() as session:
            return list(await session.scalars(stmt))

    async def get_progress(self, channel_id: int) -> int | None:
        """The newest message ID the history scan finished in this channel, if any."""
        async with self.sessions() as session:
            return await session.scalar(
                select(ScanProgress.last_message_id).where(ScanProgress.channel_id == channel_id)
            )

    async def set_progress(self, channel_id: int, message_id: int) -> None:
        stmt = insert(ScanProgress).values(channel_id=channel_id, last_message_id=message_id)
        stmt = stmt.on_conflict_do_update(
            index_elements=[ScanProgress.channel_id],
            set_={'last_message_id': stmt.excluded.last_message_id, 'updated_at': text('now()')},
        )
        async with self.sessions.begin() as session:
            await session.execute(stmt)

    async def close(self) -> None:
        await self.engine.dispose()
        logger.info('Database connections closed')


def _rename_legacy_table(conn) -> bool:
    """Move the old url-keyed attachments table out of the way. Returns True if it existed."""
    inspector = inspect(conn)
    if not inspector.has_table('attachments'):
        return False
    columns = {column['name'] for column in inspector.get_columns('attachments')}
    if 'id' in columns or 'url' not in columns:
        return False
    if inspector.has_table(LEGACY_TABLE):
        raise RuntimeError(
            f'Both an old-style attachments table and {LEGACY_TABLE} exist. '
            f'Drop or rename one of them.'
        )
    # Index names are global, and the new table reuses them.
    index_names = [index['name'] for index in inspector.get_indexes('attachments')]
    index_names.append(inspector.get_pk_constraint('attachments')['name'])
    conn.execute(text(f'ALTER TABLE attachments RENAME TO {LEGACY_TABLE}'))
    for name in index_names:
        conn.execute(text(f'ALTER INDEX "{name}" RENAME TO "{name}_legacy"'))
    logger.info('Found the old attachments table and renamed it to %s.', LEGACY_TABLE)
    return True


def _asyncpg_url(raw: str) -> URL:
    """Accept the usual postgresql:// URLs and point them at the asyncpg driver."""
    url = make_url(raw.replace('postgres://', 'postgresql://', 1))
    if url.get_backend_name() != 'postgresql':
        raise ValueError(f'DATABASE_URL must be a PostgreSQL URL, not {url.drivername}.')
    url = url.set(drivername='postgresql+asyncpg')
    # asyncpg calls libpq's sslmode "ssl".
    sslmode = url.query.get('sslmode')
    if sslmode is not None:
        url = url.difference_update_query(['sslmode']).update_query_dict({'ssl': sslmode})
    return url
