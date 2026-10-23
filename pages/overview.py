"""
pages/overview.py — 从 app.py 拆分生成（函数体逐字保留）。
"""
from __future__ import annotations

# ---- 第三方 / 标准库 ----
from collections import Counter
from datetime import datetime
from sample_data import STATUS_COLORS, STATUS_LABELS
import streamlit as st

# ---- 项目内部 ----
from config import MONTH_START_STR, SATISFACTION_COLORS, SATISFACTION_LABELS, TODAY, TODAY_STR
from services.evaluation import satisfaction_stats
from services.people import _owner_names, _user_task_count_map
from services.requirements import get_requirements
from state import USERS
from ui.components import _card, _page_header, _render_bar_chart, priority_badge, render_priority_quadrant



def page_overview():
    # t31 卡片化：统一页头 + KPI（st.metric 由全局 CSS 卡片化）+ 各区块白卡片；业务逻辑零改动
    reqs = get_requirements()
    _page_header("📊 中心总览", f"数据截至 {TODAY_STR} · 共 {len(reqs)} 条需求",
                 "➕ 新建需求", "➕ 新建需求")

    col1, col2, col3, col4, col5 = st.columns(5)
    status_count = Counter(r["status"] for r in reqs)
    with col1:
        st.metric("在跑需求", sum(status_count.get(s, 0) for s in ["待启动", "进行中", "暂停"]))
    with col2:
        st.metric("本月新增", sum(1 for r in reqs if r.get("created_at", "") >= MONTH_START_STR))
    with col3:
        st.metric("本月完成/终止", sum(1 for r in reqs if r.get("status") in ["已完成", "已终止"] and r.get("actual_completion_date", "") >= MONTH_START_STR))
    with col4:
        st.metric("暂停中", status_count.get("暂停", 0))
    with col5:
        st.metric("P0/P1", sum(1 for r in reqs if r.get("priority") in ["P0", "P1"] and r.get("status") not in ["已完成", "已终止"]))

    with _card("📋 需求状态分布", "按状态分组 · 悬停查看需求 · 点击「查看」跳转需求管理"):
        # 注入 hover CSS（各状态卡片启用悬停浮层）
        st.markdown("""
        <style>
        .hover-status-card { position: relative; cursor: pointer; }
        .hover-status-card .req-panel {
            display: none;
            position: absolute;
            top: 100%;
            left: 50%;
            transform: translateX(-50%);
            z-index: 9999;
            width: 300px;
            max-height: 360px;
            overflow-y: auto;
            background: #ffffff;
            border: 1px solid #e0e0e0;
            border-radius: 8px;
            box-shadow: 0 6px 20px rgba(0,0,0,.18);
            padding: 6px;
            text-align: left;
            font-weight: normal;
        }
        .hover-status-card:hover .req-panel { display: block; }
        .hover-status-card .req-item {
            border-bottom: 1px solid #f0f0f0;
            padding: 6px 4px;
        }
        .hover-status-card .req-item:last-child { border-bottom: none; }
        </style>
        """, unsafe_allow_html=True)

        # 构建状态卡片（有需求的状态带 hover 面板），每卡片下方带跳转按钮
        status_items = {status: [] for status in STATUS_LABELS}
        for r in reqs:
            status_items.setdefault(r.get("status"), []).append(r)
        status_cols = st.columns(len(STATUS_LABELS))
        for i, status in enumerate(STATUS_LABELS):
            with status_cols[i]:
                items = status_items.get(status, [])
                color = STATUS_COLORS.get(status, "#9E9E9E")
                panel_html = ""
                if items:
                    rows = []
                    for r in items:
                        owner = _owner_names(r)
                        due = r.get("requested_due_date") or "无截止日"
                        rows.append(
                            f'<div class="req-item">'
                            f'<b>{r["id"]}</b> {priority_badge(r.get("priority", "未知"))}'
                            f'<div style="font-size:12px;margin-top:2px;">{r["title"]}</div>'
                            f'<div style="font-size:11px;color:#666;margin-top:2px;">👤 {owner} · 📅 {due}</div>'
                            f'</div>'
                        )
                    panel_html = f'<div class="req-panel">{"".join(rows)}</div>'
                st.markdown(
                    f'<div class="hover-status-card" style="text-align:center;padding:8px;border-radius:8px;background:{color}22;">'
                    f'<div style="font-size:24px;font-weight:700;color:{color};">{status_count.get(status, 0)}</div>'
                    f'<div style="font-size:12px;color:#555;">{status}</div>'
                    f'{panel_html}'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                if st.button("🔍 查看", key=f"status_jump_{status}", use_container_width=True,
                             help=f"跳转到需求管理并筛选「{status}」"):
                    st.session_state["_pending_status_filter"] = status
                    st.session_state["_pending_nav"] = True
                    st.session_state["_pending_nav_target"] = "📦 需求管理"
                    st.rerun()

    left, right = st.columns(2)

    with left:
        with _card("🏢 按部门分布"):
            source_count = Counter(r["source"] for r in reqs)
            for src, cnt in source_count.most_common():
                pct = cnt / max(len(reqs), 1) * 100
                st.markdown(
                    f'<div style="margin-bottom:6px;"><span style="display:inline-block;width:120px;">{src}</span>'
                    f'<span style="display:inline-block;background:#1E88E5;height:14px;width:{pct*3:.0f}px;border-radius:3px;vertical-align:middle;margin:0 8px;"></span>'
                    f'<span style="font-weight:600;">{cnt}</span></div>',
                    unsafe_allow_html=True,
                )

    with right:
        with _card("👷 工程师负载", "在跑任务数量"):
            engineers = [u for u in USERS if u["role"] == "工程师"]
            task_count_map = _user_task_count_map()
            eng_loads = [(u["name"], task_count_map.get(u["id"], 0)) for u in engineers]
            eng_loads.sort(key=lambda x: -x[1])
            max_load = max((l for _, l in eng_loads), default=1)
            # 全部行拼进一个滚动容器（超出高度出滚动条，不足则无滚动条），
            # 同时把原本「每人一个 st.markdown」合并为单个元素，减少组件数
            load_rows = []
            for name, load in eng_loads:
                pct = load / max(max_load, 0.1) * 100
                color = "#E53935" if load > 8 else "#FB8C00" if load > 4 else "#43A047"
                load_rows.append(
                    f'<div style="margin-bottom:6px;"><span style="display:inline-block;width:80px;">{name}</span>'
                    f'<span style="display:inline-block;background:{color};height:14px;width:{max(pct*2,2):.0f}px;border-radius:3px;vertical-align:middle;margin:0 8px;"></span>'
                    f'<span style="font-weight:600;">{load}</span></div>'
                )
            st.markdown(
                f'<div style="max-height:240px;overflow-y:auto;padding-right:6px;">{"".join(load_rows)}</div>',
                unsafe_allow_html=True,
            )

    # ===== 需求评价（一期评价体系）=====
    with _card("⭐ 需求评价", "仅已完成需求 · 提出人/建议责任人/执行人各评一次"):
        sat = satisfaction_stats(reqs)
        st.caption("口径：只统计「已完成」需求上的评价；提出人 / 建议责任人 / 执行人各评一次，按评价条目计。")
        if sat["done"] == 0:
            st.info("暂无已完成需求，评价数据将在需求交付后产生。")
        else:
            s1, s2, s3, s4 = st.columns(4)
            s1.metric("平均分", f"{sat['avg']:.2f} / 5" if sat["rated"] else "—",
                      f"满意率 {sat['positive_rate']:.0f}%" if sat["rated"] else None)
            s2.metric("已评价 / 应评价", f"{sat['rated']} / {sat['expected']}")
            s3.metric("待评价", sat["missing"])
            s4.metric("已完成需求", sat["done"],
                      f"其中 {sat['unrated_reqs']} 条无人评价" if sat["unrated_reqs"] else None)
            if sat["rated"]:
                _render_bar_chart(
                    [(f"{k} 星 {SATISFACTION_LABELS.get(k, '')}", sat["dist"].get(k, 0),
                      SATISFACTION_COLORS.get(k, "#9E9E9E")) for k in [5, 4, 3, 2, 1]],
                    total=max(sat["rated"], 1),
                )
                # 按角色分组：提出人视角 vs 执行人视角的差异本身有洞察价值
                role_rows = []
                for role in ("提出人", "建议责任人", "执行人"):
                    vals = sat["by_role"].get(role)
                    if vals:
                        role_rows.append((f"{role}（{len(vals)} 条）", sum(vals) / len(vals)))
                if role_rows:
                    st.markdown("**按角色平均分**")
                    _render_bar_chart(
                        [(label, round(avg * 10), "#7E57C2") for label, avg in role_rows],
                        total=50,
                        show_value=lambda i: f"{role_rows[i][1]:.2f}",
                    )
            else:
                st.caption("已完成需求尚无评价记录。")

    with _card("⚠️ 风险提醒", "基于规则自动扫描"):
        risks = []
        for r in reqs:
            if r.get("status") == "暂停":
                risks.append(f'{r["id"]} 「{r["title"]}」 处于暂停状态')
            if r.get("priority") in ["P0", "P1"] and r.get("status") in ["待启动", "需求池"]:
                risks.append(f'{r["id"]} 「{r["title"]}」 高优先级但未启动')
            if r.get("requested_due_date") and r.get("status") not in ["已完成", "已终止"]:
                try:
                    due = datetime.strptime(r["requested_due_date"], "%Y-%m-%d").date()
                    if due < TODAY:
                        overdue_days = (TODAY - due).days
                        risks.append(f'{r["id"]} 「{r["title"]}」 已超期 {overdue_days} 天')
                except Exception:
                    pass
        if risks:
            for risk in risks[:10]:
                st.markdown(f"- {risk}")
        else:
            st.info("暂无风险")

    # ===== 紧急度 × 重要度 四象限 =====
    with _card():
        render_priority_quadrant()
