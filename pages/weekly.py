"""
pages/weekly.py — 从 app.py 拆分生成（函数体逐字保留）。
"""
from __future__ import annotations

# ---- 第三方 / 标准库 ----
from datetime import timedelta
import ai_helper
import streamlit as st

# ---- 项目内部 ----
from config import MONTH_START_STR, TODAY, TODAY_STR, WEEK_START_STR
from services.people import _user_load_map
from services.requirements import get_requirements
from state import USERS, USER_ID_TO_NAME
from ui.components import priority_badge



def page_weekly():
    st.markdown("## 📅 周报自动生成（A6）")
    st.caption("个人简报与中心简报")

    tab_personal, tab_center = st.tabs(["👤 个人简报", "🏢 中心简报"])

    with tab_personal:
        _render_personal_briefing()

    with tab_center:
        _render_center_briefing()


def _render_personal_briefing():
    """A6 个人简报（原 page_weekly 内容）"""
    st.markdown("### 👤 个人简报")

    user_options = {u["id"]: f'{u["name"]}（{u["role"]}）' for u in USERS}
    selected = st.selectbox("选择用户", options=list(user_options.keys()), format_func=lambda x: user_options[x])
    user_name = USER_ID_TO_NAME.get(selected, "")

    # 计算本周统计
    week_ago = WEEK_START_STR
    user_reqs = [r for r in get_requirements() if selected in r.get("owner_ids", [])]

    new_count = sum(1 for r in user_reqs if r.get("created_at", "") >= week_ago)
    due_count = sum(1 for r in user_reqs if r.get("requested_due_date", "") >= TODAY_STR and r.get("requested_due_date", "") <= TODAY_STR + timedelta(days=7).__str__()[0:10])
    due_count = sum(1 for r in user_reqs if (TODAY.strftime("%Y-%m-%d") <= r.get("requested_due_date", "9999") <= (TODAY + timedelta(days=7)).strftime("%Y-%m-%d")))
    done_count = sum(1 for r in user_reqs if r.get("actual_completion_date", "") >= week_ago)
    pending_count = due_count - done_count if due_count > done_count else 0
    blocked_count = sum(1 for r in user_reqs if r.get("status") == "暂停")
    completed_count = done_count
    active_count = sum(1 for r in user_reqs if r.get("status") in ["待启动", "进行中", "暂停"])
    high_pri_list = [r["id"] for r in user_reqs if r.get("priority") in ["P0", "P1"] and r.get("status") not in ["已完成", "已终止"]]

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("本周新增到我名下", new_count)
    with col2:
        st.metric("本周截止", due_count)
    with col3:
        st.metric("阻塞超 3 天", blocked_count)
    with col4:
        st.metric("当前在跑", active_count)

    st.markdown("---")
    if st.button("🤖 AI 生成简报提示", type="primary"):
        stats = {
            "new_count": new_count,
            "due_count": due_count,
            "done_count": done_count,
            "pending_count": pending_count,
            "blocked_count": blocked_count,
            "completed_count": completed_count,
            "active_count": active_count,
        }
        with st.spinner("AI 正在生成简报…"):
            result = ai_helper.generate_weekly_briefing(
                user_name=user_name,
                stats=stats,
                high_pri_list=high_pri_list,
                provider=st.session_state.provider,
                api_key=st.session_state.api_key,
                model=st.session_state.model or None,
            )
        if result.success:
            st.session_state["last_briefing"] = {
                "tips": result.data,
                "latency_ms": result.latency_ms,
            }
        else:
            st.session_state["last_briefing"] = None
            st.error(f"❌ 生成失败：{result.error}")

    last = st.session_state.get("last_briefing")
    if last:
        st.markdown("#### AI 简报提示")
        for tip in last.get("tips", []):
            st.markdown(f"- {tip}")
        st.caption(f"耗时 {last['latency_ms']}ms")

    # 高优先级未完成列表
    st.markdown("---")
    st.markdown("#### 高优先级未完成需求")
    if high_pri_list:
        for r in user_reqs:
            if r["id"] in high_pri_list:
                st.markdown(
                    f'- {priority_badge(r.get("priority",""))} {r["id"]} 「{r["title"]}」',
                    unsafe_allow_html=True,
                )
    else:
        st.info("无")


