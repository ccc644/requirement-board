"""
services/stats.py — 从 app.py 拆分生成（函数体逐字保留）。
"""
from __future__ import annotations

# ---- 第三方 / 标准库 ----
import db
import streamlit as st



def _cached_db_stats() -> dict:
    """侧边栏统计。内含 pg_database_size 与附件目录递归扫描，实测约 287ms。

    这行统计文字容忍最多 60 秒的陈旧度。
    """
    return db.db_stats()
