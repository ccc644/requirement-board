"""
bootstrap.py — 从 app.py 拆分生成（函数体逐字保留）。
"""
from __future__ import annotations

# ---- 第三方 / 标准库 ----
from sample_data import SAMPLE_PROJECTS, SAMPLE_REQUIREMENTS, SEED_USERS
import db
import os
import psycopg2
import streamlit as st

# ---- 项目内部 ----
from services.cache import _invalidate_data_caches
from services.requirements import _normalize_requirements
from state import _rebuild_user_maps



def init_state():
    """初始化：先确保 DB 存在并预填种子数据，再加载到 session_state 缓存"""
    try:
        db.init_db(SAMPLE_REQUIREMENTS, SAMPLE_PROJECTS, SEED_USERS)
    except (psycopg2.OperationalError, RuntimeError) as e:
        # 连接类异常（连不上/认证失败/库不存在，db._connect 的 GBK 提示也是 RuntimeError）：
        # 保留原有排查指引
        st.error(f"数据库连接失败，请检查 .env 中的 DB_* 配置（DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD）。\n\n错误详情：{e}")
        st.stop()
    except Exception as e:
        # 代码类异常（如 DDL/SQL 与代码不匹配——典型场景：代码更新后进程热重载，表结构未跟上）：
        # 不误导用户去查 .env，指明真实方向（t24 F-文案修复，源于真实故障中 IndexError 被误报为连接失败）
        st.error(f"数据库初始化失败（{type(e).__name__}）：{e}\n\n"
                 "这通常与代码/表结构不一致有关（例如更新代码后进程未重启）。请重启应用；"
                 "若重启后仍报同样错误，再按连接问题检查 .env 中的 DB_* 配置。")
        st.stop()
    if "users" not in st.session_state:
        st.session_state.users = db.load_all_users()
    # 先建好姓名映射：下方加载需求时的「子需求自动拆分」默认标题需要执行人姓名
    _rebuild_user_maps()
    if "requirements" not in st.session_state:
        raw = db.load_all_requirements()
        # 迁移：历史「待分配」状态并入「需求池」（落库，仅首次）
        for _r in raw:
            if _r.get("status") == "待分配":
                _r["status"] = "需求池"
                db.save_requirement(_r)
        st.session_state.requirements = _normalize_requirements(raw)
    if "projects" not in st.session_state:
        st.session_state.projects = db.load_all_projects()
    if "provider" not in st.session_state:
        st.session_state.provider = "deepseek"
    if "api_key" not in st.session_state:
        st.session_state.api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if "model" not in st.session_state:
        st.session_state.model = ""  # 空表示用默认
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
    _rebuild_user_maps()


def _sync_from_db() -> None:
    """检测其它会话是否改动了数据，若有则重新加载（修复多人看不到彼此改动的问题）。

    只在版本指纹变化时重载；本会话自己刚写入的版本标记为已知，不重复重载。
    """
    ver = db.data_version()
    if st.session_state.get("_data_version") == ver:
        return
    # 首次进入，仅记录版本不重载（数据已由 init_state 加载）
    if "_data_version" not in st.session_state:
        st.session_state["_data_version"] = ver
        return
    if st.session_state.pop("_own_write_pending", False):
        st.session_state["_data_version"] = ver
        return
    st.session_state["_data_version"] = ver
    st.session_state.requirements = _normalize_requirements(db.load_all_requirements())
    st.session_state.projects = db.load_all_projects()
    st.session_state.users = db.load_all_users()
    _rebuild_user_maps()
    _invalidate_data_caches()


def reload_from_db():
    """从数据库重新加载到 session_state（增删改后调用）"""
    st.session_state.requirements = _normalize_requirements(db.load_all_requirements())
    st.session_state.projects = db.load_all_projects()
