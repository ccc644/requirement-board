"""
pages/ai_showcase.py — 从 app.py 拆分生成（函数体逐字保留）。
"""
from __future__ import annotations

# ---- 第三方 / 标准库 ----
from datetime import timedelta
import ai_helper
import streamlit as st

# ---- 项目内部 ----
from config import TODAY, TODAY_STR, WEEK_START_STR
from services.requirements import apply_filter, get_requirements, simple_keyword_match
from state import USER_ID_TO_NAME



def page_ai_showcase():
    st.markdown("## 🤖 AI 助手能力总览（A1~A6）")
    st.caption("本页集中演示 6 个 AI 能力，对应 PRD 第五章")

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "A1 信息抽取", "A2 相似检测", "A3 清晰度检查",
        "A4 自动打标签", "A5 自然语言搜索", "A6 周报生成",
    ])

    with tab1:
        st.markdown("**A1 · 需求信息抽取与字段预填**")
        st.caption("将自然语言文本抽取为结构化需求字段")
        demo_input = st.text_area(
            "输入",
            value="客户示例客户C集成的客户工程师昨天在群里反馈，说我们的测试设备在高速贴装时视觉定位精度不稳，部分 IC 偏移到 0.05mm，希望我们能优化到 0.02mm，下周三之前给方案。",
            height=120,
            key="a1_input",
        )
        if st.button("运行 A1", key="a1_run"):
            with st.spinner("AI 调用中…"):
                r = ai_helper.extract_requirement_fields(
                    user_input=demo_input,
                    provider=st.session_state.provider,
                    api_key=st.session_state.api_key,
                    model=st.session_state.model or None,
                )
            if r.success:
                st.json(r.data)
                st.caption(f"耗时 {r.latency_ms}ms · 模型 {r.model}")
            else:
                st.error(r.error)

    with tab2:
        st.markdown("**A2 · 相似需求检测**")
        st.caption("判断新需求与历史候选是否实质重复")
        new_title = st.text_input("新需求标题", value="测试设备增加 BARCODE 扫码功能", key="a2_title")
        new_desc = st.text_area("新需求描述", value="客户需要在测试设备的入料口增加条码扫描，用于产品追溯。", key="a2_desc")
        if st.button("运行 A2", key="a2_run"):
            candidates = simple_keyword_match(new_title + " " + new_desc, get_requirements(), top_n=3)
            if not candidates:
                st.info("未找到候选")
            else:
                with st.spinner(f"AI 复核 {len(candidates)} 条候选…"):
                    r = ai_helper.check_similarity(
                        new_title=new_title,
                        new_description=new_desc,
                        candidates=[{"id": c["id"], "title": c["title"], "description": c.get("description", "")} for c in candidates],
                        provider=st.session_state.provider,
                        api_key=st.session_state.api_key,
                        model=st.session_state.model or None,
                    )
                if r.success:
                    for item in r.data:
                        c = next((c for c in candidates if c["id"] == item.get("id")), None)
                        if c:
                            dup = item.get("duplicate", False)
                            color = "#E53935" if dup else "#43A047"
                            st.markdown(
                                f'<div style="padding:8px;border-left:3px solid {color};margin:6px 0;">'
                                f'{"🔁 重复" if dup else "✅ 不重复"} <b>{c["id"]}</b> 「{c["title"]}」'
                                f' &nbsp;<span style="color:{color};">置信度 {item.get("confidence",0):.2f}</span>'
                                f'<div style="color:#666;font-size:12px;">{item.get("reason","")}</div></div>',
                                unsafe_allow_html=True,
                            )
                    st.caption(f"耗时 {r.latency_ms}ms")
                else:
                    st.error(r.error)

    with tab3:
        st.markdown("**A3 · 需求清晰度检查**")
        st.caption("识别信息缺失或模糊表述，给出具体补充建议")
        c_title = st.text_input("标题", value="做一下设备的优化", key="a3_title")
        c_desc = st.text_area("描述", value="客户说要尽快把设备优化一下，做得好一点，差不多就行。", key="a3_desc")
        if st.button("运行 A3", key="a3_run"):
            with st.spinner("AI 检查中…"):
                r = ai_helper.check_clarity(
                    title=c_title, description=c_desc,
                    provider=st.session_state.provider, api_key=st.session_state.api_key,
                    model=st.session_state.model or None,
                )
            if r.success:
                if r.data:
                    for i, s in enumerate(r.data, 1):
                        st.warning(f"**{i}. {s.get('dimension','')}** — {s.get('suggestion','')}")
                else:
                    st.success("描述清晰，无补充建议")
                st.caption(f"耗时 {r.latency_ms}ms")
            else:
                st.error(r.error)

    with tab4:
        st.markdown("**A4 · 自动分类与打标签**")
        st.caption("从预设标签库推荐 1-3 个标签")
        t_title = st.text_input("标题", value="HAST-III 老化舱温箱控制板国产化替代", key="a4_title")
        t_desc = st.text_area("描述", value="现用进口温箱控制板交期 16 周，需评估国产方案，温度精度 ±0.5℃，MTBF ≥ 8000h。", key="a4_desc")
        if st.button("运行 A4", key="a4_run"):
            with st.spinner("AI 推荐中…"):
                r = ai_helper.auto_tag(
                    title=t_title, description=t_desc,
                    provider=st.session_state.provider, api_key=st.session_state.api_key,
                    model=st.session_state.model or None,
                )
            if r.success:
                for t in r.data:
                    conf = t.get("confidence", 0)
                    color = "#43A047" if conf >= 0.8 else "#FB8C00" if conf >= 0.5 else "#9E9E9E"
                    st.markdown(
                        f'<span style="background:{color}22;color:{color};padding:4px 12px;border-radius:12px;margin:4px;font-weight:600;">'
                        f'#{t.get("tag","")}（{conf:.2f}）</span>',
                        unsafe_allow_html=True,
                    )
                st.caption(f"耗时 {r.latency_ms}ms")
            else:
                st.error(r.error)

    with tab5:
        st.markdown("**A5 · 自然语言搜索**")
        st.caption("将自然语言查询翻译为结构化筛选条件")
        s_query = st.text_input("查询", value="员工28负责的进行中的 P1 需求", key="a5_query")
        if st.button("运行 A5", key="a5_run"):
            with st.spinner("AI 解析中…"):
                r = ai_helper.parse_search_query(
                    query=s_query, today=TODAY_STR,
                    provider=st.session_state.provider, api_key=st.session_state.api_key,
                    model=st.session_state.model or None,
                )
            if r.success:
                st.json(r.data)
                st.caption(f"耗时 {r.latency_ms}ms")
                matched = apply_filter(get_requirements(), r.data)
                st.info(f"匹配 {len(matched)} 条")
            else:
                st.error(r.error)

    with tab6:
        st.markdown("**A6 · 周报自动生成**")
        st.caption("基于本周数据生成 1-3 条简短提示")
        if st.button("运行 A6（对当前用户）", key="a6_run"):
            selected = st.session_state.current_user_id
            user_name = USER_ID_TO_NAME.get(selected, "")
            week_ago = WEEK_START_STR
            user_reqs = [r for r in get_requirements() if selected in r.get("owner_ids", [])]
            stats = {
                "new_count": sum(1 for r in user_reqs if r.get("created_at", "") >= week_ago),
                "due_count": sum(1 for r in user_reqs if TODAY_STR <= r.get("requested_due_date", "9999") <= (TODAY + timedelta(days=7)).strftime("%Y-%m-%d")),
                "done_count": sum(1 for r in user_reqs if r.get("actual_completion_date", "") >= week_ago),
                "pending_count": 0,
                "blocked_count": sum(1 for r in user_reqs if r.get("status") == "暂停"),
                "completed_count": sum(1 for r in user_reqs if r.get("actual_completion_date", "") >= week_ago),
                "active_count": sum(1 for r in user_reqs if r.get("status") in ["待启动", "进行中", "暂停"]),
            }
            stats["pending_count"] = max(stats["due_count"] - stats["done_count"], 0)
            high_pri = [r["id"] for r in user_reqs if r.get("priority") in ["P0", "P1"] and r.get("status") not in ["已终止", "已完成"]]

            with st.spinner("AI 生成中…"):
                r = ai_helper.generate_weekly_briefing(
                    user_name=user_name, stats=stats, high_pri_list=high_pri,
                    provider=st.session_state.provider, api_key=st.session_state.api_key,
                    model=st.session_state.model or None,
                )
            if r.success:
                st.json({"stats": stats, "tips": r.data})
                st.caption(f"耗时 {r.latency_ms}ms")
            else:
                st.error(r.error)
