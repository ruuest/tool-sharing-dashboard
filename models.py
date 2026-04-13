import uuid
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

DATABASE_URL = "sqlite:///data/sharing.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def utcnow():
    return datetime.now(timezone.utc)


def gen_token():
    return uuid.uuid4().hex[:16]


class Friend(Base):
    __tablename__ = "friends"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(200), default="")
    telegram_id = Column(String(100), default="")
    invite_token = Column(String(32), unique=True, default=gen_token)
    created_at = Column(DateTime, default=utcnow)
    accesses = relationship("ToolAccess", back_populates="friend", cascade="all, delete-orphan")


class Tool(Base):
    __tablename__ = "tools"
    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String(50), unique=True, nullable=False)
    name = Column(String(200), nullable=False)
    description = Column(Text, default="")
    status = Column(String(20), default="active")  # active / coming_soon / inactive
    config_template = Column(Text, default="")
    accesses = relationship("ToolAccess", back_populates="tool", cascade="all, delete-orphan")


class ToolAccess(Base):
    __tablename__ = "tool_access"
    id = Column(Integer, primary_key=True, index=True)
    friend_id = Column(Integer, ForeignKey("friends.id"), nullable=False)
    tool_id = Column(Integer, ForeignKey("tools.id"), nullable=False)
    enabled = Column(Boolean, default=False)
    granted_at = Column(DateTime, default=utcnow)
    friend = relationship("Friend", back_populates="accesses")
    tool = relationship("Tool", back_populates="accesses")


class ActivityLog(Base):
    __tablename__ = "activity_log"
    id = Column(Integer, primary_key=True, index=True)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow)


def init_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    # Seed tools if empty
    if db.query(Tool).count() == 0:
        db.add(Tool(
            slug="note-pipeline",
            name="Note自動投稿ツール",
            description="キーワードCSVに基づいてNoteに記事を自動生成・投稿するパイプライン。SEO最適化済みの記事を毎日自動投稿します。",
            status="active",
            config_template="""\
# Note Pipeline 設定
# 1. keywords.csv を作成（1行1キーワード）
# 2. 以下の環境変数を設定
# 3. Render にデプロイ

ANTHROPIC_API_KEY=sk-ant-xxx
NOTE_EMAIL=your@email.com
NOTE_PASSWORD=your-password
KEYWORDS_CSV_URL=https://docs.google.com/spreadsheets/d/xxx/export?format=csv
SCHEDULE_CRON=0 9 * * *
""",
        ))
        db.add(Tool(
            slug="sns-auto-post",
            name="SNS自動投稿ツール",
            description="X(Twitter)・Instagram・Threadsに一括自動投稿。AIが最適なハッシュタグと投稿時間を選定します。",
            status="coming_soon",
            config_template="",
        ))
        db.commit()
    db.close()
