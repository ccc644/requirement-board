"""
services/cache.py — 从 app.py 拆分生成（函数体逐字保留）。
"""
from __future__ import annotations

# ---- 第三方 / 标准库 ----
import db
import streamlit as st

# ---- 项目内部 ----


def _cached_attachment(req_id: str, filename: str, _size: int = 0) -> bytes | None:
    """附件字节。详情页原本每次 rerun 都把所有附件全量读盘。

    _size 参与缓存 key，文件被替换（大小变化）时自然失效。
    """
    return db.read_attachment(req_id, filename)


def _cached_pdf_b64(data: bytes) -> str:
    """PDF 的 base64 编码结果。一份 5MB 的 PDF 每次 rerun 都重编码代价很高。"""
    import base64
    return base64.b64encode(data).decode("utf-8")


def _cached_table_preview(data: bytes, ext: str):
    """Excel/CSV 预览的解析结果（pandas 解析原本每次 rerun 都重跑）。"""
    import io
    import pandas as pd
    if ext == "csv":
        return pd.read_csv(io.BytesIO(data), dtype=str).fillna("")
    return pd.read_excel(io.BytesIO(data), sheet_name=0, dtype=str).fillna("")


def _invalidate_data_caches() -> None:
    """写库后清掉与统计相关的缓存，保证操作者立刻看到最新结果。

    被清的函数分散在各自业务模块，这里用函数内导入而非模块级导入——
    否则 cache 层会反向依赖业务层，形成导入环（cache ← people ← requirements ← cache）。
    """
    from services.people import _user_load_map, _user_task_count_map
    from services.projects import _project_name_by_id
    from services.stats import _cached_db_stats

    _cached_db_stats.clear()
    _project_name_by_id.clear()
    _user_task_count_map.clear()
    _user_load_map.clear()


def _invalidate_attachment_cache() -> None:
    """附件增删后调用。删除再上传同名文件可能命中同一缓存 key，必须显式清理。"""
    from services.stats import _cached_db_stats

    _cached_attachment.clear()
    _cached_db_stats.clear()


def _mark_own_write() -> None:
    """记录「本会话刚刚写过库」，用于跳过一次立即的数据版本比对。

    写库后下一次 rerun 的数据库版本必然已变（含本会话自己的写入），
    先标记为已知，避免在加载/重载之间产生不一致的瞬时状态。
    """
    st.session_state["_own_write_pending"] = True
