"""上线前最终状态确认：schema 清单 + 生产表行数 + 关键配置存在性（不打印任何密钥值）。"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

c = psycopg2.connect(
    host=os.environ.get("DB_HOST") or "localhost",
    port=os.environ.get("DB_PORT") or "5432",
    dbname=os.environ.get("DB_NAME") or "kanban_demo",
    user=os.environ.get("DB_USER") or "postgres",
    password=os.environ.get("DB_PASSWORD") or "",
)
k = c.cursor()
k.execute(
    "select nspname from pg_namespace "
    "where nspname not like 'pg\\_%' and nspname != 'information_schema' order by 1"
)
print("schema 清单：", [r[0] for r in k.fetchall()], "（应只有 public）")
for t in ("requirements", "users", "projects", "sus_survey", "eval_improvement", "meta"):
    k.execute(f"select count(*) from public.{t}")
    print(f"  public.{t} = {k.fetchone()[0]}")
c.close()

print("\nLLM 配置（只看是否存在）：")
for var in ("DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "ZHIPU_API_KEY", "OPENAI_API_KEY"):
    v = os.environ.get(var, "")
    print(f"  {var}: {'已配置' if v else '空'}")