def _render_center_briefing():
    """A6 中心简报（v0.2.4 新增，对应 PRD §F2.3 与 A6）"""
    st.markdown("### 🏢 中心简报")
    st.caption(f"数据截至 {TODAY_STR}")

    reqs = get_requirements()
    week_ago = WEEK_START_STR
    month_ago = MONTH_START_STR

    # 本周统计
    new_this_week = [r for r in reqs if r.get("created_at", "") >= week_ago]
    completed_this_week = [r for r in reqs if r.get("actual_completion_date", "") >= week_ago and r.get("status") == "已完成"]
    terminated_this_week = [r for r in reqs if r.get("actual_completion_date", "") >= week_ago and r.get("status") == "已终止"]

    col1, col2, col3 = st.columns(3)
    col1.metric("本周新增", len(new_this_week))
    col2.metric("本周完成", len(completed_this_week))
    col3.metric("本周终止", len(terminated_this_week))

    # 部门分布变化（本月 vs 上月）
    st.markdown("---")
    st.markdown("#### 按部门分布（本月新增）")
    from collections import Counter
    month_new = [r for r in reqs if r.get("created_at", "") >= month_ago]
    source_cnt = Counter(r.get("source", "其他") for r in month_new)
    total_month = max(len(month_new), 1)
    for src, cnt in source_cnt.most_common():
        pct = cnt / total_month * 100
        color = "#E53935" if src == "客服" and pct > 30 else "#1E88E5"
        st.markdown(
            f'<div style="margin-bottom:6px;"><span style="display:inline-block;width:120px;">{src}</span>'
            f'<span style="display:inline-block;background:{color};height:14px;width:{pct*3:.0f}px;border-radius:3px;vertical-align:middle;margin:0 8px;"></span>'
            f'<b>{cnt}</b> ({pct:.0f}%)</div>',
            unsafe_allow_html=True,
        )

    # 高负载工程师 Top 3
    st.markdown("---")
    st.markdown("#### 高负载工程师 Top 3（在跑需求工作量）")
    engineers = [u for u in USERS if u["role"] in ("工程师", "项目经理")]
    load_map = _user_load_map()
    eng_loads = sorted([(u["name"], load_map.get(u["id"], 0.0)) for u in engineers], key=lambda x: -x[1])[:3]
    for i, (name, load) in enumerate(eng_loads, 1):
        medal = ["🥇", "🥈", "🥉"][i-1]
        color = "#E53935" if load > 15 else "#FB8C00" if load > 8 else "#43A047"
        st.markdown(
            f'<div style="padding:8px;background:white;border-left:4px solid {color};margin:6px 0;border-radius:4px;">'
            f'{medal} <b>{name}</b> &nbsp; <span style="color:{color};font-weight:600;">{load:.1f} 人天</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # AI 一句话提示
    st.markdown("---")
    st.markdown("#### AI 一句话提示")
    if st.button("🤖 生成中心简报提示", type="primary", key="gen_center_tip"):
        active_count = sum(1 for r in reqs if r.get("status") in ("待启动", "进行中", "暂停"))
        stats = {
            "new_count": len(new_this_week),
            "completed_count": len(completed_this_week),
            "terminated_count": len(terminated_this_week),
            "active_count": active_count,
            "source_top": source_cnt.most_common(1)[0] if source_cnt else ("—", 0),
        }
        with st.spinner("AI 生成中…"):
            result = ai_helper.generate_weekly_briefing(
                user_name="研发中心",
                stats={
                    "new_count": stats["new_count"],
                    "completed_count": stats["completed_count"],
                    "active_count": stats["active_count"],
                    "due_count": 0,
                    "done_count": stats["completed_count"],
                    "pending_count": 0,
                    "blocked_count": sum(1 for r in reqs if r.get("status") == "暂停"),
                },
                high_pri_list=[r["id"] for r in reqs if r.get("priority") in ("P0", "P1") and r.get("status") not in ("已完成", "已终止")],
                provider=st.session_state.provider,
                api_key=st.session_state.api_key,
                model=st.session_state.model or None,
            )
        if result.success:
            for tip in (result.data or []):
                st.markdown(f"- {tip}")
            st.caption(f"耗时 {result.latency_ms}ms")
        else:
            st.error(f"❌ 生成失败：{result.error}")
            st.info("提示：仍可参考上方数据自行判断。")
