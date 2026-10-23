"""
pages/system_eval.py — 从 app.py 拆分生成（函数体逐字保留）。
"""
from __future__ import annotations

# ---- 第三方 / 标准库 ----
from datetime import datetime
from datetime import timedelta
from sample_data import STATUS_COLORS
import db
import streamlit as st

# ---- 项目内部 ----
from config import IMPROVEMENT_CATEGORIES, KUI_NOTE, SATISFACTION_COLORS, SATISFACTION_LABELS, SURVEY_QUESTION_SET, TODAY, TODAY_STR
from services.evaluation import _can_view_rating_detail, _imp_notify_external, _imp_source_label, _imp_transition, _ratings, _req_raters, _survey_due, _survey_stats, all_rating_records, improvement_health, satisfaction_stats
from services.notify import _notify
from services.requirements import get_requirements
from state import USER_ID_TO_NAME, get_current_user, get_users
from ui.components import _card, _page_header, _render_bar_chart, _star_html
from ui.layout import _nav_tabs



def page_system_eval():
    """📋 系统评估（管理层+主管可见）：需求评价 + KUI 问卷 + 改进闭环 + 用户活跃 + 阶段效率 + 改进动态。

    t31 卡片化：仅容器层调整（统一页头 + 卡片包裹五个区块函数的调用点）；
    区块函数本体零改动——业务逻辑、统计口径与权限门控不动。
    t38：五个区块由「竖着堆」改为横向标签互斥显示（版式参考客诉系统系统评估页的
    Tab 切换条，与「项目视图」用同一套 _nav_tabs）。
    附带收益：原来五个区块每次 rerun 都要全部执行一遍（各自含库查询/统计聚合），
    改成标签后**只执行当前这一块**，页面更快；区块函数本体仍零改动。
    """
    _page_header("📋 系统评估",
                 "系统自身的运营评估——数据来自问卷与需求评价，用于判断系统好不好用、哪里要改、改进是否落地")
    _tab = _nav_tabs(
        [
            ("rating", "⭐ 需求评价"),
            ("survey", "📝 KUI 问卷"),
            ("improvement", "🔄 改进闭环"),
            ("activity", "👥 用户活跃"),
            ("stage", "⛽ 阶段效率"),
            ("feed", "🌟 改进动态"),
        ],
        state_key="eval_tab",
    )
    # 每个标签内容仍包在卡片里（保持与其他页一致的卡片视觉）；
    # 区块函数自身已带「## 标题」级小标题，与标签文案重复，故本层不加卡片标题条。
    with _card():
        if _tab == "rating":
            _render_eval_rating_section()
        elif _tab == "survey":
            _render_eval_survey_section()
        elif _tab == "improvement":
            _render_eval_improvement_section()
        elif _tab == "activity":
            _render_eval_activity_section()
        elif _tab == "stage":
            _render_eval_stage_efficiency_section()
        else:
            _render_eval_improvement_feed()


