"""清理界面打磨轮留下的隔离验证环境：删除 kanban_demo 库内的 uicheck schema。

只删 uicheck（本轮验证专用），不触碰 public。删除前后打印 public 行数以便核对。
用法： mvp\\.venv\\Scripts\\python.exe mvp/tools/_uicheck_teardown.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

SCHEMA = "uicheck"
TABLES = ("requirements", "users", "projects", "sus_survey", "user_activity_log", "meta")


def main() -> int:
    c = psycopg2.connect(
        host=os.environ.get("DB_HOST") or "localhost",
        port=os.environ.get("DB_PORT") or "5432",
        dbname=os.environ.get("DB_NAME") or "kanban_demo",
        user=os.environ.get("DB_USER") or "postgres",
        password=os.environ.get("DB_PASSWORD") or "",
    )
    c.autocommit = True
    k = c.cursor()

    k.execute("select 1 from pg_namespace where nspname = %s", (SCHEMA,))
    if k.fetchone() is None:
        print(f"[skip] schema {SCHEMA} 不存在，无需清理")
    else:
        k.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        print(f"[ddl] 已删除隔离 schema {SCHEMA}（连带其全部测试数据）")

    k.execute("select nspname from pg_namespace where nspname in ('public', %s)", (SCHEMA,))
    print("现存相关 schema:", sorted(r[0] for r in k.fetchall()))
    print("public 业务表行数（应与验证前一致）：")
    for t in TABLES:
        k.execute(f"select count(*) from public.{t}")
        print(f"    public.{t} = {k.fetchone()[0]}")
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
