"""
pages/user_management.py — 从 app.py 拆分生成（函数体逐字保留）。
"""
from __future__ import annotations

# ---- 第三方 / 标准库 ----
from sample_data import USER_ROLES
import db
import dingtalk
import streamlit as st

# ---- 项目内部 ----
from services.people import _default_user_email, _user_notify_channel
from state import USER_ID_TO_NAME, get_current_user, get_users, reload_users
from ui.layout import _nav_tabs



def page_user_management():
    st.markdown("## 👥 用户管理")
    cur = get_current_user()
    if cur and cur["role"] != "管理层":
        _render_change_password(cur)
        return

    # 横向标签菜单（与项目视图/系统评估同一套 _nav_tabs）。
    # 原来四个区块竖着堆一长页；现拆为互斥标签，每次 rerun 只执行当前区块。
    # 「修改」与「删除」共用同一个「选择用户」下拉，必须放在同一标签。
    users = get_users()
    _tab = _nav_tabs(
        [
            ("list", "👥 现有用户"),
            ("add", "➕ 新增用户"),
            ("edit", "✏️ 修改 / 删除"),
            ("sync", "🔄 钉钉同步"),
        ],
        state_key="user_mgmt_tab",
    )
    if _tab == "list":
        _render_user_list_section(users)
    elif _tab == "add":
        _render_user_add_section(users)
    elif _tab == "edit":
        _render_user_edit_section(users)
    else:
        _render_user_sync_section(users)


def _render_change_password(cur):
    """非管理层：仅可修改自己的密码。"""
    st.caption("个人账户：仅可修改自己的密码")
    with st.form("change_own_password"):
        new_pwd = st.text_input("新密码", type="password")
        confirm_pwd = st.text_input("确认新密码", type="password")
        submit = st.form_submit_button("💾 保存密码", type="primary", use_container_width=True)
    if submit:
        if not new_pwd.strip():
            st.error("密码不能为空")
        elif new_pwd != confirm_pwd:
            st.error("两次输入的密码不一致")
        else:
            db.save_user(
                {"id": cur["id"], "name": cur["name"], "role": cur["role"], "department": cur.get("department", "")},
                password=new_pwd.strip(),
            )
            reload_users()
            st.session_state["_flash"] = {"type": "success", "text": "✅ 密码已更新"}
            st.rerun()


def _render_user_list_section(users):
    """👥 现有用户：花名册表 + 无提醒渠道预警。"""
    if users:
        rows = [
            {"ID": u["id"], "姓名": u["name"], "角色": u["role"], "部门": u.get("department", ""),
             "手机号": u.get("phone") or "", "邮箱": u.get("email") or "",
             "钉钉匹配": "✓" if u.get("dingtalk_userid") else "",
             "提醒渠道": _user_notify_channel(u)}
            for u in users
        ]
        st.dataframe(rows, use_container_width=True)
        no_channel = [u["name"] for u in users if _user_notify_channel(u) == "⚠ 无"]
        if no_channel:
            st.warning(f"⚠ 以下用户无任何可达提醒渠道（不在提醒群且未填邮箱）：{'、'.join(no_channel)}")
    else:
        st.info("暂无用户")


def _render_user_sync_section(users):
    """🔄 钉钉通讯录同步（按姓名自动匹配，免填手机号/邮箱）。"""
    st.caption(
        "按姓名自动匹配钉钉用户并回填「钉钉 userid / 手机号」，之后钉钉提醒直接 @ 到人。"
        "需在 .env 配置企业内部应用 DINGTALK_APP_KEY / DINGTALK_APP_SECRET（钉钉开放平台创建，"
        "开通通讯录读取权限）。姓名需与钉钉一致且企业内无重名，否则会列出提示。"
    )
    if not dingtalk.app_configured():
        st.info("未配置企业内部应用（DINGTALK_APP_KEY / DINGTALK_APP_SECRET），暂不可同步。")
    elif st.button("🔄 按姓名同步", key="dingtalk_sync"):
        result = dingtalk.list_org_users()
        if result is None:
            st.error("同步失败：请检查 AppKey/AppSecret 是否正确、通讯录读取权限是否已开通（详见启动控制台日志）。")
        else:
            name_to_userid, name_to_mobile, ambiguous = result
            matched, unmatched = [], []
            for u in users:
                hit = name_to_userid.get(u["name"])
                if hit:
                    # 手机号只在应用有权限且用户尚未填写时回填
                    mobile = name_to_mobile.get(u["name"], "")
                    db.save_user({**u, "dingtalk_userid": hit,
                                  **({"phone": mobile} if mobile and not u.get("phone") else {})})
                    matched.append(u["name"])
                else:
                    unmatched.append(u["name"])
            reload_users()
            amb = [n for n in ambiguous if n in {u["name"] for u in users}]
            parts = [f"✅ 匹配成功 {len(matched)} 人"]
            if unmatched:
                parts.append(f"⚠ 未匹配（钉钉通讯录无同名）：{'、'.join(unmatched)}，请为其手工填手机号")
            if amb:
                parts.append(f"⚠ 重名跳过（企业内同名多人）：{'、'.join(amb)}，请为其手工填手机号")
            st.session_state["_flash"] = {"type": "success", "text": "；".join(parts)}
            st.rerun()