def _render_eval_rating_section():
    """D1 需求评价维度（03 蓝图 §3.1）：沿用现有指标 + 低分追因清单 + 评价率催办。"""
    s = satisfaction_stats()
    if s["done"] == 0:
        st.info("暂无已完成需求")
    else:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("平均分", f"{s['avg']:.2f} / 5" if s["rated"] else "—")
        m2.metric("已评价 / 应评价", f"{s['rated']} / {s['expected']}")
        m3.metric("待评价", s["missing"])
        m4.metric("已完成需求", s["done"],
                  f"{s['unrated_reqs']} 条无人评价" if s["unrated_reqs"] else None)
        if s["rated"]:
            st.markdown(
                f'<div style="margin:6px 0;">满意率（4~5 星）'
                f'<b style="color:#43A047;"> {s["positive_rate"]:.0f}%</b></div>',
                unsafe_allow_html=True,
            )
            left, right = st.columns(2)
            with left:
                st.markdown("**星级分布**")
                _render_bar_chart(
                    [(f"{k} 星 {SATISFACTION_LABELS.get(k, '')}",
                      s["dist"].get(k, 0),
                      SATISFACTION_COLORS.get(k, "#9E9E9E")) for k in [5, 4, 3, 2, 1]],
                    total=max(s["rated"], 1),
                )
            with right:
                st.markdown("**按角色平均分**")
                role_rows = []
                for role in ("提出人", "建议责任人", "执行人"):
                    vals = s["by_role"].get(role)
                    if vals:
                        role_rows.append((f"{role}（{len(vals)} 条）", sum(vals) / len(vals)))
                if role_rows:
                    _render_bar_chart(
                        [(label, round(avg * 10), "#7E57C2") for label, avg in role_rows],
                        total=50,
                        show_value=lambda i: f"{role_rows[i][1]:.2f}",
                    )

        # 逐人明细（谁给几分）只对管理层开放；主管看得到上面的汇总与分布
        if _can_view_rating_detail():
            recs = all_rating_records()
            st.markdown("**全部评价明细**")
            for rec in recs:
                st.markdown(
                    f'<div style="border-left:3px solid #FB8C00;padding:4px 10px;margin-bottom:8px;">'
                    f'<div style="font-size:12px;color:#666;">'
                    f'{rec["name"]}（{rec["role"]}） · {rec["at"]} · {rec["req_id"]}</div>'
                    f'<div>{_star_html(rec["score"], 14)} <b>{rec["score"]}/5</b>'
                    f'<span style="margin-left:8px;color:#444;">{rec["title"]}</span></div>'
                    + (f'<div style="font-size:13px;color:#555;">　└ {rec["comment"]}</div>'
                       if rec["comment"] else "")
                    + '</div>',
                    unsafe_allow_html=True,
                )

            # 低分追因清单（03 蓝图 §3.1 新增）：≤2 星评价一键转改进项，把差评变成可跟踪的待办
            low = [r for r in recs if r["score"] <= 2]
            if low:
                with st.expander(f"🩺 低分追因清单（≤2 星，{len(low)} 条）"):
                    for rec in low:
                        st.markdown(
                            f'<div style="border-left:3px solid #E53935;padding:4px 10px;margin-bottom:6px;">'
                            f'<div style="font-size:12px;color:#666;">'
                            f'{rec["name"]}（{rec["role"]}） · {rec["at"]} · {rec["req_id"]}</div>'
                            f'<div>{_star_html(rec["score"], 13)} <b>{rec["score"]}/5</b>'
                            f'<span style="margin-left:8px;color:#444;">{rec["title"]}</span></div>'
                            + (f'<div style="font-size:13px;color:#555;">　└ {rec["comment"]}</div>'
                               if rec["comment"] else "")
                            + '</div>',
                            unsafe_allow_html=True,
                        )
                        # 转改进项：source 唯一索引防重，重复点击安全（03 蓝图 §5.1/M12）
                        if st.button("➕ 转改进项", key=f"low2imp_{rec['req_id']}_{rec['user_id']}"):
                            detail = (f"需求 {rec['req_id']}「{rec['title']}」获 {rec['score']} 星评价"
                                      f"（{rec['role']}）")
                            if rec["comment"]:
                                detail += f"：{rec['comment']}"
                            imp_id = db.create_improvement(
                                source_type="rating_comment",
                                source_id=f"{rec['req_id']}::{rec['user_id']}",
                                proposer_id=rec["user_id"], proposer_name=rec["name"],
                                detail=detail)
                            st.session_state["_flash"] = {
                                "type": "success",
                                "text": f"✅ 已转入改进闭环（{imp_id}），可在下方「🔁 改进闭环」推进与回告",
                            }
                            st.rerun()

            # 评价率催办（03 蓝图 §3.1/§6 新增，仅管理层）：对全部待评价关联人发提醒。
            # req=None 绕过 _notify 的 P0-P2 优先级门控（改进/催办类消息无优先级概念）。
            if s["missing"] > 0 and st.button("🛎 提醒未评价", key="eval_remind_rating",
                                              help="向全部已完成需求中尚未评价的关联人发送提醒"):
                ids = []
                for r in get_requirements():
                    if r.get("status") != "已完成":
                        continue
                    for uid in _req_raters(r):
                        if uid not in _ratings(r):
                            ids.append(uid)
                ids = list(dict.fromkeys(ids))
                if ids:
                    res = _notify(ids, f"您有 {s['missing']} 条已完成需求待评价，请登录需求看板完成 1-5 星评价。",
                                  req=None)
                    st.session_state["_flash"] = {
                        "type": "success" if res["delivered"] else "warning",
                        "text": (f"已提醒 {len(res['delivered'])} 人"
                                 + (f"，另有 {len(res['dead'])} 人无可达渠道（未配置联系方式），建议补档案"
                                    if res["dead"] else "")),
                    }
                else:
                    st.session_state["_flash"] = {"type": "info", "text": "当前没有待评价的关联人。"}
                st.rerun()

            with st.expander("📥 导出原始评价记录"):
                import pandas as pd
                st.download_button(
                    "下载 CSV",
                    data=pd.DataFrame([{
                        "需求编号": r["req_id"], "需求标题": r["title"],
                        "评价人": r["name"], "身份": r["role"],
                        "星级": r["score"], "补充说明": r["comment"], "评价时间": r["at"],
                    } for r in recs]).to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"需求评价明细_{TODAY_STR}.csv",
                    mime="text/csv",
                )
        else:
            st.caption("🔒 逐人评价明细仅管理层可见。" + (
                f" 本需求共 {s['rated_reqs']} 条已有评价。" if s["rated_reqs"] else ""))


