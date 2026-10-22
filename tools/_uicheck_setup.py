"""界面改版验证专用：在 kanban_demo 库内建**隔离 schema** uicheck 并灌入与生产同形的参考数据。

为什么不是另建一个库（背景纪律）：
    21/22/23 号审查报告明确「不得对生产库 public 执行 DDL/DML」，而 AppTest / 浏览器
    渲染都会真实触发 init_db 与埋点写入。当前 DB 角色 demoapp **无 CREATEDB 权限**，
    因此改为在同一个库内建独立 schema：所有不带 schema 前缀的表名经 search_path
    解析到 uicheck，生产 public 表**一个字节都不动**。

怎么保证 app 不碰 public：
    db.py 的连接参数不含 search_path，libpq 会读取环境变量 PGOPTIONS；
    本脚本与启动命令都设置 ``PGOPTIONS=-c search_path=uicheck,public``，
    于是 app 的建表/读写全部落在 uicheck。已验证 ``show search_path`` 生效。

参考数据只复制渲染所需的 users / projects / requirements；
user_activity_log / sus_survey / eval_improvement **刻意不复制**（不带走生产行为数据）。

用法：
    mvp\\.venv\\Scripts\\python.exe mvp/tools/_uicheck_setup.py           # 重建 schema + 灌数
    mvp\\.venv\\Scripts\\python.exe mvp/tools/_uicheck_setup.py --keep    # 保留已有 schema
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

SCHEMA = "uicheck"
COPY_TABLES = ("users", "projects", "requirements")  # 渲染所需；行为日志类刻意排除
PK = {"users": "id", "projects": "id", "requirements": "id"}

ROOT = Path(__file__).resolve().parents[1]

# 必须在 import db.py / psycopg2 之前设置：libpq 在建立连接时读取 PGOPTIONS
os.environ["PGOPTIONS"] = f"-c search_path={SCHEMA},public"

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import psycopg2  # noqa: E402
import psycopg2.extensions  # noqa: E402


def _conn() -> "psycopg2.extensions.connection":
    c = psycopg2.connect(
        host=os.environ.get("DB_HOST") or "localhost",
        port=os.environ.get("DB_PORT") or "5432",
        dbname=os.environ.get("DB_NAME") or "kanban_demo",
        user=os.environ.get("DB_USER") or "postgres",
        password=os.environ.get("DB_PASSWORD") or "",
    )
    c.autocommit = True
    return c


def reset_schema(keep: bool) -> None:
    """建（或重建）隔离 schema。DDL 仅作用于 uicheck，不触碰 public。"""
    c = _conn()
    cur = c.cursor()
    cur.execute("show search_path")
    print(f"[env] PGOPTIONS search_path = {cur.fetchone()[0]}")
    cur.execute("select current_user, current_database()")
    print(f"[env] user/db = {cur.fetchone()}")
    if not keep:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        print(f"[ddl] dropped schema {SCHEMA}")
    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    cur.execute(
        "select nspname from pg_namespace where nspname = %s and pg_get_userbyid(nspowner) = current_user",
        (SCHEMA,),
    )
    owned = cur.fetchone()
    print(f"[ddl] schema {SCHEMA} ready (owner=current_user: {bool(owned)})")
    if not owned:
        raise SystemExit(f"拒绝执行：{SCHEMA} 的 owner 不是当前用户，后续 DDL 可能越权")
    c.close()


def init_schema_tables() -> None:
    """用 app 自己的 init_db 建表（走真实迁移路径，与生产同构）。"""
    sys.path.insert(0, str(ROOT))
    import db as appdb  # noqa: E402
    import sample_data  # noqa: E402

    assert appdb.DB_NAME == (os.environ.get("DB_NAME") or "kanban_demo")
    appdb.init_db(sample_data.SAMPLE_REQUIREMENTS, sample_data.PROJECTS, sample_data.SEED_USERS)
    print(f"[schema] init_db 完成（表全部落在 {SCHEMA}）")


def copy_reference_data() -> None:
    """从 public 只读复制参考数据到 uicheck（upsert）。"""
    c = _conn()
    cur = c.cursor()
    for table in COPY_TABLES:
        cur.execute(f"select * from public.{table}")
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        if not rows:
            print(f"[copy] public.{table}: 0 rows（跳过）")
            continue
        col_list = ", ".join(f'"{x}"' for x in cols)
        placeholders = ", ".join(["%s"] * len(cols))
        updates = ", ".join(f'"{x}" = EXCLUDED."{x}"' for x in cols if x != PK[table])
        sql = (
            f'INSERT INTO {SCHEMA}.{table} ({col_list}) VALUES ({placeholders}) '
            f'ON CONFLICT ("{PK[table]}") DO UPDATE SET {updates}'
        )
        cur.executemany(sql, rows)
        print(f"[copy] public.{table}: {len(rows)} rows → {SCHEMA}.{table}")
    c.close()


def pre_dismiss_survey(user_name: str = "员工01") -> None:
    """给量测账号预置一条「已触达」问卷记录，避免自动弹窗遮挡页面量测。

    走 db.new_survey_dismissed 真实路径（格式与冷却口径一致），写入隔离 schema。
    """
    sys.path.insert(0, str(ROOT))
    import db as appdb  # noqa: E402

    c = _conn()
    cur = c.cursor()
    cur.execute(f"select id, name, role, department from {SCHEMA}.users where name = %s", (user_name,))
    row = cur.fetchone()
    c.close()
    if not row:
        print(f"[survey] 未找到用户 {user_name}，跳过预置")
        return
    uid, name, role, dept = row
    rec = appdb.new_survey_dismissed({"id": uid, "name": name, "role": role, "department": dept})
    print(f"[survey] 为 {name}({uid}) 预置已触达记录 rec={rec}，冷却期 {appdb.SURVEY_COOLDOWN_DAYS if hasattr(appdb, 'SURVEY_COOLDOWN_DAYS') else '?'} 天")


def summarize() -> None:
    c = _conn()
    cur = c.cursor()
    print("[verify] 隔离 schema 行数：")
    for t in ("users", "projects", "requirements", "user_activity_log", "sus_survey", "eval_improvement"):
        cur.execute(f"select count(*) from {SCHEMA}.{t}")
        print(f"    {SCHEMA}.{t} = {cur.fetchone()[0]}")
    print("[verify] 生产 public 行数（应保持不变）：")
    for t in ("requirements", "user_activity_log", "sus_survey"):
        cur.execute(f"select count(*) from public.{t}")
        print(f"    public.{t} = {cur.fetchone()[0]}")
    c.close()


def main() -> int:
    keep = "--keep" in sys.argv
    print(f"目标隔离 schema = {SCHEMA}（生产 public 只读）")
    reset_schema(keep=keep)
    init_schema_tables()
    copy_reference_data()
    pre_dismiss_survey()
    summarize()
    print("\n✅ 隔离验证环境就绪。启动命令：")
    print(f'   $env:PGOPTIONS="-c search_path={SCHEMA},public"')
    print("   mvp\\.venv\\Scripts\\python.exe -m streamlit run mvp/app.py --server.port 8555 --server.headless true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
