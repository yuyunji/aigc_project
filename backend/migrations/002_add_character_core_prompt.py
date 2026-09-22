"""
迁移 002 —— 每镜人物角色核心提示词行

问题背景：角色服装一致性的唯一载体此前是首行全局风格前缀里的外观描述，
与资产拆解的角色数据（含「服装」字段与多着装状态）脱节，跨镜头容易服装漂移；
视频生成也没有显式引用资产图的机制（只有对画面文本做子串模糊匹配）。

新增列（可空，旧数据不受影响）：
  storyboards.character_core_prompt
      每镜块内「人物角色核心提示词：@角色名（着装状态：…）」行，
      解析器从原文块摘出后单独入库，供视频 prompt 前置注入与 @资产图引用解析。

无 Alembic，手工执行：python -m migrations.002_add_character_core_prompt
脚本幂等，重复执行安全。
"""
import os
import sys

import psycopg
from psycopg import sql

# 允许以 `python migrations/xxx.py` 直接运行
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402

# (表名, 列名, 列注释)
COLUMNS = [
    (
        "storyboards",
        "character_core_prompt",
        "每镜人物角色核心提示词（@角色名+着装状态）",
    ),
]


def dsn(url: str) -> str:
    """postgresql+psycopg://... → psycopg 可直连的 postgresql://..."""
    return url.replace("+psycopg", "", 1)


def column_exists(cur, table: str, column: str) -> bool:
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE table_schema = current_schema() AND table_name = %s AND column_name = %s",
        (table, column),
    )
    return cur.fetchone()[0] > 0


def main() -> int:
    with psycopg.connect(dsn(settings.database_url)) as conn:
        with conn.cursor() as cur:
            changed = 0
            for table, column, comment in COLUMNS:
                if column_exists(cur, table, column):
                    print(f"  [skip] {table}.{column} 已存在")
                    continue
                cur.execute(
                    sql.SQL("ALTER TABLE {} ADD COLUMN {} TEXT NULL").format(
                        sql.Identifier(table), sql.Identifier(column)
                    )
                )
                # COMMENT ON 是 utility 语句，不接受绑定参数，只能拼字面量
                cur.execute(
                    sql.SQL("COMMENT ON COLUMN {}.{} IS {}").format(
                        sql.Identifier(table),
                        sql.Identifier(column),
                        sql.Literal(comment),
                    )
                )
                print(f"  [ok]   {table}.{column} 已创建")
                changed += 1
        conn.commit()
        print(f"迁移完成：新增 {changed} 列")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