def _render_eval_survey_section():
    """D2 系统使用体验问卷维度（03 蓝图 §3.2/§4）：沿用现有 + KUI 分档/逐题均分/小样本预警/口径统一。"""
    q = _survey_stats()
    if q["reached"] == 0:
        st.info("暂无问卷记录")
        return
    reach_rate = q["submitted"] / q["reached"] * 100 if q["reached"] else 0
    m1, m2, m3 = st.columns(3)
    m1.metric("触达", q["reached"])
    m2.metric("提交", q["submitted"], f"{reach_rate:.0f}% 提交率")
    m3.metric("KUI 均分", f"{q['avg']:.2f} / 5" if q["submitted"] else "—")
    # 提交率口径脚注（03 蓝图 §3.2-M3）：mvp「打开即写 dismissed」使触达完全可观测，提交率为精确口径
    st.caption("提交率 = 提交 / 触达（精确口径：问卷弹窗打开即记录触达，含打开后未填者）")

    # 管理层催填（F-02 / 03 蓝图 §5.5②/§6「数字→动作」）：对冷却期外的全体用户发邀请。
    # 逻辑镜像 D1 的评价催办；req=None 绕过 _notify 的 P0-P2 优先级门控（催办类消息无优先级概念）；
    # 面向 _survey_due=True（冷却期外）的用户，不写库、不新增依赖。
    if _can_view_rating_detail():
        if st.button("📢 提醒填写问卷", key="eval_remind_survey",
                     help="向冷却期已过（当前可填）的全部用户发送填写邀请"):
            ids = [u["id"] for u in get_users() if _survey_due(u["id"])]
            if ids:
                res = _notify(ids, "邀请填写系统使用体验问卷（约 1 分钟）：登录需求看板即可填写，"
                                   "您的改进建议会进入跟踪与回告闭环。", req=None)
                st.session_state["_flash"] = {
                    "type": "success" if res["delivered"] else "warning",
                    "text": (f"已邀请 {len(res['delivered'])} 人填写问卷"
                             + (f"，另有 {len(res['dead'])} 人无可达渠道（未配置联系方式），建议补档案"
                                if res["dead"] else "")),
                }
            else:
                st.session_state["_flash"] = {"type": "info", "text": "当前所有用户都在问卷冷却期内，无需催填。"}
            st.rerun()

    if not q["submitted"]:
        st.caption("尚无提交记录（触达记录不计入统计口径）")
        return

    left, right = st.columns(2)
    with left:
        st.markdown("**总分分布**")
        _render_bar_chart(
            [(f"{k:.0f} 分", q["dist"].get(k, 0), "#1E88E5") for k in [5, 4, 3, 2, 1]],
            total=max(q["submitted"], 1),
        )
    with right:
        st.markdown("**按角色平均分**")
        role_rows = sorted(
            ((role, sum(v) / len(v), len(v)) for role, v in q["by_role"].items()),
            key=lambda x: -x[1],
        )
        _render_bar_chart(
            [(f"{role}（{cnt} 人）", round(avg * 10), "#7E57C2") for role, avg, cnt in role_rows],
            total=50,
            show_value=lambda i, row=role_rows: f"{row[i][1]:.2f}",
        )

    # KUI 自建 4 档分布（03 蓝图 §4.2）+ 逐题均分（§4.3，定位「哪一题拖低总分」）
    left2, right2 = st.columns(2)
    with left2:
        st.markdown("**KUI 分档分布**")
        _render_bar_chart(
            [(name, cnt, color) for name, cnt, color in q["kui_dist"]],
            total=max(q["submitted"], 1),
        )
        st.caption(KUI_NOTE)
    with right2:
        st.markdown("**逐题均分**")
        per_q = q["per_question"].get(SURVEY_QUESTION_SET)
        if per_q is None and q["per_question"]:
            # 当前版本暂无数据时退而展示已有版本（未来改题干后的过渡期）
            per_q = next(iter(q["per_question"].values()))
        if per_q:
            _render_bar_chart(
                [(f"Q{i+1}", round(avg * 10), "#F4511E") for i, (_ques, avg, _cnt) in enumerate(per_q)],
                total=50,
                show_value=lambda i, rows=per_q: f"{rows[i][1]:.2f}",
            )
            st.caption("；".join(f"Q{i+1}＝{ques}" for i, (ques, _avg, _cnt) in enumerate(per_q)))
        else:
            st.caption("暂无逐题数据（历史记录未留存逐题答案）")

    # 小样本预警（03 蓝图 §3.2/M15：内部 12 人团队阈值取 5，而非客诉系统的 30）
    if q["low_sample"]:
        st.warning(f"⚠ 样本不足（提交 {q['submitted']} 份 < {q['min_sample']} 份），结论仅供参考。")

    if q["features"]:
        st.markdown("**常用功能排行**")
        top = q["features"].most_common()
        _render_bar_chart(
            [(name, cnt, "#00897B") for name, cnt in top],
            total=max(top[0][1], 1),
        )

    if q["comments"]:
        st.markdown("**改进建议**")
        if _can_view_rating_detail():
            # 点名式原文仅管理层（口径统一裁决，03 蓝图 §5.4/C5——与逐人评价明细同为点名式同事数据）
            for r in q["comments"]:
                st.markdown(
                    f'<div style="border-left:3px solid #1E88E5;padding:4px 10px;margin-bottom:8px;">'
                    f'<div style="font-size:12px;color:#666;">{r.get("username", "")}'
                    f'（{r.get("role", "")}） · {r.get("submitted_at") or r.get("created_at", "")}</div>'
                    f'<div>{r.get("comment", "")}</div></div>',
                    unsafe_allow_html=True,
                )
        else:
            # 主管：匿名化卡片（保留角色/部门与文本，不含姓名——满足推动组员填问卷的管理诉求）
            for r in q["comments"]:
                st.markdown(
                    f'<div style="border-left:3px solid #90A4AE;padding:4px 10px;margin-bottom:8px;">'
                    f'<div style="font-size:12px;color:#666;">（{r.get("role", "")} · {r.get("department", "")}）'
                    f' · {r.get("submitted_at") or r.get("created_at", "")}</div>'
                    f'<div>{r.get("comment", "")}</div></div>',
                    unsafe_allow_html=True,
                )
            st.caption("🔒 按统一口径，建议点名原文仅管理层可见。")

    # 逐题图旁「查看相关建议」（03 蓝图 §6：数字 → 动作）：关键词过滤，只读
    if q["comments"]:
        with st.expander("🔎 查看相关改进建议（按关键词过滤，只读）"):
            kw = st.text_input("关键词", key="imp_kw_lookup", placeholder="例如：AI、搜索、附件、导出……")
            hits = [c for c in q["comments"] if (not kw.strip()) or kw.strip() in (c.get("comment") or "")]
            if not hits:
                st.caption("没有匹配的建议。")
            for c in hits:
                who = c.get("username", "") if _can_view_rating_detail() else f'（{c.get("role", "")}）'
                st.markdown(
                    f'<div style="border-left:3px solid #B0BEC5;padding:4px 10px;margin-bottom:6px;">'
                    f'<div style="font-size:12px;color:#666;">{who} · '
                    f'{c.get("submitted_at") or c.get("created_at", "")}</div>'
                    f'<div>{c.get("comment", "")}</div></div>',
                    unsafe_allow_html=True,
                )

    st.markdown("---")
    # 导出按角色分流（F-01）：管理层导出点名原文；主管导出匿名版——与屏上卡片口径一致，
    # 否则 C5 的收紧会被同一页面的下载按钮绕过（记录ID 形如 SV-{user_id}-{时间}，同源泄漏 user_id）。
    if _can_view_rating_detail():
        with st.expander("📥 导出原始问卷记录"):
            export_rows = [{
                "记录ID": r["id"], "姓名": r["username"], "角色": r["role"], "部门": r["department"],
                "类型": "已提交" if r["response_type"] == "submitted" else "仅触达",
                "总分": r["score"], "题干版本": r.get("question_set") or "v1",
                "常用功能": "、".join(r.get("features") or []),
                "改进建议": r.get("comment", ""), "触达时间": r["created_at"],
                "提交时间": r["submitted_at"] or "",
            } for r in q["rows"]]
            import pandas as pd
            st.download_button(
                "下载 CSV",
                data=pd.DataFrame(export_rows).to_csv(index=False).encode("utf-8-sig"),
                file_name=f"系统使用体验问卷_{TODAY_STR}.csv",
                mime="text/csv",
            )
    else:
        # 主管匿名导出：无「姓名」键；「记录ID」内嵌 user_id，替换为不含 user_id 的纯序号
        with st.expander("📥 导出问卷记录（匿名，无姓名与用户ID）"):
            export_rows = [{
                "序号": i + 1, "角色": r["role"], "部门": r["department"],
                "类型": "已提交" if r["response_type"] == "submitted" else "仅触达",
                "总分": r["score"], "题干版本": r.get("question_set") or "v1",
                "常用功能": "、".join(r.get("features") or []),
                "改进建议": r.get("comment", ""), "触达时间": r["created_at"],
                "提交时间": r["submitted_at"] or "",
            } for i, r in enumerate(q["rows"])]
            import pandas as pd
            st.download_button(
                "下载 CSV",
                data=pd.DataFrame(export_rows).to_csv(index=False).encode("utf-8-sig"),
                file_name=f"系统使用体验问卷_匿名_{TODAY_STR}.csv",
                mime="text/csv",
            )
            st.caption("🔒 匿名版不含姓名与用户ID（记录ID 已替换为序号），与您屏上可见的匿名卡片信息量一致。")


