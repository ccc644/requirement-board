"""
pages/my_kanban.py — 从 app.py 拆分生成（函数体逐字保留）。
"""
from __future__ import annotations

# ---- 第三方 / 标准库 ----
from datetime import datetime
from sample_data import STATUS_COLORS
import streamlit as st

# ---- 项目内部 ----
from config import TODAY
from services.requirements import get_requirements
from state import USER_ID_TO_NAME
from ui.components import _card, _page_header, priority_badge, render_priority_quadrant



def page_my_kanban():
    # t31 卡片化：统一页头 + 看板列/四象限白卡片；业务逻辑零改动
    user_id = st.session_state.current_user_id
    user_name = USER_ID_TO_NAME.get(user_id, "未知")

    my_reqs = [
        r for r in get_requirements()
        if user_id in r.get("owner_ids", [])
        or r.get("requester_id") == user_id
        or r.get("requested_owner_id") == user_id
    ]

    _page_header(f"📋 我的看板 · {user_name}", f"与您相关的需求共 {len(my_reqs)} 条",
                 "➕ 新建需求", "➕ 新建需求")

    if not my_reqs:
        st.info("您当前没有关联的需求")
        return

    status_groups = {
        "待确认": [],
        "需求池": [],
        "待启动": [],
        "进行中": [],
        "暂停": [],
        "已完成": [],
        "已终止": [],
    }
    for r in my_reqs:
        s = r.get("status")
        if s in status_groups:
            status_groups[s].append(r)

    with _card("📋 我的需求", "按状态分列 · 点击卡片下方「查看」进入详情"):
        cols = st.columns(len(status_groups))
        for col, (status, items) in zip(cols, status_groups.items()):
            with col:
                st.markdown(
                    f'<div style="background:{STATUS_COLORS[status]}11;padding:8px;border-radius:8px 8px 0 0;border-left:4px solid {STATUS_COLORS[status]};">'
                    f'<span style="font-weight:600;color:{STATUS_COLORS[status]};">{status}</span>'
                    f'<span style="float:right;color:#777;">{len(items)}</span></div>',
                    unsafe_allow_html=True,
                )
                with st.container(height=520):
                    for r in items:
                        card_html = (
                            f'<div style="background:white;border:1px solid #ECEFF1;border-radius:6px;padding:10px;margin:6px 0;">'
                            f'<div style="font-size:11px;color:#888;margin-bottom:4px;">{r["id"]}</div>'
                            f'<div style="font-weight:600;font-size:13px;margin-bottom:6px;">{r["title"]}</div>'
                            f'<div>{priority_badge(r.get("priority","未知"))}</div>'
                            f'<div style="margin-top:6px;font-size:11px;color:#666;">'
                        )
                        if r.get("requested_due_date"):
                            try:
                                due = datetime.strptime(r["requested_due_date"], "%Y-%m-%d").date()
                                days_left = (due - TODAY).days
                                status = r.get("status", "")
                                if status in ("已完成", "已终止"):
                                    card_html += f'⏰ {r["requested_due_date"]}'
                                elif days_left < 0:
                                    card_html += f'⏰ {r["requested_due_date"]}（<span style="color:#E53935;font-weight:600;">逾期 {-days_left} 天</span>）'
                                else:
                                    card_html += f'⏰ {r["requested_due_date"]}（剩 {days_left} 天）'
                            except Exception:
                                card_html += f'⏰ {r["requested_due_date"]}'
                        card_html += '</div>'
                        # 子需求进度摘要（多执行人需求自动拆分后展示）
                        my_subs = r.get("sub_requirements") or []
                        if my_subs:
                            my_done = sum(1 for s in my_subs if s.get("status") in ("已完成", "已终止"))
                            card_html += (
                                f'<div style="margin-top:4px;font-size:11px;color:#1565C0;">'
                                f'🧩 子需求完成 {my_done}/{len(my_subs)}</div>'
                            )
                        card_html += '</div>'
                        st.markdown(card_html, unsafe_allow_html=True)
                        if st.button("查看", key=f"card_{r['id']}", use_container_width=True):
                            st.session_state["selected_req_id"] = r["id"]
                            st.session_state["_pending_nav"] = True
                            st.session_state["_pending_nav_target"] = "📄 需求详情"
                            st.rerun()

    # 我的看板四象限（仅当前用户相关需求）
    with _card():
        render_priority_quadrant(my_reqs, render_matrix=False)
