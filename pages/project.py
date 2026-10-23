"""
pages/project.py — 从 app.py 拆分生成（函数体逐字保留）。
"""
from __future__ import annotations

# ---- 第三方 / 标准库 ----
from collections import Counter
from datetime import datetime
from sample_data import STATUS_COLORS, STATUS_LABELS
import db
import streamlit as st

# ---- 项目内部 ----
from config import PROJECT_GROUPS, TODAY
from services.cache import _invalidate_data_caches, _mark_own_write
from services.people import _owner_names
from services.projects import _group_projects, _project_display_name, _req_effective_group, get_projects
from services.requirements import get_requirements
from state import USER_ID_TO_NAME
from ui.components import priority_badge
from ui.layout import _nav_tabs



def page_project_view():
    st.markdown("## 📂 项目视图")
    st.caption(
        "项目负责人专用，按「关联项目」标签筛选 · 未关联小项目的需求按标题自动归入大项目"
        "（A/B/C → 对应大项目，否则含 AI → 研发AI赋能，都不是 → 其他）"
    )

    all_projects = get_projects()

    # 横向标签菜单（用户要求：参考客诉系统系统评估页的 Tab 样式）。
    # 原来的 st.radio（大项目视图 / 单项目视图）改为标签条，并新增「管理项目」独立入口——
    # 此前项目管理是拼在两个视图底部的一段（两个视图各自调用 _render_project_management），
    # 内容重复且不好找；现在提为同级标签，三个视图互斥、各管一摊。
    _mode = _nav_tabs(
        [("group", "🏢 大项目视图"), ("single", "📄 单项目视图"), ("manage", "⚙️ 管理项目")],
        state_key="proj_view_mode_tab",
    )

    if not all_projects and _mode != "manage":
        st.info("暂无项目，请切换到「⚙️ 管理项目」新建")
        return

    if _mode == "group":
        _render_group_view(all_projects)
    elif _mode == "single":
        _render_single_project_view(all_projects)
    else:
        _render_project_management()