def _render_improvement_console(imps: list[dict]) -> None:
    """改进项操作台（仅管理层，03 蓝图 §3.3/§6）：新建 / 推进 / 指派 / 归类 / 回告 / 导出。"""
    st.markdown("**改进项操作台**（仅管理层；主管视角为下方匿名化列表）")

    # ＋ 新建改进项（manual，合法可多条，不参与唯一索引防重）
    with st.expander("➕ 新建改进项"):
        users = get_users()
        uid_opts = [u["id"] for u in users]
        c1, c2 = st.columns(2)
        with c1:
            new_summary = st.text_input("一句话标题（回告文案用）", key="imp_new_summary", max_chars=100)
            new_proposer = st.selectbox(
                "提出人", options=uid_opts, key="imp_new_proposer",
                format_func=lambda uid: f"{USER_ID_TO_NAME.get(uid, uid)}（{uid}）")
            new_category = st.selectbox("归类", options=["（未归类）"] + IMPROVEMENT_CATEGORIES,
                                        key="imp_new_category")
        with c2:
            new_detail = st.text_area("详细说明（≤500 字）", key="imp_new_detail", max_chars=500)
        if st.button("创建", type="primary", key="imp_new_submit"):
            if not (new_summary.strip() or new_detail.strip()):
                st.error("请至少填写标题或详细说明。")
            else:
                try:
                    imp_id = db.create_improvement(
                        source_type="manual", source_id=None,
                        proposer_id=new_proposer,
                        proposer_name=USER_ID_TO_NAME.get(new_proposer, ""),
                        summary=new_summary.strip(), detail=new_detail.strip(),
                        category="" if new_category == "（未归类）" else new_category)
                    st.session_state["_flash"] = {"type": "success", "text": f"✅ 已创建改进项 {imp_id}"}
                    st.rerun()
                except Exception as e:
                    st.error(f"创建失败：{e}")

    # 批量补告（03 蓝图 §5.3）：已搁置项默认不回告，这里供管理层批量补告存量
    shelved_unnotified = [i for i in imps if i["status"] == "已搁置" and not i["notified_at"]]
    if shelved_unnotified and st.button(f"📢 批量补告已搁置项（{len(shelved_unnotified)} 条）",
                                        key="imp_notify_shelved_batch"):
        me = get_current_user() or {}
        delivered = 0
        for it in shelved_unnotified:
            text = (f"您在需求看板提出的改进建议「{it['summary']}」已搁置。"
                    + (f"说明：{it['resolution_note']}。" if it.get("resolution_note") else ""))
            res = _notify([it["proposer_id"]], text, req=None)
            if it["proposer_id"] in res.get("delivered", []):
                db.mark_improvement_notified(it["id"], me.get("role", ""))
                delivered += 1
        st.session_state["_flash"] = {
            "type": "success" if delivered else "warning",
            "text": f"批量补告完成：外发送达 {delivered}/{len(shelved_unnotified)} 条"
                    "（未送达者的站内提醒已生效）。",
        }
        st.rerun()

    for it in imps:
        src = _imp_source_label(it["source_type"])
        owner = USER_ID_TO_NAME.get(it["owner_id"], "未指派") if it["owner_id"] else "未指派"
        title = (f"{it['status']} ｜ {it['summary']}"
                 f"（{it['proposer_name'] or it['proposer_id']} · {src} · 负责人 {owner}）")
        with st.expander(title):
            st.markdown(
                f'<div style="font-size:13px;color:#555;">'
                f'提出人：{it["proposer_name"] or it["proposer_id"]}'
                + (f'（{it["proposer_role"]}·{it["proposer_dept"]}）' if it.get("proposer_role") else '')
                + f' ｜ 来源：{src}（{it["source_id"] or "—"}）｜ 归类：{it["category"] or "未归类"}'
                f'<br>创建：{it["created_at"]} ｜ 更新：{it["updated_at"] or "—"}'
                + (f' ｜ 关闭：{it["closed_at"]}' if it["closed_at"] else '')
                + (f' ｜ 已回告：{it["notified_at"]}' if it["notified_at"] else ' ｜ 未回告')
                + (f' ｜ 提出人已知悉：{it["proposer_ack_at"]}' if it["proposer_ack_at"] else '')
                + '</div>',
                unsafe_allow_html=True,
            )
            st.markdown(f"**原文**：{it['detail']}")
            if it["resolution_note"]:
                st.markdown(f"**处理结论**：{it['resolution_note']}")

            note_key = f"imp_note_{it['id']}"
            # 只渲染合法目标状态（流转白名单由 db.IMPROVEMENT_TRANSITIONS 定义，DAO 再校验一层）
            targets = db.IMPROVEMENT_TRANSITIONS.get(it["status"], ())
            btn = {"处理中": ("▶ 开始处理", "primary"), "已上线": ("✅ 标记已上线", "primary"),
                   "不采纳": ("🚫 不采纳", "secondary"), "已搁置": ("⏸ 已搁置", "secondary"),
                   "待处理": ("♻ 重新激活", "secondary")}
            if targets:
                st.text_input("处理结论（推进到终态时必填：上线说明 / 不采纳原因 / 搁置原因）",
                              key=note_key, max_chars=500)
                cols = st.columns(min(len(targets), 3))
                for idx, ts in enumerate(targets):
                    label, kind = btn[ts]
                    if cols[idx % len(cols)].button(label, key=f"imp_go_{it['id']}_{ts}",
                                                    type=kind, use_container_width=True):
                        _imp_transition(it, ts, note_key)
            # 终态（已上线/不采纳）且未回告：精准回告入口
            if it["status"] in ("已上线", "不采纳") and not it["notified_at"]:
                if st.button("📢 回告提出人", key=f"imp_notify_{it['id']}", use_container_width=True):
                    _imp_notify_external(it)

            # 指派负责人 / 修改归类与摘要（非终态可指派；任意状态可改摘要归类）
            with st.form(f"imp_meta_form_{it['id']}"):
                st.markdown("**指派负责人 / 修改归类与摘要**")
                owner_opts = ["（不指派）"] + [u["id"] for u in get_users()]
                pick_owner = st.selectbox(
                    "负责人", options=owner_opts, key=f"imp_owner_{it['id']}",
                    index=0 if not it["owner_id"] else owner_opts.index(it["owner_id"]),
                    format_func=lambda uid: ("（不指派）" if uid == "（不指派）"
                                             else f"{USER_ID_TO_NAME.get(uid, uid)}（{uid}）"),
                    disabled=it["status"] in db.IMPROVEMENT_TERMINAL)
                pick_summary = st.text_input("摘要", value=it["summary"], key=f"imp_sum_{it['id']}",
                                             max_chars=100)
                pick_cat = st.selectbox("归类", options=["（未归类）"] + IMPROVEMENT_CATEGORIES,
                                        key=f"imp_cat_{it['id']}",
                                        index=0 if not it["category"] else (["（未归类）"] + IMPROVEMENT_CATEGORIES).index(it["category"]))
                if st.form_submit_button("保存", type="primary"):
                    me = get_current_user() or {}
                    if not (it["status"] in db.IMPROVEMENT_TERMINAL):
                        ok, msg = db.assign_improvement_owner(
                            it["id"], "" if pick_owner == "（不指派）" else pick_owner, me.get("role", ""))
                        if not ok:
                            st.error(msg)
                    ok, msg = db.update_improvement_meta(
                        it["id"], me.get("role", ""), summary=pick_summary,
                        category="" if pick_cat == "（未归类）" else pick_cat)
                    if not ok:
                        st.error(msg)
                    else:
                        st.session_state["_flash"] = {"type": "success", "text": f"✅ {it['id']} 已保存"}
                        st.rerun()

    with st.expander("📥 导出改进项记录"):
        import pandas as pd
        st.download_button(
            "下载 CSV",
            data=pd.DataFrame([{
                "ID": i["id"], "状态": i["status"], "来源": _imp_source_label(i["source_type"]),
                "来源ID": i["source_id"] or "", "提出人": i["proposer_name"] or i["proposer_id"],
                "归类": i["category"], "摘要": i["summary"], "原文": i["detail"],
                "负责人": USER_ID_TO_NAME.get(i["owner_id"], "") if i["owner_id"] else "",
                "处理结论": i["resolution_note"], "创建": i["created_at"], "更新": i["updated_at"],
                "关闭": i["closed_at"], "已回告": i["notified_at"], "提出人已知悉": i["proposer_ack_at"],
            } for i in imps]).to_csv(index=False).encode("utf-8-sig"),
            file_name=f"改进项记录_{TODAY_STR}.csv",
            mime="text/csv",
        )


