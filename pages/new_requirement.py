"""
pages/new_requirement.py — 从 app.py 拆分生成（函数体逐字保留）。
"""
from __future__ import annotations

# ---- 第三方 / 标准库 ----
from datetime import datetime
from sample_data import ALL_TAGS, PRIORITY_LABELS, SOURCE_LABELS
import ai_helper
import db
import streamlit as st

# ---- 项目内部 ----
from config import PROJECT_STAGES, TODAY_STR
from services.projects import _project_display_name, get_projects
from services.requirements import gen_req_id, get_requirements, save_requirement, simple_keyword_match
from state import USERS, USER_ID_TO_NAME
from ui.components import _date_picker



def page_new_requirement():
    # 填写计时（阶段效率埋点）：首次进入本页起算，setdefault 保证后续 rerun
    # （AI 抽取、换控件、传附件）不重置基准；提交后由 _reset_new_requirement_form 清零。
    # 用户离开再回来会沿用旧基准导致虚高——统计端以 4 小时为界丢弃（见提交处）。
    st.session_state.setdefault("_req_form_open_dt", datetime.now())
    st.markdown("## ➕ 新建需求")

    st.markdown("#### 步骤 1：粘贴原始信息（聊天 / 邮件 / 描述）")
    raw_input = st.text_area(
        "原始信息",
        height=150,
        placeholder="例如：客户示例客户C集成的客户工程师昨天在群里反馈，说我们的测试设备在高速贴装时视觉定位精度不稳，部分 IC 偏移到 0.05mm，希望我们能优化到 0.02mm，下周三之前给方案。",
        label_visibility="collapsed",
    )
    st.caption("💡 习惯语音输入的话：可先在微信/QQ 等社交软件里发语音，长按转文字后复制，粘贴到此处即可。")

    col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 4])
    with col_btn1:
        run_extract = st.button("🤖 AI 智能抽取", type="primary", disabled=not raw_input.strip())
    with col_btn2:
        use_template = st.button("📋 手动表单")

    if run_extract:
        with st.spinner("AI 正在抽取字段…"):
            result = ai_helper.extract_requirement_fields(
                user_input=raw_input,
                provider=st.session_state.provider,
                api_key=st.session_state.api_key,
                model=st.session_state.model or None,
            )
        st.session_state["last_extract_result"] = {
            "success": result.success,
            "data": result.data,
            "raw": result.raw,
            "error": result.error,
            "latency_ms": result.latency_ms,
            "model": result.model,
        }
        # 标签推荐已合并进 A1 抽取（recommended_tags），不再单独调用 A4
        if result.success and result.data:
            _rec_tags = result.data.get("recommended_tags", [])
            st.session_state["last_tag_result"] = {
                "success": True,
                "data": [{"tag": t, "confidence": 1.0} for t in _rec_tags],
            }
            # 初始化表单值
            st.session_state["form_values"] = result.data
            # 同步到表单 widget 的 session_state key
            # （表单字段已带 key，key 存在时 value/index 参数会被 Streamlit 忽略，必须直接写 session_state）
            _d = result.data
            st.session_state["req_title"] = _d.get("title", "")
            st.session_state["req_description"] = _d.get("description", "")
            st.session_state["req_customer"] = _d.get("customer", "")
            _due = _d.get("requested_due_date", "")
            st.session_state["req_due_year"] = ""
            st.session_state["req_due_month"] = "（请选择）"
            st.session_state["req_due_day"] = ""
            if _due:
                try:
                    _y, _m, _dd = (int(x) for x in _due.split("-"))
                    st.session_state["req_due_year"] = str(_y)
                    st.session_state["req_due_month"] = f"{_m}月"
                    st.session_state["req_due_day"] = str(_dd)
                except Exception:
                    pass
            st.session_state["req_source"] = _d.get("source") if _d.get("source") in SOURCE_LABELS else "（请选择）"
            _pri = _d.get("priority_hint", "未知")
            st.session_state["req_priority"] = _pri if _pri in PRIORITY_LABELS else "未知"
            # 建议责任人：只有原文包含唯一的完整主管姓名时才自动选中，避免“客户工程师”等称呼误匹配。
            st.session_state["req_owner"] = "（请选择）"
            st.session_state.pop("a1_owner_hint", None)
            _requested_owner = _d.get("requested_owner", "")
            if _requested_owner:
                _heads = [u for u in USERS if u["role"] == "主管"]
                _matched = [u["name"] for u in _heads if u["name"] in _requested_owner]
                if len(_matched) == 1:
                    st.session_state["req_owner"] = _matched[0]
                else:
                    st.session_state["a1_owner_hint"] = _requested_owner
            # AI 建议的价值/复杂度评分（「未知」已在 ai_helper 归一化为「未评」）
            _vr = _d.get("value_rating", "未评")
            st.session_state["form_value_rating"] = _vr if _vr in ("高", "中", "低", "未评") else "未评"
            _cr = _d.get("complexity_rating", "未评")
            st.session_state["form_complexity_rating"] = _cr if _cr in ("高", "中", "低", "未评") else "未评"

    last = st.session_state.get("last_extract_result")
    if last:
        if last["success"]:
            st.success(f"✅ AI 抽取完成 · 耗时 {last['latency_ms']}ms · 模型 {last['model']}")
            _uncertain = (last.get("data") or {}).get("uncertain_fields") or []
            _field_labels = {
                "title": "标题", "description": "描述", "source": "部门", "customer": "客户名",
                "requested_due_date": "期望完成日期", "priority_hint": "优先级", "requested_owner": "责任人", "tags": "标签",
            }
            if _uncertain:
                st.warning("请重点确认：" + "、".join(_field_labels.get(item, item) for item in _uncertain))
            else:
                st.caption("AI 已完成首轮预填；提交前请以原始材料核对字段，不确定项会保持为空。")
        else:
            st.error(f"❌ AI 抽取失败：{last['error']}")
            st.info("👉 您仍可使用下方手动表单填写")

    if st.session_state.get("a1_owner_hint"):
        st.info(f"AI 在原文中识别到责任人提及“{st.session_state['a1_owner_hint']}”，但未与系统主管名单精确匹配，请在下方手动确认。")

    # 表单（无论 AI 是否成功都显示，便于用户修改）
    # 处理「提交后清空表单」标记（必须在表单渲染前执行，避免 widget 已实例化报错）
    if st.session_state.pop("_clear_form", False):
        _reset_new_requirement_form()

    form_values = st.session_state.get("form_values") or {}

    st.markdown("---")
    st.markdown("#### 步骤 2：确认 / 修改字段")

    uploader_version = st.session_state.get("req_uploader_version", 0)

    # 关联项目 + 项目阶段（放在表单外，便于「阶段」随「项目」实时禁用/启用）
    proj_col1, proj_col2 = st.columns(2)
    with proj_col1:
        project_name = st.selectbox(
            "关联项目",
            options=["（无）"] + [_project_display_name(p) for p in get_projects()],
            key="req_project",
        )
    with proj_col2:
        project_stage = st.selectbox(
            "项目阶段",
            options=["（请选择）"] + PROJECT_STAGES,
            key="req_stage",
            disabled=(project_name == "（无）"),
        )
    st.caption(
        "💡 关联项目选「（无）」时，大项目视图按标题自动归类：含 A/B/C → 对应大项目"
        "（多标签取最先出现的）；否则含 AI → 研发AI赋能；都不是 → 其他"
    )

    with st.form("req_form"):
        col_l, col_r = st.columns(2)
        with col_l:
            title = st.text_input("标题 *", value=form_values.get("title", ""), key="req_title")
            source_options = ["（请选择）"] + SOURCE_LABELS
            source_default = 0
            if form_values.get("source") in SOURCE_LABELS:
                source_default = source_options.index(form_values.get("source"))
            source = st.selectbox(
                "部门 *",
                options=source_options,
                index=source_default,
                key="req_source",
            )
            customer = st.text_input("客户名", value=form_values.get("customer", ""), key="req_customer")
            priority = st.selectbox(
                "优先级",
                options=PRIORITY_LABELS,
                index=PRIORITY_LABELS.index(form_values.get("priority_hint", "未知")) if form_values.get("priority_hint") in PRIORITY_LABELS else len(PRIORITY_LABELS) - 1,
                key="req_priority",
            )
        with col_r:
            description = st.text_area("描述 *", value=form_values.get("description", ""), height=150, key="req_description")
            requested_due_date = _date_picker("期望完成日期", form_values.get("requested_due_date", ""), prefix="req_due")
            heads = [u for u in USERS if u["role"] == "主管"]
            selected_head_name = st.selectbox(
                "建议责任人 *",
                options=["（请选择）"] + [u["name"] for u in heads],
                index=0,
                key="req_owner",
            )
            st.caption("仅在原文明确出现系统主管的完整姓名时自动预填；“客户工程师”等称呼需人工确认。")
            head = next((u for u in heads if u["name"] == selected_head_name), None)

        # 价值-复杂度评分（v0.2.2 新增，可选）
        st.markdown("##### 价值-复杂度评分（可选，留空=未评）")
        st.caption("仅项目负责人/管理层在评审时建议填写；工程师无需填。AI 也可推断（会标 AI 标记）。")
        vc_col1, vc_col2 = st.columns(2)
        with vc_col1:
            value_rating = st.selectbox(
                "价值（对客户/公司业务）",
                options=["未评"] + ["高", "中", "低"],
                index=0,
                key="form_value_rating",
            )
        with vc_col2:
            complexity_rating = st.selectbox(
                "复杂度（技术实现）",
                options=["未评"] + ["高", "中", "低"],
                index=0,
                key="form_complexity_rating",
            )

        # 佐证材料（必填）— v0.2.0 新增
        st.markdown("##### 佐证材料 *（必填，符合「书面留痕」原则）")
        st.caption("至少填写一项链接或上传一份文件。例如：邮件原文链接、聊天截图、技术协议、会议纪要。")
        evidence_link = st.text_input(
            "佐证材料链接 1",
            placeholder="例如：邮件原文链接 / 共享盘路径 / 钉钉消息链接",
            key="evidence_link_1",
        )
        evidence_link2 = st.text_input(
            "佐证材料链接 2（可选）",
            placeholder="可选的第二条链接",
            key="evidence_link_2",
        )
        uploaded_evidence = st.file_uploader(
            "或上传文件（可多选，支持图片/文档/音频）",
            type=["png", "jpg", "jpeg", "pdf", "docx", "xlsx", "txt", "eml",
                  "mp3", "wav", "m4a", "aac", "ogg", "amr", "wma", "flac"],
            accept_multiple_files=True,
            key=f"evidence_upload_{uploader_version}",
        )

        # 标签推荐
        st.markdown("##### AI 推荐标签（A4）")
        tag_result_data = st.session_state.get("last_tag_result", {})
        recommended_tags = []
        if tag_result_data.get("success") and tag_result_data.get("data"):
            recommended_tags = [t.get("tag") for t in tag_result_data["data"] if t.get("tag")]

        selected_tags = []
        if recommended_tags:
            tag_cols = st.columns(min(len(recommended_tags) + len(ALL_TAGS), 6))
            # 推荐标签高亮
            for tag in ALL_TAGS:
                checked = tag in recommended_tags
                if st.checkbox(tag, value=checked, key=f"tag_{tag}"):
                    selected_tags.append(tag)
        else:
            st.caption("（暂无 AI 推荐，可手动勾选）")
            for tag in ALL_TAGS:
                if st.checkbox(tag, value=False, key=f"tag_{tag}"):
                    selected_tags.append(tag)

        submit_clicked = st.form_submit_button("提交需求", type="primary")

    # 提交反馈（紧邻提交按钮显示）
    submit_result = st.session_state.pop("_req_submit_result", None)
    if submit_result:
        if submit_result["type"] == "success":
            st.success(submit_result["text"])
        else:
            st.error(submit_result["text"])

    # A3 清晰度检查（在表单外，提交前触发）
    st.markdown("---")
    st.markdown("#### 步骤 3：AI 清晰度检查（A3）")
    if st.button("🔍 检查清晰度", disabled=not (title and description)):
        with st.spinner("AI 正在检查…"):
            result = ai_helper.check_clarity(
                title=title,
                description=description,
                source=source,
                customer=customer,
                provider=st.session_state.provider,
                api_key=st.session_state.api_key,
                model=st.session_state.model or None,
            )
        if result.success:
            st.session_state["last_clarity_result"] = result.data
        else:
            st.session_state["last_clarity_result"] = None
            st.error(f"❌ 清晰度检查失败：{result.error}")

    last_clarity = st.session_state.get("last_clarity_result")
    if last_clarity is not None:
        if last_clarity:
            st.warning(f"AI 提出 {len(last_clarity)} 条建议（可选）：")
            for i, s in enumerate(last_clarity, 1):
                st.markdown(f"**{i}. {s.get('dimension', '')}** — {s.get('suggestion', '')}")
        else:
            st.success("✅ 描述清晰，AI 无补充建议")

    # A2 相似检测
    st.markdown("---")
    st.markdown("#### 步骤 4：AI 相似需求检测（A2）")
    if st.button("🔎 检测相似需求", disabled=not (title and description)):
        # 简单基于关键词在现有需求中找 Top 3 候选（POC 用，避免 Embedding 部署）
        candidates = simple_keyword_match(title + " " + description, get_requirements(), top_n=3)
        if not candidates:
            st.info("未找到候选")
        else:
            with st.spinner(f"AI 正在复核 {len(candidates)} 条候选…"):
                result = ai_helper.check_similarity(
                    new_title=title,
                    new_description=description,
                    candidates=[{"id": c["id"], "title": c["title"], "description": c.get("description", "")} for c in candidates],
                    provider=st.session_state.provider,
                    api_key=st.session_state.api_key,
                    model=st.session_state.model or None,
                )
            if result.success:
                st.session_state["last_sim_result"] = {"candidates": candidates, "judgement": result.data}
            else:
                st.error(f"❌ 相似检测失败：{result.error}")

    last_sim = st.session_state.get("last_sim_result")
    if last_sim:
        st.markdown("**Top 3 相似候选**：")
        judgement = {item["id"]: item for item in last_sim["judgement"]} if last_sim.get("judgement") else {}
        for c in last_sim["candidates"]:
            j = judgement.get(c["id"], {})
            dup = j.get("duplicate", False)
            conf = j.get("confidence", 0)
            icon = "🔁" if dup else "✅"
            color = "#E53935" if dup else "#43A047"
            st.markdown(
                f'<div style="padding:8px;border-left:3px solid {color};margin:6px 0;">'
                f'{icon} <b>{c["id"]}</b> 「{c["title"]}」'
                f' &nbsp;<span style="color:{color};font-weight:600;">{"重复" if dup else "不重复"}（{conf:.2f}）</span>'
                f'<div style="color:#666;font-size:12px;">{j.get("reason","")}</div></div>',
                unsafe_allow_html=True,
            )

    # 提交逻辑（在表单外，因为 form_submit 已经在 form 内）
    if submit_clicked:
        if not title or not description:
            st.session_state["_req_submit_result"] = {"type": "error", "text": "标题与描述为必填项"}
            st.rerun()
        elif source not in SOURCE_LABELS:
            st.session_state["_req_submit_result"] = {"type": "error", "text": "部门为必填项，请选择"}
            st.rerun()
        elif not head:
            st.session_state["_req_submit_result"] = {"type": "error", "text": "建议责任人为必填项，请选择"}
            st.rerun()
        elif not (evidence_link.strip() or evidence_link2.strip() or uploaded_evidence):
            st.session_state["_req_submit_result"] = {"type": "error", "text": "佐证材料为必填项（至少一条链接或一个文件），符合「书面留痕」原则"}
            st.rerun()
        elif project_name != "（无）" and project_stage == "（请选择）":
            st.session_state["_req_submit_result"] = {"type": "error", "text": "已关联项目，请选择项目阶段"}
            st.rerun()
        else:
            # 填写耗时（阶段效率埋点）：从首次进入本页到点击提交的秒数；
            # 超过 4 小时视为「挂起跨会话」（离开页面很久再回来提交），不纳入统计。
            fill_secs = None
            _opened = st.session_state.get("_req_form_open_dt")
            if isinstance(_opened, datetime):
                _d = (datetime.now() - _opened).total_seconds()
                if 0 < _d <= 4 * 3600:
                    fill_secs = round(_d)
            # 是否 AI 预填：本会话做过一次成功的智能抽取即算（form_values/表单值已被预填）
            ai_assisted = bool((st.session_state.get("last_extract_result") or {}).get("success"))

            # 生成需求 ID（先于 evidence 构造，上传文件按此 ID 落盘归档）
            new_req_id = gen_req_id()

            # 构造 evidence 列表
            ev_list = []
            if evidence_link.strip():
                ev_list.append({
                    "type": "other", "label": evidence_link.strip()[:80],
                    "link": evidence_link.strip(),
                    "uploaded_by": st.session_state.current_user_id,
                    "uploaded_at": TODAY_STR,
                })
            if evidence_link2.strip():
                ev_list.append({
                    "type": "other", "label": evidence_link2.strip()[:80],
                    "link": evidence_link2.strip(),
                    "uploaded_by": st.session_state.current_user_id,
                    "uploaded_at": TODAY_STR,
                })
            for f in (uploaded_evidence or []):
                # 上传文件：真正保存内容到 data/attachments/<req_id>/，供详情页下载
                content = f.read()
                meta = db.save_attachment(
                    req_id=new_req_id,
                    filename=f.name,
                    content=content,
                    uploaded_by=st.session_state.current_user_id,
                )
                ev_list.append({
                    "type": "file",
                    "label": f.name,
                    "link": "",
                    "filename": meta["filename"],
                    "size": meta["size"],
                    "uploaded_by": st.session_state.current_user_id,
                    "uploaded_at": TODAY_STR,
                })

            # 建议责任人 = 所选主管
            requested_owner_id = head["id"] if head else ""
            department = head.get("department", "") if head else ""
            rating_source_label = ""
            if value_rating != "未评" or complexity_rating != "未评":
                rating_source_label = "人工"

            new_req = {
                "id": new_req_id,
                "title": title,
                "description": description,
                "evidence": ev_list,
                "source": source,
                "source_ref": "",
                "customer": customer,
                "requester_id": st.session_state.current_user_id,
                "requested_owner_id": requested_owner_id,
                "owner_ids": [],
                "department": department,
                "project_id": next((p["id"] for p in get_projects() if _project_display_name(p) == project_name), "") if project_name != "（无）" else "",
                "project_stage": project_stage if (project_name != "（无）" and project_stage != "（请选择）") else "",
                "status": "待确认",
                "priority": priority,
                "value_rating": value_rating,
                "complexity_rating": complexity_rating,
                "rating_source": rating_source_label,
                "tags": selected_tags,
                "requested_due_date": requested_due_date,
                "created_at": TODAY_STR,
                # ---- 阶段效率埋点（系统评估 → 阶段效率）----
                # created_at 只有日期精度，做阶段耗时需要秒级锚点；status_history 首条
                # 记录「（新建）→ 待确认」，与后续流转记录构成完整时间链；
                # form_fill_seconds / ai_assisted 供「填写效率」与 AI 增益对比。
                "created_dt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "form_fill_seconds": fill_secs,
                "ai_assisted": ai_assisted,
                "status_history": [{
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "user": USER_ID_TO_NAME.get(st.session_state.current_user_id, ""),
                    "from": "（新建）",
                    "to": "待确认",
                }],
                "review_state": "待责任人确认",
                "proposed_changes": None,
                "effort_estimate_days": 0,
                "comments": [],
                "liked_by": [],
            }
            save_requirement(new_req)
            # 【暂停提醒·试用】进入需求池前的共识流程提醒，试用效果好后取消注释恢复
            # _notify(
            #     [requested_owner_id],
            #     f"{USER_ID_TO_NAME.get(st.session_state.current_user_id, '同事')} 提交了新需求「{title}」，等待您确认。",
            #     req=new_req,
            # )
            st.session_state["_req_submit_result"] = {
                "type": "success",
                "text": f"✅ 已创建：{new_req['id']} 「{new_req['title']}」\n\n📎 已附 {len(ev_list)} 份佐证材料 · 状态：待确认（等待责任人确认后进入需求池）",
            }
            # 标记清空表单（在下次渲染前执行，避免 widget 已实例化报错）
            st.session_state["_clear_form"] = True
            st.rerun()


