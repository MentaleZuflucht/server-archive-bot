"""
PostgreSQL database manager using SQLAlchemy for the Discord server archive bot.
"""

import logging
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import IntegrityError
from models import Base, Attachment


class DatabaseManager:
    """PostgreSQL database manager using SQLAlchemy."""

    def __init__(self, database_url: str):
        """
        Initialize the database manager.

        Args:
            database_url: PostgreSQL connection string.
        """
        self.logger = logging.getLogger('bot.database')

        # Create engine with connection pooling
        self.engine = create_engine(
            database_url,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=3600,
            echo=False
        )

        self.SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine
        )

        self._init_database()

    def _init_database(self):
        """Create all tables if they don't exist."""
        try:
            # Create all tables
            Base.metadata.create_all(bind=self.engine)
            self.logger.info('Database tables created/verified successfully')
        except Exception as e:
            self.logger.error(f'Error initializing database: {e}')
            raise

    def get_session(self) -> Session:
        """Get a new database session."""
        return self.SessionLocal()

    def is_downloaded(self, url: str) -> bool:
        """
        Check if an attachment URL has already been downloaded.

        Args:
            url: The Discord attachment URL

        Returns:
            True if the attachment exists in the database, False otherwise
        """
        with self.get_session() as session:
            try:
                result = session.query(Attachment).filter(Attachment.url == url).first()
                return result is not None
            except Exception as e:
                self.logger.error(f'Error checking if attachment is downloaded: {e}')
                return False

    def add_attachment(self, url: str, filename: str, channel_id: int, message_date: datetime) -> bool:
        """
        Add a new attachment record to the database.

        Args:
            url: The Discord attachment URL
            filename: Original filename of the attachment
            channel_id: Discord channel ID where the attachment was found
            message_date: When the original Discord message was sent

        Returns:
            True if the attachment was added successfully, False if it already exists
        """
        with self.get_session() as session:
            try:
                # Create new attachment record
                attachment = Attachment(
                    url=url,
                    filename=filename,
                    channel_id=channel_id,
                    message_date=message_date
                )

                session.add(attachment)
                session.commit()

                self.logger.debug(f'Added attachment to database: {filename}')
                return True

            except IntegrityError:
                session.rollback()
                self.logger.debug(f'Attachment already exists in database: {url}')
                return False

            except Exception as e:
                session.rollback()
                self.logger.error(f'Error adding attachment to database: {e}')
                return False

    def close(self):
        """Close the database engine and all connections."""
        try:
            self.engine.dispose()
            self.logger.info('Database connections closed')
        except Exception as e:
            self.logger.error(f'Error closing database connections: {e}')
