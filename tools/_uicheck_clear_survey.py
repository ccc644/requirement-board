"""清掉隔离库里为量测预置的问卷触达记录，让「填写系统使用问卷」按钮恢复可点。

背景：_uicheck_setup.py 会为该账号预置一条 dismissed 记录，避免问卷弹窗遮挡界面量测；
但副作用是冷却期（3 天）内该按钮被禁用，人工验证时点不动（用户实测反馈）。
本脚本只作用于隔离 schema uicheck，生产 public 零写入。

用法： mvp\\.venv\\Scripts\\python.exe mvp/tools/_uicheck_clear_survey.py [用户名]
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


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "员工01"
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
        print(f"[skip] 隔离 schema {SCHEMA} 不存在（未在验证态）")
        c.close()
        return 0
    k.execute(f"select count(*) from {SCHEMA}.sus_survey where username = %s", (name,))
    before = k.fetchone()[0]
    k.execute(f"delete from {SCHEMA}.sus_survey where username = %s", (name,))
    print(f"[clear] {SCHEMA}.sus_survey 中 {name} 的记录：{before} 条 → 0 条（问卷按钮恢复可点）")
    k.execute(f"select count(*) from {SCHEMA}.sus_survey")
    print(f"[verify] 剩余 {k.fetchone()[0]} 条")
    k.execute("select count(*) from public.sus_survey")
    print(f"[verify] public.sus_survey = {k.fetchone()[0]}（应未受影响）")
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
