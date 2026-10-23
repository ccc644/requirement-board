"""
ui/dialogs.py — 从 app.py 拆分生成（函数体逐字保留）。
"""
from __future__ import annotations

# ---- 第三方 / 标准库 ----
import db
import streamlit as st

# ---- 项目内部 ----
from config import SATISFACTION_LABELS, SURVEY_COOLDOWN_DAYS, SURVEY_FEATURES, SURVEY_QUESTIONS, SURVEY_QUESTION_SET, SURVEY_SCORE_LABELS
from services.evaluation import _pending_ratings, _rater_role, _ratings, _req_raters, _set_rating, _survey_due
from services.requirements import get_requirement, save_requirement
from state import get_current_user
from ui.components import _star_html



def _render_rating_notice(user_id: str) -> None:
    """主区提醒条 + 评价入口（仅提出人本人可见）。"""
    pending = _pending_ratings(user_id)
    if not pending:
        return
    n = len(pending)
    titles = "、".join(f"「{r['title']}」" for r in pending[:3])
    more = " 等" if n > 3 else ""
    st.info(f"⭐ 您有 {n} 条已完成需求待评价：{titles}{more}。"
            f"您是本需求的关联人，评价用于衡量协作与交付质量。")
    options = {f"{r['id']} · {r['title']}": r["id"] for r in pending}
    opts = list(options.keys())
    # 评价完一条后待选列表会变，残留的选中值不在新选项中会导致组件报错，这里先清掉
    if st.session_state.get("rate_pick") not in opts:
        st.session_state.pop("rate_pick", None)
    c1, c2 = st.columns([4, 1])
    with c1:
        pick = st.selectbox("选择要评价的需求", options=opts, key="rate_pick")
    with c2:
        st.markdown('<div style="height:28px;"></div>', unsafe_allow_html=True)
        if st.button("⭐ 去评价", type="primary", use_container_width=True, key="rate_go"):
            st.session_state["_rating_req"] = options[pick]
            st.rerun()


def _render_improvement_notice(user_id: str) -> None:
    """改进建议进展回告提醒条（仅提出人本人可见，03 蓝图 §5.5 + 队长 t11 硬要求）。

    与 _render_rating_notice 同构的独立渲染路径（不并入 _pending_notices_all 的 7 元组，C9）。
    站内可见性只由「需回告终态 + 未确认(ack)」决定——即使钉钉/邮件未配置或外发失败，
    提出人登录后也一定在主区看到进展，闭环不会因外发通道缺失而断掉。
    """
    items = db.pending_improvement_notice(user_id)
    if not items:
        return
    for it in items[:3]:
        note = f"说明：{it['resolution_note']}" if it.get("resolution_note") else ""
        st.success(f"📢 您提出的改进建议有进展：「{it['summary']}」已{it['status']}。{note}")
        if st.button("✅ 知道了", key=f"imp_ack_{it['id']}"):
            ok, msg = db.ack_improvement(it["id"], user_id)
            if not ok:
                st.error(msg)
            st.rerun()
    if len(items) > 3:
        st.info(f"还有 {len(items) - 3} 条改进建议的进展待确认，见下方「我提交的改进建议」。")


def _render_my_improvements(user_id: str) -> None:
    """「我的反馈与进展」个人视图（03 蓝图 §3.3：每个用户仅本人）。

    与提醒条互补：提醒条只报终态，这里展示本人全部改进项的当前状态——
    让「提了有用」在处理过程中就可见，而不必等到终态。
    """
    mine = db.load_improvements(proposer_id=user_id, with_proposer=False)
    if not mine:
        return
    with st.expander(f"🧭 我提交的改进建议（{len(mine)} 条）"):
        for it in mine:
            flags = []
            if it["notified_at"]:
                flags.append(f"已回告 {it['notified_at']}")
            if it["proposer_ack_at"]:
                flags.append("您已确认")
            st.markdown(
                f'<div style="border-left:3px solid #00897B;padding:4px 10px;margin-bottom:6px;">'
                f'<div>「{it["summary"]}」 · <b>{it["status"]}</b>'
                + (f' —— {it["resolution_note"]}' if it["resolution_note"] else '')
                + '</div>'
                f'<div style="font-size:12px;color:#666;">提出于 {it["created_at"]}'
                + (f' · {" · ".join(flags)}' if flags else '')
                + '</div></div>',
                unsafe_allow_html=True,
            )


