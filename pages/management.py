"""
pages/management.py — 从 app.py 拆分生成（函数体逐字保留）。
"""
from __future__ import annotations

# ---- 第三方 / 标准库 ----
from datetime import datetime
from sample_data import RATING_COLORS, SOURCE_LABELS, STATUS_LABELS
import streamlit as st

# ---- 项目内部 ----
from config import TODAY, TODAY_STR
from services.excel import EXCEL_COLUMN_MAP, _cached_excel_parse, _req_to_excel_row
from services.people import _owner_names
from services.projects import _project_display_name, _req_project_name, get_projects
from services.requirements import gen_req_id, get_requirements, save_requirement
from state import USERS, USER_ID_TO_NAME
from ui.components import priority_badge, status_badge



def page_management():
    st.markdown("## 📦 需求管理")
    st.caption("全需求列表 + Excel 导入/导出")

    tab_list, tab_import, tab_export = st.tabs(["📋 需求列表", "📥 Excel 导入", "📤 Excel 导出"])

    # ===== 列表 Tab =====
    with tab_list:
        reqs = get_requirements()
        st.markdown(f"**共 {len(reqs)} 条需求**")

        # 筛选
        with st.expander("🔍 筛选", expanded=False):
            f_col1, f_col2, f_col3, f_col4 = st.columns(4)
            with f_col1:
                # 从中心总览状态分布跳转过来时，预设状态筛选
                if "_pending_status_filter" in st.session_state:
                    st.session_state["mgmt_f_status"] = [st.session_state.pop("_pending_status_filter")]
                f_status = st.multiselect("状态", options=STATUS_LABELS, default=[], key="mgmt_f_status")
            with f_col2:
                f_source = st.multiselect("部门", options=SOURCE_LABELS, default=[])
            with f_col3:
                f_priority = st.multiselect("优先级", options=["P0", "P1", "P2", "P3"], default=[])
            with f_col4:
                f_owner = st.multiselect("执行人", options=[u["name"] for u in USERS], default=[])

            f_col5, f_col6 = st.columns([1, 2])
            with f_col5:
                f_project = st.multiselect("关联项目", options=[_project_display_name(p) for p in get_projects()], default=[], key="mgmt_f_project")
            with f_col6:
                f_keyword = st.text_input("关键词（标题/描述）", "")

        # 应用筛选
        filtered = []
        for r in reqs:
            if f_status and r.get("status") not in f_status: continue
            if f_source and r.get("source") not in f_source: continue
            if f_priority and r.get("priority") not in f_priority: continue
            if f_owner:
                owner_names = {USER_ID_TO_NAME.get(i, "") for i in r.get("owner_ids", []) if i}
                if not (owner_names & set(f_owner)): continue
            if f_project and _req_project_name(r) not in f_project: continue
            if f_keyword.strip():
                kw = f_keyword.strip().lower()
                if kw not in r.get("title", "").lower() and kw not in r.get("description", "").lower():
                    continue
            filtered.append(r)

        # 分页参数
        PAGE_SIZE = 20
        total_pages = max(1, (len(filtered) + PAGE_SIZE - 1) // PAGE_SIZE)
        page_key = "mgmt_page"
        if page_key not in st.session_state:
            st.session_state[page_key] = 0
        # 筛选条件变化时回到第一页
        filter_fp = (tuple(f_status or []), tuple(f_source or []), tuple(f_priority or []), tuple(f_owner or []), tuple(f_project or []), f_keyword.strip())
        if st.session_state.get("_last_filter_fp") != filter_fp:
            st.session_state[page_key] = 0
            st.session_state["_last_filter_fp"] = filter_fp
        page = st.session_state[page_key]
        page = max(0, min(page, total_pages - 1))
        start = page * PAGE_SIZE
        end = start + PAGE_SIZE
        page_items = filtered[start:end]

        st.caption(f"筛选后：{len(filtered)} 条 · 第 {page+1}/{total_pages} 页")

        # 用 HTML 表格展示（不用 dataframe，因为字段太多，HTML 更可控）
        if not filtered:
            st.info("没有匹配的需求")
        else:
            header_html = (
                '<div style="display:flex;flex-wrap:wrap;background:#1F4E79;color:white;padding:8px;border-radius:6px 6px 0 0;font-size:14px;font-weight:600;">'
                '<div style="width:90px;">ID</div>'
                '<div style="width:280px;">标题</div>'
                '<div style="width:70px;">状态</div>'
                '<div style="width:70px;">部门</div>'
                '<div style="width:110px;">关联项目</div>'
                '<div style="width:40px;">优先级</div>'
                '<div style="width:50px;">价值</div>'
                '<div style="width:50px;">复杂度</div>'
                '<div style="width:70px;">执行人</div>'
                '<div style="width:80px;">期望完成</div>'
                '<div style="width:60px;">操作</div>'
                '</div>'
            )
            st.markdown(header_html, unsafe_allow_html=True)
            for r in page_items:
                v = r.get("value_rating", "未评")
                c = r.get("complexity_rating", "未评")
                v_color = RATING_COLORS.get(v, "#BDBDBD")
                c_color = RATING_COLORS.get(c, "#BDBDBD")
                owner_name = _owner_names(r, "—")
                due = r.get("requested_due_date") or "—"
                # 过期标红
                due_display = due
                if due != "—":
                    try:
                        d = datetime.strptime(due, "%Y-%m-%d").date()
                        if d < TODAY and r.get("status") not in ["已完成", "已终止"]:
                            due_display = f'<span style="color:#E53935;font-weight:600;">{due}⚠</span>'
                    except Exception: pass
                row_html = (
                    f'<div style="display:flex;flex-wrap:wrap;padding:6px 8px;border-bottom:1px solid #EEE;font-size:13px;align-items:center;">'
                    f'<div style="width:90px;color:#666;">{r["id"]}</div>'
                    f'<div style="width:280px;">{r["title"][:35]}</div>'
                    f'<div style="width:70px;">{status_badge(r["status"])}</div>'
                    f'<div style="width:70px;color:#555;">{r.get("source","")}</div>'
                    f'<div style="width:110px;color:#555;">{_req_project_name(r) or "—"}</div>'
                    f'<div style="width:40px;">{priority_badge(r.get("priority","未知"))}</div>'
                    f'<div style="width:50px;color:{v_color};font-weight:600;">{v}</div>'
                    f'<div style="width:50px;color:{c_color};font-weight:600;">{c}</div>'
                    f'<div style="width:70px;">{owner_name}</div>'
                    f'<div style="width:80px;">{due_display}</div>'
                    f'</div>'
                )
                info_col, act_col = st.columns([1, 0.08], gap="small")
                with info_col:
                    st.markdown(row_html, unsafe_allow_html=True)
                with act_col:
                    if st.button("查看", key=f"mgmt_view_{r['id']}", use_container_width=True):
                        st.session_state["selected_req_id"] = r["id"]
                        st.session_state["_pending_nav"] = True
                        st.session_state["_pending_nav_target"] = "📄 需求详情"
                        st.rerun()
            # 分页导航（始终显示）
            nav_c1, nav_c2, nav_c3, nav_c4, nav_c5, nav_c6 = st.columns([1, 1, 1, 1, 1, 1])
            with nav_c2:
                if st.button("◀ 上一页", disabled=(page == 0), use_container_width=True, key="mgmt_prev"):
                    st.session_state[page_key] = max(0, page - 1)
                    st.rerun()
            with nav_c3:
                jump_to = st.number_input(
                    "跳转", min_value=1, max_value=total_pages, value=page + 1,
                    step=1, key="mgmt_jump", label_visibility="collapsed",
                )
                if jump_to != page + 1:
                    st.session_state[page_key] = jump_to - 1
                    st.rerun()
            with nav_c4:
                st.markdown(
                    f'<div style="text-align:center;padding-top:8px;color:#666;font-size:13px;">'
                    f'第 {page+1} / {total_pages} 页</div>',
                    unsafe_allow_html=True,
                )
            with nav_c5:
                if st.button("下一页 ▶", disabled=(page >= total_pages - 1), use_container_width=True, key="mgmt_next"):
                    st.session_state[page_key] = min(total_pages - 1, page + 1)
                    st.rerun()

    # ===== 导出 Tab =====
    with tab_export:
        st.markdown("### 📤 导出为 Excel")
        st.caption("导出当前所有需求（含全部字段）。evidence 和 comments 数组序列化为 JSON 字符串。")

        exp_col1, exp_col2 = st.columns([1, 2])
        with exp_col1:
            if st.button("📊 生成 Excel 文件", type="primary", use_container_width=True):
                try:
                    import pandas as pd
                    import io
                    rows = [_req_to_excel_row(r) for r in get_requirements()]
                    df = pd.DataFrame(rows, columns=[c[0] for c in EXCEL_COLUMN_MAP])
                    buf = io.BytesIO()
                    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                        df.to_excel(writer, index=False, sheet_name="需求列表")
                    buf.seek(0)
                    st.session_state["_export_bytes"] = buf.getvalue()
                    st.success(f"✅ 生成成功，{len(rows)} 条需求")
                except Exception as e:
                    st.error(f"导出失败：{e}")

        with exp_col2:
            xlsx_bytes = st.session_state.get("_export_bytes")
            if xlsx_bytes:
                fname = f"requirements_export_{TODAY_STR.replace('-','')}.xlsx"
                st.download_button(
                    label=f"⬇ 下载 {fname}",
                    file_name=fname,
                    data=xlsx_bytes,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )

        st.markdown("---")
        st.markdown("##### 模板下载（用于导入）")
        st.caption("空白模板，含字段说明与示例行。填好后到「📥 导入」Tab 上传。")
        if st.button("📋 下载导入模板"):
            try:
                import pandas as pd
                import io
                template_data = [
                    {"标题": "示例：SMT线增加扫码", "描述": "客户要求入料口加扫码，用于追溯", "部门": "销售",
                     "状态": "需求池", "优先级": "P2", "客户名": "示例客户",
                     "提出人": "赵新宇", "建议责任人": "", "责任人": "",
                     "关联项目": "SMT视觉检测线", "期望完成日期": "2026-09-30",
                     "价值评分": "中", "复杂度评分": "低", "评分来源": "人工",
                     "标签": "视觉检测设备, 软件, 新功能",
                     "佐证材料链接": "https://example.com/email-forwarded",
                     "估算工作量(人天)": 8, "创建日期": TODAY_STR,
                     "实际完成日期": "", "终止原因": "",
                     "佐证材料数": 0, "评论数": 0},
                    {"标题": "", "描述": "", "部门": "", "状态": "", "优先级": "",
                     "客户名": "", "提出人": "", "建议责任人": "", "责任人": "",
                     "关联项目": "", "期望完成日期": "", "价值评分": "", "复杂度评分": "",
                     "评分来源": "", "标签": "", "佐证材料链接": "",
                     "估算工作量(人天)": "", "创建日期": "", "实际完成日期": "",
                     "终止原因": "", "佐证材料数": "", "评论数": ""},
                ]
                df = pd.DataFrame(template_data, columns=[c[0] for c in EXCEL_COLUMN_MAP])
                buf = io.BytesIO()
                with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                    df.to_excel(writer, index=False, sheet_name="导入模板")
                buf.seek(0)
                st.download_button(
                    label="⬇ 下载 模板.xlsx",
                    file_name="requirements_template.xlsx",
                    data=buf.getvalue(),
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            except Exception as e:
                st.error(f"模板生成失败：{e}")

    # ===== 导入 Tab =====
    with tab_import:
        st.markdown("### 📥 从 Excel 导入")
        st.caption("上传 .xlsx 文件，自动校验并批量导入。建议先下载模板按格式填写。")

        allow_overwrite = st.checkbox("允许覆盖已存在的需求ID", value=False)
        uploaded = st.file_uploader("选择 Excel 文件", type=["xlsx", "xls"], key="mgmt_upload")

        if uploaded is not None:
            try:
                # 解析结果按文件内容缓存：上传后每次 rerun 不再重复 pd.read_excel / 逐行校验
                preview_rows, parsed_reqs = _cached_excel_parse(
                    uploaded.getvalue(), st.session_state.current_user_id
                )
                st.success(f"✅ 文件解析成功，共 {len(parsed_reqs)} 行")

                # 显示原始数据预览（前 5 行）
                with st.expander("原始数据预览（前 5 行）", expanded=False):
                    st.dataframe(preview_rows, use_container_width=True)

                # ID 冲突检查（依赖当前库与「允许覆盖」勾选，必须实时计算，不能进缓存）
                existing_ids = {r["id"] for r in get_requirements()}
                parsed_out = []
                for req, errs in parsed_reqs:
                    conflict = False
                    if req["id"] and req["id"] in existing_ids and not allow_overwrite:
                        errs.append(f"ID「{req['id']}」已存在（未勾选允许覆盖）")
                        conflict = True
                    parsed_out.append((req, errs, conflict))

                ok_reqs = [(req, conflict) for req, errs, conflict in parsed_out if not errs]
                err_reqs = [(idx + 2, req, errs) for idx, (req, errs, conflict) in enumerate(parsed_out) if errs]

                # 汇总
                m_col1, m_col2, m_col3 = st.columns(3)
                with m_col1:
                    st.metric("可导入", len(ok_reqs))
                with m_col2:
                    st.metric("有问题", len(err_reqs))
                with m_col3:
                    existing_count = sum(1 for req, c in ok_reqs if c)
                    st.metric("其中覆盖", existing_count)

                # 错误报告
                if err_reqs:
                    st.warning(f"⚠ {len(err_reqs)} 行有问题，将跳过：")
                    err_rows = []
                    for row_num, req, errs in err_reqs:
                        err_rows.append({"Excel 行号": row_num, "标题": req["title"][:30], "问题": "；".join(errs)})
                    st.dataframe(err_rows, use_container_width=True)

                    # 错误报告下载（保持原交互：直接一个下载按钮）
                    import pandas as pd_err
                    import io as io_err
                    err_buf = io_err.BytesIO()
                    pd_err.DataFrame(err_rows).to_excel(err_buf, index=False, sheet_name="错误报告")
                    err_buf.seek(0)
                    st.download_button(
                        label="⬇ 下载错误报告",
                        file_name=f"import_errors_{TODAY_STR.replace('-','')}.xlsx",
                        data=err_buf.getvalue(),
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )

                # 导入确认
                if ok_reqs:
                    st.markdown("---")
                    st.markdown(f"##### 待导入 {len(ok_reqs)} 条（预览前 5 条）")
                    for req, _ in ok_reqs[:5]:
                        st.markdown(
                            f'- {req["id"] or "（新ID）"} 「{req["title"]}」 · 部门:{req["source"]} · 状态:{req["status"]}',
                        )
                    if len(ok_reqs) > 5:
                        st.caption(f"...还有 {len(ok_reqs)-5} 条")

                    if st.button(f"✅ 确认导入 {len(ok_reqs)} 条", type="primary"):
                        inserted = 0
                        overwritten = 0
                        for req, conflict in ok_reqs:
                            if not req["id"]:
                                req["id"] = gen_req_id()
                            if conflict:
                                overwritten += 1
                            else:
                                inserted += 1
                            save_requirement(req)
                        st.session_state["_flash"] = {"type": "success", "text": f"✅ 导入完成：新增 {inserted} 条，覆盖 {overwritten} 条"}
                        st.rerun()
                else:
                    st.error("没有可导入的数据，请先修正错误")

            except Exception as e:
                st.error(f"文件解析失败：{e}")
                st.caption("请确认：①文件是 .xlsx 格式；②第一行是中文列名（建议先下载模板参考）；③「标题」「描述」「部门」「佐证材料链接」是必填列。")
