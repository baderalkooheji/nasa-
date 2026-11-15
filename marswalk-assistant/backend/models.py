"""
Marswalk AI Assistant - Database Models
SQLAlchemy models for conversation history and mission memory (SQLite).

Note: `metadata` is reserved by SQLAlchemy's declarative base, so the Python attribute is `meta`
while the database column keeps the name "metadata".
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text, create_engine
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker
from sqlalchemy.pool import StaticPool

from config import settings

Base = declarative_base()


class Conversation(Base):
    """Conversation sessions with the AI assistant."""
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), unique=True, index=True, nullable=False)
    title = Column(String(256), default="New Conversation")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Boolean, default=True)

    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")
    mission_context = relationship("MissionContext", back_populates="conversation", uselist=False, cascade="all, delete-orphan")


class Message(Base):
    """Individual messages in a conversation."""
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False, index=True)
    role = Column(String(32), nullable=False)  # user, assistant
    content = Column(Text, nullable=False)
    tool_calls = Column(JSON, nullable=True)
    tool_results = Column(JSON, nullable=True)
    meta = Column("metadata", JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    conversation = relationship("Conversation", back_populates="messages")


class MissionContext(Base):
    """Mission memory for one conversation: routes, analyses, map state, objectives."""
    __tablename__ = "mission_context"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), unique=True, nullable=False)

    current_mission = Column(String(128), default="EVA-01 · Jezero delta")
    selected_area = Column(JSON, nullable=True)          # last place the assistant analysed / zoomed to
    current_route = Column(JSON, nullable=True)          # summary of the latest generated route
    route_comparisons = Column(JSON, nullable=True)      # routes generated in this conversation (summaries, by id)
    selected_layers = Column(JSON, nullable=True)        # visible layers reported by the page
    scientific_analyses = Column(JSON, nullable=True)    # short log of analyses (terrain, EVA, environment)
    mission_objectives = Column(JSON, nullable=True)

    map_view = Column(String(16), default="2d")
    map_center = Column(JSON, nullable=True)
    selected_point = Column(JSON, nullable=True)         # the point selected on the Marswalk map

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    conversation = relationship("Conversation", back_populates="mission_context")


class Document(Base):
    """Ingested documents for RAG."""
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String(256), nullable=False)
    filepath = Column(String(512), nullable=False)
    content_hash = Column(String(64), unique=True, index=True)
    title = Column(String(256), nullable=True)
    source_type = Column(String(64), nullable=True)      # pdf, txt, md, json
    meta = Column("metadata", JSON, nullable=True)       # front matter: source_url, organization, …
    chunk_count = Column(Integer, default=0)
    is_processed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    chunks = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan")


class DocumentChunk(Base):
    """Document chunks for retrieval."""
    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    embedding_id = Column(String(64), nullable=True, index=True)
    meta = Column("metadata", JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    document = relationship("Document", back_populates="chunks")


engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
    echo=False,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """Initialize database tables."""
    Base.metadata.create_all(bind=engine)


def get_db() -> Session:
    """FastAPI dependency: one database session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