def _render_improvement_feed_public() -> None:
    """🌟 改进动态（全员公示，07 审查 F-05 裁决 A）：所有登录用户在主区可见的只读公示。

    03 蓝图 §5/§5.5 的「向全员展示已落地成果」落地于此（评估页内的同名区块仍仅管理层+主管）；
    只显示摘要与上线时间，不新增敏感信息（取数即不查提出人姓名，with_proposer=False）。
    """
    live = db.load_improvements(status="已上线", with_proposer=False)
    if not live:
        return
    with st.expander(f"🌟 改进动态（已上线 {len(live)} 条）"):
        for it in live[:10]:
            st.markdown(
                f'<div style="border-left:3px solid #43A047;padding:3px 10px;margin-bottom:4px;">'
                f'🎉 「{it["summary"]}」已上线'
                f'<span style="color:#666;font-size:12px;margin-left:8px;">'
                f'{it["closed_at"] or it["updated_at"] or ""}</span></div>',
                unsafe_allow_html=True,
            )


def _rating_dialog(req_id: str):
    """评价弹窗。保持「未提交就持续渲染」——Streamlit 每次交互重跑脚本，
    若在打开时就把 _rating_req 消费掉，用户在文本框里打字触发重跑会把弹窗刷掉。"""
    req = get_requirement(req_id)
    me = st.session_state.current_user_id
    if not req:
        st.session_state.pop("_rating_req", None)
        st.rerun()
    if me not in _req_raters(req):
        st.error("您不是本需求的关联人，无法评价。")
        if st.button("关闭", key=f"rate_close_{req_id}"):
            st.session_state.pop("_rating_req", None)
            st.rerun()
        return

    existing = _ratings(req).get(me, {})
    st.markdown(f"**{req['id']}** · {req['title']}")
    st.caption(f"您在本需求中的身份：**{_rater_role(req, me)}**。"
               f"请对协作过程与交付结果做评价，用于衡量需求全流程质量。")

    # 进度条：只报人数，不泄露任何人的具体分数（可见性口径 = 仅管理层看全部）
    raters = _req_raters(req)
    done_n = len(_ratings(req))
    st.caption(f"本需求需 {len(raters)} 人评价，目前已完成 {done_n} 人"
               + ("，其中包含您" if me in _ratings(req) else ""))

    st.markdown("**满意度**")
    star_key = f"rate_stars_{req_id}"
    # 已评过的人重新打开时，星级预填上次的选择（重选即覆盖）
    if existing.get("score") and star_key not in st.session_state:
        st.session_state[star_key] = int(existing["score"]) - 1
    idx = st.feedback("stars", key=star_key)
    score = (idx + 1) if idx is not None else 0
    if score:
        note = "（覆盖原评价）" if existing.get("score") and int(existing["score"]) != score else ""
        st.caption(f"已选择 {score} 星 —— {SATISFACTION_LABELS.get(score, '')}{note}")
    elif existing.get("score"):
        st.caption(f"当前评价：{_star_html(existing['score'])} {existing['score']}/5")

    comment = st.text_area(
        "补充说明（可选）",
        value=existing.get("comment", ""),
        placeholder="例如：交付及时、实现与预期一致；或：功能与预期有偏差、沟通不畅……",
        max_chars=500,
        key=f"rate_comment_{req_id}",
    )

    c1, c2 = st.columns(2)
    with c1:
        if st.button("✅ 提交评价", type="primary", use_container_width=True, key=f"rate_submit_{req_id}"):
            if not score:
                st.error("请先选择星级")
            else:
                _set_rating(req, me, score, comment)
                save_requirement(req)
                # 不群发通知：一条需求有 N 个关联人，逐条评价广播会变成刷屏。
                # 未评价者的提醒由主区常驻提醒条承担（见 _render_rating_notice）。
                st.session_state.pop("_rating_req", None)
                st.session_state["_flash"] = {
                    "type": "success",
                    "text": f"✅ 感谢评价：{'⭐' * score}（{SATISFACTION_LABELS.get(score, '')}）",
                }
                st.rerun()
    with c2:
        if st.button("稍后再说", use_container_width=True, key=f"rate_cancel_{req_id}"):
            st.session_state.pop("_rating_req", None)
            st.rerun()