def _render_user_add_section(users):
    """➕ 新增用户。"""
    with st.form("add_user_form"):
        c1, c2 = st.columns(2)
        with c1:
            new_name = st.text_input("姓名 *")
            new_role = st.selectbox("角色", options=USER_ROLES, index=2)  # 默认工程师
            new_phone = st.text_input("手机号（钉钉@提醒用，可空）", placeholder="用于钉钉提醒时 @ 到人")
            new_email = st.text_input(
                "邮箱（邮件提醒用，留空自动按「姓名拼音@example.com」生成）",
                placeholder="用于接收邮件提醒",
            )
        with c2:
            new_dept = st.text_input("部门")
            new_pwd = st.text_input("初始密码 *", type="password", value="123456")
            new_in_group = st.checkbox("在钉钉提醒群中（不勾选则不群@；邮件提醒与是否勾选无关）", value=True)
        add_submit = st.form_submit_button("✅ 新增用户", type="primary", use_container_width=True)
    if add_submit:
        if not new_name.strip():
            st.error("姓名不能为空")
        elif any(u["name"] == new_name.strip() for u in users):
            st.error(f"姓名「{new_name.strip()}」已存在")
        elif not new_pwd.strip():
            st.error("初始密码不能为空")
        else:
            uid = f"U{db.next_user_seq():03d}"
            db.save_user(
                {"id": uid, "name": new_name.strip(), "role": new_role, "department": new_dept.strip(),
                 "phone": new_phone.strip(),
                 "email": new_email.strip() or _default_user_email(new_name.strip()),
                 "notify_by_group": new_in_group},
                password=new_pwd.strip(),
            )
            reload_users()
            st.session_state["_flash"] = {"type": "success", "text": f"✅ 已新增用户：{new_name.strip()}（{uid}）"}
            st.rerun()


def _render_user_edit_section(users):
    """✏️ 修改 / 删除用户（删除与修改共用同一个「选择用户」，必须同标签）。"""
    if not users:
        st.info("暂无用户")
        return
    edit_id = st.selectbox(
        "选择用户",
        options=[u["id"] for u in users],
        format_func=lambda x: f"{USER_ID_TO_NAME.get(x, x)}（{x}）",
    )
    eu = next((u for u in users if u["id"] == edit_id), None)
    if not eu:
        return
    with st.form("edit_user_form"):
        e1, e2 = st.columns(2)
        with e1:
            e_name = st.text_input("姓名", value=eu["name"])
            e_role = st.selectbox(
                "角色",
                options=USER_ROLES,
                index=USER_ROLES.index(eu["role"]) if eu["role"] in USER_ROLES else 0,
            )
            e_phone = st.text_input("手机号（钉钉@提醒用，可空）", value=eu.get("phone") or "")
            e_email = st.text_input(
                "邮箱（邮件提醒用，留空按「姓名拼音@example.com」生成）",
                value=eu.get("email") or "",
            )
        with e2:
            e_dept = st.text_input("部门", value=eu.get("department", ""))
            e_pwd = st.text_input("重置密码（留空则不修改）", type="password", value="")
            e_in_group = st.checkbox("在钉钉提醒群中（不勾选则不群@；邮件提醒与是否勾选无关）",
                                     value=eu.get("notify_by_group", True))
        save_submit = st.form_submit_button("💾 保存修改", use_container_width=True)
    if save_submit:
        if not e_name.strip():
            st.error("姓名不能为空")
        elif any(u["name"] == e_name.strip() and u["id"] != edit_id for u in users):
            st.error(f"姓名「{e_name.strip()}」已被其他用户使用")
        else:
            db.save_user(
                {"id": edit_id, "name": e_name.strip(), "role": e_role, "department": e_dept.strip(),
                 "phone": e_phone.strip(),
                 "email": e_email.strip() or _default_user_email(e_name.strip()),
                 "notify_by_group": e_in_group},
                password=e_pwd.strip() or None,
            )
            reload_users()
            st.session_state["_flash"] = {"type": "success", "text": f"✅ 已更新用户：{e_name.strip()}"}
            st.rerun()

    # 删除（与修改共用上面选中的用户，故留在同一标签内）
    st.markdown("---")
    st.markdown("### 🗑 删除用户")
    is_self = edit_id == st.session_state.get("current_user_id")
    mgmt_count = sum(1 for u in users if u["role"] == "管理层")
    is_last_mgmt = eu["role"] == "管理层" and mgmt_count <= 1
    if is_self:
        st.warning("不能删除当前登录的账号")
    elif is_last_mgmt:
        st.warning("不能删除最后一位管理层账号")
    else:
        st.warning(f"确定删除用户「{eu['name']}」？此操作不可恢复。")
        confirm = st.checkbox("我确认删除", key=f"del_user_confirm_{edit_id}")
        if st.button("🗑 确认删除", disabled=not confirm, key=f"del_user_btn_{edit_id}"):
            db.delete_user(edit_id)
            reload_users()
            st.session_state["_flash"] = {"type": "success", "text": f"✅ 已删除用户：{eu['name']}"}
            st.rerun()