def _reset_new_requirement_form():
    """清空表单：将各控件 session_state 重置为默认值。必须在表单渲染前调用（否则会报 widget 已实例化）。"""
    # 文本输入 / 文本域 / 链接
    for k in ["req_title", "req_description", "req_customer",
              "evidence_link_1", "evidence_link_2"]:
        st.session_state[k] = ""
    # 下拉框回到“未选择”，不要把“其他”或第一位主管伪装成 AI 结果。
    st.session_state.req_source = "（请选择）"
    st.session_state.req_priority = PRIORITY_LABELS[-1]  # 未知
    st.session_state.req_owner = "（请选择）"
    st.session_state.req_project = "（无）"
    st.session_state.req_stage = "（请选择）"
    st.session_state.req_due_year = ""
    st.session_state.req_due_month = "（请选择）"
    st.session_state.req_due_day = ""
    st.session_state.form_value_rating = "未评"
    st.session_state.form_complexity_rating = "未评"
    # 文件上传：不能直接赋值，通过更换 key（递增版本号）来重置
    st.session_state.req_uploader_version = st.session_state.get("req_uploader_version", 0) + 1
    # 标签
    for tag in ALL_TAGS:
        st.session_state[f"tag_{tag}"] = False
    # 临时状态
    for k in ["form_values", "last_extract_result", "last_tag_result", "last_clarity_result", "last_sim_result", "a1_owner_hint"]:
        st.session_state.pop(k, None)
    # 填写计时基准也一并清掉：下一次填写从重新打开表单起算（阶段效率埋点）
    st.session_state.pop("_req_form_open_dt", None)
