#TEST ! 注入依賴
"""Database module."""

from contextlib import contextmanager, AbstractContextManager
from typing import Callable
import logging
from sqlalchemy import create_engine, orm
from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import inspect

logger = logging.getLogger(__name__)
Base = declarative_base()

class Database:
    def __init__(self, db_url: str) -> None:
        self._engine = create_engine(db_url, echo=False, pool_pre_ping=True)
        self._session_factory = orm.scoped_session(
            orm.sessionmaker(
                autocommit=False,
                autoflush=False,
                bind=self._engine,
            ),
        )
        self._inspector = inspect(self._engine)
        self._base = Base

    def create_database(self) -> None:
        self._base.metadata.create_all(self._engine)

    @property
    def engine(self):
        return self._engine

    @property
    def inspector(self):
        return self._inspector

    @property
    def base(self):
        return self._base

    @contextmanager
    def session(self) -> Callable[..., AbstractContextManager[Session]]:  # type: ignore
        session: Session = self._session_factory()
        try:
            yield session
        except Exception:
            logger.exception("Session rollback because of exception")
            session.rollback()
            raise
        finally:
            session.close()