def _render_project_management(selected_pid: str = "", p_info: dict | None = None, req_count: int = 0):
    """统一的项目管理面板：新建 / 修改名称 / 删除"""
    # 提交成功后清空表单：必须在控件实例化前清空，否则报「cannot be modified after widget is instantiated」
    if st.session_state.pop("_clear_new_proj_form", False):
        st.session_state["mgmt_new_name"] = ""
        st.session_state["mgmt_new_group"] = "（请选择）"  # 下拉框不能置空串，回到占位项
        st.session_state["mgmt_new_highlight"] = ""
    if st.session_state.pop("_clear_rename_proj_form", False) and selected_pid:
        st.session_state[f"mgmt_rename_name_{selected_pid}"] = ""
        # 所属大项目下拉框回到当前已保存值（不产生待保存变更）
        cur = ((p_info or {}).get("code") or "").strip()
        st.session_state[f"mgmt_rename_group_{selected_pid}"] = cur or "（未分组）"
        st.session_state[f"mgmt_rename_highlight_{selected_pid}"] = ""

    with st.expander("🛠 项目管理", expanded=False):

        # ---- 新建项目 ----
        st.markdown("##### ➕ 新建项目")
        with st.form("mgmt_new_project"):
            new_name = st.text_input("项目名称 *", placeholder="例如：XX型号全自动测试设备", key="mgmt_new_name")
            new_group = st.selectbox("所属大项目 *", options=["（请选择）"] + PROJECT_GROUPS, key="mgmt_new_group")
            new_highlight = st.text_input("核心亮点/定制需求（选填）", placeholder="例如：支持在线视觉检测", key="mgmt_new_highlight")
            if st.form_submit_button("✅ 创建项目", type="primary", use_container_width=True):
                if not new_name.strip():
                    st.error("项目名称不能为空")
                elif new_group == "（请选择）":
                    st.error("请选择所属大项目")
                else:
                    existing = get_projects()
                    if any(p["name"] == new_name.strip() for p in existing):
                        st.error(f"项目名称「{new_name.strip()}」已存在")
                    else:
                        seq = db.next_project_seq()
                        new_proj = {"id": f"P{seq:03d}", "name": new_name.strip(), "code": new_group, "highlight": new_highlight.strip()}
                        db.save_project(new_proj)
                        st.session_state.projects = db.load_all_projects()
                        _invalidate_data_caches()
                        _mark_own_write()
                        # 提交成功后清空表单（下次渲染前统一清）
                        st.session_state["_clear_new_proj_form"] = True
                        st.session_state["_flash"] = {"type": "success", "text": f"✅ 已创建项目：{new_proj['id']} 「{_project_display_name(new_proj)}」"}
                        st.rerun()

        # ---- 修改与删除（仅选中项目时显示） ----
        if selected_pid and p_info:
            st.markdown("---")
            st.markdown(f"##### ✏️ 修改「{_project_display_name(p_info)}」")

            # 修改名称 / 所属大项目 / 核心亮点（名称留空=不修改）
            with st.form("mgmt_rename_project"):
                rename_name = st.text_input("新项目名称", placeholder=f"当前：{p_info['name']}", key=f"mgmt_rename_name_{selected_pid}")
                cur_code = (p_info.get("code") or "").strip()
                group_options = ["（未分组）"] + list(PROJECT_GROUPS)
                if cur_code and cur_code not in group_options:
                    group_options.append(cur_code)  # 历史遗留大项目名，保留为选项避免保存时被悄悄改写
                rename_group = st.selectbox(
                    "所属大项目",
                    options=group_options,
                    index=group_options.index(cur_code) if cur_code else 0,
                    key=f"mgmt_rename_group_{selected_pid}",
                )
                rename_highlight = st.text_input("核心亮点/定制需求（选填）", value=p_info.get("highlight", ""), placeholder="例如：支持在线视觉检测", key=f"mgmt_rename_highlight_{selected_pid}")
                if st.form_submit_button("💾 保存修改", use_container_width=True):
                    new_name_val = rename_name.strip() if rename_name.strip() else p_info["name"]
                    # 检查重名（排除自身）
                    existing = get_projects()
                    name_conflict = new_name_val != p_info["name"] and any(p["name"] == new_name_val and p["id"] != selected_pid for p in existing)
                    if name_conflict:
                        st.error(f"项目名称「{new_name_val}」已被其他项目使用")
                    else:
                        p_info["name"] = new_name_val
                        p_info["code"] = "" if rename_group == "（未分组）" else rename_group
                        p_info["highlight"] = rename_highlight.strip()
                        db.save_project(p_info)
                        st.session_state.projects = db.load_all_projects()
                        _invalidate_data_caches()
                        _mark_own_write()
                        # 提交成功后清空表单（下次渲染前统一清）
                        st.session_state["_clear_rename_proj_form"] = True
                        st.session_state["_flash"] = {"type": "success", "text": f"✅ 已更新项目「{_project_display_name(p_info)}」"}
                        st.rerun()

            # 删除项目
            st.markdown("---")
            st.markdown("##### 🗑 删除项目")
            st.warning(f"⚠ 确定要删除项目「{_project_display_name(p_info)}」吗？")
            st.caption(f"该项目关联 {req_count} 条需求，删除后这些需求的「关联项目」将变为空。此操作不可恢复。")
            confirm_del = st.checkbox("我确认删除此项目", key=f"mgmt_confirm_del_{selected_pid}")
            if st.button("🗑 确认删除", type="secondary", disabled=not confirm_del, key=f"mgmt_del_{selected_pid}"):
                db.delete_project(selected_pid)
                st.session_state.projects = db.load_all_projects()
                _invalidate_data_caches()
                _mark_own_write()
                st.session_state["_pending_proj_reset"] = True
                st.session_state["_flash"] = {"type": "success", "text": f"✅ 已删除项目「{_project_display_name(p_info)}」"}
                st.rerun()