def _maybe_show_survey() -> None:
    """登录后按冷却期自动弹出问卷。

    先落一条 dismissed 记录再弹——Streamlit 捕获不到弹窗被 X 关闭的动作，
    把「已触达」前置到打开时刻，用户点 X 后冷却期依然生效（口径见 db.new_survey_dismissed）。
    """
    if not st.session_state.get("_survey_pending"):
        if st.session_state.get("_survey_checked"):
            return
        st.session_state["_survey_checked"] = True
        me = get_current_user()
        if not me or not _survey_due(me["id"]):
            return
        st.session_state["_survey_rec"] = db.new_survey_dismissed(me)
        st.session_state["_survey_pending"] = True
    rec_id = st.session_state.get("_survey_rec")
    if rec_id:
        _survey_dialog(rec_id)


def _survey_dialog(rec_id: str):
    st.caption(
        f"占用您约 1 分钟。结果仅用于改进系统，{SURVEY_COOLDOWN_DAYS} 天内不会再打扰您。"
    )

    with st.form("survey_form", clear_on_submit=False):
        st.markdown("**请为以下说法选择同意程度（1=很不同意，5=非常同意）**")
        answers = {}
        for i, q in enumerate(SURVEY_QUESTIONS):
            answers[q] = st.radio(
                q,
                options=[1, 2, 3, 4, 5],
                index=2,
                horizontal=True,
                format_func=lambda x: f"{x} · {SURVEY_SCORE_LABELS.get(x, '')}",
                key=f"sv_q{i}",
            )
        features = st.multiselect(
            "您经常使用的功能（可多选，不计分）",
            options=SURVEY_FEATURES,
            key="sv_features",
        )
        comment = st.text_area(
            "改进建议（可选）",
            placeholder="哪里用起来别扭、希望增加什么……",
            max_chars=500,
            key="sv_comment",
        )
        c1, c2 = st.columns(2)
        with c1:
            submitted = st.form_submit_button("✅ 提交", type="primary", use_container_width=True)
        with c2:
            dismissed = st.form_submit_button("稍后再说", use_container_width=True)

    if submitted:
        score = sum(answers.values()) / len(answers) if answers else 0.0
        db.submit_survey(rec_id, round(score, 2), answers, features, comment,
                         question_set=SURVEY_QUESTION_SET)
        # 改进建议自动进入闭环（03 蓝图 §5.1）：comment 非空即自动建项；
        # 建项失败只打日志、绝不阻断问卷提交（吸收客诉「埋点失败不抛异常」纪律）。
        if (comment or "").strip():
            try:
                _u = get_current_user()
                if _u:
                    db.create_improvement(
                        source_type="survey", source_id=rec_id,
                        proposer_id=_u["id"], proposer_name=_u.get("name", ""),
                        detail=(comment or "").strip(),
                    )
                else:
                    print("[eval] 问卷建议未自动建改进项：拿不到当前用户")
            except Exception as e:
                print(f"[eval] 问卷建议自动建改进项失败（不影响提交）：{e}")
        st.session_state["_survey_pending"] = False
        st.session_state["_flash"] = {"type": "success", "text": "✅ 感谢反馈，已记录"}
        st.rerun()
    if dismissed:
        # 记录已在弹窗打开时落为 dismissed，这里只需关闭
        st.session_state["_survey_pending"] = False
        st.rerun()
