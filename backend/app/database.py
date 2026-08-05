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
    对已有表自动添加缺失的列（SQLite ALTER TABLE 兼容方案）。
    """
    # noinspection PyUnresolvedReferences
    import app.models.task         # noqa: F401
    import app.models.outline      # noqa: F401
    import app.models.character    # noqa: F401
    import app.models.storyboard   # noqa: F401
    import app.models.media        # noqa: F401

    Base.metadata.create_all(bind=engine)

    # ── SQLite 列迁移：为旧 storyboards 表补全新列 ──
    _migrate_storyboard_columns()


def _migrate_storyboard_columns():
    """为旧 storyboards 表添加缺失的列（SQLite ALTER TABLE 安全迁移）"""
    new_columns = {
        "scene_title": "VARCHAR(200)",
        "location": "VARCHAR(200)",
        "time_of_day": "VARCHAR(50)",
        "characters_in_scene": "VARCHAR(500)",
        "camera_movement": "VARCHAR(200)",
        "dialogue": "TEXT",
        "visual_description": "TEXT",
        "image_prompt": "TEXT",
        "duration_seconds": "FLOAT",
    }
    with engine.connect() as conn:
        # 获取已有列名
        existing = {row[1] for row in conn.execute("PRAGMA table_info(storyboards)")}
        for col_name, col_type in new_columns.items():
            if col_name not in existing:
                try:
                    conn.execute(
                        f"ALTER TABLE storyboards ADD COLUMN {col_name} {col_type}"
                    )
                except Exception:
                    pass  # 已存在或数据库引擎不支持
        conn.commit()


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
