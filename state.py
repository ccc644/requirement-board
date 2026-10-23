"""
state.py — 从 app.py 拆分生成（函数体逐字保留）。
"""
from __future__ import annotations

# ---- 第三方 / 标准库 ----
import auth
import db
import json
import streamlit as st
import streamlit.components.v1 as components

# ---- 项目内部 ----
from config import NAV_STATE_KEY
from services.cache import _invalidate_data_caches, _mark_own_write



USERS: list[dict] = []


USER_NAME_TO_ID: dict[str, str] = {}


USER_ID_TO_NAME: dict[str, str] = {}


_AUTH_COOKIE = "kb_token"


_AUTH_TOKEN_TTL = 30 * 86400  # 30 天（与 auth._TOKEN_TTL 同值，改时两处同步）


def _rebuild_user_maps():
    """从 session_state 重建用户名 <-> ID 查找表（用户增删改后调用）"""
    # 原地更新而非重新绑定：其它模块用 from state import 持有这些容器，
    # 重新绑定会让它们仍指向旧对象（拆分前同处一个命名空间，没有这个问题）。
    users = st.session_state.get("users", [])
    USERS.clear()
    USERS.extend(users)
    USER_NAME_TO_ID.clear()
    USER_NAME_TO_ID.update({u["name"]: u["id"] for u in users})
    USER_ID_TO_NAME.clear()
    USER_ID_TO_NAME.update({u["id"]: u["name"] for u in users})


def get_users() -> list[dict]:
    return st.session_state.get("users", [])


def get_current_user() -> dict | None:
    uid = st.session_state.get("current_user_id")
    if not uid:
        return None
    return next((u for u in get_users() if u["id"] == uid), None)


def reload_users():
    """用户增删改后，重新加载并重建查找表"""
    st.session_state.users = db.load_all_users()
    _rebuild_user_maps()
    _invalidate_data_caches()
    _mark_own_write()


def _emit_auth_cookie_js(tok: str | None) -> None:
    """零高 iframe 写/清登录 Cookie。tok=None 时置 max-age=0 即清除。
    组件 iframe 与主文档同源，document.cookie 直写本站 Cookie。"""
    val = (f"{_AUTH_COOKIE}=; max-age=0; path=/" if tok is None else
           f"{_AUTH_COOKIE}={tok}; max-age={_AUTH_TOKEN_TTL}; path=/; SameSite=Lax")
    components.html(f"<script>document.cookie={json.dumps(val)};</script>", height=0)


def _try_restore_login_from_cookie() -> bool:
    """未登录时尝试从 Cookie 还原登录态（浏览器刷新场景），成功返回 True。"""
    try:
        tok = st.context.cookies.get(_AUTH_COOKIE)
    except Exception:
        return False
    uid = auth.consume_token(tok)
    if not uid or not any(u["id"] == uid for u in get_users()):
        return False
    st.session_state.current_user_id = uid
    st.session_state.logged_in = True
    # 与登录成功同口径补一条 login 埋点：刷新=新会话，DAU/WAU 口径需要它；
    # 失败只打日志，绝不阻塞还原（吸收客诉「埋点失败不抛异常」纪律）
    u = get_current_user()
    if u:
        try:
            db.log_user_activity(u["id"], u.get("name", ""), u.get("role", ""), "", "login")
        except Exception as e:
            print(f"[activity] 刷新还原登录埋点写入失败（不影响登录）：{e}")
    return True


def _do_logout():
    """退出登录（t31：从侧边栏内联代码提取为函数，供顶部用户胶囊复用，清理逻辑逐字保留）。

    刷新不掉线的配套清理（本函数其余逻辑不动）：服务端令牌立即作废，
    并标记清除浏览器 Cookie——只清 session 不清 Cookie 的话，刷新又会被还原回来。
    """
    try:
        auth.revoke_token(st.context.cookies.get(_AUTH_COOKIE))
    except Exception:
        pass  # 读不到 Cookie（极端环境）也不阻塞登出
    st.session_state["_clear_auth_cookie"] = True
    st.session_state.pop("logged_in", None)
    st.session_state.pop("current_user_id", None)
    # 评价体系的本会话标记要一并清掉：否则同一浏览器换人登录时，
    # 下一个人会继承「本会话已检查/已弹过问卷」的状态而收不到问卷
    for k in ("_survey_checked", "_survey_pending", "_survey_rec", "_rating_req"):
        st.session_state.pop(k, None)
    # 改进闭环操作台与用户活跃埋点的会话状态同款处理（C10：新增 session 键必须纳入
    # 登出清理，否则换人登录会串状态——改进闭环控件键以 imp_/_imp 前缀命名；
    # _activity_last_page 若不清理，换人登录后首个页面切换会被误判为「未变化」而漏记；
    # t34 F-04：_global_search 前缀覆盖 Header 全局搜索的 _global_search_q/_global_search_last 两键，
    # 残留键虽经审查实测无可见后果，仍纳入清扫保持清单完整）
    for k in [k for k in list(st.session_state)
              if k.startswith("imp_") or k.startswith("_imp") or k.startswith("_activity")
              or k.startswith("_global_search")]:
        st.session_state.pop(k, None)
    # t31 导航迁移：清理旧 radio 键与当前页键，换人登录从第一页重新开始
    st.session_state.pop("nav_radio", None)
    st.session_state.pop(NAV_STATE_KEY, None)
    # A5C 对话搜索：聊天历史含上一个用户的提问与检索结果，换人登录必须清掉
    st.session_state.pop("chat_msgs", None)
    st.rerun()
