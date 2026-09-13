"""
迁移 001 —— 保留导演脚本原文 / 片头定调段

问题背景：旧解析只把逐镜字段拆进入库，脚本原文与「片头定调段」被丢弃，
结果页无法还原模板的四段结构，编辑也无从回填。

新增列（均可空，旧数据不受影响）：
  storyboards.raw_script       每镜的原文脚本块（编辑的事实来源，保存时按它重解析）
  tasks.opening_section        片头定调段原文（作品信息卡 + 开场定调）

无 Alembic，手工执行：python -m migrations.001_add_raw_script_and_opening_section
脚本幂等，重复执行安全。
"""
import os
import sys

import pymysql

# 允许以 `python migrations/xxx.py` 直接运行
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402

# (表名, 列名, 建列语句, 注释)
COLUMNS = [
    (
        "storyboards",
        "raw_script",
        "ADD COLUMN raw_script TEXT NULL COMMENT '导演脚本原文块（编辑事实来源）'",
    ),
    (
        "tasks",
        "opening_section",
        "ADD COLUMN opening_section TEXT NULL COMMENT '片头定调段原文（作品信息卡+开场定调）'",
    ),
]


def parse_dsn(url: str) -> dict:
    """mysql+pymysql://user:pass@host:port/db?charset=... → pymysql 连接参数"""
    body = url.split("://", 1)[1]
    creds, rest = body.split("@", 1)
    user, password = creds.split(":", 1)
    hostport, dbname = rest.split("/", 1)
    dbname = dbname.split("?", 1)[0]
    if ":" in hostport:
        host, port = hostport.split(":", 1)
    else:
        host, port = hostport, "3306"
    return {
        "host": host,
        "port": int(port),
        "user": user,
        "password": password,
        "database": dbname,
        "charset": "utf8mb4",
    }


def column_exists(cur, table: str, column: str) -> bool:
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (table, column),
    )
    return cur.fetchone()[0] > 0


def main() -> int:
    conn = pymysql.connect(**parse_dsn(settings.database_url))
    try:
        with conn.cursor() as cur:
            changed = 0
            for table, column, ddl in COLUMNS:
                if column_exists(cur, table, column):
                    print(f"  [skip] {table}.{column} 已存在")
                    continue
                cur.execute(f"ALTER TABLE {table} {ddl}")
                print(f"  [ok]   {table}.{column} 已创建")
                changed += 1
        conn.commit()
        print(f"迁移完成：新增 {changed} 列")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
