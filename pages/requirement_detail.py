"""
pages/requirement_detail.py — 从 app.py 拆分生成（函数体逐字保留）。
"""
from __future__ import annotations

# ---- 第三方 / 标准库 ----
from datetime import datetime
from sample_data import EVIDENCE_TYPES, PRIORITY_LABELS, STATUS_LABELS
import db
import streamlit as st

# ---- 项目内部 ----
from config import PROJECT_STAGES, SATISFACTION_LABELS, TODAY_STR
from services.cache import _cached_attachment, _invalidate_attachment_cache
from services.evaluation import _can_view_rating_detail, _rater_role, _ratings, _req_raters
from services.notify import _notify
from services.people import _owner_names
from services.projects import _project_display_name, get_projects
from services.requirements import _aggregate_parent_status, _record_status_change, _sync_sub_requirements, delete_requirement, get_requirement, get_requirements, save_requirement
from state import USERS, USER_ID_TO_NAME, USER_NAME_TO_ID
from ui.components import _date_picker, _render_file_preview, _star_html, priority_badge, rating_badge, status_badge, tag_chip



def page_requirement_detail():
    st.markdown("## 📄 需求详情")

    req_ids = [r["id"] for r in get_requirements()]
    # 如果从看板点击「查看」跳转过来，覆盖当前选中的需求
    preselected = st.session_state.pop("selected_req_id", None)
    if preselected and preselected in req_ids:
        st.session_state["detail_select_req"] = preselected
    # 确保 session_state 中的值在 req_ids 中有效（防止数据变更后失效）
    if st.session_state.get("detail_select_req") not in req_ids:
        st.session_state["detail_select_req"] = req_ids[0] if req_ids else None
    title_by_id = {r["id"]: r["title"] for r in get_requirements()}
    req_id = st.selectbox(
        "选择需求",
        options=req_ids,
        key="detail_select_req",
        format_func=lambda x: f"{x} · {title_by_id.get(x, '')}"[:60],
    )
    if not req_id:
        return

    req = get_requirement(req_id)
    if not req:
        st.warning("需求不存在")
        return

    st.markdown(f"### {req['title']}")
    st.markdown(
        f'{status_badge(req["status"])} &nbsp; {priority_badge(req.get("priority","未知"))} &nbsp; '
        f'<span style="color:#666;">{req["id"]}</span>',
        unsafe_allow_html=True,
    )

    st.markdown("---")
    col_a, col_b, col_c, col_d = st.columns(4)
    with col_a:
        st.markdown(f"**部门**<br>{req.get('source','')}", unsafe_allow_html=True)
    with col_b:
        requester = USER_ID_TO_NAME.get(req.get("requester_id", ""), "—")
        st.markdown(f"**提出人**<br>{requester}", unsafe_allow_html=True)
    with col_c:
        requested_owner = USER_ID_TO_NAME.get(req.get("requested_owner_id", ""), "—")
        st.markdown(f"**建议负责人**<br>{requested_owner}", unsafe_allow_html=True)
    with col_d:
        owner = _owner_names(req)
        st.markdown(f"**执行人**<br>{owner}", unsafe_allow_html=True)

    # 点赞（可点赞/取消，提出人不能给自己点赞）
    liked_by = req.get("liked_by", [])
    me = st.session_state.current_user_id
    like_names = "、".join(USER_ID_TO_NAME.get(u, "未知") for u in liked_by)
    if like_names:
        st.caption(f"👍 {len(liked_by)} 人觉得有用：{like_names}")
    if me == req.get("requester_id"):
        st.caption("（您是本需求的提出人，不能给自己点赞）" + (f" 当前获赞 {len(liked_by)}" if liked_by else ""))
    elif me in liked_by:
        if st.button(f"👍 已点赞 {len(liked_by)} · 点击取消", key=f"unlike_{req['id']}"):
            req["liked_by"] = [u for u in liked_by if u != me]
            save_requirement(req)
            st.rerun()
    else:
        if st.button(f"👍 觉得有用，点赞（当前 {len(liked_by)}）", key=f"like_{req['id']}"):
            req.setdefault("liked_by", []).append(me)
            save_requirement(req)
            st.rerun()

    # 需求评价（一期）：提出人 / 建议责任人 / 执行人各评一次。
    # 可见性口径：普通人只看得到自己那条；管理层可展开看全部明细（与用户确认的口径一致）。
    if req.get("status") == "已完成":
        _raters = _req_raters(req)
        _ratings_map = _ratings(req)
        _is_rater = me in _raters
        _is_mgr = _can_view_rating_detail()
        if _raters and (_is_rater or _is_mgr):
            st.markdown("---")
            st.markdown("**需求评价**")
            st.caption(f"本需求需 {len(_raters)} 人评价（提出人 / 建议责任人 / 执行人），"
                       f"目前已完成 {len(_ratings_map)} 人。")
            _mine = _ratings_map.get(me)
            if _mine:
                st.markdown(
                    f'我的评价：{_star_html(_mine["score"], 17)} &nbsp;'
                    f'<b>{_mine["score"]}/5</b> · {SATISFACTION_LABELS.get(_mine["score"], "")} '
                    f'<span style="color:#666;font-size:12px;">{_mine.get("at", "")}</span>',
                    unsafe_allow_html=True,
                )
                if _mine.get("comment"):
                    st.caption(f"我的补充说明：{_mine['comment']}")
                if _is_rater and st.button("✏️ 修改我的评价", key=f"rerate_{req['id']}"):
                    st.session_state["_rating_req"] = req["id"]
                    st.rerun()
            elif _is_rater:
                st.caption(f"您在本需求中的身份是「{_rater_role(req, me)}」，尚未评价。")
                if st.button("⭐ 去评价", type="primary", key=f"rate_here_{req['id']}"):
                    st.session_state["_rating_req"] = req["id"]
                    st.rerun()

            if _is_mgr and _ratings_map:
                with st.expander(f"👁 全部评价明细（{len(_ratings_map)}/{len(_raters)} 人，仅管理层可见）"):
                    for _uid in _raters:
                        _rec = _ratings_map.get(_uid)
                        _name = USER_ID_TO_NAME.get(_uid, _uid)
                        _role = _rater_role(req, _uid)
                        if _rec:
                            st.markdown(
                                f'{_star_html(_rec["score"], 15)} <b>{_rec["score"]}/5</b> '
                                f'{_name}（{_role}）'
                                f'<span style="color:#666;font-size:12px;"> · {_rec.get("at", "")}</span>',
                                unsafe_allow_html=True,
                            )
                            if _rec.get("comment"):
                                st.caption(f"　└ {_rec['comment']}")
                        else:
                            st.caption(f"⏳ {_name}（{_role}）尚未评价")
                    _scored = [int(v["score"]) for v in _ratings_map.values() if v.get("score")]
                    if _scored:
                        st.markdown(f"**本需求均分：{sum(_scored) / len(_scored):.2f} / 5**")

    st.markdown("---")
    st.markdown("**描述**")
    st.markdown(req.get("description", "—"))

    # 关联项目（可补充 / 修改）
    st.markdown("---")
    st.markdown("**关联项目**")
    st.caption(
        "💡 选「（无）」时，大项目视图按标题自动归类：含 A/B/C → 对应大项目；"
        "否则含 AI → 研发AI赋能；都不是 → 其他"
    )
    all_projects = get_projects()
    if not all_projects:
        st.caption("暂无项目，请先到「项目视图」新建项目后再关联。")
    else:
        proj_options = ["（无）"] + [_project_display_name(p) for p in all_projects]
        current_proj = next((p for p in all_projects if p["id"] == req.get("project_id", "")), None)
        current_proj_name = _project_display_name(current_proj) if current_proj else ""
        proj_index = proj_options.index(current_proj_name) if current_proj_name in proj_options else 0
        stage_options = ["（请选择）"] + PROJECT_STAGES
        cur_stage = req.get("project_stage", "")
        stage_index = stage_options.index(cur_stage) if cur_stage in stage_options else 0
        pcol, scol = st.columns(2)
        with pcol:
            new_proj_name = st.selectbox("关联项目", options=proj_options, index=proj_index, key=f"detail_proj_{req['id']}")
        with scol:
            new_stage = st.selectbox("项目阶段", options=stage_options, index=stage_index, key=f"detail_stage_{req['id']}", disabled=(new_proj_name == "（无）"))
        if st.button("💾 保存关联项目", key=f"save_proj_{req['id']}", use_container_width=True):
            if new_proj_name != "（无）" and new_stage == "（请选择）":
                st.error("已关联项目，请选择项目阶段")
            else:
                req["project_id"] = next((p["id"] for p in all_projects if _project_display_name(p) == new_proj_name), "") if new_proj_name != "（无）" else ""
                req["project_stage"] = new_stage if (new_proj_name != "（无）" and new_stage != "（请选择）") else ""
                save_requirement(req)
                st.session_state["_flash"] = {"type": "success", "text": "✅ 已更新关联项目"}
                st.rerun()

    # 佐证材料（v0.2.0 新增）
    st.markdown("---")
    st.markdown(f"**佐证材料**（共 {len(req.get('evidence', []))} 份）")
    evidence_list = req.get("evidence", [])
    if evidence_list:
        for i, ev_item in enumerate(evidence_list, 1):
            ev_type_label = EVIDENCE_TYPES.get(ev_item.get("type", "other"), ev_item.get("type", "其他"))
            uploader = USER_ID_TO_NAME.get(ev_item.get("uploaded_by", ""), "—")
            # 上传文件类佐证：落盘存储，提供下载按钮；链接类：直接展示链接
            if ev_item.get("filename"):
                size_kb = ev_item.get("size", 0) / 1024
                file_data = _cached_attachment(
                    req["id"], ev_item.get("filename", ""), ev_item.get("size", 0)
                )
                info_col, dl_col = st.columns([4, 1])
                with info_col:
                    st.markdown(
                        f'<div style="padding:8px;background:#FFF8E1;border-left:3px solid #FFA726;border-radius:4px;margin:6px 0;">'
                        f'<span style="color:#666;font-size:11px;">#{i} · {ev_type_label} · 上传人 {uploader}</span>'
                        f'<div style="margin-top:4px;">📎 {ev_item.get("label", "")}'
                        f' &nbsp; <span style="color:#666;font-size:11px;">{size_kb:.1f} KB</span></div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                with dl_col:
                    if file_data:
                        st.download_button(
                            "⬇下载",
                            data=file_data,
                            file_name=ev_item.get("label", ev_item.get("filename", "evidence")),
                            key=f"dl_ev_{req['id']}_{i}",
                            use_container_width=True,
                        )
                if file_data:
                    _render_file_preview(file_data, ev_item.get("label", ev_item.get("filename", "佐证文件")))
            else:
                st.markdown(
                    f'<div style="padding:8px;background:#FFF8E1;border-left:3px solid #FFA726;border-radius:4px;margin:6px 0;">'
                    f'<span style="color:#666;font-size:11px;">#{i} · {ev_type_label} · 上传人 {uploader}</span>'
                    f'<div style="margin-top:4px;">📎 {ev_item.get("label", "")}</div>'
                    f'<div style="color:#1976D2;font-size:12px;margin-top:2px;word-break:break-all;">{ev_item.get("link", "")}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
    else:
        st.warning("⚠️ 该需求未附佐证材料（不符合「书面留痕」原则）")

    if req.get("tags"):
        st.markdown("**标签**")
        st.markdown("".join(tag_chip(t) for t in req["tags"]), unsafe_allow_html=True)

    if req.get("customer") or req.get("source_ref"):
        st.markdown("---")
        st.markdown("**客户与来源**")
        if req.get("customer"):
            st.markdown(f"客户：{req['customer']}")
        if req.get("source_ref"):
            st.markdown(f"来源引用：{req['source_ref']}")

    # 终止原因（v0.2.0 新增）
    if req.get("status") == "已终止" and req.get("termination_reason"):
        st.markdown("---")
        st.markdown(f"**终止原因**：<span style='color:#5D4037;'>{req['termination_reason']}</span>", unsafe_allow_html=True)

    # 价值-复杂度评分（v0.2.2 新增）
    st.markdown("---")
    st.markdown("**价值-复杂度评分**")
    v_r = req.get("value_rating", "未评")
    c_r = req.get("complexity_rating", "未评")
    src = req.get("rating_source", "")
    if v_r == "未评" and c_r == "未评":
        st.caption("（未评）")
    else:
        st.markdown(
            rating_badge("价值", v_r, src) + " &nbsp; " + rating_badge("复杂度", c_r, src),
            unsafe_allow_html=True,
        )
        # 给出象限解读
        quadrant_hint = ""
        if v_r == "高" and c_r == "低":
            quadrant_hint = "速胜（Quick Win）— 建议优先做"
        elif v_r == "高" and c_r == "中":
            quadrant_hint = "战略项目 — 主动规划"
        elif v_r == "高" and c_r == "高":
            quadrant_hint = "重大项目 — 重点保障"
        elif v_r == "中" and c_r == "低":
            quadrant_hint = "顺手做 — 资源有空隙时做"
        elif v_r == "中" and c_r == "中":
            quadrant_hint = "正常推进"
        elif v_r == "中" and c_r == "高":
            quadrant_hint = "审慎评估"
        elif v_r == "低" and c_r == "低":
            quadrant_hint = "填空任务 — 资源紧张可弃"
        elif v_r == "低" and c_r == "中":
            quadrant_hint = "鸡肋 — 建议考虑终止"
        elif v_r == "低" and c_r == "高":
            quadrant_hint = "「钱坑」— 建议主动终止"
        if quadrant_hint:
            st.caption(f"📍 矩阵位置：{quadrant_hint}")

    # 需求确认（共识） / 状态变更
    if req["status"] == "待确认":
        _render_consensus(req)
    elif req["status"] == "已终止":
        # 终态锁定：已终止的需求不可再变更状态
        st.markdown("---")
        st.markdown("**状态变更**")
        st.info(f"🔒 该需求已终止，状态不可再变更。终止原因：{req.get('termination_reason') or '（未填写）'}")

        # 删除已终止需求
        with st.expander("🗑 删除需求"):
            st.caption("删除后该需求将从系统移除，不再显示、不参与统计。此操作不可恢复。")
            confirm = st.checkbox("我确认删除此需求", key=f"del_req_confirm_{req['id']}")
            if st.button("🗑 确认删除", key=f"del_req_{req['id']}", disabled=not confirm):
                req_id = req["id"]
                delete_requirement(req_id)
                st.session_state["_flash"] = {"type": "info", "text": f"已删除需求 {req_id}"}
                st.rerun()
    else:
        _render_status_change(req)

    # 子需求（多执行人自动拆分，一一对应独立流转）
    _render_sub_requirements(req)

    # 附件（F5 执行过程产物，区别于 evidence 佐证材料）
    st.markdown("---")
    st.markdown(f"**📎 附件**（执行过程产物，区别于「佐证材料」与「评论区附件」。共 {len(req.get('attachments', []))} 份）")
    st.caption("上传测试报告、改进图纸、调试日志等执行过程中产生的文件。文件保存在本地 data/attachments/ 目录。")

    existing_atts = req.get("attachments", [])
    if existing_atts:
        for i, att in enumerate(existing_atts):
            att_size_kb = att.get("size", 0) / 1024
            uploader_name = USER_ID_TO_NAME.get(att.get("uploaded_by", ""), "—")
            file_data = _cached_attachment(
                req["id"], att.get("filename", ""), att.get("size", 0)
            )
            cols = st.columns([4, 1, 1])
            with cols[0]:
                st.markdown(
                    f'<div style="padding:6px;background:#F5F5F5;border-radius:4px;">'
                    f'📎 <b>{att.get("filename", att.get("original_name", ""))}</b>'
                    f' &nbsp; <span style="color:#666;font-size:11px;">{att_size_kb:.1f} KB · {att.get("uploaded_at","")} · {uploader_name}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            with cols[1]:
                if file_data:
                    st.download_button(
                        "⬇下载",
                        data=file_data,
                        file_name=att.get("filename", "attachment"),
                        key=f"dl_att_{req['id']}_{i}",
                        use_container_width=True,
                    )
            with cols[2]:
                if st.button("🗑删除", key=f"del_att_{req['id']}_{i}", use_container_width=True):
                    db.remove_attachment(req["id"], att.get("filename", ""))
                    req["attachments"] = [a for a in existing_atts if a.get("filename") != att.get("filename")]
                    save_requirement(req)
                    _invalidate_attachment_cache()
                    st.session_state["_flash"] = {"type": "success", "text": "已删除附件"}
                    st.rerun()
            if file_data:
                _render_file_preview(file_data, att.get("original_name", att.get("filename", "附件")))
    else:
        st.caption("（暂无附件）")

    # 上传新附件
    new_att = st.file_uploader(
        "上传附件（支持 png/jpg/pdf/docx/xlsx/txt/eml/mp3/wav 等图片、文档与音频）",
        type=["png", "jpg", "jpeg", "gif", "pdf", "docx", "doc", "xlsx", "xls", "txt", "eml", "csv", "md",
              "mp3", "wav", "m4a", "aac", "ogg", "amr", "wma", "flac"],
        key=f"att_up_{req['id']}",
    )
    if new_att is not None:
        if st.button("💾 保存附件", key=f"save_att_{req['id']}"):
            content = new_att.read()
            att_meta = db.save_attachment(
                req_id=req["id"],
                filename=new_att.name,
                content=content,
                uploaded_by=st.session_state.current_user_id,
            )
            req.setdefault("attachments", []).append(att_meta)
            save_requirement(req)
            _invalidate_attachment_cache()
            st.session_state["_flash"] = {"type": "success", "text": f"✅ 已上传：{new_att.name}（{len(content)/1024:.1f} KB）"}
            st.rerun()

    # 评论时间线
    st.markdown("---")
    st.markdown("**评论时间线**")
    for ci, c in enumerate(req.get("comments", [])):
        st.markdown(
            f'<div style="padding:8px;background:#F5F5F5;border-radius:6px;margin:6px 0;">'
            f'<span style="font-weight:600;">{c.get("user","")}</span>'
            f'<span style="color:#888;font-size:12px;margin-left:8px;">{c.get("time","")}</span>'
            f'<div style="margin-top:4px;">{c.get("text","")}</div></div>',
            unsafe_allow_html=True,
        )
        # 评论区附件：该评论随附上传的文件，独立于「佐证材料」「附件」
        for fi, f_meta in enumerate(c.get("files") or []):
            file_data = _cached_attachment(req["id"], f_meta.get("filename", ""), f_meta.get("size", 0))
            f_size_kb = f_meta.get("size", 0) / 1024
            fcols = st.columns([4, 1])
            with fcols[0]:
                st.markdown(
                    f'<div style="margin-left:10px;padding:4px 8px;background:#FAFAFA;'
                    f'border:1px solid #EEE;border-radius:4px;font-size:12px;">'
                    f'📎 <b>{f_meta.get("original_name", f_meta.get("filename", ""))}</b>'
                    f' &nbsp;<span style="color:#999;">{f_size_kb:.1f} KB</span></div>',
                    unsafe_allow_html=True,
                )
            with fcols[1]:
                if file_data:
                    st.download_button(
                        "⬇下载",
                        data=file_data,
                        file_name=f_meta.get("filename", "file"),
                        key=f"dl_cmt_{req['id']}_{ci}_{fi}",
                        use_container_width=True,
                    )
    # 发送评论后清空输入框（须在控件实例化前清）
    if st.session_state.pop(f"_clear_comment_{req['id']}", False):
        st.session_state[f"new_comment_{req['id']}"] = ""
    comment_ver = st.session_state.get(f"comment_uploader_ver_{req['id']}", 0)
    new_comment = st.text_input("添加评论", key=f"new_comment_{req['id']}")
    comment_files = st.file_uploader(
        "评论区附件（可选，可多选，随评论保存，不进入上方「附件」列表）",
        type=["png", "jpg", "jpeg", "pdf", "docx", "xlsx", "txt", "eml",
              "mp3", "wav", "m4a", "aac", "ogg", "amr", "wma", "flac"],
        accept_multiple_files=True,
        key=f"comment_files_{req['id']}_{comment_ver}",
    )
    if st.button("发送评论", key=f"send_comment_{req['id']}") and new_comment.strip():
        me = st.session_state.current_user_id
        # 评论区附件：随评论保存（与「佐证材料」「附件」三分类独立）
        file_metas = []
        for f in (comment_files or []):
            content = f.read()
            file_metas.append(db.save_attachment(req_id=req["id"], filename=f.name, content=content, uploaded_by=me))
        req.setdefault("comments", []).append({
            "user": USER_ID_TO_NAME.get(me, "我"),
            "time": TODAY_STR,
            "text": new_comment.strip(),
            "files": file_metas,
        })
        save_requirement(req)
        if file_metas:
            _invalidate_attachment_cache()
        st.session_state[f"_clear_comment_{req['id']}"] = True
        st.session_state[f"comment_uploader_ver_{req['id']}"] = comment_ver + 1
        st.rerun()


def _render_consensus(req):
    """待确认状态下的需求共识：主管接受/驳回/提调整，提出人确认/拒绝调整。"""
    requester_name = USER_ID_TO_NAME.get(req.get("requester_id", ""), "—")
    owner_name = USER_ID_TO_NAME.get(req.get("requested_owner_id", ""), "未指定")
    review_state = req.get("review_state", "待责任人确认")
    me = st.session_state.current_user_id
    proposal = req.get("proposed_changes")

    st.markdown("---")
    st.markdown("### 🤝 需求确认（共识）")
    st.caption(f"提出人：{requester_name} · 主管：{owner_name} · 双方达成共识后方可进入需求池")

    if not req.get("requested_owner_id"):
        st.warning("该需求尚未指定主管，无法进行确认。")
        return

    # 提出人可撤回（待确认阶段，进入需求池之前）：撤回 = 直接删除该需求
    if me == req.get("requester_id"):
        with st.expander("↩ 撤回需求（提出人）"):
            st.caption("撤回后该需求将被删除，不再显示、不参与任何统计。此操作不可恢复。")
            confirm = st.checkbox("我确认撤回并删除此需求", key=f"cons_withdraw_confirm_{req['id']}")
            if st.button("↩ 确认撤回", key=f"cons_withdraw_{req['id']}", disabled=not confirm):
                req_id = req["id"]
                delete_requirement(req_id)
                st.session_state["_flash"] = {"type": "info", "text": f"已撤回并删除需求 {req_id}"}
                st.rerun()

    if review_state == "待责任人确认":
        if req.get("requester_reply"):
            st.info(f"💬 提出人补充说明：{req['requester_reply']}")
        if me == req.get("requested_owner_id"):
            st.info("您是主管，请确认是否接受该需求，或反馈修改意见。")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("✅ 接受，进入需求池", key="cons_accept", type="primary"):
                    if proposal:
                        if proposal.get("requested_due_date"):
                            req["requested_due_date"] = proposal["requested_due_date"]
                        if proposal.get("priority") not in (None, ""):
                            req["priority"] = proposal["priority"]
                        if proposal.get("effort_estimate_days") is not None:
                            req["effort_estimate_days"] = proposal["effort_estimate_days"]
                    req["proposed_changes"] = None
                    req["requester_reply"] = None
                    _record_status_change(req, req.get("status", ""), "需求池", owner_name)
                    req["status"] = "需求池"
                    req["review_state"] = "已达成共识"
                    save_requirement(req)
                    _notify(
                        [req.get("requester_id", "")],
                        f"您提出的需求「{req['title']}」已被主管采纳，进入需求池。",
                        req=req,
                    )
                    st.session_state["_flash"] = {"type": "success", "text": f"✅ 已接受，{req['id']} 进入需求池"}
                    st.rerun()
            with c2:
                # 驳回后清空驳回原因（须在控件实例化前清）
                if st.session_state.pop("_clear_cons_reject", False):
                    st.session_state["cons_reject_reason"] = ""
                reject_reason = st.text_input("驳回原因", key="cons_reject_reason", placeholder="说明驳回理由")
                if st.button("❌ 驳回（终止）", key="cons_reject", disabled=not reject_reason.strip()):
                    _record_status_change(req, req.get("status", ""), "已终止", owner_name)
                    req["status"] = "已终止"
                    req["termination_reason"] = reject_reason.strip()
                    req["actual_completion_date"] = TODAY_STR
                    req["review_state"] = "已驳回"
                    save_requirement(req)
                    # 【暂停提醒·试用】共识流程驳回提醒，试用效果好后取消注释恢复
                    # _notify(
                    #     [req.get("requester_id", "")],
                    #     f"您提出的需求「{req['title']}」已被驳回终止"
                    #     f"（原因：{reject_reason.strip()}），请到看板查看。",
                    #     req=req,
                    # )
                    st.session_state["_clear_cons_reject"] = True
                    st.session_state["_flash"] = {"type": "success", "text": f"已驳回，{req['id']} 已终止"}
                    st.rerun()

            with st.expander("✏️ 反馈修改意见（改范围 / 截止日期 / 优先级）"):
                with st.form("cons_propose"):
                    new_due = _date_picker("期望完成日期", req.get("requested_due_date", ""), prefix=f"cons_due_{req['id']}")
                    pri_options = PRIORITY_LABELS
                    new_priority = st.selectbox(
                        "优先级",
                        options=pri_options,
                        index=pri_options.index(req.get("priority", "未知")) if req.get("priority", "未知") in pri_options else len(pri_options) - 1,
                    )
                    new_effort = st.number_input("估算工作量（人天）", min_value=0.0, value=float(req.get("effort_estimate_days") or 0), step=0.5)
                    note = st.text_area("调整说明", placeholder="说明需要调整的内容与理由")
                    if st.form_submit_button("提交调整"):
                        req["proposed_changes"] = {
                            "requested_due_date": new_due.strip(),
                            "priority": new_priority,
                            "effort_estimate_days": new_effort,
                            "note": note.strip(),
                        }
                        req["review_state"] = "待提出人确认"
                        save_requirement(req)
                        # 【暂停提醒·试用】共识流程反馈调整提醒，试用效果好后取消注释恢复
                        # _notify(
                        #     [req.get("requester_id", "")],
                        #     f"主管「{owner_name}」对需求「{req['title']}」反馈了修改意见，等待您确认。",
                        #     req=req,
                        # )
                        st.session_state["_flash"] = {"type": "success", "text": "已反馈修改意见，等待提出人补充说明"}
                        st.rerun()
        else:
            st.info(f"等待主管「{owner_name}」处理…")

    elif review_state == "待提出人确认":
        if proposal:
            st.warning(f"主管「{owner_name}」反馈以下修改意见：")
            if proposal.get("requested_due_date"):
                st.markdown(f"- **期望完成日期**：{proposal.get('requested_due_date')}")
            if proposal.get("priority") not in (None, ""):
                st.markdown(f"- **优先级**：{proposal.get('priority')}")
            if proposal.get("effort_estimate_days") is not None:
                st.markdown(f"- **估算工作量（人天）**：{proposal.get('effort_estimate_days')}")
            if proposal.get("note"):
                st.markdown(f"- **修改意见**：{proposal.get('note')}")
        if me == req.get("requester_id"):
            st.info("请根据主管的修改意见填写补充说明，提交后由主管最终确认。")
            reply_ver = st.session_state.get("cons_reply_uploader_ver", 0)
            with st.form("cons_reply"):
                reply = st.text_area("补充说明", placeholder="说明您的补充 / 澄清内容")
                reply_files = st.file_uploader(
                    "补充佐证文件（可选，可多选，支持图片/文档/音频）",
                    type=["png", "jpg", "jpeg", "pdf", "docx", "xlsx", "txt", "eml",
                          "mp3", "wav", "m4a", "aac", "ogg", "amr", "wma", "flac"],
                    accept_multiple_files=True,
                    key=f"cons_reply_files_{reply_ver}",
                )
                if st.form_submit_button("📤 提交补充说明"):
                    if not reply.strip():
                        st.error("请填写补充说明")
                    else:
                        req["requester_reply"] = reply.strip()
                        # 上传的文件补充到佐证材料
                        for f in (reply_files or []):
                            content = f.read()
                            meta = db.save_attachment(req_id=req["id"], filename=f.name, content=content, uploaded_by=me)
                            req.setdefault("evidence", []).append({
                                "type": "file",
                                "label": f.name,
                                "link": "",
                                "filename": meta["filename"],
                                "size": meta["size"],
                                "uploaded_by": me,
                                "uploaded_at": TODAY_STR,
                            })
                        req["review_state"] = "待责任人确认"
                        req.setdefault("comments", []).append({
                            "user": USER_ID_TO_NAME.get(me, "我"),
                            "time": TODAY_STR,
                            "text": f"提出人提交补充说明：{reply.strip()}",
                        })
                        save_requirement(req)
                        # 【暂停提醒·试用】共识流程补充说明提醒，试用效果好后取消注释恢复
                        # _notify(
                        #     [req.get("requested_owner_id", "")],
                        #     f"{requester_name} 对需求「{req['title']}」提交了补充说明，等待您再次确认。",
                        #     req=req,
                        # )
                        if reply_files:
                            _invalidate_attachment_cache()
                        st.session_state["cons_reply_uploader_ver"] = reply_ver + 1
                        st.session_state["_flash"] = {"type": "success", "text": "已提交补充说明，等待主管确认"}
                        st.rerun()
        else:
            st.info(f"等待提出人「{requester_name}」填写补充说明…")
    else:
        if review_state == "已驳回":
            st.warning("该需求此前已被主管驳回终止，但状态被改回了「待确认」，处于异常状态。")
            if st.button("🔧 恢复为「已终止」", key="cons_restore_terminated"):
                req["status"] = "已终止"
                save_requirement(req)
                st.rerun()
        else:
            st.caption(f"共识状态：{review_state}")


def _render_status_change(req):
    """非「待确认」状态下的状态流转：切换状态、指派执行人、改日期/评分。"""
    me = st.session_state.current_user_id
    st.markdown("---")
    st.markdown("**状态变更**")
    # 内联成功提示（在当前区块显示，无需回到页面顶部）
    sc = st.session_state.pop("_status_change_flash", None)
    if sc and sc.get("req_id") == req["id"]:
        st.success(sc["text"])
    # 「待确认」是共识门禁状态，只能由新建/共识流程进入，不允许手动改入
    status_options = [s for s in STATUS_LABELS if s != "待确认"]
    new_status = st.selectbox(
        "切换状态",
        options=status_options,
        index=status_options.index(req["status"]) if req["status"] in status_options else 0,
        key=f"status_select_{req['id']}",
    )

    # 终止原因输入（如果新状态是「已终止」）
    term_reason_input = ""
    if new_status == "已终止":
        term_reason_input = st.text_input(
            "请填写终止原因（必填）",
            value=req.get("termination_reason", ""),
            key=f"term_reason_{req['id']}",
        )

    # 执行人指派（仅建议责任人，可多选）+ 工作量评估（建议责任人或执行人）
    assign_owners = None
    new_effort = None
    if new_status in ("待启动", "进行中"):
        owner_ids = [i for i in req.get("owner_ids", []) if i]
        current_owner = "、".join(USER_ID_TO_NAME.get(i, "未知") for i in owner_ids) if owner_ids else "未指派"
        is_requested_owner = (me == req.get("requested_owner_id"))
        is_executor = (me in owner_ids)
        # 指派 / 调整执行人：仅建议责任人，可指定任何人（不限部门/角色），可多选
        if is_requested_owner:
            name_to_dept = {u["name"]: (u.get("department") or "") for u in USERS}
            assign_owners = st.multiselect(
                "指派 / 调整执行人（可多选）",
                options=[u["name"] for u in USERS],
                default=[USER_ID_TO_NAME.get(i, i) for i in owner_ids],
                format_func=lambda x: f"{x}（{name_to_dept.get(x, '')}）" if name_to_dept.get(x) else x,
                key=f"assign_owner_{req['id']}",
            )
        else:
            st.caption(f"当前执行人：{current_owner}（仅建议责任人可指派/调整）")
        # 工作量评估：建议责任人 或 执行人 均可改
        if is_requested_owner or is_executor:
            new_effort = st.number_input(
                "工作量评估（人天）",
                min_value=0.0,
                value=float(req.get("effort_estimate_days") or 0),
                step=0.5,
                key=f"effort_{req['id']}",
            )

    # 期望完成日期（终态「已完成 / 已终止」无需填写）
    new_due_date = ""
    if new_status not in ("已完成", "已终止"):
        new_due_date = _date_picker("期望完成日期", req.get("requested_due_date", ""), prefix=f"due_date_{req['id']}")

    # 价值-复杂度评分修改（任意状态都可改）
    edit_rating = st.checkbox("编辑价值/复杂度评分", value=False, key=f"edit_rating_{req['id']}")
    new_v, new_c = None, None
    if edit_rating:
        rc1, rc2 = st.columns(2)
        with rc1:
            v_options = ["未评", "高", "中", "低"]
            new_v = st.selectbox(
                "价值",
                options=v_options,
                index=v_options.index(req.get("value_rating", "未评")) if req.get("value_rating", "未评") in v_options else 0,
                key=f"edit_v_{req['id']}",
            )
        with rc2:
            new_c = st.selectbox(
                "复杂度",
                options=v_options,
                index=v_options.index(req.get("complexity_rating", "未评")) if req.get("complexity_rating", "未评") in v_options else 0,
                key=f"edit_c_{req['id']}",
            )

    status_changed = new_status != req["status"]
    if status_changed or new_effort is not None:
        if st.button("✅ 确认变更", key=f"confirm_{req['id']}"):
            # 校验
            if new_status == "已终止" and not term_reason_input.strip():
                st.error("「已终止」状态必须填写终止原因")
            else:
                newly_assigned: list[str] = []  # 本次变更新指派的执行人（用于钉钉提醒）
                if status_changed:
                    _record_status_change(req, req["status"], new_status, USER_ID_TO_NAME.get(me, "我"))
                req["status"] = new_status
                if new_status in ("已完成", "已终止"):
                    req["actual_completion_date"] = TODAY_STR
                if new_status == "已终止":
                    req["termination_reason"] = term_reason_input.strip()
                if new_status in ("待启动", "进行中") and assign_owners is not None:
                    old_ids = [i for i in req.get("owner_ids", []) if i]
                    new_ids = [USER_NAME_TO_ID.get(n, "") for n in assign_owners if USER_NAME_TO_ID.get(n)]
                    newly_assigned = [i for i in new_ids if i not in old_ids]
                    # 指派提醒：只保留「沿用且已消读」的执行人，新增/换人的执行人会收到提醒
                    req["assign_seen_by"] = [
                        u for u in req.get("assign_seen_by", []) if u in old_ids and u in new_ids
                    ]
                    req["owner_ids"] = new_ids
                    # 多执行人：自动保持「子需求 ↔ 执行人」一一对应（新增补建、移除删除）
                    _sync_sub_requirements(req)
                if new_status in ("待启动", "进行中") and new_effort is not None:
                    req["effort_estimate_days"] = new_effort
                # 保存期望完成日期
                if new_due_date.strip():
                    req["requested_due_date"] = new_due_date.strip()
                # 保存评分（如果用户开了评分编辑）
                if edit_rating:
                    req["value_rating"] = new_v
                    req["complexity_rating"] = new_c
                    if new_v != "未评" or new_c != "未评":
                        req["rating_source"] = "人工"
                save_requirement(req)
                # 钉钉提醒：完成 → 同步提出人与建议负责人；指派 → 提醒新增的执行人
                if new_status == "已完成":
                    _notify(
                        [req.get("requester_id", ""), req.get("requested_owner_id", "")],
                        f"需求「{req['title']}」（{req['id']}）已完成，特此同步。",
                        req=req,
                    )
                if newly_assigned:
                    sub_cnt = len(req.get("sub_requirements") or [])
                    tail = (
                        f"已自动拆分为 {sub_cnt} 条子需求，请到「需求详情」查看并流转您负责的子需求。"
                        if sub_cnt else "请到「我的看板」查看。"
                    )
                    _notify(
                        newly_assigned,
                        f"需求「{req['title']}」（{req['id']}）已指派给您执行，"
                        f"当前状态「{new_status}」，{tail}",
                        req=req,
                    )
                flash_text = f"✅ 已成功更新状态为「{new_status}」" if status_changed else "✅ 已成功更新"
                st.session_state["_status_change_flash"] = {"req_id": req["id"], "text": flash_text}
                st.rerun()


def _render_sub_requirements(req):
    """子需求区：每条子需求与一位执行人一一对应，状态由对应执行人独立流转（负责人可代改），
    父需求状态按「全量联动」自动推导。"""
    subs = req.get("sub_requirements") or []
    if not subs:
        return
    me = st.session_state.current_user_id
    is_head = (me == req.get("requested_owner_id"))
    done_cnt = sum(1 for s in subs if s.get("status") in ("已完成", "已终止"))

    # 内联成功提示（区别于父需求「状态变更」区块的提示）
    sflash = st.session_state.pop("_sub_change_flash", None)
    if sflash and sflash.get("req_id") == req["id"]:
        st.success(sflash["text"])

    st.markdown("---")
    st.markdown(f"**🧩 子需求**（{len(subs)} 条 · 与执行人一一对应 · 进度 {done_cnt}/{len(subs)}）")
    st.caption("每条子需求由对应执行人独立流转状态，建议责任人可代改并编辑子需求内容；父需求状态随子需求自动联动。")

    for sub in subs:
        cur = sub.get("status", "待启动")
        owner_name = USER_ID_TO_NAME.get(sub.get("owner_id", ""), "—")
        can_operate = (me == sub.get("owner_id")) or is_head
        note_html = (
            f'<div style="font-size:11px;color:#999;margin-top:2px;">{sub.get("note", "")}</div>'
            if sub.get("note") else ""
        )
        row = st.columns([4, 1, 3])
        with row[0]:
            st.markdown(
                f'<div style="padding:6px 8px;background:#F5F5F5;border-radius:4px;">'
                f'<b>{sub["title"]}</b>&nbsp;<span style="color:#888;font-size:11px;">{sub["id"]}</span>'
                f'<div style="font-size:12px;color:#555;margin-top:2px;">👤 {owner_name}</div>'
                f'{note_html}</div>',
                unsafe_allow_html=True,
            )
        with row[1]:
            st.markdown(status_badge(cur), unsafe_allow_html=True)
        with row[2]:
            if can_operate:
                status_opts = [s for s in STATUS_LABELS if s != "待确认"]
                new_st = st.selectbox(
                    "切换状态",
                    options=status_opts,
                    index=status_opts.index(cur) if cur in status_opts else 0,
                    key=f"sub_st_{sub['id']}",
                    label_visibility="collapsed",
                )
                term_reason = ""
                if new_st == "已终止":
                    term_reason = st.text_input(
                        "终止原因（必填）",
                        value=sub.get("termination_reason", ""),
                        key=f"sub_term_{sub['id']}",
                    )
                if st.button("✅ 确认变更", key=f"sub_ok_{sub['id']}", use_container_width=True):
                    if new_st == "已终止" and not term_reason.strip():
                        st.error("「已终止」必须填写终止原因")
                    elif new_st == cur:
                        st.info("状态未变化")
                    else:
                        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                        my_name = USER_ID_TO_NAME.get(me, "我")
                        sub["history"].append({"time": now_str, "user": my_name, "from": cur, "to": new_st})
                        sub["status"] = new_st
                        if new_st == "已终止":
                            sub["termination_reason"] = term_reason.strip()
                        # 父需求状态全量联动
                        agg = _aggregate_parent_status(req)
                        parent_changed = False
                        if agg and agg != req["status"]:
                            _record_status_change(req, req["status"], agg, "（子需求联动）")
                            req["status"] = agg
                            if agg in ("已完成", "已终止"):
                                req["actual_completion_date"] = TODAY_STR
                            parent_changed = True
                        save_requirement(req)
                        # 提醒：子需求完成 → 通知负责人；父需求自动完成 → 同步提出人与负责人
                        all_subs = req.get("sub_requirements") or []
                        d_cnt = sum(1 for s in all_subs if s.get("status") in ("已完成", "已终止"))
                        if new_st == "已完成" and me != req.get("requested_owner_id"):
                            _notify(
                                [req.get("requested_owner_id", "")],
                                f"子需求「{sub['title']}」（{sub['id']}）已由 {my_name} 标记完成，"
                                f"父需求「{req['title']}」进度 {d_cnt}/{len(all_subs)}。",
                                req=req,
                            )
                        if parent_changed and agg == "已完成":
                            _notify(
                                [req.get("requester_id", ""), req.get("requested_owner_id", "")],
                                f"需求「{req['title']}」（{req['id']}）全部子需求已完成，需求已完成。",
                                req=req,
                            )
                        flash_text = f"✅ 子需求 {sub['id']} 已更新为「{new_st}」"
                        if parent_changed:
                            flash_text += f"，父需求联动为「{agg}」"
                        st.session_state["_sub_change_flash"] = {"req_id": req["id"], "text": flash_text}
                        st.rerun()
            else:
                st.caption(f"由执行人 {owner_name} 操作")

        with st.expander(f"🕘 变更记录（{sub['id']} · {len(sub.get('history') or [])} 条）"):
            hist = sub.get("history") or []
            if hist:
                for h in reversed(hist):
                    st.markdown(f"- {h['time']} · {h['user']}：{h.get('from', '—')} → {h.get('to', '—')}")
            else:
                st.caption("暂无流转记录")
        if is_head:
            with st.expander(f"✏️ 编辑子需求内容（{sub['id']}）"):
                with st.form(f"sub_edit_{sub['id']}"):
                    edit_title = st.text_input("子需求标题", value=sub.get("title", ""), key=f"sub_edit_t_{sub['id']}")
                    edit_note = st.text_area("说明（选填）", value=sub.get("note", ""), height=68, key=f"sub_edit_n_{sub['id']}")
                    if st.form_submit_button("💾 保存", use_container_width=True):
                        sub["title"] = edit_title.strip() or sub.get("title", "")
                        sub["note"] = edit_note.strip()
                        save_requirement(req)
                        st.session_state["_sub_change_flash"] = {"req_id": req["id"], "text": f"✅ 已更新子需求 {sub['id']}"}
                        st.rerun()