def _render_eval_improvement_section():
    """D3 改进闭环健康度维度（03 蓝图 §3.3，一期核心）。"""
    st.caption("问卷与评价中提出的建议在此跟踪：待处理 → 处理中 → 已上线 / 不采纳 / 已搁置；"
               "终态后回告提出人（站内提醒 + 钉钉/邮件尽力而为）。")
    imps = db.load_improvements(with_proposer=_can_view_rating_detail())
    h = improvement_health(imps)
    if h["total"] == 0:
        st.info("暂无改进项。用户在问卷中提交建议会自动进入闭环；管理层也可从低分评价一键转入，或在此新建。")
        return

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("全部改进项", h["total"])
    m2.metric("待处理积压", h["backlog"])
    m3.metric("处理中", h["by_status"].get("处理中", 0))
    m4.metric("平均处理时长", f"{h['avg_days']:.1f} 天" if h["terminal"] else "—",
              help="mean(closed_at − created_at)，仅统计已上线/不采纳（有明确处置结论的终态）")
    m5.metric("回告率", f"{h['notify_rate']:.0f}%" if h["terminal"] else "—",
              help="已外发回告（notified_at）的终项 / 终项总数；站内已确认率另见下方说明")
    if h["backlog_warn"]:
        st.warning(f"⚠ 待处理改进项已积压 {h['backlog']} 条（>3），建议尽快认领或关闭。")
    if h["terminal"]:
        st.caption(f"终项（已上线+不采纳）{h['terminal']} 条：已外发回告 {h['notified']} 条，"
                   f"提出人已确认（站内「知道了」）{h['acked']} 条（{h['ack_rate']:.0f}%）。"
                   "站内提醒不依赖外发成功——外发失败项仍会在提出人主区显示。")

    st.markdown("**状态分布**")
    _render_bar_chart(
        [(s, h["by_status"].get(s, 0), color) for s, color in
         zip(db.IMPROVEMENT_STATUSES, ("#FB8C00", "#1E88E5", "#43A047", "#9E9E9E", "#B0BEC5"))],
        total=max(h["total"], 1),
    )

    if _can_view_rating_detail():
        _render_improvement_console(imps)
    else:
        # 主管：匿名化只读列表（取数层已不含 proposer_name，03 蓝图 §5.4/§5.6-7）
        st.markdown("**改进项列表（匿名，仅汇总与推进状态）**")
        st.table([{
            "状态": i["status"], "归类": i["category"] or "未归类",
            "摘要": i["summary"], "详情（截断）": i["detail"][:60] + ("…" if len(i["detail"]) > 60 else ""),
            "创建时间": i["created_at"],
        } for i in imps])
        st.caption("🔒 按统一口径，改进项的提出人姓名仅管理层可见；如需推动组员提建议，可使用匿名视图。")


