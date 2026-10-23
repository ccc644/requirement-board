"""需求看板 AI 助手 — 应用入口。

分层边界（由 app.py 拆分而来，函数体逐字保留）：
  config.py      常量与业务枚举
  state.py       登录态、用户查找表（跨模块共享的可变容器）
  bootstrap.py   启动初始化与跨会话数据同步
  services/      业务逻辑与数据聚合（不含 UI）
  ui/            可复用组件、布局、弹窗、样式
  pages/         各页面
  assets/        CSS 样式表
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---- 第三方 ----
import streamlit as st

# ---- 标准库 / 项目内平铺模块 ----
import auth
import db

# ---- 内部模块 ----
from bootstrap import _sync_from_db, init_state
from config import APP_SUBTITLE, APP_TITLE
from pages.management import page_management
from pages.my_kanban import page_my_kanban
from pages.new_requirement import page_new_requirement
from pages.overview import page_overview
from pages.project import page_project_view
from pages.requirement_detail import page_requirement_detail
from pages.search import page_search
from pages.system_eval import page_system_eval
from pages.user_management import page_user_management
from pages.weekly import page_weekly
from services.notices import _pending_notices_all
from services.requirements import save_requirement
from state import _emit_auth_cookie_js, _try_restore_login_from_cookie, get_current_user
from ui.dialogs import _maybe_show_survey, _rating_dialog, _render_improvement_feed_public, _render_improvement_notice, _render_my_improvements, _render_rating_notice
from ui.layout import _render_app_header, _resolve_nav, render_sidebar
from ui.styles import _inject_global_css, _inject_login_css

def render_login_page():
    """登录页：按姓名 + 密码校验，成功后写入 current_user_id 与 logged_in。

    版式为系统自有风格（深色底 + 居中白卡），样式见 _inject_login_css。
    """
    with st.container(key="kb_login_card"):
        st.markdown(
            '<div class="kb-login-brand">'
            '<div class="kb-login-logo">🛠️</div>'
            f'<div class="kb-login-title">{APP_TITLE}</div>'
            f'<div class="kb-login-sub">{APP_SUBTITLE}</div>'
            '</div>',
            unsafe_allow_html=True,
        )

        with st.form("login_form"):
            name = st.text_input("姓名", placeholder="请输入您的姓名")
            password = st.text_input("密码", type="password", placeholder="请输入密码")
            submit = st.form_submit_button("登 录", type="primary", use_container_width=True)

        # 使用说明收在卡片内的折叠区，默认收起，保持卡片干净。
        # 这里的样式由 [data-testid="stExpander"] 命中（该挂钩已在包内自证存在），
        # 不依赖 st-key- 类——避免出现「写了规则但没给 key」的死选择器。
        with st.expander("ℹ️ 使用说明", expanded=False):
            st.markdown(
                "- 本系统为内网部署，登录后请勿在公共设备上保存密码。\n"
                "- 忘记密码请联系管理员在「用户管理」中重置。"
            )

    if submit:
        if not name.strip():
            st.error("请输入姓名")
        else:
            user = db.authenticate(name.strip(), password)
            if user:
                st.session_state.current_user_id = user["id"]
                st.session_state.logged_in = True
                # 刷新不掉线：签发令牌并安排下一轮写 Cookie——不能在本轮直接写：
                # 马上要 st.rerun()，iframe 组件会在 JS 执行前被卸载，Cookie 写不上
                st.session_state["_set_auth_cookie"] = auth.issue_token(user["id"])
                # 用户活跃埋点（D4，t20）：登录成功记一条 action='login'——mvp 是单页应用，
                # 用户停在一页不动时只看 page_view 会低估活跃度，登录事件兜底；
                # 失败只打日志，绝不阻塞登录（吸收客诉「埋点失败不抛异常」纪律）
                try:
                    db.log_user_activity(user["id"], user.get("name", ""),
                                         user.get("role", ""), "", "login")
                except Exception as e:
                    print(f"[activity] 登录埋点写入失败（不影响登录）：{e}")
                st.session_state["_flash"] = {"type": "success", "text": f"✅ 欢迎回来，{user['name']}"}
                st.rerun()
            else:
                st.error("姓名或密码错误，请重试。")




def main():
    st.set_page_config(
        page_title=APP_TITLE,
        page_icon="🛠️",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    # 隐藏 Streamlit 自带的顶部工具栏（Deploy 按钮 + ⋮ 菜单 + 运行状态指示）。
    # 该栏 position:fixed 悬浮在页顶，会遮挡业务内容；内部工具用不到 Deploy 入口。
    st.markdown(
        """
        <style>
            header[data-testid='stHeader'] { display: none; }
            div[data-testid='stToolbar'] { display: none; }
            div[data-testid='stStatusWidget'] { display: none; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    init_state()

    # 未登录：先尝试从 Cookie 还原登录态（浏览器刷新不掉线），还原不了再进登录页
    if not st.session_state.get("logged_in"):
        _try_restore_login_from_cookie()

    # 未登录：先显示登录页（登录页有专属样式，独立于三段式外壳的 _inject_global_css）
    if not st.session_state.get("logged_in"):
        _inject_login_css()
        # 退出登录的 Cookie 清除动作放在登录页这一轮完成（登出后马上 rerun，
        # 组件 iframe 会在 JS 执行前被卸载，写不进去——与登录侧同一节奏错开一轮）
        if st.session_state.pop("_clear_auth_cookie", False):
            _emit_auth_cookie_js(None)
        render_login_page()
        return

    # 登录成功后的 Cookie 写入动作：安排在 rerun 之后的这一轮执行（见 render_login_page 注释）
    _tok_to_set = st.session_state.pop("_set_auth_cookie", None)
    if _tok_to_set:
        _emit_auth_cookie_js(_tok_to_set)

    # 感知其它会话的写入（否则 A 提的需求 B 看不到）
    _sync_from_db()

    # ---- 页面字典与角色过滤（t31 起提前到侧边栏之前：导航需要它）----
    all_pages = {
        "📊 中心总览": page_overview,
        "📋 我的看板": page_my_kanban,
        "📂 项目视图": page_project_view,
        "📦 需求管理": page_management,
        "➕ 新建需求": page_new_requirement,
        "📄 需求详情": page_requirement_detail,
        "🔍 AI 搜索": page_search,
        "📅 周报": page_weekly,
        "📋 系统评估": page_system_eval,
        "👥 用户管理": page_user_management,
    }
    # 系统评估页：管理层 + 主管可见（主管用于推动组员填问卷、看趋势）。
    # 但页内的「逐人评价明细」仍只对管理层开放，见 _can_view_rating_detail。
    _cur = get_current_user()
    pages = dict(all_pages)
    if not _cur or _cur.get("role") not in ("管理层", "主管"):
        pages.pop("📋 系统评估", None)

    # ---- t31 导航迁移：解析当前页（含 _pending_nav 跳转消费）----
    selection = _resolve_nav(pages)

    # 用户活跃埋点（D4，t20；t31 随导航迁移，语义不变）：页面切换记一条 action='page_view'
    # （page_path=导航项中文名，与页面显示名一致、可读——与旧 radio 选中值同一取值空间）。
    # 用 session_state 记住「上次已记录过的页面」，同一页面值不重复写——
    # Streamlit 每次交互都会重跑脚本，不去重会把一次访问放大成几十条；
    # 失败只打日志，绝不阻塞页面渲染。
    try:
        if selection != st.session_state.get("_activity_last_page"):
            _me_act = get_current_user()
            if _me_act:
                db.log_user_activity(_me_act["id"], _me_act.get("name", ""),
                                     _me_act.get("role", ""), selection, "page_view")
            st.session_state["_activity_last_page"] = selection
    except Exception as e:
        print(f"[activity] 页面埋点写入失败（不影响渲染）：{e}")

    # 一次性汇总当前用户的全部待办提醒（避免对全量需求扫多遍）。
    # t31 起提前到侧边栏/Header 之前：待办总数供「我的看板」角标与 Header 铃铛使用；
    # 7 元组解包一行原样保留（仅调用位置上移）。
    pending, feedback, rejects, pool_owner, pool_requester, assignments, done_notices = _pending_notices_all(
        st.session_state.current_user_id
    )
    _todo_total = sum(len(x) for x in
                      (pending, feedback, rejects, pool_owner, pool_requester, assignments, done_notices))

    # ---- 三段式布局（t31）：全局样式 → 深色侧边栏（导航）→ 问卷弹窗 → 顶部 Header ----
    _inject_global_css()
    render_sidebar(pages, selection, _todo_total)

    # 系统使用体验问卷：按冷却期自动弹出（弹窗内部自行判断是否需要弹）
    _maybe_show_survey()

    # 跨页面 flash 消息（避免 st.rerun 刷掉 success/error）
    flash = st.session_state.pop("_flash", None)
    if flash:
        if flash.get("type") == "success":
            st.success(flash["text"])
        elif flash.get("type") == "error":
            st.error(flash["text"])
        elif flash.get("type") == "warning":
            st.warning(flash["text"])
        elif flash.get("type") == "info":
            st.info(flash["text"])

    # 顶部 Header（t31，取代原 st.title/st.caption 全局标题）：页标题 + 搜索 + 铃铛 + 用户胶囊
    _render_app_header(selection, _todo_total)

    # 建议责任人：待确认提醒
    if pending:
        n = len(pending)
        titles = "、".join(f"「{r['title']}」" for r in pending[:3])
        more = " 等" if n > 3 else ""
        st.warning(f"📬 您有 {n} 条需求待确认（您为指定责任人）：{titles}{more}。请及时处理。")
        if st.button("📋 前往「我的看板」确认", type="primary"):
            st.session_state["_pending_nav"] = True
            st.session_state["_pending_nav_target"] = "📋 我的看板"
            st.rerun()

    # 提出人：主管反馈/调整待确认提醒
    if feedback:
        n = len(feedback)
        titles = "、".join(f"「{r['title']}」" for r in feedback[:3])
        more = " 等" if n > 3 else ""
        st.warning(f"💬 主管对您提出的 {n} 条需求给出了反馈/调整建议：{titles}{more}。请确认是否采纳。")
        if st.button("📋 前往「我的看板」查看反馈", type="primary"):
            st.session_state["_pending_nav"] = True
            st.session_state["_pending_nav_target"] = "📋 我的看板"
            st.rerun()

    # 提出人：需求被负责人驳回
    if rejects:
        n = len(rejects)
        titles = "、".join(f"「{r['title']}」" for r in rejects[:3])
        more = " 等" if n > 3 else ""
        st.error(f"❌ 您提出的 {n} 条需求已被负责人驳回并终止：{titles}{more}。")
        if st.button("✅ 知道了", key="reject_ack_all"):
            me = st.session_state.current_user_id
            for r in rejects:
                r.setdefault("reject_seen_by", []).append(me)
                save_requirement(r)
            st.rerun()

    # 已完成提醒：同步给提出人和建议负责人
    if done_notices:
        n = len(done_notices)
        titles = "、".join(f"「{r['title']}」" for r in done_notices[:3])
        more = " 等" if n > 3 else ""
        st.success(f"🎉 您参与的需求已完成：{titles}{more}。")
        if st.button("✅ 知道了", key="done_ack_all"):
            me = st.session_state.current_user_id
            for r in done_notices:
                r.setdefault("completion_seen_by", []).append(me)
                save_requirement(r)
            st.rerun()

    # 提出人：已完成需求待评价（满意度，一期）
    _render_rating_notice(st.session_state.current_user_id)
    if st.session_state.get("_rating_req"):
        _rating_dialog(st.session_state["_rating_req"])

    # 改进闭环回告（三期，03 蓝图 §5.5）：站内提醒条与个人进展视图。
    # 渲染顺序：需求流转待办 → 已完成待评价 → 改进项进展回告 → 页面内容。
    # 站内可见性由「终态 + 未确认」决定，不依赖外发通道是否配置成功（队长 t11 硬要求）。
    _render_improvement_notice(st.session_state.current_user_id)
    _render_my_improvements(st.session_state.current_user_id)
    # 🌟 改进动态全员公示（F-05 裁决 A）：所有登录用户可见的已上线成果（只读，仅摘要+时间）
    _render_improvement_feed_public()

    # 负责人：需求进入需求池，提醒前往处理
    if pool_owner:
        n = len(pool_owner)
        titles = "、".join(f"「{r['title']}」" for r in pool_owner[:3])
        more = " 等" if n > 3 else ""
        st.info(f"📋 您有 {n} 条需求已进入需求池，请前往指派执行人处理：{titles}{more}。")
        if st.button("📋 前往「需求管理」处理", key="pool_owner_go"):
            me = st.session_state.current_user_id
            for r in pool_owner:
                r.setdefault("pool_seen_by", []).append(me)
                save_requirement(r)
            st.session_state["_pending_nav"] = True
            st.session_state["_pending_nav_target"] = "📦 需求管理"
            st.rerun()

    # 提出人：需求已被采纳
    if pool_requester:
        n = len(pool_requester)
        titles = "、".join(f"「{r['title']}」" for r in pool_requester[:3])
        more = " 等" if n > 3 else ""
        st.success(f"✅ 您提出的 {n} 条需求已被采纳，已进入需求池：{titles}{more}。")
        if st.button("✅ 知道了", key="pool_requester_ack"):
            me = st.session_state.current_user_id
            for r in pool_requester:
                r.setdefault("pool_seen_by", []).append(me)
                save_requirement(r)
            st.rerun()

    # 执行人：新分配的需求任务
    if assignments:
        n = len(assignments)
        titles = "、".join(f"「{r['title']}」" for r in assignments[:3])
        more = " 等" if n > 3 else ""
        st.info(f"📌 您有 {n} 条新分配的需求任务：{titles}{more}。请及时跟进。")
        if st.button("📋 前往「我的看板」查看", key="assign_go", type="primary"):
            me = st.session_state.current_user_id
            for r in assignments:
                r.setdefault("assign_seen_by", []).append(me)
                save_requirement(r)
            st.session_state["_pending_nav"] = True
            st.session_state["_pending_nav_target"] = "📋 我的看板"
            st.rerun()

    pages[selection]()




if __name__ == "__main__":
    main()
