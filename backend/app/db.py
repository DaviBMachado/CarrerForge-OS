import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

# Database location defaults to backend/data/careerforge.db
BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BACKEND_DIR / "data" / "careerforge.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH.as_posix()}")

if DATABASE_URL.startswith("sqlite"):
    if not DATABASE_URL.startswith("sqlite:///:memory:"):
        db_file = DATABASE_URL.replace("sqlite:///", "")
        Path(db_file).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        echo=False,
    )

    @event.listens_for(Engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        # Enable foreign keys and WAL mode for SQLite
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()
else:
    engine = create_engine(DATABASE_URL, echo=False)


def init_db() -> None:
    # shortcut: tables auto-created on startup; Alembic handles migrations
    from backend.app import models  # noqa: F401

    SQLModel.metadata.create_all(engine)
    if DATABASE_URL.startswith("sqlite"):
        with engine.connect() as conn:
            cursor = conn.exec_driver_sql("PRAGMA table_info(experiences)")
            cols = {row[1] for row in cursor.fetchall()}
            if "is_contract" not in cols:
                conn.exec_driver_sql(
                    "ALTER TABLE experiences ADD COLUMN is_contract BOOLEAN DEFAULT 0"
                )
            if "is_academic" not in cols:
                conn.exec_driver_sql(
                    "ALTER TABLE experiences ADD COLUMN is_academic BOOLEAN DEFAULT 0"
                )
            conn.commit()


def get_db() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
