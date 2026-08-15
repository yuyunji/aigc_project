"""
数据库连接 & 建表
使用 SQLAlchemy ORM，支持 SQLite / MySQL（通过 DATABASE_URL 切换）
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.config import settings

is_sqlite = settings.database_url.startswith("sqlite")

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if is_sqlite else {},
    pool_pre_ping=not is_sqlite,   # MySQL 断连自动重连
    pool_recycle=3600,
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """ORM 基类"""
    pass


def init_db():
    """
    初始化数据库，创建所有表。
    必须先 import 所有 model 类，SQLAlchemy 才能发现它们并建表。
    全新创建，依赖 create_all 按最终 schema 建全表（含新增列）。
    """
    # noinspection PyUnresolvedReferences
    import app.models.task         # noqa: F401
    import app.models.outline      # noqa: F401
    import app.models.character    # noqa: F401
    import app.models.storyboard   # noqa: F401
    import app.models.media        # noqa: F401
    import app.models.asset        # noqa: F401

    Base.metadata.create_all(bind=engine)

    # 服务重启后，把上次进程遗留的 running 状态标记为 failed（后台任务已丢失）
    _reset_stale_running()


def _reset_stale_running():
    """重启后清理卡在 running 的媒体/资产（asyncio 后台任务已随进程消失）"""
    from app.models.media import MediaAsset
    from app.models.asset import AssetItem

    db = SessionLocal()
    try:
        db.query(MediaAsset).filter(MediaAsset.status == "running").update(
            {"status": "failed", "error_message": "服务重启，任务中断"}
        )
        db.query(AssetItem).filter(AssetItem.image_status == "running").update(
            {"image_status": "failed", "error_message": "服务重启，任务中断"}
        )
        db.commit()
    finally:
        db.close()


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
