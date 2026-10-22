"""
一次性迁移脚本：把 SQLite 里的 users 表迁移到 PostgreSQL。

背景：应用从 SQLite 切换到 PostgreSQL 时，
- 用户（users）需要保留 → 由本脚本迁移
- 需求/项目（requirements / projects）按「全新开始」处理，由应用首次启动时重新导入种子数据

用法（在项目目录下，先确保 .env 已填好 DB_* 并安装了依赖）：
    python migrate_sqlite_to_pg.py
"""
from __future__ import annotations
import os
import sqlite3
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

APP_DIR = Path(__file__).parent
load_dotenv(APP_DIR / ".env")

SQLITE_PATH = APP_DIR / "data" / "kanban.db"

DB_HOST = os.environ.get("DB_HOST") or "localhost"
DB_PORT = os.environ.get("DB_PORT") or "5432"
DB_NAME = os.environ.get("DB_NAME") or "kanban_demo"
DB_USER = os.environ.get("DB_USER") or "postgres"
DB_PASSWORD = os.environ.get("DB_PASSWORD") or ""


def main() -> None:
    if not SQLITE_PATH.exists():
        print(f"[错误] 未找到 SQLite 数据库：{SQLITE_PATH}")
        print("       请确认之前用旧版应用跑过，或直接跳过迁移（用户会按种子账号重新初始化）。")
        return

    # 1) 从 SQLite 读取用户
    sqlite_conn = sqlite3.connect(str(SQLITE_PATH))
    try:
        rows = sqlite_conn.execute(
            "SELECT id, name, role, department, password_hash, salt, created_at FROM users"
        ).fetchall()
    finally:
        sqlite_conn.close()

    if not rows:
        print("[提示] SQLite 中 users 表为空，无需迁移。")
        return

    # 2) 写入 PostgreSQL（upsert，以 SQLite 为准）
    pg = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD
    )
    pg.autocommit = True
    try:
        cur = pg.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                role TEXT NOT NULL,
                department TEXT,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_at TEXT
            )
        """)
        for u in rows:
            cur.execute(
                """
                INSERT INTO users (id, name, role, department, password_hash, salt, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    role = EXCLUDED.role,
                    department = EXCLUDED.department,
                    password_hash = EXCLUDED.password_hash,
                    salt = EXCLUDED.salt,
                    created_at = EXCLUDED.created_at
                """,
                u,
            )
    finally:
        pg.close()

    print(f"[完成] 已迁移 {len(rows)} 个用户到 PostgreSQL（{DB_HOST}:{DB_PORT}/{DB_NAME}）。")
    print("       之后启动应用，需求/项目会按种子数据重新初始化。")


if __name__ == "__main__":
    main()
