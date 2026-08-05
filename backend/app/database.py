"""
SQLite 数据库连接 & 建表
使用 SQLAlchemy ORM，本地文件数据库，无需额外服务
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},  # SQLite 需要此参数
    echo=False
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """ORM 基类"""
    pass


def init_db():
    """
    初始化数据库，创建所有表。
    必须先 import 所有 model 类，SQLAlchemy 才能发现它们并建表。
    """
    # noinspection PyUnresolvedReferences
    import app.models.task         # noqa: F401
    import app.models.outline      # noqa: F401
    import app.models.character    # noqa: F401
    import app.models.storyboard   # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_db():
    """
    FastAPI 依赖：为每个请求创建独立 session，请求结束自动关闭。
    路由中使用: db: Session = Depends(get_db)
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
