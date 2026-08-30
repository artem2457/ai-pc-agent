from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import ROOT, settings


class Base(DeclarativeBase):
    pass


def utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=utcnow)


class Device(Base):
    __tablename__ = "devices"
    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    device_id = Column(String, unique=True, nullable=False)
    hostname = Column(String, default="")
    os = Column(String, default="unknown")
    hardware = Column(Text, default="{}")
    status = Column(String, default="offline")
    last_seen = Column(DateTime, default=utcnow)
    created_at = Column(DateTime, default=utcnow)


class Enrollment(Base):
    __tablename__ = "enrollments"
    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    token = Column(String, unique=True, nullable=False)
    label = Column(String, default="USB Agent")
    used = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)


class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    device_pk = Column(Integer, ForeignKey("devices.id"), nullable=False)
    user_message = Column(Text, nullable=False)
    status = Column(String, default="queued")
    plan_json = Column(Text, default="[]")
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class CommandLog(Base):
    __tablename__ = "command_logs"
    id = Column(Integer, primary_key=True)
    device_pk = Column(Integer, ForeignKey("devices.id"), nullable=False)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True)
    command_id = Column(String, nullable=False)
    action = Column(String, nullable=False)
    params = Column(Text, default="{}")
    status = Column(String, default="sent")
    stdout = Column(Text, default="")
    stderr = Column(Text, default="")
    exit_code = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id = Column(Integer, primary_key=True)
    device_pk = Column(Integer, ForeignKey("devices.id"), nullable=False)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow)


class McpKey(Base):
    __tablename__ = "mcp_keys"
    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    key = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=utcnow)


def make_engine():
    db_path = settings.database_url
    if db_url_is_sqlite(db_path):
        if "///" in db_path and ":memory:" not in db_path:
            path = db_path.split("///", 1)[1]
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        elif ":memory:" not in db_path:
            (ROOT / "data").mkdir(parents=True, exist_ok=True)
    connect_args = {"check_same_thread": False} if db_url_is_sqlite(db_path) else {}
    poolclass = StaticPool if db_url_is_sqlite(db_path) and ":memory:" in db_path else None
    return create_engine(db_path, connect_args=connect_args, poolclass=poolclass)


def db_url_is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