def _render_eval_activity_section():
    """D4 用户活跃（t20 新增，03 蓝图的有意范围扩展）：DAU/WAU/MAU + 页面热度 + 逐人明细。

    如实口径：埋点只能向前生效——上线前的历史访问无法回溯，指标自上线起从 0 累积，
    不得用其他表的时间戳（如 requirements.created_at）伪造活跃度数字。
    权限：DAU/WAU/MAU 与页面热度对管理层+主管可见；逐人明细属「点名式同事数据」，
    仅管理层可见（复用 _can_view_rating_detail，与逐人评价明细同口径）。
    """
    a = db.activity_stats()
    if not a.get("has_data"):
        st.info("暂无活跃记录：埋点自本次上线后开始累积（今天之前的历史访问无法回溯），"
                "近 24 小时/近 7 天/近 30 天活跃与页面热度会从 0 起步、随使用逐步变得有意义。")
        return
    m1, m2, m3 = st.columns(3)
    # 标签用「近 24 小时」而非「今日」：dau 是滚动 24 小时窗口（created_at >= now-1天），
    # 不是日历日——上午打开页面时「今日」会把昨日午后的活跃误读为今天（t24 R-1 修复）
    m1.metric("近 24 小时活跃（DAU）", a["dau"])
    m2.metric("近 7 天活跃（WAU）", a["wau"])
    m3.metric("近 30 天活跃（MAU）", a["mau"])
    # 累计使用量（向上汇报口径）：滚动窗口只回答「最近热不热」，累计数回答「一共用了多少」
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("注册用户数", a["total_users"])
    c2.metric("累计登录次数", a["total_logins"])
    c3.metric("累计页面访问次数", a["total_views"])
    c4.metric("MAU 渗透率", f"{a['mau'] / a['total_users'] * 100:.0f}%" if a["total_users"] else "—",
              help="近 30 天活跃用户 ÷ 注册用户数，反映系统在全员中的覆盖面")
    st.caption("口径：DAU/WAU/MAU 为近 1/7/30 天内有埋点记录（登录或页面访问）的去重用户数，"
               "DAU 是滚动 24 小时窗口；累计数自埋点上线起算，历史不回溯。")

    # 每日活跃趋势（近 30 个日历日，含今天；无记录的日期补 0，曲线连续不断轴）
    import pandas as pd  # 与其他导出同款局部导入
    by_day = {d["date"]: d["users"] for d in a["daily"]}
    days = [(TODAY - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(29, -1, -1)]
    st.markdown("**每日活跃人数（近 30 天，按日历日去重）**")
    st.line_chart(
        pd.DataFrame({"活跃人数": [by_day.get(d, 0) for d in days]}, index=days),
        color="#00897B", height=220,
    )

    # CSV 导出（向领导交数的出口）：汇总 + 每日趋势 + 页面热度 + 逐人明细；
    # 逐人明细沿用页面同一权限（仅管理层），主管导出的文件不含该节
    def _activity_csv() -> str:
        sections = [
            ("汇总", pd.DataFrame([
                {"指标": "近24小时活跃（DAU）", "数值": a["dau"]},
                {"指标": "近7天活跃（WAU）", "数值": a["wau"]},
                {"指标": "近30天活跃（MAU）", "数值": a["mau"]},
                {"指标": "注册用户数", "数值": a["total_users"]},
                {"指标": "累计登录次数", "数值": a["total_logins"]},
                {"指标": "累计页面访问次数", "数值": a["total_views"]},
                {"指标": "统计截止", "数值": TODAY_STR},
            ])),
            ("每日活跃·近30天", pd.DataFrame([
                {"日期": d["date"], "活跃人数": d["users"], "埋点次数": d["events"]}
                for d in a["daily"]
            ])),
            ("页面热度·近30天", pd.DataFrame([
                {"页面": p, "访问次数": c} for p, c in a["page_heat"]
            ])),
        ]
        if _can_view_rating_detail():
            sections.append(("逐人明细·近30天·仅管理层", pd.DataFrame([{
                "姓名": u["user_name"] or u["user_id"], "角色": u["role"],
                "访问次数": u["visits"], "活跃天数": u["active_days"],
                "最近活跃": u["last_active"],
            } for u in a["user_activity"]])))
        return "\n".join(f"【{name}】\n{df.to_csv(index=False).rstrip('\n')}"
                         for name, df in sections)

    st.download_button(
        "⬇ 下载用户活跃 CSV",
        data=_activity_csv().encode("utf-8-sig"),
        file_name=f"用户活跃_{TODAY_STR}.csv",
        mime="text/csv",
        use_container_width=False,
        help="含汇总、每日活跃趋势、页面热度"
             + ("、逐人明细（仅管理层）" if _can_view_rating_detail() else ""),
    )

    if a["page_heat"]:
        st.markdown("**页面热度（近 30 天访问次数）**")
        _render_bar_chart(
            [(p, c, "#00897B") for p, c in a["page_heat"]],
            total=max(a["page_heat"][0][1], 1),
        )
    else:
        st.caption("近 30 天暂无页面访问记录（只有登录事件）。")
    # 逐人明细：仅管理层（点名式同事数据）；主管可见上面的汇总与页面热度
    if _can_view_rating_detail():
        if a["user_activity"]:
            st.markdown("**逐人活跃明细（近 30 天，仅管理层可见）**")
            st.table([{
                "姓名": u["user_name"] or u["user_id"], "角色": u["role"],
                "访问次数": u["visits"], "活跃天数": u["active_days"],
                "最近活跃": u["last_active"],
            } for u in a["user_activity"]])
    else:
        st.caption("🔒 逐人活跃明细仅管理层可见。")


def _render_eval_stage_efficiency_section():
    """⛽ 阶段效率：表单填写耗时 + 各阶段停留 + 交付周期/按期率 + 卡单预警。

    数据口径（与「用户活跃」区块同一原则，如实声明）：
    - 填写耗时（form_fill_seconds）与阶段停留（status_history）为本次新增埋点，
      存量需求没有这些字段——不计入、也不得用 created_at 等日级时间戳伪造，
      指标自上线起从 0 累积；
    - 交付周期与按期完成率用存量字段（created_at / actual_completion_date，日级精度），
      全量历史可算，作为埋点积累期的对照基线。
    权限：汇总指标对管理层+主管可见；「逐人填写耗时明细」属点名式数据，
    仅管理层可见（复用 _can_view_rating_detail，与用户活跃明细同口径）。
    """
    reqs = get_requirements()
    now = datetime.now()

    def _avg(xs):
        return (sum(xs) / len(xs)) if xs else None

    # ---- 1) 表单填写耗时（新埋点）----
    fills = [r for r in reqs
             if isinstance(r.get("form_fill_seconds"), (int, float))
             and r["form_fill_seconds"] > 0]
    all_secs = sorted(r["form_fill_seconds"] for r in fills)
    ai_secs = sorted(r["form_fill_seconds"] for r in fills if r.get("ai_assisted"))
    manual_secs = sorted(r["form_fill_seconds"] for r in fills if not r.get("ai_assisted"))

    # ---- 2) 阶段停留（新埋点）：相邻两条流转记录的间隔 = 前一阶段的停留时长 ----
    seg_days: dict[str, list[float]] = {}
    for r in reqs:
        pts = [(h.get("to"), _dt_any(h.get("time"))) for h in (r.get("status_history") or [])]
        for (stage, t0), (_nxt, t1) in zip(pts, pts[1:]):
            if stage and t0 and t1 and t1 >= t0:
                seg_days.setdefault(stage, []).append((t1 - t0).total_seconds() / 86400.0)
    confirm_days = seg_days.get("待确认", [])

    # ---- 3) 交付周期与按期率（存量字段，全量历史可算）----
    delivery, on_time, with_due = [], 0, 0
    for r in reqs:
        if r.get("status") != "已完成":
            continue
        fin = _dt_any(r.get("actual_completion_date"))
        created = _dt_any(r.get("created_at"))
        if fin and created and fin >= created:
            delivery.append((fin - created).total_seconds() / 86400.0)
        due = _dt_any(r.get("requested_due_date"))
        if due and fin:
            with_due += 1
            if fin <= due:
                on_time += 1

    # ---- 4) 卡单预警：开放状态停留超过阶段阈值 ----
    # 待确认是共识门禁要求快；需求池/待启动是排队分配；进行中/暂停周期天然更长。
    # 存量需求没有留痕：仅「待确认/需求池」可用创建日期近似进入时间（误差≤1 天），
    # 后续阶段无法知道何时进入——宁缺毋假，不计入预警。
    STUCK_RULES = {"待确认": 3, "需求池": 7, "待启动": 7, "进行中": 14, "暂停": 14}
    stuck = []
    for r in reqs:
        cur = r.get("status")
        if cur not in STUCK_RULES:
            continue
        hist = r.get("status_history") or []
        if hist:
            anchor = _dt_any(hist[-1].get("time"))
        elif cur in ("待确认", "需求池"):
            anchor = _dt_any(r.get("created_dt")) or _dt_any(r.get("created_at"))
        else:
            anchor = None
        if anchor and anchor <= now:
            days = (now - anchor).total_seconds() / 86400.0
            if days > STUCK_RULES[cur]:
                stuck.append((days, r))
    stuck.sort(key=lambda x: -x[0])

    # ---- 指标行（埋点类指标与存量指标并排，help 注明各自口径）----
    m1, m2, m3, m4 = st.columns(4)
    a_fill, a_confirm, a_deliv = _avg(all_secs), _avg(confirm_days), _avg(delivery)
    m1.metric("平均填写表单", _fmt_fill_secs(a_fill) if a_fill else "—",
              help="从打开「新建需求」页到提交成功的耗时（超 4 小时的挂起会话不计入）；埋点自本功能上线起累积")
    m2.metric("待确认平均耗时", f"{a_confirm:.1f} 天" if a_confirm is not None else "—",
              help="需求从提交到主管确认入池的平均停留，即共识速度；埋点自本功能上线起累积")
    m3.metric("平均交付周期", f"{a_deliv:.1f} 天" if a_deliv is not None else "—",
              help="已完成需求：实际完成日期 − 创建日期（日级精度，全量历史可算）")
    m4.metric("按期完成率", f"{on_time / with_due * 100:.0f}%" if with_due else "—",
              help="已完成且约定了期望完成日期的需求中，实际完成 ≤ 期望日期的比例")

    # ---- 表单填写效率 ----
    st.markdown("**表单填写效率**")
    if not fills:
        st.info("暂无填写耗时记录：该埋点自本功能上线起累积（此前创建的需求没有计时，不伪造数字）。")
    else:
        c_ai, c_man = st.columns(2)
        c_ai.metric(f"AI 预填平均 · {len(ai_secs)} 条", _fmt_fill_secs(_avg(ai_secs)) if ai_secs else "—")
        c_man.metric(f"纯手动平均 · {len(manual_secs)} 条", _fmt_fill_secs(_avg(manual_secs)) if manual_secs else "—")
        med = all_secs[len(all_secs) // 2]
        st.caption(f"口径：{len(fills)} 条有效记录 · 中位数 {_fmt_fill_secs(med)} · "
                   f"最长 {_fmt_fill_secs(all_secs[-1])}；超 4 小时的挂起会话未计入。")
        if _can_view_rating_detail():
            st.markdown("**逐人填写明细（近 30 条，仅管理层可见）**")
            st.table([{
                "需求": r["id"],
                "标题": (r.get("title") or "")[:18],
                "提出人": USER_ID_TO_NAME.get(r.get("requester_id", ""), r.get("requester_id") or "—"),
                "耗时": _fmt_fill_secs(r["form_fill_seconds"]),
                "AI 预填": "是" if r.get("ai_assisted") else "否",
                "提交时间": r.get("created_dt") or r.get("created_at") or "",
            } for r in sorted(fills, key=lambda r: r.get("created_dt") or "", reverse=True)[:30]])
        else:
            st.caption("🔒 逐人填写明细仅管理层可见。")

    # ---- 各阶段平均停留 ----
    st.markdown("**各阶段平均停留**")
    order = [s for s in ["待确认", "需求池", "待启动", "进行中", "暂停"] if s in seg_days]
    if order:
        rows = [(s, _avg(seg_days[s]), STATUS_COLORS.get(s, "#999")) for s in order]
        texts = [f"{_avg(seg_days[s]):.1f} 天 · {len(seg_days[s])} 段" for s in order]
        _render_bar_chart(rows, total=max(r[1] for r in rows), show_value=lambda i: texts[i])
        st.caption("口径：相邻两条流转记录的间隔 = 前一阶段的停留时长；仅统计已走完的历史停留段，"
                   "当前正在停留的时段见下方「卡单预警」；自埋点上线起累积。")
    else:
        st.info("暂无阶段停留记录：流转留痕（status_history）自本功能上线起累积，"
                "此前创建的需求没有分钟级时间链，不伪造数字。")

    # ---- 卡单预警 ----
    st.markdown("**卡单预警**")
    if stuck:
        st.warning(f"{len(stuck)} 条需求在当前阶段停留过久"
                   "（阈值：待确认 3 天 · 需求池/待启动 7 天 · 进行中/暂停 14 天）")
        st.table([{
            "需求": r["id"],
            "标题": (r.get("title") or "")[:18],
            "当前状态": r.get("status"),
            "已停留": f"{days:.0f} 天",
            "负责人": USER_ID_TO_NAME.get(r.get("requested_owner_id", ""), r.get("requested_owner_id") or "—"),
        } for days, r in stuck[:15]])
        if len(stuck) > 15:
            st.caption(f"仅列出停留最久的前 15 条，共 {len(stuck)} 条。")
    else:
        st.success("无卡单：所有开放状态的需求都在阶段阈值内正常流转"
                   "（存量需求仅对待确认/需求池按创建日期近似判断，后续阶段无留痕不计入）。")


def _render_eval_improvement_feed():
    """🌟 改进动态（03 蓝图 §5.5/§6）：已上线改进项公示，强化「提了有用」的正反馈。"""
    live = db.load_improvements(status="已上线", with_proposer=True)
    if not live:
        st.caption("暂无已上线的改进项。建议被采纳上线后会在这里公示（保留提出人姓名——已上线内容无点名敏感性）。")
        return
    for it in live[:20]:
        st.markdown(
            f'<div style="border-left:3px solid #43A047;padding:4px 10px;margin-bottom:6px;">'
            f'<div>🎉 「{it["summary"]}」已上线</div>'
            f'<div style="font-size:12px;color:#666;">'
            f'提出人：{it["proposer_name"] or it["proposer_id"]} · {it["closed_at"] or it["updated_at"] or ""}'
            + (f' · {it["resolution_note"]}' if it["resolution_note"] else '')
            + '</div></div>',
            unsafe_allow_html=True,
        )


def _dt_any(s) -> datetime | None:
    """解析三种精度的时间串（秒级/分钟级/日级），失败返回 None。

    阶段效率统计要同时读新埋点（%H:%M 的流转记录、%H:%M:%S 的 created_dt）
    与存量字段（日级的 created_at / actual_completion_date），
    统一走这一个入口，避免每个调用点各写一串 try。
    """
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(s or "").strip(), fmt)
        except ValueError:
            continue
    return None


def _fmt_fill_secs(secs: float) -> str:
    """填写耗时的展示格式：秒级埋点按人类习惯归到分钟。"""
    return f"{secs / 60:.1f} 分钟" if secs >= 60 else f"{secs:.0f} 秒"