def _render_project_dashboard(reqs, heading: str):
    """渲染一个项目（或一个大项目聚合）的看板：指标 + 风险 + 状态分组 + 执行人统计。"""
    st.markdown("---")
    st.markdown(f"### {heading}")
    st.caption(f"共 {len(reqs)} 条需求")

    # 关键指标
    active = [r for r in reqs if r.get("status") in ("需求池", "待启动", "进行中", "暂停")]
    completed = [r for r in reqs if r.get("status") == "已完成"]
    terminated = [r for r in reqs if r.get("status") == "已终止"]
    unassigned = [r for r in reqs if r.get("status") == "需求池" and not r.get("owner_ids")]
    overdue = []
    for r in reqs:
        if r.get("requested_due_date") and r.get("status") not in ("已完成", "已终止"):
            try:
                d = datetime.strptime(r["requested_due_date"], "%Y-%m-%d").date()
                if d < TODAY:
                    overdue.append(r)
            except Exception:
                pass

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("需求总数", len(reqs))
    col2.metric("在跑", len(active))
    col3.metric("已完成", len(completed))
    col4.metric("已终止", len(terminated))
    col5.metric("未指派", len(unassigned))

    # 风险提醒
    if overdue:
        st.error(f"⚠ {len(overdue)} 条已超期：{', '.join(r['id'] for r in overdue[:5])}")
    if unassigned:
        st.warning(f"⚠ {len(unassigned)} 条未指派执行人：{', '.join(r['id'] for r in unassigned[:5])}")

    # 按状态分组展示
    st.markdown("---")
    st.markdown("### 按状态分组")
    status_groups = {s: [] for s in STATUS_LABELS}
    for r in reqs:
        s = r.get("status", "需求池")
        if s in status_groups:
            status_groups[s].append(r)

    cols = st.columns(len(STATUS_LABELS))
    for col, (status, items) in zip(cols, status_groups.items()):
        with col:
            st.markdown(
                f'<div style="background:{STATUS_COLORS[status]}11;padding:6px;border-radius:6px 6px 0 0;border-left:3px solid {STATUS_COLORS[status]};">'
                f'<span style="font-weight:600;color:{STATUS_COLORS[status]};font-size:12px;">{status}</span>'
                f'<span style="float:right;color:#777;font-size:11px;">{len(items)}</span></div>',
                unsafe_allow_html=True,
            )
            for r in items[:8]:
                owner = _owner_names(r)
                due = r.get("requested_due_date", "—")
                overdue_mark = ""
                if due != "—":
                    try:
                        dd = datetime.strptime(due, "%Y-%m-%d").date()
                        if dd < TODAY and r.get("status") not in ("已完成", "已终止"):
                            overdue_mark = ' <span style="color:#E53935;">⚠</span>'
                    except Exception:
                        pass
                st.markdown(
                    f'<div style="background:white;border:1px solid #EEE;padding:6px;margin:4px 0;font-size:11px;">'
                    f'{priority_badge(r.get("priority", "未知"))}'
                    f' <b>{r["id"]}</b>{overdue_mark}'
                    f'<div style="margin-top:3px;">{r["title"][:25]}</div>'
                    f'<div style="color:#666;font-size:10px;margin-top:2px;">👤 {owner} · 📅 {due}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            if len(items) > 8:
                st.caption(f"...还有 {len(items)-8} 条")

    # 按执行人统计
    st.markdown("---")
    st.markdown("### 按执行人统计")
    owner_counts = Counter()
    for r in reqs:
        ids = [i for i in r.get("owner_ids", []) if i]
        if ids:
            for i in ids:
                owner_counts[USER_ID_TO_NAME.get(i, "未知")] += 1
        else:
            owner_counts["未指派"] += 1
    for name, cnt in owner_counts.most_common():
        st.markdown(
            f'<div><span style="display:inline-block;width:80px;">{name}</span>'
            f'<span style="display:inline-block;background:#1E88E5;height:12px;width:{cnt*15}px;border-radius:2px;vertical-align:middle;margin:0 6px;"></span>'
            f'<b>{cnt}</b></div>',
            unsafe_allow_html=True,
        )


def _render_group_view(all_projects):
    """大项目视图：A/B/C/D 概览卡片 + 选择一个大项目看聚合看板（点击卡片下方按钮即自动选中）。"""
    groups = _group_projects(all_projects)
    # 需求 → 大项目 归类：关联小项目沿用其归属；未关联的按标题自动归类（A/B/C/AI→其他），
    # 保证每条需求在大项目视图都有落点（此前未关联的需求会从视图里凭空消失）
    pid_to_code = {p["id"]: (p.get("code") or "").strip() for p in all_projects}
    reqs_by_group: dict[str, list[dict]] = {}
    for r in get_requirements():
        reqs_by_group.setdefault(_req_effective_group(r, pid_to_code), []).append(r)
    for extra in reqs_by_group:  # 孤儿需求（关联的小项目已被删）兜底出「未分组」卡片
        groups.setdefault(extra, [])
    group_labels = list(groups.keys())

    # 卡片点击写入的选择值可能因分组变化而失效（如项目被删），兜底回第一个
    if st.session_state.get("proj_group_sel") not in group_labels:
        st.session_state["proj_group_sel"] = group_labels[0]

    # 大项目卡片网格：每行最多 4 张，超出自动换行（大项目多时每张仍保有可读宽度）
    CARDS_PER_ROW = 4
    st.markdown("### 大项目概览")
    for row_start in range(0, len(group_labels), CARDS_PER_ROW):
        row_groups = group_labels[row_start:row_start + CARDS_PER_ROW]
        cols = st.columns(len(row_groups))
        for col, g in zip(cols, row_groups):
            gp_reqs = reqs_by_group.get(g, [])
            active = sum(1 for r in gp_reqs if r.get("status") in ("需求池", "待启动", "进行中", "暂停"))
            done = sum(1 for r in gp_reqs if r.get("status") == "已完成")
            # 当前选中的大项目卡片高亮显示
            selected = st.session_state.get("proj_group_sel") == g
            border_color = "#1E88E5" if selected else "#E0E0E0"
            bg_color = "#E3F2FD" if selected else "#FFFFFF"
            with col:
                st.markdown(
                    f'<div style="border:2px solid {border_color};background:{bg_color};'
                    f'border-radius:8px;padding:10px;text-align:center;">'
                    f'<div style="font-size:18px;font-weight:700;color:#1F4E79;">{g}</div>'
                    f'<div style="color:#666;font-size:12px;margin-top:4px;">{len(groups[g])} 个小项目</div>'
                    f'<div style="color:#555;font-size:12px;margin-top:2px;">需求 {len(gp_reqs)} · 在跑 {active} · 完成 {done}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                if st.button(
                    "🔍 查看详情",
                    key=f"group_card_{g}",
                    use_container_width=True,
                    type="primary" if selected else "secondary",
                    help=f"查看大项目「{g}」的聚合看板",
                ):
                    st.session_state["proj_group_sel"] = g
                    st.rerun()

    st.markdown("---")
    st.caption("💡 点击上方卡片中的「🔍 查看详情」切换下方看板")
    # 选中项由卡片点击控制（原下拉框已按需求隐藏）
    sel_group = st.session_state["proj_group_sel"]
    gp_reqs = reqs_by_group.get(sel_group, [])
    _render_project_dashboard(gp_reqs, f"{sel_group}（{len(groups[sel_group])} 个小项目）")


def _render_single_project_view(all_projects):
    """单项目视图：「选择项目」仅列出当前大项目下的小项目（大项目在「大项目视图」中选定）。"""
    groups = _group_projects(all_projects)
    group_labels = list(groups.keys())
    # 兜底：大项目被删/未选过时回第一个
    if st.session_state.get("proj_group_sel") not in group_labels:
        st.session_state["proj_group_sel"] = group_labels[0]
    sel_group = st.session_state["proj_group_sel"]
    group_projects = groups[sel_group]

    st.caption(f"当前大项目：**{sel_group}**（在「大项目视图」中点击卡片切换）")

    # 固定大项目恒在卡片列表中，可能暂无小项目
    if not group_projects:
        st.info(f"大项目「{sel_group}」下暂无小项目，请到「⚙️ 管理项目」新建。")
        return

    project_options = {p["id"]: _project_display_name(p) for p in group_projects}
    # 删除项目后重置 selectbox
    if st.session_state.pop("_pending_proj_reset", False):
        st.session_state.proj_view_pid = list(project_options.keys())[0]
    # 切换大项目后，原选中的小项目可能不在当前大项目下，兜底回第一个
    if st.session_state.get("proj_view_pid") not in project_options:
        st.session_state.proj_view_pid = list(project_options.keys())[0]
    selected_pid = st.selectbox(
        "选择项目（仅当前大项目）",
        options=list(project_options.keys()),
        format_func=lambda x: project_options.get(x, x),
        key="proj_view_pid",
    )
    if not selected_pid:
        return

    project_reqs = [r for r in get_requirements() if r.get("project_id") == selected_pid]
    p_info = next((p for p in all_projects if p["id"] == selected_pid), None)
    if not p_info:
        st.warning("项目不存在")
        return

    heading = _project_display_name(p_info)
    if (p_info.get("code") or "").strip():
        heading += f" · 大项目 {(p_info.get('code') or '').strip()}"
    _render_project_dashboard(project_reqs, heading)
